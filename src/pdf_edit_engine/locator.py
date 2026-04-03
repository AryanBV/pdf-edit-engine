"""TextLocator module — find text in PDFs with operator-level precision."""

from __future__ import annotations

import io as _io
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pikepdf

from pdf_edit_engine.encoding import FontResolverCache
from pdf_edit_engine.fragments import TJReconstructor
from pdf_edit_engine.models import (
    ContentElement,
    FontInfo,
    TextCharacter,
)
from pdf_edit_engine.state import GraphicsStateTracker

if TYPE_CHECKING:
    from pdf_edit_engine.encoding import FontResolver
    from pdf_edit_engine.models import TextMatch

logger = logging.getLogger(__name__)

_DEFAULT_WIDTH: float = 600.0


# ── Glyph width helpers ───────────────────────────────────────────────


def _parse_cid_widths(cid_font: pikepdf.Dictionary) -> dict[int, float]:
    """Parse a CIDFont /W array into a CID → width mapping.

    The /W array uses two formats:
    - [cid_start [w1 w2 ...]] — consecutive widths starting at cid_start
    - [cid_start cid_end width] — range of CIDs with the same width

    Args:
        cid_font: The CIDFont dictionary (DescendantFonts[0]).

    Returns:
        Dict mapping CID integers to widths in font units.
    """
    widths: dict[int, float] = {}
    if "/W" not in cid_font:
        return widths
    w_array: pikepdf.Array = cid_font["/W"]  # type: ignore[assignment]
    w_items: list[pikepdf.Object] = list(w_array)  # type: ignore[call-overload]
    i = 0
    while i < len(w_items):
        cid_start = int(w_items[i])
        i += 1
        if i >= len(w_items):
            break
        next_item = w_items[i]
        if isinstance(next_item, pikepdf.Array):
            # [cid_start [w1, w2, ...]]
            for j in range(len(next_item)):
                widths[cid_start + j] = float(next_item[j])
            i += 1
        else:
            # [cid_start cid_end width]
            if i + 1 >= len(w_items):
                break
            cid_end = int(next_item)
            width = float(w_items[i + 1])
            for cid in range(cid_start, cid_end + 1):
                widths[cid] = width
            i += 2
    return widths


def _parse_simple_widths(font_dict: pikepdf.Dictionary) -> dict[int, float]:
    """Parse a simple font /Widths array into a char_code → width mapping.

    Args:
        font_dict: The font dictionary.

    Returns:
        Dict mapping character codes to widths in font units.
    """
    widths: dict[int, float] = {}
    if "/Widths" not in font_dict:
        return widths
    first_char_obj = font_dict.get("/FirstChar")
    first_char = int(first_char_obj) if first_char_obj is not None else 0
    w_arr: pikepdf.Array = font_dict["/Widths"]  # type: ignore[assignment]
    for i in range(len(w_arr)):
        widths[first_char + i] = float(w_arr[i])
    return widths


