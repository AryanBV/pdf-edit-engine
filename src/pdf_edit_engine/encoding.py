"""Font encoding resolver — bidirectional mapping between content stream bytes and Unicode."""

from __future__ import annotations

import io
import logging

import pikepdf
from pdfminer.cmapdb import CMapParser, FileUnicodeMap
from pdfminer.encodingdb import EncodingDB

logger = logging.getLogger(__name__)


class FontResolver:
    """Resolves font encoding for bidirectional byte <-> Unicode conversion.

    Supports Identity-H CIDFont (2-byte CIDs via ToUnicode CMap),
    WinAnsiEncoding, MacRomanEncoding, and custom encodings with /Differences.

    Args:
        font_dict: The pikepdf font dictionary object.
        font_name: The font resource name (e.g., 'F1').
    """

    def __init__(self, font_dict: pikepdf.Dictionary, font_name: str) -> None:
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

        self._detect_and_init(font_dict)

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
        self._unicode_to_cid = {v: k for k, v in self._cid_to_unicode.items()}

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
        self._unicode_to_byte = {v: k for k, v in self._byte_to_unicode.items()}

    def _init_macRoman(self) -> None:
        """Initialize MacRomanEncoding from pdfminer's standard table."""
        self._encoding_type = "MacRoman"
        self._is_cid = False
        self._byte_width = 1

        self._byte_to_unicode = dict(EncodingDB.mac2unicode)
        self._unicode_to_byte = {v: k for k, v in self._byte_to_unicode.items()}

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

        self._unicode_to_byte = {v: k for k, v in self._byte_to_unicode.items()}

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

        Args:
            text: Unicode text to check.

        Returns:
            Tuple of (all_encodable, list_of_missing_characters).
        """
        missing: list[str] = []
        if self._is_cid:
            for ch in text:
                if ch not in self._unicode_to_cid:
                    missing.append(ch)
        else:
            for ch in text:
                if ch not in self._unicode_to_byte:
                    missing.append(ch)
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
    """Cache FontResolver instances per PDF to avoid re-parsing font dicts.

    Uses the page object's PDF object generation pair and font name as key.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[int, int, str], FontResolver] = {}

    def clear(self) -> None:
        """Discard all cached FontResolver instances."""
        self._cache.clear()

    def evict(self, page: pikepdf.Page, font_name: str) -> None:
        """Remove a specific cached resolver (e.g. after font extension)."""
        objgen: tuple[int, int] = page.obj.objgen
        key = (objgen[0], objgen[1], font_name)
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
            A FontResolver instance (cached per page+font).
        """
        objgen: tuple[int, int] = page.obj.objgen
        key = (objgen[0], objgen[1], font_name)
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

    # Try pdfminer's glyph name database
    try:
        from pdfminer.glyphlist import glyphname2unicode

        return glyphname2unicode.get(name)
    except ImportError:
        _COMMON: dict[str, str] = {
            "space": " ",
            "period": ".",
            "comma": ",",
            "hyphen": "-",
            "colon": ":",
            "semicolon": ";",
        }
        return _COMMON.get(name)
