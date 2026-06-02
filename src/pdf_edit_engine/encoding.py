"""Font encoding resolver — bidirectional mapping between content stream bytes and Unicode."""

from __future__ import annotations

import io
import logging
import unicodedata

import pikepdf
from pdfminer.cmapdb import CMapParser, FileUnicodeMap
from pdfminer.encodingdb import EncodingDB

from pdf_edit_engine._pathutil import (
    MAX_FONT_STREAM_BYTES,
    MAX_TOUNICODE_BYTES,
    read_stream_bounded,
)
from pdf_edit_engine.errors import EncodingError, FontStreamTooLargeError

logger = logging.getLogger(__name__)


def _build_reverse_map(forward: dict[int, str]) -> tuple[dict[str, int], dict[str, int]]:
    """Build the Unicode→byte/CID reverse maps, splitting single from ligature keys.

    Returns ``(primary, ligatures)``:

    * ``primary`` — single-codepoint Unicode value (``len == 1``) → lowest code.
      A precomposed ligature *character* (e.g. ``"ﬁ"`` 'ﬁ', ``len == 1``)
      is a real single glyph and belongs HERE, not in ``ligatures``.
    * ``ligatures`` — multi-codepoint Unicode value (``len(value) > 1``, e.g. a
      typed ``"fi"``) → lowest code. These are the dangerous collapse keys,
      routed to a separate map so the default encode path never greedily
      collapses typed-separate text against them (INV-B-9).

    When multiple codes map to the same Unicode value (e.g. WinAnsi maps both
    0x20 and 0xAD to space), the lowest code wins. The lowest-code tie-break is
    preserved INDEPENDENTLY within each map via the ``sorted(forward)``
    iteration + first-wins, so standard ASCII bytes are still used for encoding.

    Args:
        forward: The byte/CID → Unicode forward map.

    Returns:
        A ``(primary, ligatures)`` 2-tuple of reverse maps.
    """
    primary: dict[str, int] = {}
    ligatures: dict[str, int] = {}
    for code in sorted(forward):
        val = forward[code]
        target = primary if len(val) == 1 else ligatures
        if val not in target:
            target[val] = code
    return primary, ligatures


def _classify_ligature(uval: str) -> str:
    """Classify a multi-codepoint ligature Unicode value (INV-B-9).

    Returns ``"discretionary"`` or ``"mandatory"``.

    * ``discretionary`` — NFKC normalization decomposes the value into plain
      base letters, i.e. the ligature is a typographic nicety the engine may
      safely DECLINE to apply (a typed ``"fi"``/``"ft"``, or a precomposed Latin
      presentation form U+FB00-U+FB06 whose NFKC expands to ASCII letters).
      These are OFF by default — the replacement text's separate glyphs render
      fine.
    * ``mandatory`` — NFKC is a no-op (or does not yield plain Latin base
      letters), e.g. an Arabic presentation form that has no plain-letter
      spacing equivalent. These MUST be applied or the text is unrenderable.

    A precomposed ligature *character* (``len == 1``, e.g. ``"ﬁ"``) never
    reaches this function — ``_build_reverse_map`` routes it to the primary map.

    Args:
        uval: The multi-codepoint ligature Unicode value (typed pair etc.).

    Returns:
        ``"discretionary"`` or ``"mandatory"``.
    """
    nfkc = unicodedata.normalize("NFKC", uval)
    # NFKC decomposes a Latin presentation form into a run of plain letters
    # (category L*) that are representable as separate glyphs → discretionary.
    if nfkc != uval and all(unicodedata.category(c).startswith("L") for c in nfkc):
        return "discretionary"
    # A typed multi-letter run already all plain Latin/Latin-Extended letters
    # (e.g. "fi" == "fi" under NFKC) renders fine as separate glyphs → also
    # discretionary. The ``< 0x0250`` guard keeps this rule to Latin so a value
    # with combining marks or non-Latin letters does NOT get silently declined.
    if all(unicodedata.category(c).startswith("L") and ord(c) < 0x0250 for c in uval):
        return "discretionary"
    return "mandatory"


