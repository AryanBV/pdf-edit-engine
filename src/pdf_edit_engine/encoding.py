"""Font encoding resolver — bidirectional mapping between content stream bytes and Unicode."""

from __future__ import annotations

import io
import logging

import pikepdf
from pdfminer.cmapdb import CMapParser, FileUnicodeMap
from pdfminer.encodingdb import EncodingDB

logger = logging.getLogger(__name__)


def _build_reverse_map(forward: dict[int, str]) -> dict[str, int]:
    """Build Unicode→byte/CID reverse map, preferring the lowest code.

    When multiple codes map to the same Unicode character (e.g. WinAnsi
    maps both 0x20 and 0xAD to space), the lowest code wins.  This
    ensures standard ASCII bytes are used for encoding.
    """
    reverse: dict[str, int] = {}
    for code in sorted(forward):
        char = forward[code]
        if char not in reverse:
            reverse[char] = code
    return reverse


class FontResolver:
    """Resolves font encoding for bidirectional byte <-> Unicode conversion.

    Supports Identity-H CIDFont (2-byte CIDs via ToUnicode CMap),
    WinAnsiEncoding, MacRomanEncoding, and custom encodings with /Differences.

    Args:
        font_dict: The pikepdf font dictionary object.
        font_name: The font resource name (e.g., 'F1').
        pdf: The open ``pikepdf.Pdf`` that owns ``font_dict``. Optional
            for backward compatibility — when ``None`` (e.g. unit tests
            that build resolvers from synthetic dicts), the
            ``fonts.font_has_codepoint`` cmap-coverage check is
            bypassed and ``can_encode`` falls back to its pre-pikepdf-
            10.5.1 best-effort behaviour. Required for the ARY-349
            fix (pikepdf 10.5.1 dropped the back-pointer attribute on
            ``Object``, so the cache key in ``fonts._font_dict_key``
            must receive ``pdf`` out-of-band).
    """

    def __init__(
        self,
        font_dict: pikepdf.Dictionary,
        font_name: str,
        pdf: pikepdf.Pdf | None = None,
    ) -> None:
        self._font_name = font_name
        self._encoding_type: str = "Custom"
        self._is_cid: bool = False
        self._byte_width: int = 1

        # Lookup tables — only one pair is populated depending on encoding
        self._cid_to_unicode: dict[int, str] = {}
        self._unicode_to_cid: dict[str, int] = {}
        self._byte_to_unicode: dict[int, str] = {}
        self._unicode_to_byte: dict[str, int] = {}

        # Max ligature length for greedy encoding
        self._max_ligature_len: int = 1

        # v0.1.3 (Phase 5) can_encode strengthening — coverage check.
        # /FirstChar..LastChar bounds and /Widths-key set let us verify
        # the byte slot is actually populated in the embedded font. The
        # font_dict reference enables a back-pointer to the binary for
        # the fonts.font_has_codepoint cmap-coverage check (no fontTools
        # import in this module — preserves dep-boundary table).
        self._font_dict: pikepdf.Dictionary = font_dict
        self._pdf: pikepdf.Pdf | None = pdf
        self._first_char: int | None = None
        self._last_char: int | None = None
        self._widths_keys: frozenset[int] = frozenset()

        self._detect_and_init(font_dict)
        self._init_widths_bounds(font_dict)

    def _init_widths_bounds(self, font_dict: pikepdf.Dictionary) -> None:
        """Populate /FirstChar, /LastChar, /Widths-key set for simple fonts.

        CID fonts use /W (range-based) instead — those are handled by
        widths.parse_cid_widths and aren't checked here. The CID branch
        of can_encode already performs coverage checks via the ToUnicode
        CMap (audit-bundle finding #2: CID's _unicode_to_cid double-duties
        as coverage).
        """
        if self._is_cid:
            return
        first = font_dict.get("/FirstChar")
        last = font_dict.get("/LastChar")
        widths = font_dict.get("/Widths")
        try:
            if first is not None and last is not None:
                self._first_char = int(first)
                self._last_char = int(last)
            if widths is not None and self._first_char is not None and self._last_char is not None:
                # /Widths is an array starting at /FirstChar. A byte b has
                # an explicit width when first_char + i == b for some i in
                # range(len(widths)) AND widths[i] is non-zero (zero typically
                # means the slot is reserved but unmapped).
                widths_list = list(widths)  # type: ignore[call-overload]
                self._widths_keys = frozenset(
                    self._first_char + i for i, w in enumerate(widths_list) if float(w) != 0.0
                )
        except (TypeError, ValueError):
            # Malformed /Widths or /FirstChar — leave as None/empty;
            # can_encode falls back to encoding-map membership only.
            self._first_char = None
            self._last_char = None
            self._widths_keys = frozenset()

    def _detect_and_init(self, font_dict: pikepdf.Dictionary) -> None:
        """Detect encoding type and initialize lookup tables."""
        subtype_obj = font_dict.get("/Subtype")
        subtype = str(subtype_obj) if subtype_obj is not None else ""
        encoding_val = font_dict.get("/Encoding")

        if subtype == "/Type0":
            self._init_identity_h(font_dict)
        elif encoding_val is not None:
            encoding_str = str(encoding_val)
            if encoding_str == "/WinAnsiEncoding":
                self._init_winAnsi()
            elif encoding_str == "/MacRomanEncoding":
                self._init_macRoman()
            elif isinstance(encoding_val, pikepdf.Dictionary):
                self._init_custom(encoding_val)
            else:
                self._init_winAnsi()
        else:
            # Default to WinAnsi for simple fonts without explicit encoding
            self._init_winAnsi()

    def _init_identity_h(self, font_dict: pikepdf.Dictionary) -> None:
        """Initialize from a Type0/Identity-H CIDFont with ToUnicode CMap."""
        self._encoding_type = "Identity-H"
        self._is_cid = True
        self._byte_width = 2

        if "/ToUnicode" not in font_dict:
            logger.warning("Type0 font %s has no ToUnicode CMap", self._font_name)
            return

        tu_stream = font_dict["/ToUnicode"]
        tu_bytes: bytes = tu_stream.read_bytes()
        cmap = FileUnicodeMap()
        CMapParser(cmap, io.BytesIO(tu_bytes)).run()

        self._cid_to_unicode = dict(cmap.cid2unichr)
        self._unicode_to_cid = _build_reverse_map(self._cid_to_unicode)

        # Track max ligature length for greedy encode
        for uval in self._cid_to_unicode.values():
            if len(uval) > self._max_ligature_len:
                self._max_ligature_len = len(uval)

    def _init_winAnsi(self) -> None:
        """Initialize WinAnsiEncoding from pdfminer's standard table."""
        self._encoding_type = "WinAnsi"
        self._is_cid = False
        self._byte_width = 1

        self._byte_to_unicode = dict(EncodingDB.win2unicode)
        self._unicode_to_byte = _build_reverse_map(self._byte_to_unicode)

    def _init_macRoman(self) -> None:
        """Initialize MacRomanEncoding from pdfminer's standard table."""
        self._encoding_type = "MacRoman"
        self._is_cid = False
        self._byte_width = 1

        self._byte_to_unicode = dict(EncodingDB.mac2unicode)
        self._unicode_to_byte = _build_reverse_map(self._byte_to_unicode)

    def _init_custom(self, encoding_dict: pikepdf.Dictionary) -> None:
        """Initialize custom encoding with /BaseEncoding + /Differences."""
        self._encoding_type = "Custom"
        self._is_cid = False
        self._byte_width = 1

        # Start from base encoding
        base_obj = encoding_dict.get("/BaseEncoding")
        base = str(base_obj) if base_obj is not None else "/WinAnsiEncoding"
        if base == "/MacRomanEncoding":
            self._byte_to_unicode = dict(EncodingDB.mac2unicode)
        else:
            self._byte_to_unicode = dict(EncodingDB.win2unicode)

        # Apply /Differences overrides
        if "/Differences" in encoding_dict:
            diffs = encoding_dict["/Differences"]
            code = 0
            for item in list(diffs):  # type: ignore[call-overload]
                item_str = str(item)
                if not item_str.startswith("/"):
                    code = int(item)
                else:
                    glyph_name = item_str.lstrip("/")
                    unicode_char = _glyph_name_to_unicode(glyph_name)
                    if unicode_char is not None:
                        self._byte_to_unicode[code] = unicode_char
                    code += 1

        self._unicode_to_byte = _build_reverse_map(self._byte_to_unicode)

    # ── Public API ──────────────────────────────────────────────────────

    def decode(self, raw_bytes: bytes) -> str:
        """Convert content stream bytes to a Unicode string.

        Args:
            raw_bytes: Raw bytes from a Tj/TJ string operand.

        Returns:
            Decoded Unicode string.

        Raises:
            KeyError: If a byte/CID has no mapping.
        """
        if self._is_cid:
            result: list[str] = []
            for i in range(0, len(raw_bytes), 2):
                cid = (raw_bytes[i] << 8) | raw_bytes[i + 1]
                result.append(self._cid_to_unicode[cid])
            return "".join(result)
        else:
            return "".join(self._byte_to_unicode[b] for b in raw_bytes)

    def encode(self, text: str) -> bytes:
        """Convert a Unicode string to content stream bytes for this font.

        Uses greedy longest-match for ligature substitution in CID fonts.

        Args:
            text: Unicode text to encode.

        Returns:
            Encoded bytes suitable for Tj/TJ operators.

        Raises:
            KeyError: If a character cannot be encoded.
        """
        if self._is_cid:
            result = bytearray()
            i = 0
            while i < len(text):
                # Greedy longest-match for ligatures
                matched = False
                max_len = min(self._max_ligature_len, len(text) - i)
                for length in range(max_len, 0, -1):
                    substr = text[i : i + length]
                    if substr in self._unicode_to_cid:
                        cid = self._unicode_to_cid[substr]
                        result.append(cid >> 8)
                        result.append(cid & 0xFF)
                        i += length
                        matched = True
                        break
                if not matched:
                    raise KeyError(f"Cannot encode character: {text[i]!r}")
            return bytes(result)
        else:
            return bytes(self._unicode_to_byte[ch] for ch in text)

    def can_encode(self, text: str) -> tuple[bool, list[str]]:
        """Check if all characters in the text can be encoded with this font.

        v0.1.3 (Phase 5, audit-bundle scope): the non-CID branch was
        strengthened from "encoding-map membership only" to a three-step
        coverage check:

        1. ``ch`` is in ``_unicode_to_byte`` (existing — encoding map).
        2. The mapped byte falls in ``[/FirstChar, /LastChar]`` AND has a
           non-zero entry in ``/Widths`` (verifies the slot is populated
           in the PDF font dict, not just the abstract encoding table).
        3. ``ord(ch)`` maps to a glyph in the embedded ``/FontFile2``
           (delegated to ``fonts.font_has_codepoint`` to keep fontTools
           out of this module). When ``/FontFile2`` cannot be parsed,
           the helper returns True (best-effort) so we don't regress on
           unverifiable fonts.

        For Identity-H CID fonts the existing greedy ``_unicode_to_cid``
        check already double-duties as coverage (per audit-bundle finding
        #2), so the CID branch is unchanged.

        Args:
            text: Unicode text to check.

        Returns:
            Tuple of (all_encodable, list_of_missing_characters).
        """
        missing: list[str] = []
        if self._is_cid:
            # Greedy longest-match — same algorithm as encode() so that
            # ligature sequences (e.g. 'fi') are recognised as encodable.
            i = 0
            while i < len(text):
                matched = False
                max_len = min(self._max_ligature_len, len(text) - i)
                for length in range(max_len, 0, -1):
                    if text[i : i + length] in self._unicode_to_cid:
                        i += length
                        matched = True
                        break
                if not matched:
                    missing.append(text[i])
                    i += 1
        else:
            # Lazy import to avoid circular: encoding ← fonts requires
            # fontTools, but encoding itself must NOT import fontTools
            # (CLAUDE.md dep-boundary table). The lazy import keeps the
            # boundary intact at module-load time.
            from pdf_edit_engine.fonts import font_has_codepoint

            have_widths_metadata = (
                self._first_char is not None
                and self._last_char is not None
                and bool(self._widths_keys)
            )
            for ch in text:
                # Check 1: encoding-map membership (existing).
                if ch not in self._unicode_to_byte:
                    missing.append(ch)
                    continue

                byte_val = self._unicode_to_byte[ch]

                # Check 2: byte in /FirstChar..LastChar AND /Widths entry.
                # Skip when the font dict lacks these (some legacy fonts
                # omit /FirstChar/LastChar — best-effort fall-through).
                if have_widths_metadata:
                    assert self._first_char is not None  # narrowed by guard
                    assert self._last_char is not None
                    if not (self._first_char <= byte_val <= self._last_char):
                        missing.append(ch)
                        continue
                    if byte_val not in self._widths_keys:
                        missing.append(ch)
                        continue

                # Check 3: codepoint covered by the embedded /FontFile2.
                # Skipped when ``self._pdf`` is None (pre-ARY-349 callers
                # that constructed FontResolver without a pdf reference,
                # e.g. unit tests). pikepdf 10.5.1 removed the back-
                # pointer attribute that earlier versions exposed on
                # Object, so the pdf must be threaded in explicitly to
                # key the _FONTFILE2_CACHE.
                if self._pdf is not None and not font_has_codepoint(
                    self._pdf, self._font_dict, ord(ch)
                ):
                    missing.append(ch)
                    continue
        return (len(missing) == 0, missing)

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def encoding_type(self) -> str:
        """Encoding type: 'Identity-H', 'WinAnsi', 'MacRoman', or 'Custom'."""
        return self._encoding_type

    @property
    def is_cid_font(self) -> bool:
        """Whether this is a CIDFont (2-byte encoding)."""
        return self._is_cid

    @property
    def byte_width(self) -> int:
        """Bytes per character: 1 for simple fonts, 2 for CIDFont."""
        return self._byte_width


