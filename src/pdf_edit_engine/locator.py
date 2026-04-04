"""TextLocator module — find text in PDFs with operator-level precision."""

from __future__ import annotations

import io as _io
import logging
import re
from pathlib import Path
from typing import Literal

import pikepdf

from pdf_edit_engine.encoding import FontResolver, FontResolverCache
from pdf_edit_engine.fragments import TJReconstructor
from pdf_edit_engine.models import (
    ContentElement,
    FontInfo,
    TextCharacter,
    TextMatch,
)
from pdf_edit_engine.state import GraphicsStateTracker
from pdf_edit_engine.widths import GlyphWidthCache, parse_cid_widths

logger = logging.getLogger(__name__)


# ── Content stream interpretation ──────────────────────────────────────


# Operators handled by GraphicsStateTracker
_STATE_OPS: frozenset[str] = frozenset(
    {
        "q",
        "Q",
        "cm",
        "BT",
        "ET",
        "Tm",
        "Td",
        "TD",
        "T*",
        "Tf",
        "Tc",
        "Tw",
        "Tz",
        "TL",
        "Tr",
        "Ts",
        "g",
        "G",
        "rg",
        "RG",
        "k",
        "K",
        "cs",
        "CS",
        "sc",
        "SC",
        "scn",
        "SCN",
    }
)

# Path construction operators
_PATH_CONSTRUCT_OPS: frozenset[str] = frozenset({"m", "l", "c", "v", "y", "re", "h"})