def _is_pua(codepoint: int) -> bool:
    """Return True iff ``codepoint`` is in a Unicode Private-Use Area.

    Covers the BMP PUA (U+E000-F8FF) and the two supplementary PUA planes
    (U+F0000-FFFFD, U+100000-10FFFD). Used by the B.3 ToUnicode-absent
    recovery gate: PUA codepoints carry no real text, so they are dropped
    from a recovered map and a majority-PUA map is rejected outright.
    """
    return (
        0xE000 <= codepoint <= 0xF8FF
        or 0xF0000 <= codepoint <= 0xFFFFD
        or 0x100000 <= codepoint <= 0x10FFFD
    )


class FontResolver:
    """Resolves font encoding for bidirectional byte <-> Unicode conversion.

    Supports Identity-H CIDFont (2-byte CIDs via ToUnicode CMap),
    WinAnsiEncoding, MacRomanEncoding, and custom encodings with /Differences.

    Args:
        font_dict: The pikepdf font dictionary object.
        font_name: The font resource name (e.g., 'F1').
    """

    def __init__(
        self,
        font_dict: pikepdf.Dictionary,
        font_name: str,
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

        # INV-B-9: multi-codepoint ligature values are split out of the primary
        # reverse map so the default encode path never greedily collapses
        # typed-separate text. Populated only on the Identity-H CID path (simple
        # byte encodings never carry multi-codepoint values); the byte branch of
        # encode/can_encode never consults it.
        self._ligature_to_cid: dict[str, int] = {}

        # Max ligature length for greedy encoding
        self._max_ligature_len: int = 1

        # B.3 (v0.2.0): ToUnicode-absent Identity-H recovery state.
        # _tounicode_recovered is True when _cid_to_unicode was rebuilt by
        # inverting the embedded font's cmap (no /ToUnicode present and the
        # recovery gates passed). _untextable_cidfont is True when a Type0
        # font has no /ToUnicode AND recovery was impossible (gates failed,
        # no /FontFile2, empty cmap, or a majority-PUA recovered map). The
        # WRITE path (surgeon) reads _tounicode_recovered to refuse
        # new-glyph extension on a recovered font (the /ToUnicode write is
        # out of scope here); callers surface _untextable_cidfont as a typed
        # Degradation. Both default False — only the Identity-H branch sets
        # them.
        self._tounicode_recovered: bool = False
        self._untextable_cidfont: bool = False

        # v0.1.3 (Phase 5) can_encode strengthening — coverage check.
        # /FirstChar..LastChar bounds and /Widths-key set let us verify
        # the byte slot is actually populated in the embedded font. The
        # font_dict reference enables a back-pointer to the binary for
        # the fonts.font_has_codepoint cmap-coverage check (no fontTools
        # import in this module — preserves dep-boundary table).
        self._font_dict: pikepdf.Dictionary = font_dict
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
        """Initialize from a Type0/Identity-H CIDFont.

        Primary path: parse ``/ToUnicode`` into the CID→Unicode map. Two
        embedded-cmap recovery paths then reconcile read-side integrity gaps,
        both delegating the raw cmap inversion to ``fonts.reverse_embedded_cmap``
        (gated per the M0 spike verdict, see ``_recover_cid_to_unicode``):

        - **B.3 — whole-map absent.** When ``/ToUnicode`` is absent (or parses
          to an empty map) the font would otherwise have NO text mapping —
          ``find()``/``get_text`` return nothing. The whole map is recovered;
          ``_tounicode_recovered`` is set so the WRITE path refuses extension.
        - **B.5 — partial map.** When ``/ToUnicode`` maps *some* CIDs but omits
          others, ``decode()`` raises ``KeyError`` on an unmapped CID and the
          locator drops the *entire* run that contains it. The omitted CIDs are
          filled ADDITIVELY from the embedded cmap (see
          ``_reconcile_partial_tounicode``). CIDs already present in
          ``/ToUnicode`` are authoritative and never overwritten, so a font
          with complete, correct ``/ToUnicode`` is unaffected; and because the
          real map is preserved, ``_tounicode_recovered`` stays ``False`` (the
          font remains writable — the gap was a hole, not a missing map).
        """
        self._encoding_type = "Identity-H"
        self._is_cid = True
        self._byte_width = 2

        if "/ToUnicode" in font_dict:
            tu_stream = font_dict["/ToUnicode"]
            try:
                # A1.3 / INV-W-4: bound the ToUnicode CMap decode. On a bomb,
                # leave _cid_to_unicode empty and fall into the recovery branch
                # below so find()/get_text() never crash on a bombed ToUnicode.
                tu_bytes: bytes = read_stream_bounded(
                    tu_stream, max_decoded=MAX_TOUNICODE_BYTES, label="ToUnicode"
                )
                cmap = FileUnicodeMap()
                CMapParser(cmap, io.BytesIO(tu_bytes)).run()
                self._cid_to_unicode = dict(cmap.cid2unichr)
            except FontStreamTooLargeError:
                logger.warning(
                    "Type0 font %s has an oversize ToUnicode CMap (INV-W-4 bound); "
                    "treating as no usable ToUnicode",
                    self._font_name,
                )

        if not self._cid_to_unicode:
            # No /ToUnicode (or it yielded nothing): attempt gated embedded-
            # cmap recovery (B.3). On failure the font is flagged untextable so
            # callers can surface it; the maps stay empty and the locator
            # fails safe (KeyError drops the element — missing, not corrupt).
            logger.warning("Type0 font %s has no usable ToUnicode CMap", self._font_name)
            self._cid_to_unicode = self._recover_cid_to_unicode(font_dict)
        else:
            # Non-empty /ToUnicode (B.3 did NOT fire): fill any CIDs the map
            # omits from the embedded cmap, additively (B.5). Disjoint from the
            # branch above by construction.
            self._reconcile_partial_tounicode(font_dict)

        self._unicode_to_cid, self._ligature_to_cid = _build_reverse_map(self._cid_to_unicode)

        # Track max ligature length for greedy encode. Now purely informational
        # for the CID path (the ligature map is consulted directly), but kept
        # because it bounds the greedy longest-match window in _encode_step and
        # is read by INV-A-4.
        for uval in self._cid_to_unicode.values():
            if len(uval) > self._max_ligature_len:
                self._max_ligature_len = len(uval)

    def _recover_cid_to_unicode(self, font_dict: pikepdf.Dictionary) -> dict[int, str]:
        """Recover a CID→Unicode map for a ToUnicode-absent Identity-H font.

        Implements the M0 Rank-2.5 spike gates (READ = partial-with-
        degradation behind gates). Returns the recovered map on success, or
        an empty dict (and sets ``_untextable_cidfont``) when recovery is
        impossible or unsafe:

        1. **Encoding gate.** ``/Encoding`` must be ``/Identity-H`` — the only
           encoding under which the content-stream code equals the CID.
        2. **CIDToGIDMap gate.** The descendant CIDFont's ``/CIDToGIDMap``
           must be ``/Identity`` or absent (defaults to Identity). Any other
           value (a GID-remapping stream) breaks the CID==GID assumption that
           ``reverse_embedded_cmap`` relies on.
        3. **PUA rejection.** Recovered codepoints in the Private-Use Area
           (U+E000-F8FF, U+F0000-FFFFD, U+100000-10FFFD) are dropped — they
           carry no real text. If the recovered map is *majority* PUA the
           font is treated as UNRECOVERED (symbol-encoded subset whose cmap
           is useless for text), so no garbage text is fabricated.

        Args:
            font_dict: The top-level Type0 font dictionary.

        Returns:
            The recovered ``CID → single-char Unicode`` map, or ``{}`` when
            recovery is impossible/unsafe (with ``_untextable_cidfont`` set).
        """
        self._untextable_cidfont = True  # cleared only on successful recovery

        raw = self._gated_embedded_cmap_map(font_dict)
        if not raw:
            return {}

        # Gate 3: drop PUA codepoints; majority-PUA => unrecovered.
        non_pua = {cid: ch for cid, ch in raw.items() if not _is_pua(ord(ch))}
        if len(non_pua) * 2 < len(raw):
            # Majority (>= half) of the recovered codepoints are PUA: the
            # embedded cmap is a symbol/glyph-index table with no real text.
            return {}

        self._untextable_cidfont = False
        self._tounicode_recovered = True
        return non_pua

    def _gated_embedded_cmap_map(self, font_dict: pikepdf.Dictionary) -> dict[int, str]:
        """Invert the embedded ``/FontFile2`` cmap behind the recovery gates.

        Shared core for both embedded-cmap paths — B.3 whole-map recovery
        (:meth:`_recover_cid_to_unicode`) and B.5 partial-map fill
        (:meth:`_reconcile_partial_tounicode`). Performs only the gates that
        guarantee the CID==GID inversion is *meaningful* and returns the raw
        ``CID → single-char Unicode`` candidate map; it sets **no** instance
        flags and applies **no** majority-PUA policy (each caller layers its own
        policy on top):

        1. **Encoding gate.** ``/Encoding`` must be ``/Identity-H`` — the only
           encoding under which the content-stream code equals the CID.
        2. **CIDToGIDMap gate.** The descendant CIDFont's ``/CIDToGIDMap`` must
           be ``/Identity`` or absent. Any GID-remapping stream breaks the
           CID==GID assumption that the inversion relies on.

        Returns ``{}`` when a gate fails, ``/FontFile2`` is absent or unreadable,
        or the embedded font exposes no usable cmap. (CFF ``/FontFile3`` and
        Type1 ``/FontFile`` are out of scope — ARY-279.)

        Args:
            font_dict: The top-level Type0 font dictionary.

        Returns:
            The raw ``CID(==GID) → single-char Unicode`` candidate map, or ``{}``.
        """
        # Gate 1: encoding must be Identity-H.
        encoding_val = font_dict.get("/Encoding")
        if encoding_val is None or str(encoding_val) != "/Identity-H":
            return {}

        descendants = font_dict.get("/DescendantFonts")
        if descendants is None or len(descendants) == 0:
            return {}
        cid_font = descendants[0]

        # Gate 2: /CIDToGIDMap must be /Identity or absent.
        c2g = cid_font.get("/CIDToGIDMap")
        if c2g is not None and str(c2g) != "/Identity":
            return {}

        descriptor = cid_font.get("/FontDescriptor")
        if descriptor is None:
            return {}
        font_file = descriptor.get("/FontFile2")
        if font_file is None:
            return {}

        try:
            # A1.3 / INV-W-4: bound the embedded /FontFile2 decode. A bomb
            # raises FontStreamTooLargeError (an Exception) -> caught here ->
            # returns {} (no recovery map); find()/get_text() stays crash-free.
            font_bytes = bytes(
                read_stream_bounded(font_file, max_decoded=MAX_FONT_STREAM_BYTES, label="font")
            )
        except Exception:  # noqa: BLE001 — best-effort recovery; any read error = no map
            return {}

        # Lazy, function-local import keeps fontTools out of encoding.py's
        # module-level dependencies (CLAUDE.md dep-boundary table), mirroring
        # the can_encode → fonts.font_has_codepoint pattern. The fonts helper
        # owns all fontTools parsing and returns {} on any parse failure.
        from pdf_edit_engine.fonts import recover_cid_to_unicode_from_fontfile2

        return recover_cid_to_unicode_from_fontfile2(font_bytes)

    def _reconcile_partial_tounicode(self, font_dict: pikepdf.Dictionary) -> None:
        """Fill CIDs the parsed ``/ToUnicode`` omits, from the embedded cmap (B.5).

        Called only when ``/ToUnicode`` parsed to a **non-empty** map (B.3's
        whole-map recovery did NOT fire). For each CID present in the embedded
        cmap inversion but absent from ``self._cid_to_unicode``, add the
        inverted codepoint — but ONLY when it is not a Private-Use-Area
        codepoint (PUA carries no real text). CIDs the ``/ToUnicode`` already
        maps are authoritative and left untouched.

        **Coordinate-system alignment gate (soundness).** The inversion from
        ``reverse_embedded_cmap`` is keyed by the embedded font's GID
        (``getGlyphID``). That equals the content-stream CID *only* when the
        embedded font's glyph numbering coincides with the CID space. Many
        producers instead emit **full-font GIDs as CIDs** and embed a
        renumbered subset, bridging the two with ``/ToUnicode``; for those the
        inversion's keys are subset GIDs that do NOT equal the content CIDs, so
        filling them would inject bogus CID→Unicode entries and corrupt the
        reverse map (``_unicode_to_cid``), breaking the WRITE path.

        We detect alignment positively: the inversion must **agree with the
        present /ToUnicode on every CID they share**, with at least one shared
        CID to anchor the proof. Zero disagreement means the two maps live in
        the same coordinate system (CID==embedded-GID), so the inversion's
        other keys are real CIDs and safe to fill. Any disagreement (or no
        overlap at all) means the systems differ — we fill nothing, leaving the
        partial ``/ToUnicode`` exactly as parsed (fail-safe: the locator still
        drops the unmapped run, same as before B.5).

        Net effect:

        - complete, correct ``/ToUnicode`` → zero fills (no-op);
        - partial ``/ToUnicode`` on a CID==GID subset → holes closed soundly;
        - full-GID-CID font (subset-GID inversion) → no fill, no regression.

        Mutates ``self._cid_to_unicode`` in place; intentionally does NOT set
        ``_tounicode_recovered`` / ``_untextable_cidfont`` — the real
        ``/ToUnicode`` is preserved, so the font stays writable and textable.

        Args:
            font_dict: The top-level Type0 font dictionary.
        """
        candidate = self._gated_embedded_cmap_map(font_dict)
        if not candidate:
            return

        # Alignment proof: agreement on shared CIDs, at least one anchor.
        shared = candidate.keys() & self._cid_to_unicode.keys()
        if not shared:
            return
        if any(candidate[cid] != self._cid_to_unicode[cid] for cid in shared):
            return

        for cid, ch in candidate.items():
            if cid in self._cid_to_unicode:
                continue  # /ToUnicode is authoritative — never overwrite.
            if _is_pua(ord(ch)):
                continue  # PUA codepoint carries no real text.
            self._cid_to_unicode[cid] = ch

    def _init_winAnsi(self) -> None:
        """Initialize WinAnsiEncoding from pdfminer's standard table."""
        self._encoding_type = "WinAnsi"
        self._is_cid = False
        self._byte_width = 1

        self._byte_to_unicode = dict(EncodingDB.win2unicode)
        # Simple byte encodings never carry multi-codepoint values, so the
        # ligature map is empty and unused on the byte branch of encode.
        self._unicode_to_byte, _lig = _build_reverse_map(self._byte_to_unicode)

    def _init_macRoman(self) -> None:
        """Initialize MacRomanEncoding from pdfminer's standard table."""
        self._encoding_type = "MacRoman"
        self._is_cid = False
        self._byte_width = 1

        self._byte_to_unicode = dict(EncodingDB.mac2unicode)
        # Simple byte encodings never carry multi-codepoint values.
        self._unicode_to_byte, _lig = _build_reverse_map(self._byte_to_unicode)

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

        # Simple byte encodings never carry multi-codepoint values.
        self._unicode_to_byte, _lig = _build_reverse_map(self._byte_to_unicode)

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

    def _encode_step(
        self,
        text: str,
        i: int,
        *,
        allow_discretionary_ligatures: bool,
    ) -> tuple[int | None, int, str | None]:
        """Decide the CID encoding for ONE step starting at ``text[i]`` (INV-B-9).

        Shared by ``encode`` and ``can_encode`` so the two cannot drift — both
        consult the SAME per-step decision (lockstep). CID path only; the
        non-CID byte branch never calls this.

        Resolution order:

        1. **Mandatory ligatures** — greedy longest match in
           ``_ligature_to_cid`` whose ``_classify_ligature(...)`` is
           ``"mandatory"``. ALWAYS applied (the components have no plain-letter
           spacing equivalent).
        2. **Discretionary ligatures** — same greedy match, applied ONLY when
           ``allow_discretionary_ligatures`` is True. Otherwise fall through to
           single-codepoint encoding (no collapse).
        3. **Single codepoint** — ``text[i]`` in ``_unicode_to_cid``.
        4. **Unencodable** — ``(None, 1, None)``.

        Args:
            text: The full text being encoded.
            i: The index of the step's first character.
            allow_discretionary_ligatures: Opt into discretionary collapse.

        Returns:
            ``(cid_or_None, advance, ligature_kind_or_None)``. ``cid`` is None
            when ``text[i]`` is unencodable (``advance`` is then 1).
            ``ligature_kind`` is ``"mandatory"``/``"discretionary"`` when a
            ligature CID was chosen, else None.
        """
        max_len = min(self._max_ligature_len, len(text) - i)
        for length in range(max_len, 1, -1):  # down to 2; single chars below
            substr = text[i : i + length]
            cid = self._ligature_to_cid.get(substr)
            if cid is None:
                continue
            kind = _classify_ligature(substr)
            if kind == "mandatory":
                return (cid, length, "mandatory")
            if kind == "discretionary" and allow_discretionary_ligatures:
                return (cid, length, "discretionary")
            # discretionary, not allowed → do NOT collapse; fall through.
        ch = text[i]
        single = self._unicode_to_cid.get(ch)
        if single is not None:
            return (single, 1, None)
        return (None, 1, None)

    def _verify_roundtrip(
        self,
        text: str,
        encoded: bytes,
        allow_discretionary_ligatures: bool,
    ) -> None:
        """Self-verify ``decode(encode(text))`` is NFKC-faithful (INV-B-9).

        Runs only on a FULLY-encoded string (an unencodable char raises
        ``KeyError`` before this is reached), so a missing glyph is reported by
        ``can_encode``/``KeyError`` and never silently rebranded here. Both
        sides are NFKC-normalized before comparison, so a benign canonical/
        ligature shape difference (e.g. a legitimate mandatory collapse to a
        precomposed glyph, ``NFKC("ﬁ") == "fi"``) does NOT false-refuse; only a
        genuine glyph-identity loss (a CID decoding to a different string)
        raises.

        Args:
            text: The original requested text.
            encoded: The bytes produced by ``encode``.
            allow_discretionary_ligatures: The flag ``encode`` used (kept for
                signature symmetry / future diagnostics).

        Raises:
            EncodingError: When the decoded text is not NFKC-equal to ``text``.
        """
        decoded = self.decode(encoded)
        if unicodedata.normalize("NFKC", decoded) == unicodedata.normalize("NFKC", text):
            return
        raise EncodingError(
            f"encode/decode round-trip altered text for {self._font_name!r}: "
            f"{text!r} -> {decoded!r}"
        )

    def encode(
        self,
        text: str,
        *,
        allow_discretionary_ligatures: bool = False,
        _observed: list[str] | None = None,
    ) -> bytes:
        """Convert a Unicode string to content stream bytes for this font.

        INV-B-9: typed-separate text is NOT greedily collapsed into a
        discretionary ligature CID by default. A *mandatory* ligature (one with
        no plain-letter spacing equivalent, e.g. an Arabic presentation form) is
        always applied; a *discretionary* ligature (typed Latin "fi", a Latin
        presentation form) is applied only when ``allow_discretionary_ligatures``
        is True. Every CID-path encode self-verifies ``decode(encode(text))`` is
        NFKC-faithful and raises ``EncodingError`` on a genuine glyph-identity
        loss.

        Args:
            text: Unicode text to encode.
            allow_discretionary_ligatures: When True, opt into collapsing typed
                text into a discretionary ligature CID. Default False
                (no-collapse). Must match the value passed to ``can_encode`` for
                the same text (lockstep — see ``can_encode``).
            _observed: Optional out-param. When provided, each SOURCE substring
                that was collapsed into a ligature CID (mandatory or opted-in
                discretionary) is appended, so callers can surface a
                ``ligature_substituted`` Degradation. Never populated on the
                default no-ligature path.

        Returns:
            Encoded bytes suitable for Tj/TJ operators.

        Raises:
            KeyError: If a character cannot be encoded.
            EncodingError: If the encode/decode round-trip is not NFKC-faithful.
        """
        if self._is_cid:
            result = bytearray()
            i = 0
            while i < len(text):
                cid, adv, kind = self._encode_step(
                    text, i, allow_discretionary_ligatures=allow_discretionary_ligatures
                )
                if cid is None:
                    raise KeyError(f"Cannot encode character: {text[i]!r}")
                result.append(cid >> 8)
                result.append(cid & 0xFF)
                if kind is not None and _observed is not None:
                    _observed.append(text[i : i + adv])
                i += adv
            encoded = bytes(result)
            self._verify_roundtrip(text, encoded, allow_discretionary_ligatures)
            return encoded
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

        For Identity-H CID fonts the ``_unicode_to_cid`` check double-duties as
        coverage (per audit-bundle finding #2). INV-B-9: the CID branch now
        drives the SAME ``_encode_step`` decision the DEFAULT ``encode`` uses
        (``allow_discretionary_ligatures=False``) so it never mis-predicts
        encodability.

        LOCKSTEP RULE: ``can_encode`` must be called with the same
        ``allow_discretionary_ligatures`` value the corresponding ``encode``
        will use. For B.9 every write-path caller stays default-OFF, so
        ``can_encode(text)`` exactly predicts ``encode(text)`` encodability. A
        discretionary opt-in only ADDS encodability (never removes it), so the
        default-OFF check is conservative — it never reports a char encodable
        that an opted-in encode would reject.

        Args:
            text: Unicode text to check.

        Returns:
            Tuple of (all_encodable, list_of_missing_characters).
        """
        missing: list[str] = []
        if self._is_cid:
            # Lockstep with the DEFAULT encode (no discretionary collapse).
            i = 0
            while i < len(text):
                cid, adv, _kind = self._encode_step(text, i, allow_discretionary_ligatures=False)
                if cid is None:
                    missing.append(text[i])
                    i += 1
                else:
                    i += adv
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
                # font_has_codepoint re-parses /FontFile2 on every call
                # (no caching — see fonts.font_has_codepoint docstring
                # for the deletion rationale post-ARY-349).
                if not font_has_codepoint(self._font_dict, ord(ch)):
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

    @property
    def is_tounicode_recovered(self) -> bool:
        """Whether the CID→Unicode map was recovered from the embedded cmap.

        True only for Identity-H CIDFonts that shipped no usable ``/ToUnicode``
        and passed the B.3 recovery gates. The WRITE path (surgeon) reads this
        to refuse new-glyph extension on a recovered font — the ``/ToUnicode``
        write needed for extension is out of scope for B.3.
        """
        return self._tounicode_recovered

    @property
    def is_untextable_cidfont(self) -> bool:
        """Whether this Type0 font has no text mapping at all.

        True when a Type0/Identity-H font has no usable ``/ToUnicode`` AND
        embedded-cmap recovery was impossible (gates failed, no ``/FontFile2``,
        empty cmap, or a majority-PUA recovered map). Callers surface this as a
        typed ``untextable_cidfont`` Degradation. ``find()``/``get_text`` still
        fail safe (the locator drops unmappable elements).
        """
        return self._untextable_cidfont


class FontResolverCache:
    """Cache FontResolver instances per font dict to avoid re-parsing.

    Keyed by the FONT DICT's object generation pair (not the page's), so
    pages that share a font via indirect reference resolve to the same
    cached instance. Evicting on any one page clears the entry for every
    page that references the same font dict — critical after
    ``extend_subset`` mutates a shared font (ARY-278).
    """

    def __init__(self) -> None:
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
            self._cache[key] = FontResolver(font_dict, font_name)
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