class _GlyphWidthCache:
    """Caches parsed font width tables for efficient per-glyph lookups."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[int, float]] = {}

    def get_width(
        self,
        page: pikepdf.Page,
        font_name: str,
        char_code: int,
    ) -> float:
        """Return glyph width in font units (divide by 1000 for text space).

        Args:
            page: The page containing the font.
            font_name: Font resource name (e.g., 'F1').
            char_code: The character/CID code.

        Returns:
            Width in font units. Defaults to 600 if lookup fails.
        """
        if font_name not in self._cache:
            self._cache[font_name] = self._load_widths(page, font_name)
        return self._cache[font_name].get(char_code, _DEFAULT_WIDTH)

    def _load_widths(
        self, page: pikepdf.Page, font_name: str,
    ) -> dict[int, float]:
        font_key = font_name if font_name.startswith("/") else f"/{font_name}"
        try:
            font_obj = page["/Resources"]["/Font"][font_key]
        except (KeyError, TypeError):
            logger.warning("Font %s not found in page resources", font_name)
            return {}
        font_dict = pikepdf.Dictionary(font_obj)  # type: ignore[arg-type]
        subtype_obj = font_dict.get("/Subtype")
        subtype = str(subtype_obj) if subtype_obj is not None else ""
        if subtype == "/Type0":
            # CIDFont — widths in DescendantFonts[0]/W
            try:
                cid_font = font_dict["/DescendantFonts"][0]
                return _parse_cid_widths(
                    pikepdf.Dictionary(cid_font),  # type: ignore[arg-type]
                )
            except (KeyError, IndexError):
                logger.warning("Cannot parse /W for CIDFont %s", font_name)
                return {}
        else:
            return _parse_simple_widths(font_dict)


# ── Content stream interpretation ──────────────────────────────────────


# Operators handled by GraphicsStateTracker
_STATE_OPS: frozenset[str] = frozenset({
    "q", "Q", "cm", "BT", "ET", "Tm", "Td", "TD", "T*",
    "Tf", "Tc", "Tw", "Tz", "TL", "Tr", "Ts",
    "g", "G", "rg", "RG", "k", "K",
    "cs", "CS", "sc", "SC", "scn", "SCN",
})

# Path construction operators
_PATH_CONSTRUCT_OPS: frozenset[str] = frozenset({"m", "l", "c", "v", "y", "re", "h"})

# Path painting / termination operators
_PATH_PAINT_OPS: frozenset[str] = frozenset({
    "S", "s", "f", "F", "f*", "B", "b", "B*", "b*", "n",
})

# Color operators that emit state_change elements
_COLOR_OPS: frozenset[str] = frozenset({"g", "G", "rg", "RG", "k", "K"})


class ContentStreamInterpreter:
    """Walks a page's content stream and produces a list of ContentElement records.

    Args:
        page: The pikepdf Page object.
        page_number: 0-indexed page number.
    """

    def __init__(self, page: pikepdf.Page, page_number: int) -> None:
        self._page = page
        self._page_number = page_number
        self._tracker = GraphicsStateTracker()
        self._font_cache = FontResolverCache()
        self._width_cache = _GlyphWidthCache()
        self._current_resolver: FontResolver | None = None
        self._reconstructor: TJReconstructor | None = None
        self._elements: list[ContentElement] = []
        # Path accumulation
        self._path_points: list[tuple[float, float]] = []
        self._path_start_index: int = 0

    def interpret(self) -> list[ContentElement]:
        """Parse and walk the content stream, building the element index.

        Returns:
            List of ContentElement records covering all content.
        """
        ops = pikepdf.parse_content_stream(self._page)
        for idx, instruction in enumerate(ops):
            operands = instruction.operands
            operator = instruction.operator
            op_str = str(operator)
            self._dispatch(idx, op_str, list(operands))
        return self._elements

    def _dispatch(
        self, idx: int, op: str, operands: list[object],
    ) -> None:
        """Route an operator to the appropriate handler."""
        # State operators
        if op in _STATE_OPS:
            self._tracker.process_operator(op, operands)
            if op == "Tf":
                self._on_tf(operands)
            if op in _COLOR_OPS:
                self._emit_state_change(idx)
            return

        # Text-showing operators
        if op == "TJ":
            self._handle_tj_array(idx, operands)
            return
        if op == "Tj":
            self._handle_tj_single(idx, operands)
            return
        if op == "'":
            self._handle_quote(idx, operands)
            return
        if op == '"':
            self._handle_double_quote(idx, operands)
            return

        # Path construction
        if op in _PATH_CONSTRUCT_OPS:
            self._accumulate_path(idx, op, operands)
            return

        # Path painting / termination
        if op in _PATH_PAINT_OPS:
            self._emit_path(idx)
            return

        # XObject invocation
        if op == "Do":
            self._handle_do(idx, operands)
            return

        # Clipping operators — just track for path accumulation
        if op in {"W", "W*"}:
            return

    # ── Font handling ─────────────────────────────────────────────────

    def _on_tf(self, operands: list[object]) -> None:
        """Update font resolver and reconstructor after Tf operator."""
        font_name = str(operands[0]).lstrip("/")
        try:
            resolver = self._font_cache.get_resolver(self._page, font_name)
            self._current_resolver = resolver
            self._reconstructor = TJReconstructor(resolver)
        except (KeyError, TypeError):
            logger.warning("Cannot resolve font %s", font_name)
            self._current_resolver = None
            self._reconstructor = None

    # ── Text handling ─────────────────────────────────────────────────

    def _handle_tj_single(
        self, idx: int, operands: list[object],
    ) -> None:
        """Handle Tj operator (single string)."""
        if self._current_resolver is None:
            return
        raw = bytes(operands[0])  # type: ignore[call-overload]
        if not raw:
            return
        try:
            decoded = self._current_resolver.decode(raw)
        except KeyError:
            return
        if not decoded:
            return
        chars = self._make_text_chars(decoded, raw, idx, tj_fragment_index=None)
        if chars:
            self._emit_text_element(idx, idx, chars, decoded)

    def _handle_tj_array(
        self, idx: int, operands: list[object],
    ) -> None:
        """Handle TJ operator (array of strings and kerning values)."""
        if self._reconstructor is None or self._current_resolver is None:
            return
        tj_array = list(operands[0])  # type: ignore[call-overload]
        reconstructed = self._reconstructor.reconstruct(tj_array)
        if not reconstructed.full_text:
            return

        chars: list[TextCharacter] = []
        font_name = self._tracker.font_name or ""
        font_size = self._tracker.font_size
        resolver = self._current_resolver
        fill_color = self._tracker.fill_color or (0.0,)

        # Walk TJ array items sequentially for correct positioning
        pending_tj: float = 0.0
        frag_idx = 0
        for item in tj_array:
            if isinstance(item, (int, float)):
                pending_tj += float(item)
            elif isinstance(item, pikepdf.String):
                raw = bytes(item)
                if not raw:
                    continue
                try:
                    decoded = resolver.decode(raw)
                except KeyError:
                    continue
                if not decoded:
                    continue
                byte_width = resolver.byte_width
                # Apply pending TJ displacement before this fragment
                if pending_tj != 0.0:
                    self._tracker.apply_tj_displacement(pending_tj)
                for ci, ch in enumerate(decoded):
                    pos = self._tracker.get_text_position()
                    char_code = self._char_code(raw, ci, byte_width)
                    w = self._width_cache.get_width(
                        self._page, font_name, char_code,
                    )
                    width_ts = w / 1000.0
                    chars.append(TextCharacter(
                        unicode_char=ch,
                        page_x=pos[0],
                        page_y=pos[1],
                        width=width_ts * font_size,
                        height=font_size,
                        font_name=font_name,
                        font_size=font_size,
                        color=fill_color,
                        operator_index=idx,
                        byte_position=ci * byte_width,
                        tj_fragment_index=frag_idx,
                    ))
                    self._tracker.advance_by_glyph(width_ts, char_code)
                pending_tj = 0.0
                frag_idx += 1

        if chars:
            self._emit_text_element(idx, idx, chars, reconstructed.full_text)

    def _handle_quote(
        self, idx: int, operands: list[object],
    ) -> None:
        """Handle ' operator (T* then Tj)."""
        self._tracker.process_operator("T*", [])
        self._handle_tj_single(idx, operands)

    def _handle_double_quote(
        self, idx: int, operands: list[object],
    ) -> None:
        """Handle " operator (set Tw, Tc, then ' with string)."""
        if len(operands) >= 3:
            self._tracker.process_operator("Tw", [operands[0]])
            self._tracker.process_operator("Tc", [operands[1]])
            self._handle_quote(idx, [operands[2]])

    def _make_text_chars(
        self,
        decoded: str,
        raw: bytes,
        op_idx: int,
        *,
        tj_fragment_index: int | None,
    ) -> list[TextCharacter]:
        """Create TextCharacter entries for a decoded string (Tj path)."""
        chars: list[TextCharacter] = []
        resolver = self._current_resolver
        if resolver is None:
            return chars
        font_name = self._tracker.font_name or ""
        font_size = self._tracker.font_size
        fill_color = self._tracker.fill_color or (0.0,)
        byte_width = resolver.byte_width

        for ci, ch in enumerate(decoded):
            pos = self._tracker.get_text_position()
            char_code = self._char_code(raw, ci, byte_width)
            w = self._width_cache.get_width(self._page, font_name, char_code)
            width_ts = w / 1000.0
            chars.append(TextCharacter(
                unicode_char=ch,
                page_x=pos[0],
                page_y=pos[1],
                width=width_ts * font_size,
                height=font_size,
                font_name=font_name,
                font_size=font_size,
                color=fill_color,
                operator_index=op_idx,
                byte_position=ci * byte_width,
                tj_fragment_index=tj_fragment_index,
            ))
            self._tracker.advance_by_glyph(width_ts, char_code)
        return chars

    @staticmethod
    def _char_code(raw: bytes, char_index: int, byte_width: int) -> int:
        """Extract the character/CID code for a given character index."""
        offset = char_index * byte_width
        if byte_width == 2 and offset + 1 < len(raw):
            return (raw[offset] << 8) | raw[offset + 1]
        if offset < len(raw):
            return raw[offset]
        return 0

    def _emit_text_element(
        self,
        start_idx: int,
        end_idx: int,
        chars: list[TextCharacter],
        text_content: str,
    ) -> None:
        """Create and append a text ContentElement."""
        font_size = chars[0].font_size if chars else 1.0
        x0 = min(c.page_x for c in chars)
        y0 = min(c.page_y for c in chars) - font_size * 0.25
        x1 = max(c.page_x + c.width for c in chars)
        y1 = max(c.page_y for c in chars) + font_size * 0.75
        self._elements.append(ContentElement(
            type="text",
            page=self._page_number,
            operator_range=(start_idx, end_idx + 1),
            bbox=(x0, y0, x1, y1),
            graphics_state=self._tracker.snapshot(),
            text_content=text_content,
            characters=chars,
        ))

    # ── State change ──────────────────────────────────────────────────

    def _emit_state_change(self, idx: int) -> None:
        """Emit a state_change ContentElement for color operators."""
        self._elements.append(ContentElement(
            type="state_change",
            page=self._page_number,
            operator_range=(idx, idx + 1),
            bbox=(0.0, 0.0, 0.0, 0.0),
            graphics_state=self._tracker.snapshot(),
        ))

    # ── Path handling ─────────────────────────────────────────────────

    def _accumulate_path(
        self, idx: int, op: str, operands: list[object],
    ) -> None:
        """Accumulate path construction coordinates."""
        if not self._path_points:
            self._path_start_index = idx
        floats = [float(x) for x in operands]  # type: ignore[arg-type]
        if op == "re" and len(floats) >= 4:
            x, y, w, h = floats[0], floats[1], floats[2], floats[3]
            self._path_points.extend([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
        elif op in {"m", "l"} and len(floats) >= 2:
            self._path_points.append((floats[0], floats[1]))
        elif op == "c" and len(floats) >= 6:
            self._path_points.extend([
                (floats[0], floats[1]),
                (floats[2], floats[3]),
                (floats[4], floats[5]),
            ])
        elif op in {"v", "y"} and len(floats) >= 4:
            self._path_points.extend([
                (floats[0], floats[1]),
                (floats[2], floats[3]),
            ])

    def _emit_path(self, idx: int) -> None:
        """Emit a path ContentElement from accumulated points."""
        if self._path_points:
            xs = [p[0] for p in self._path_points]
            ys = [p[1] for p in self._path_points]
            bbox = (min(xs), min(ys), max(xs), max(ys))
        else:
            bbox = (0.0, 0.0, 0.0, 0.0)
        self._elements.append(ContentElement(
            type="path",
            page=self._page_number,
            operator_range=(self._path_start_index, idx + 1),
            bbox=bbox,
            graphics_state=self._tracker.snapshot(),
        ))
        self._path_points.clear()

    # ── XObject handling ──────────────────────────────────────────────

    def _handle_do(self, idx: int, operands: list[object]) -> None:
        """Handle Do operator (XObject invocation)."""
        xobj_name = str(operands[0]).lstrip("/")
        try:
            resources = self._page["/Resources"]
            xobj_dict = resources["/XObject"]
            xobj_key = f"/{xobj_name}"
            xobj = xobj_dict[xobj_key]
            sub_obj = xobj.get("/Subtype")
            subtype = str(sub_obj) if sub_obj is not None else ""
        except (KeyError, TypeError):
            return

        # Compute bbox from current CTM for images
        ctm = self._tracker.ctm
        x, y = float(ctm[4]), float(ctm[5])
        w, h = float(ctm[0]), float(ctm[3])
        bbox = (x, y, x + w, y + h)

        if subtype == "/Image":
            self._elements.append(ContentElement(
                type="image",
                page=self._page_number,
                operator_range=(idx, idx + 1),
                bbox=bbox,
                graphics_state=self._tracker.snapshot(),
                xobject_name=xobj_name,
            ))
        else:
            # Form XObject or other — record without recursion
            self._elements.append(ContentElement(
                type="xobject",
                page=self._page_number,
                operator_range=(idx, idx + 1),
                bbox=bbox,
                graphics_state=self._tracker.snapshot(),
                xobject_name=xobj_name,
            ))


# ── Index cache ────────────────────────────────────────────────────────


_index_cache: dict[tuple[str, int], list[ContentElement]] = {}


def _build_index(
    page: pikepdf.Page, page_number: int,
) -> list[ContentElement]:
    """Build (or retrieve cached) content element index for a page."""
    interpreter = ContentStreamInterpreter(page, page_number)
    return interpreter.interpret()


# ── Page resolution ────────────────────────────────────────────────────


def _resolve_pages(
    pdf: pikepdf.Pdf, page: int | None,
) -> list[tuple[int, pikepdf.Page]]:
    """Resolve page parameter to list of (page_number, page_object) pairs.

    Args:
        pdf: The open PDF.
        page: 0-indexed page number, or None for all pages.

    Returns:
        List of (page_number, page_object) pairs.

    Raises:
        IndexError: If the page number is out of range.
    """
    if page is not None:
        if page < 0 or page >= len(pdf.pages):
            raise IndexError(
                f"Page {page} out of range (PDF has {len(pdf.pages)} pages)"
            )
        return [(page, pdf.pages[page])]
    return list(enumerate(pdf.pages))


# ── Line grouping ──────────────────────────────────────────────────────


def _group_into_lines(elements: list[ContentElement]) -> list[str]:
    """Group text elements into lines based on y-position proximity.

    Elements should be pre-sorted by y descending, x ascending.

    Args:
        elements: Sorted text ContentElement list.

    Returns:
        List of text lines.
    """
    if not elements:
        return []

    lines: list[list[ContentElement]] = []
    current_line: list[ContentElement] = [elements[0]]
    current_y = elements[0].bbox[3]  # y1 (top of bbox)

    for elem in elements[1:]:
        elem_y = elem.bbox[3]
        # Estimate line height from bbox
        line_height = elem.bbox[3] - elem.bbox[1]
        threshold = max(line_height * 0.5, 2.0)
        if abs(current_y - elem_y) <= threshold:
            current_line.append(elem)
        else:
            lines.append(current_line)
            current_line = [elem]
            current_y = elem_y

    lines.append(current_line)

    result: list[str] = []
    for line_elems in lines:
        # Sort by x within the line
        line_elems.sort(key=lambda e: e.bbox[0])
        parts: list[str] = []
        for elem in line_elems:
            if elem.text_content:
                parts.append(elem.text_content)
        result.append(" ".join(parts))
    return result


# ── Font info extraction ───────────────────────────────────────────────

# Subset prefix pattern: 6 uppercase letters + '+'
_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def _build_font_info(
    font_obj: pikepdf.Object, font_name: str,
) -> FontInfo:
    """Extract FontInfo metadata from a font dictionary.

    Args:
        font_obj: The pikepdf font object.
        font_name: Font resource name (e.g., 'F1').

    Returns:
        FontInfo with encoding, subset, and embedding metadata.
    """
    font_dict = pikepdf.Dictionary(font_obj)  # type: ignore[arg-type]
    subtype_obj = font_dict.get("/Subtype")
    subtype = str(subtype_obj) if subtype_obj is not None else ""
    base_font_obj = font_dict.get("/BaseFont")
    base_font = str(base_font_obj).lstrip("/") if base_font_obj is not None else "Unknown"

    # Detect subset prefix
    is_subset = bool(_SUBSET_PREFIX.match(base_font))
    postscript_name = _SUBSET_PREFIX.sub("", base_font)

    # Encoding type
    encoding_type: Literal["WinAnsi", "Identity-H", "Custom"]
    if subtype == "/Type0":
        encoding_type = "Identity-H"
    else:
        enc = font_dict.get("/Encoding")
        if enc is not None:
            enc_str = str(enc)
            if enc_str == "/WinAnsiEncoding":
                encoding_type = "WinAnsi"
            elif isinstance(enc, pikepdf.Dictionary):
                encoding_type = "Custom"
            else:
                encoding_type = "WinAnsi"
        else:
            encoding_type = "WinAnsi"

    # Glyph count
    glyph_count = 0
    if subtype == "/Type0" and "/DescendantFonts" in font_dict:
        try:
            cid_font = font_dict["/DescendantFonts"][0]
            cid_dict = pikepdf.Dictionary(cid_font)  # type: ignore[arg-type]
            fd = cid_dict.get("/FontDescriptor")
            if fd is not None and "/FontFile2" in fd:
                try:
                    from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

                    font_stream = fd["/FontFile2"]
                    font_bytes = font_stream.read_bytes()
                    tt = TTFont(_io.BytesIO(font_bytes))
                    glyph_count = len(tt.getGlyphOrder())
                    tt.close()
                except Exception:
                    # Fallback: count /W entries
                    if "/W" in cid_dict:
                        widths = _parse_cid_widths(cid_dict)
                        glyph_count = len(widths)
            elif "/W" in cid_dict:
                widths = _parse_cid_widths(cid_dict)
                glyph_count = len(widths)
        except (KeyError, IndexError):
            pass
    elif "/Widths" in font_dict:
        w_arr_count: pikepdf.Array = font_dict["/Widths"]  # type: ignore[assignment]
        glyph_count = len(w_arr_count)

    # Embedded type from FontDescriptor
    embedded_type = _detect_embedded_type(font_dict, subtype)

    return FontInfo(
        name=font_name,
        postscript_name=postscript_name,
        encoding_type=encoding_type,
        is_subset=is_subset,
        glyph_count=glyph_count,
        embedded_type=embedded_type,
    )


def _detect_embedded_type(
    font_dict: pikepdf.Dictionary, subtype: str,
) -> Literal["TrueType", "CFF", "Type1"]:
    """Detect the embedded font type from FontDescriptor.

    Returns:
        'TrueType', 'CFF', or 'Type1'.
    """
    fd = _get_font_descriptor(font_dict, subtype)
    if fd is not None:
        if "/FontFile2" in fd:
            return "TrueType"
        if "/FontFile3" in fd:
            return "CFF"
        if "/FontFile" in fd:
            return "Type1"
    # Infer from subtype
    if subtype in {"/TrueType", "/Type0"}:
        return "TrueType"
    if subtype == "/Type1":
        return "Type1"
    return "TrueType"


def _get_font_descriptor(
    font_dict: pikepdf.Dictionary, subtype: str,
) -> pikepdf.Object | None:
    """Get the FontDescriptor from a font dict, handling CIDFonts."""
    if "/FontDescriptor" in font_dict:
        return font_dict["/FontDescriptor"]
    if subtype == "/Type0" and "/DescendantFonts" in font_dict:
        try:
            cid_font = font_dict["/DescendantFonts"][0]
            cid_dict = pikepdf.Dictionary(cid_font)  # type: ignore[arg-type]
            if "/FontDescriptor" in cid_dict:
                return cid_dict["/FontDescriptor"]
        except (KeyError, IndexError):
            pass
    return None


# ── Public API ─────────────────────────────────────────────────────────


def find(
    pdf_path: str,
    search_text: str,
    *,
    page: int | None = None,
    case_sensitive: bool = True,
) -> list[TextMatch]:
    """Locate text in a PDF, returning matches with operator references.

    Args:
        pdf_path: Path to the PDF file.
        search_text: Text to search for.
        page: Restrict search to a specific page (0-indexed). None searches all pages.
        case_sensitive: Whether the search is case-sensitive.

    Returns:
        List of TextMatch objects with character positions and operator references.
    """
    raise NotImplementedError


def get_text(pdf_path: str, *, page: int | None = None) -> str:
    """Extract all text from a PDF or a specific page.

    Args:
        pdf_path: Path to the PDF file.
        page: Specific page to extract (0-indexed). None extracts all pages.

    Returns:
        Extracted text content.
    """
    path = Path(pdf_path)
    with pikepdf.open(path) as pdf:
        pages = _resolve_pages(pdf, page)
        all_text: list[str] = []
        for page_num, page_obj in pages:
            elements = _build_index(page_obj, page_num)
            text_elements = [
                e for e in elements
                if e.type == "text" and e.text_content
            ]
            # Sort by y descending (top of page first), then x ascending
            text_elements.sort(key=lambda e: (-e.bbox[3], e.bbox[0]))
            lines = _group_into_lines(text_elements)
            all_text.append("\n".join(lines))
        return "\n".join(all_text)


def get_fonts(pdf_path: str, *, page: int | None = None) -> list[FontInfo]:
    """List all fonts used in a PDF or a specific page.

    Args:
        pdf_path: Path to the PDF file.
        page: Specific page to analyze (0-indexed). None analyzes all pages.

    Returns:
        List of FontInfo objects describing each font.
    """
    path = Path(pdf_path)
    with pikepdf.open(path) as pdf:
        pages = _resolve_pages(pdf, page)
        fonts: list[FontInfo] = []
        seen: set[str] = set()
        for _, page_obj in pages:
            try:
                font_dict = page_obj["/Resources"]["/Font"]
            except (KeyError, TypeError):
                continue
            font_keys = list(font_dict.keys())
            for key in font_keys:
                name = str(key).lstrip("/")
                if name in seen:
                    continue
                seen.add(name)
                fonts.append(_build_font_info(font_dict[key], name))
        return fonts