# Path painting / termination operators
_PATH_PAINT_OPS: frozenset[str] = frozenset(
    {
        "S",
        "s",
        "f",
        "F",
        "f*",
        "B",
        "b",
        "B*",
        "b*",
        "n",
    }
)

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
        self._width_cache = GlyphWidthCache()
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
        self,
        idx: int,
        op: str,
        operands: list[object],
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
        self,
        idx: int,
        operands: list[object],
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
        self,
        idx: int,
        operands: list[object],
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
                # Iterate by CID (byte_width chunks) to handle ligatures
                self._walk_cids(
                    raw,
                    byte_width,
                    resolver,
                    font_name,
                    font_size,
                    fill_color,
                    idx,
                    frag_idx,
                    chars,
                )
                pending_tj = 0.0
                frag_idx += 1

        if chars:
            self._emit_text_element(idx, idx, chars, reconstructed.full_text)

    def _handle_quote(
        self,
        idx: int,
        operands: list[object],
    ) -> None:
        """Handle ' operator (T* then Tj)."""
        self._tracker.process_operator("T*", [])
        self._handle_tj_single(idx, operands)

    def _handle_double_quote(
        self,
        idx: int,
        operands: list[object],
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

        self._walk_cids(
            raw,
            byte_width,
            resolver,
            font_name,
            font_size,
            fill_color,
            op_idx,
            tj_fragment_index,
            chars,
        )
        return chars

    def _walk_cids(
        self,
        raw: bytes,
        byte_width: int,
        resolver: FontResolver,
        font_name: str,
        font_size: float,
        fill_color: tuple[float, ...],
        op_idx: int,
        frag_idx: int | None,
        chars: list[TextCharacter],
    ) -> None:
        """Iterate by CID over *raw* bytes and emit TextCharacters.

        For CID fonts a single CID may decode to multiple Unicode
        characters (ligatures like ``fi``, ``ft``).  This method uses
        the **CID's** width from the /W table and distributes it evenly
        among the ligature sub-characters, keeping glyph advance correct.
        """
        num_cids = len(raw) // byte_width if byte_width > 0 else 0
        for cid_idx in range(num_cids):
            offset = cid_idx * byte_width
            char_code = self._char_code(raw, cid_idx, byte_width)
            w = self._width_cache.get_width(self._page, font_name, char_code)
            width_ts = w / 1000.0

            # Decode just this CID
            cid_bytes = raw[offset : offset + byte_width]
            try:
                cid_text = resolver.decode(cid_bytes)
            except KeyError:
                # Advance position even for unmappable CIDs
                self._tracker.advance_by_glyph(width_ts, char_code)
                continue

            n_sub = len(cid_text) if cid_text else 1
            sub_width = width_ts / n_sub

            for sub_ci, ch in enumerate(cid_text):
                pos = self._tracker.get_text_position()
                chars.append(
                    TextCharacter(
                        unicode_char=ch,
                        page_x=pos[0],
                        page_y=pos[1],
                        width=sub_width * font_size,
                        height=font_size,
                        font_name=font_name,
                        font_size=font_size,
                        color=fill_color,
                        operator_index=op_idx,
                        byte_position=offset,
                        tj_fragment_index=frag_idx,
                        rendering_mode=self._tracker.text_rendering_mode,
                    )
                )
                # Advance only on the last sub-character of the ligature
                if sub_ci == n_sub - 1:
                    self._tracker.advance_by_glyph(width_ts, char_code)

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
        self._elements.append(
            ContentElement(
                type="text",
                page=self._page_number,
                operator_range=(start_idx, end_idx + 1),
                bbox=(x0, y0, x1, y1),
                graphics_state=self._tracker.snapshot(),
                text_content=text_content,
                characters=chars,
            )
        )

    # ── State change ──────────────────────────────────────────────────

    def _emit_state_change(self, idx: int) -> None:
        """Emit a state_change ContentElement for color operators."""
        self._elements.append(
            ContentElement(
                type="state_change",
                page=self._page_number,
                operator_range=(idx, idx + 1),
                bbox=(0.0, 0.0, 0.0, 0.0),
                graphics_state=self._tracker.snapshot(),
            )
        )

    # ── Path handling ─────────────────────────────────────────────────

    def _accumulate_path(
        self,
        idx: int,
        op: str,
        operands: list[object],
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
            self._path_points.extend(
                [
                    (floats[0], floats[1]),
                    (floats[2], floats[3]),
                    (floats[4], floats[5]),
                ]
            )
        elif op in {"v", "y"} and len(floats) >= 4:
            self._path_points.extend(
                [
                    (floats[0], floats[1]),
                    (floats[2], floats[3]),
                ]
            )

    def _emit_path(self, idx: int) -> None:
        """Emit a path ContentElement from accumulated points."""
        if self._path_points:
            xs = [p[0] for p in self._path_points]
            ys = [p[1] for p in self._path_points]
            bbox = (min(xs), min(ys), max(xs), max(ys))
        else:
            bbox = (0.0, 0.0, 0.0, 0.0)
        self._elements.append(
            ContentElement(
                type="path",
                page=self._page_number,
                operator_range=(self._path_start_index, idx + 1),
                bbox=bbox,
                graphics_state=self._tracker.snapshot(),
            )
        )
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
            self._elements.append(
                ContentElement(
                    type="image",
                    page=self._page_number,
                    operator_range=(idx, idx + 1),
                    bbox=bbox,
                    graphics_state=self._tracker.snapshot(),
                    xobject_name=xobj_name,
                )
            )
        else:
            # Form XObject or other — record without recursion
            self._elements.append(
                ContentElement(
                    type="xobject",
                    page=self._page_number,
                    operator_range=(idx, idx + 1),
                    bbox=bbox,
                    graphics_state=self._tracker.snapshot(),
                    xobject_name=xobj_name,
                )
            )


# ── Index cache ────────────────────────────────────────────────────────
#
# Single-PDF cache: the MCP use case processes one PDF at a time.
# Switching to a different path clears the entire cache.

_cached_path: str | None = None
_cached_elements: dict[int, list[ContentElement]] = {}


def _build_index(
    page: pikepdf.Page,
    page_number: int,
    pdf_path: str | None = None,
) -> list[ContentElement]:
    """Build (or retrieve cached) content element index for a page.

    Args:
        page: The pikepdf page object.
        page_number: 0-indexed page number.
        pdf_path: Resolved file path for caching.  When provided, results
                  are cached and reused across calls for the same PDF.
    """
    global _cached_path, _cached_elements  # noqa: PLW0603

    if pdf_path is not None:
        if pdf_path != _cached_path:
            _cached_path = pdf_path
            _cached_elements = {}
        if page_number in _cached_elements:
            return _cached_elements[page_number]

    interpreter = ContentStreamInterpreter(page, page_number)
    elements = interpreter.interpret()

    if pdf_path is not None:
        _cached_elements[page_number] = elements
    return elements


# ── Page resolution ────────────────────────────────────────────────────


def _resolve_pages(
    pdf: pikepdf.Pdf,
    page: int | None,
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
            raise IndexError(f"Page {page} out of range (PDF has {len(pdf.pages)} pages)")
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
    font_obj: pikepdf.Object,
    font_name: str,
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
                        widths = parse_cid_widths(cid_dict)
                        glyph_count = len(widths)
            elif "/W" in cid_dict:
                widths = parse_cid_widths(cid_dict)
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
    font_dict: pikepdf.Dictionary,
    subtype: str,
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
    font_dict: pikepdf.Dictionary,
    subtype: str,
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


# ── Flat-string builder for find() ────────────────────────────────────


def _build_flat_string(
    text_elements: list[ContentElement],
) -> tuple[str, list[TextCharacter | None]]:
    """Build a flat string and parallel char_map from sorted text elements.

    Between elements on the same line with a horizontal gap, a space is
    inserted (mapped to ``None`` in *char_map*).  Between elements on
    different lines, a newline is inserted.

    Args:
        text_elements: Text ContentElements sorted by reading order
                       (y descending, x ascending).

    Returns:
        Tuple of *(flat_string, char_map)* where each position in
        *flat_string* has a corresponding entry in *char_map* — either
        a :class:`TextCharacter` or ``None`` for inserted separators.
    """
    parts: list[str] = []
    char_map: list[TextCharacter | None] = []

    prev_y: float | None = None
    prev_end_x: float = 0.0
    prev_font_size: float = 12.0

    for elem in text_elements:
        chars = elem.characters
        if not chars:
            continue

        elem_y = elem.bbox[3]  # top of bbox
        elem_x = chars[0].page_x
        font_size = chars[0].font_size
        threshold = max(font_size * 0.5, 2.0)

        if prev_y is not None:
            if abs(prev_y - elem_y) <= threshold:
                # Same line — insert space if there is a horizontal gap
                gap = elem_x - prev_end_x
                avg_w = prev_font_size * 0.3
                if gap > avg_w:
                    parts.append(" ")
                    char_map.append(None)
            else:
                # Different line
                parts.append("\n")
                char_map.append(None)

        for ch in chars:
            parts.append(ch.unicode_char)
            char_map.append(ch)

        last_char = chars[-1]
        prev_end_x = last_char.page_x + last_char.width
        prev_y = elem_y
        prev_font_size = font_size

    return "".join(parts), char_map


# ── Public API ─────────────────────────────────────────────────────────


def find(
    pdf_path: str,
    search_text: str,
    *,
    page: int | None = None,
    case_sensitive: bool = True,
) -> list[TextMatch]:
    """Locate text in a PDF, returning matches with operator references.

    Builds a flat string from the page's text elements with inferred space
    and newline separators, then performs literal substring search.  Each
    match is mapped back to the underlying :class:`TextCharacter` objects
    and content-stream operator indices.

    Args:
        pdf_path: Path to the PDF file.
        search_text: Text to search for (literal, not regex).
        page: Restrict search to a specific page (0-indexed).
              ``None`` searches all pages.
        case_sensitive: Whether the search is case-sensitive.

    Returns:
        List of :class:`TextMatch` objects.  Empty list when *search_text*
        is empty or no matches are found.
    """
    if not search_text:
        return []

    path = Path(pdf_path)
    resolved = str(path.resolve())
    matches: list[TextMatch] = []

    with pikepdf.open(path) as pdf:
        pages = _resolve_pages(pdf, page)

        for page_num, page_obj in pages:
            elements = _build_index(page_obj, page_num, resolved)
            text_elements = [e for e in elements if e.type == "text" and e.text_content]
            text_elements.sort(key=lambda e: (-e.bbox[3], e.bbox[0]))

            flat, char_map = _build_flat_string(text_elements)

            # Literal substring search
            haystack = flat if case_sensitive else flat.lower()
            needle = search_text if case_sensitive else search_text.lower()
            start = 0
            while True:
                idx = haystack.find(needle, start)
                if idx == -1:
                    break
                end = idx + len(needle)
                start = idx + 1  # allow overlapping matches

                matched_chars = [char_map[i] for i in range(idx, end) if char_map[i] is not None]
                # Narrow type for mypy
                real_chars: list[TextCharacter] = [c for c in matched_chars if c is not None]
                if not real_chars:
                    continue

                # Bounding box
                fs = real_chars[0].font_size
                x0 = min(c.page_x for c in real_chars)
                y0 = min(c.page_y for c in real_chars) - fs * 0.25
                x1 = max(c.page_x + c.width for c in real_chars)
                y1 = max(c.page_y for c in real_chars) + fs * 0.75

                # FontInfo from first character
                first_font = real_chars[0].font_name
                font_key = first_font if first_font.startswith("/") else f"/{first_font}"
                try:
                    font_obj = page_obj["/Resources"]["/Font"][font_key]
                    font_info = _build_font_info(font_obj, first_font)
                except (KeyError, TypeError):
                    font_info = FontInfo(
                        name=first_font,
                        postscript_name=first_font,
                        encoding_type="WinAnsi",
                        is_subset=False,
                        glyph_count=0,
                        embedded_type="TrueType",
                    )

                operator_refs = sorted({c.operator_index for c in real_chars})

                matches.append(
                    TextMatch(
                        matched_text=flat[idx:end],
                        page_number=page_num,
                        bounding_box=(x0, y0, x1, y1),
                        characters=real_chars,
                        font_info=font_info,
                        operator_refs=operator_refs,
                    )
                )

    return matches


def get_text(pdf_path: str, *, page: int | None = None) -> str:
    """Extract all text from a PDF or a specific page.

    Args:
        pdf_path: Path to the PDF file.
        page: Specific page to extract (0-indexed). None extracts all pages.

    Returns:
        Extracted text content.
    """
    path = Path(pdf_path)
    resolved = str(path.resolve())
    with pikepdf.open(path) as pdf:
        pages = _resolve_pages(pdf, page)
        all_text: list[str] = []
        for page_num, page_obj in pages:
            elements = _build_index(page_obj, page_num, resolved)
            text_elements = [e for e in elements if e.type == "text" and e.text_content]
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