class FontResolverCache:
    """Cache FontResolver instances per font dict to avoid re-parsing.

    Keyed by the FONT DICT's object generation pair (not the page's), so
    pages that share a font via indirect reference resolve to the same
    cached instance. Evicting on any one page clears the entry for every
    page that references the same font dict — critical after
    ``extend_subset`` mutates a shared font (ARY-278).
    """

    def __init__(self, pdf: pikepdf.Pdf | None = None) -> None:
        # ``pdf`` is threaded into each FontResolver so that the
        # ``fonts.font_has_codepoint`` coverage check can build a stable
        # _FONTFILE2_CACHE key under pikepdf 10.5.1 (which removed the
        # back-pointer attribute that earlier versions exposed on
        # Object — see ARY-349). Optional for backward compatibility
        # with callers that don't have the pdf in scope (notably
        # ``locator.ContentStreamInterpreter``, which only decodes
        # text and never needs the cmap-coverage check).
        self._pdf: pikepdf.Pdf | None = pdf
        self._cache: dict[tuple[int, int, str], FontResolver] = {}

    def clear(self) -> None:
        """Discard all cached FontResolver instances."""
        self._cache.clear()

    def _make_key(
        self,
        page: pikepdf.Page,
        font_name: str,
    ) -> tuple[int, int, str]:
        """Compute the cache key from the font dict's objgen.

        Shared fonts (indirect references from multiple pages) all
        resolve to the same ``(font_obj_gen, font_name)`` key.
        """
        font_key = font_name if font_name.startswith("/") else f"/{font_name}"
        font_obj = page["/Resources"]["/Font"][font_key]
        try:
            objgen = font_obj.objgen
        except AttributeError:
            objgen = (0, 0)  # inline (direct) font dict — rare
        return (objgen[0], objgen[1], font_name)

    def evict(self, page: pikepdf.Page, font_name: str) -> None:
        """Remove a cached resolver (clears all pages sharing the font)."""
        key = self._make_key(page, font_name)
        self._cache.pop(key, None)

    def get_resolver(
        self,
        page: pikepdf.Page,
        font_name: str,
    ) -> FontResolver:
        """Get or create a FontResolver for a font on a page.

        Args:
            page: The pikepdf Page object.
            font_name: Font resource name (e.g., 'F1', without '/').

        Returns:
            A FontResolver instance. Pages sharing the font via
            indirect reference share one cached instance.
        """
        key = self._make_key(page, font_name)
        if key not in self._cache:
            font_key = font_name if font_name.startswith("/") else f"/{font_name}"
            font_obj = page["/Resources"]["/Font"][font_key]
            font_dict = pikepdf.Dictionary(font_obj)  # type: ignore[arg-type]
            self._cache[key] = FontResolver(font_dict, font_name, self._pdf)
        return self._cache[key]


def _glyph_name_to_unicode(name: str) -> str | None:
    """Convert an Adobe glyph name to a Unicode character.

    Handles 'uniXXXX' format and common glyph names via pdfminer.

    Args:
        name: Adobe glyph name (e.g., 'A', 'space', 'uni0041').

    Returns:
        Unicode character, or None if unmappable.
    """
    # Handle uniXXXX format
    if name.startswith("uni") and len(name) == 7:
        try:
            return chr(int(name[3:], 16))
        except ValueError:
            pass

    # pdfminer.six is a hard dependency in pyproject.toml, so the import
    # never fails at runtime — no fallback dict is needed.
    from pdfminer.glyphlist import glyphname2unicode

    return glyphname2unicode.get(name)
