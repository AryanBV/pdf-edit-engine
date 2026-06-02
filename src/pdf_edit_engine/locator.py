"""TextLocator module — find text in PDFs with operator-level precision."""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Literal

import pikepdf

from pdf_edit_engine._pathutil import _parse_content_stream, open_pdf
from pdf_edit_engine.encoding import FontResolver, FontResolverCache
from pdf_edit_engine.errors import OperatorError
from pdf_edit_engine.fonts import classify_embedded_outline, embedded_glyph_count
from pdf_edit_engine.fragments import TJReconstructor
from pdf_edit_engine.models import (
    ContentElement,
    Degradation,
    FontInfo,
    TextBlock,
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
        ops = _parse_content_stream(self._page, context="locator.interpret")
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
#
# WARNING: Thread-unsafe global cache. This library is single-threaded.
# The planned MCP wrapper (pdf-edit-mcp) must serialize all calls to the
# Python engine. Do not use concurrent.futures or multiprocessing to call
# find()/replace() in parallel.

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
    try:
        elements = interpreter.interpret()
    except (UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
        # F-C-03 / INV-W0-9: drop {exc} from the user-visible message — a
        # malformed content stream can carry attacker-controlled bytes.
        # Forensic traceback is captured by the logger.error below.
        logger.error("content-stream interpret failed on page %d", page_number, exc_info=True)
        raise OperatorError(
            f"Failed to parse content stream on page {page_number}: {type(exc).__name__}"
        ) from exc

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
        OperatorError: If the page number is out of range.
    """
    if page is not None:
        if page < 0 or page >= len(pdf.pages):
            raise OperatorError(f"Page {page} out of range (PDF has {len(pdf.pages)} pages)")
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
    prev_line_height = elements[0].bbox[3] - elements[0].bbox[1]

    for elem in elements[1:]:
        elem_y = elem.bbox[3]
        elem_line_height = elem.bbox[3] - elem.bbox[1]
        # ARY-N1 fix: take the MIN of adjacent line-heights as the
        # threshold base. Using the current element's line_height alone
        # caused vertically-adjacent text in very different font sizes
        # (e.g. a 36pt heading immediately above a 110pt badge run) to
        # be grouped as a single line. The bigger element's bbox height
        # produced a threshold (55pt) wide enough to absorb the smaller
        # element's distinct line above. min() makes the threshold
        # symmetric: two lines are merged only if their gap is small
        # relative to BOTH font sizes.
        threshold = max(min(prev_line_height, elem_line_height) * 0.5, 2.0)
        if abs(current_y - elem_y) <= threshold:
            current_line.append(elem)
        else:
            lines.append(current_line)
            current_line = [elem]
            current_y = elem_y
        prev_line_height = elem_line_height

    lines.append(current_line)

    result: list[str] = []
    for line_elems in lines:
        # Sort by x within the line
        line_elems.sort(key=lambda e: e.bbox[0])
        line_parts: list[str] = []
        prev_end_x: float | None = None
        prev_font_size: float = 12.0
        for elem in line_elems:
            if not elem.text_content:
                continue
            chars = elem.characters
            if prev_end_x is not None and chars:
                gap = chars[0].page_x - prev_end_x
                # ARY-N1 fix: use font_size * 0.25 (≈ space-glyph width for
                # typical Latin fonts) as the gap threshold. The previous
                # heuristic (avg-char-width * 0.5 of the immediately
                # preceding fragment) misbehaved when the PDF emits one
                # glyph per text-showing operator (Chrome, some Word
                # exports). A single wide glyph ('m', 'w') produced a
                # threshold of ~8pt at 24pt Helvetica, large enough to
                # absorb genuine inter-word gaps and merge consecutive
                # words into single tokens.
                threshold_x = max(prev_font_size * 0.25, 2.0)
                if gap > threshold_x:
                    line_parts.append(" ")
            line_parts.append(elem.text_content)
            if chars:
                last_c = chars[-1]
                prev_end_x = last_c.page_x + last_c.width
                prev_font_size = chars[0].font_size
        result.append("".join(line_parts))
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

    # Glyph count (INV-C-10): when an embedded font stream is present, route
    # the count through fonts.embedded_glyph_count — the dependency-boundary-
    # safe helper that introspects the REAL count per outline type
    # (glyf/CFF/CFF2/Type1) WITHOUT importing fontTools/cffLib/t1Lib into
    # locator. A sparse /W dict is NOT a glyph count, so the prior /W-length
    # fabrication is gone for embedded fonts. When the helper returns 0 AND a
    # stream IS present (the font claims to be embedded but is unparseable),
    # surface a typed font_subset_introspection_failed Degradation so the
    # caller knows the count is unknown rather than zero-by-truth. A
    # non-embedded simple font (standard-14, no /FontFile*) keeps the
    # /Widths-length proxy and emits nothing (no over-surfacing).
    fd = _get_font_descriptor(font_dict, subtype)
    embedded_present = fd is not None and (
        "/FontFile2" in fd or "/FontFile3" in fd or "/FontFile" in fd
    )
    degradations: list[Degradation] = []
    glyph_count = 0
    if embedded_present:
        assert fd is not None  # narrowed by embedded_present
        glyph_count = embedded_glyph_count(fd)
        if glyph_count == 0:
            embedded_type_for_detail = _detect_embedded_type(font_dict, subtype)
            degradations.append(
                Degradation(
                    kind="font_subset_introspection_failed",
                    detail=f"{embedded_type_for_detail}:{font_name}",
                    severity="warning",
                )
            )
    elif subtype == "/Type0" and "/DescendantFonts" in font_dict:
        try:
            cid_font = font_dict["/DescendantFonts"][0]
            cid_dict = pikepdf.Dictionary(cid_font)  # type: ignore[arg-type]
            if "/W" in cid_dict:
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
        degradations=degradations,
    )


def _detect_embedded_type(
    font_dict: pikepdf.Dictionary,
    subtype: str,
) -> Literal["TrueType", "CFF", "Type1", "cff2", "opentype-glyf", "type3", "unknown"]:
    """Detect the embedded font type from FontDescriptor.

    Routes through ``fonts.classify_embedded_outline`` so the label is
    derived from the outline table the binary ACTUALLY carries, not the
    ``/FontFile2`` vs ``/FontFile3`` slot (INV-C-9). This shares one truth
    source with ``fonts._extract_font_bytes`` — the two producers of
    ``FontInfo.embedded_type`` can never disagree. The fontTools dependency
    stays inside ``fonts``; ``locator`` only calls the helper (CLAUDE.md
    dependency-boundary table).

    Returns:
        'TrueType', 'CFF', 'Type1', 'cff2', 'opentype-glyf', 'type3', or
        'unknown'.
    """
    fd = _get_font_descriptor(font_dict, subtype)
    if fd is not None and ("/FontFile2" in fd or "/FontFile3" in fd or "/FontFile" in fd):
        return classify_embedded_outline(fd)  # type: ignore[return-value]
    # No embedded stream — infer from subtype.
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
        # See _group_into_lines for the symmetric-min rationale.
        threshold = max(min(prev_font_size, font_size) * 0.5, 2.0)

        if prev_y is not None:
            if abs(prev_y - elem_y) <= threshold:
                # Same line — insert space if there is a horizontal gap.
                # See _group_into_lines for rationale on the threshold:
                # using the previous fragment's avg char-width breaks
                # for one-glyph-per-operator PDFs (Chrome, some Word).
                # font_size * 0.25 is the space-glyph proxy that holds
                # regardless of fragment granularity.
                gap = elem_x - prev_end_x
                threshold_x = max(prev_font_size * 0.25, 2.0)
                if gap > threshold_x:
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


# ── Unicode-normalization-aware search view (INV-D-5) ──────────────────


def _cluster_spans(flat: str) -> list[tuple[int, int]]:
    """Split *flat* into base+combining-mark cluster spans.

    A cluster is a base character (Unicode combining class 0) followed by
    any number of combining marks (non-zero combining class). This is the
    granularity at which NFC composition (base + mark → precomposed) is
    well-defined, so normalizing per cluster lets us recombine a split
    ``base`` + ``mark`` sequence while still mapping the result back to a
    contiguous range of original positions.

    Args:
        flat: The flat string produced by :func:`_build_flat_string`.

    Returns:
        List of ``(start, stop)`` half-open index ranges into *flat*; the
        ranges partition ``range(len(flat))`` in order. A leading combining
        mark with no base (rare/degenerate) forms its own single-char span.
    """
    spans: list[tuple[int, int]] = []
    n = len(flat)
    i = 0
    while i < n:
        start = i
        i += 1  # consume the base (or a leading orphan combining mark)
        while i < n and unicodedata.combining(flat[i]) != 0:
            i += 1
        spans.append((start, i))
    return spans


def _normalized_search_view(
    flat: str,
    *,
    case_sensitive: bool,
) -> tuple[str, list[int], list[int]]:
    """Build an NFC-normalized search view of *flat* with a position map.

    Canonical equivalence (Unicode NFC/NFD) means a precomposed accent
    (``"é"`` = U+00E9) and its decomposed form (``"e"`` + U+0301) are the
    same text but distinct codepoint sequences. ``str.find`` compares
    codepoints, so a query in one form misses text stored in the other.
    To match across forms we search an NFC-normalized projection of *flat*.

    Normalization can change length (NFC collapses 2 codepoints → 1; NFD
    expands 1 → 2), so a naive ``unicodedata.normalize(flat)`` would
    desynchronise the parallel ``char_map`` that the caller indexes to
    recover the underlying :class:`TextCharacter` objects (the addressing
    the subsequent replace splice relies on). Instead we normalize one
    **base+combining-mark cluster** at a time (see :func:`_cluster_spans`)
    and record, for every normalized character, both the *first* and *last*
    original flat index of the cluster it came from. A match whose
    boundaries land on cluster edges then maps to a range covering each
    touched cluster in full — never a partial splice.

    Case folding is applied **inside** this projection (per normalized
    character) rather than by the caller via ``norm.lower()``: ``str.lower``
    can itself change length (e.g. U+0130 → ``"i"`` + U+0307), which would
    again desync the position arrays. Folding here keeps the returned
    string and the two origin arrays exactly the same length.

    Args:
        flat: The flat string produced by :func:`_build_flat_string`,
            1:1 with the caller's ``char_map``.
        case_sensitive: When ``False``, each normalized character is
            lower-cased; origin entries are duplicated to track any length
            change so the three returned sequences stay aligned.

    Returns:
        Tuple ``(norm, origin_first, origin_last)``. ``norm`` is the
        NFC-normalized (and optionally lower-cased) search string.
        ``origin_first`` and ``origin_last`` are both the same length as
        ``norm``; for normalized position ``k``, ``origin_first[k]`` /
        ``origin_last[k]`` are the first / last original flat (hence
        ``char_map``) indices of the cluster that produced ``norm[k]``.
        Every normalized character of one cluster shares the same first/last
        pair, so the values are constant within a cluster and strictly
        increase across cluster boundaries.
    """
    norm_parts: list[str] = []
    origin_first: list[int] = []
    origin_last: list[int] = []
    for start, stop in _cluster_spans(flat):
        cluster = flat[start:stop]
        normalized = unicodedata.normalize("NFC", cluster)
        first_origin = start
        last_origin = stop - 1
        for nch in normalized:
            emitted = nch if case_sensitive else nch.lower()
            for ech in emitted:
                norm_parts.append(ech)
                origin_first.append(first_origin)
                origin_last.append(last_origin)
    return "".join(norm_parts), origin_first, origin_last


# ── Public API ─────────────────────────────────────────────────────────


def find(
    pdf_path: str,
    search_text: str,
    *,
    page: int | None = None,
    case_sensitive: bool = True,
    password: str | bytes | None = None,
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
        password: Optional password to open an encrypted PDF (A2.3 / INV-W-5).

    Returns:
        List of :class:`TextMatch` objects.  Empty list when *search_text*
        is empty or no matches are found.

        Note: TextMatch objects contain operator indices into the content
        stream. After any replace() call on the same PDF, previously
        returned TextMatch objects are invalidated. Use batch_replace()
        for multi-edit workflows, or call find() again after each
        replace().
    """
    if not search_text:
        return []

    path = Path(pdf_path)
    resolved = str(path.resolve())
    matches: list[TextMatch] = []

    with open_pdf(path, password=password) as pdf:
        pages = _resolve_pages(pdf, page)

        for page_num, page_obj in pages:
            elements = _build_index(page_obj, page_num, resolved)
            text_elements = [e for e in elements if e.type == "text" and e.text_content]
            text_elements.sort(key=lambda e: (-e.bbox[3], e.bbox[0]))

            flat, char_map = _build_flat_string(text_elements)

            # INV-D-5: search an NFC-normalized projection of the flat text
            # so a query in one Unicode normalization form (NFC "é" =
            # U+00E9) locates target text stored in the other (NFD "e" +
            # U+0301), and vice-versa. ``origin[k]`` maps each normalized
            # position back to the original flat/char_map index, keeping the
            # operator/byte addressing of the returned characters intact for
            # the subsequent replace splice. Normalizing per base+mark
            # cluster (not the whole string at once) preserves a stable
            # position map across length-changing normalization.
            haystack, origin_first, origin_last = _normalized_search_view(
                flat, case_sensitive=case_sensitive
            )
            needle_norm = unicodedata.normalize("NFC", search_text)
            needle = needle_norm if case_sensitive else needle_norm.lower()
            start = 0
            while True:
                nidx = haystack.find(needle, start)
                if nidx == -1:
                    break
                nend = nidx + len(needle)
                start = nidx + 1  # allow overlapping matches

                # Cluster-boundary safety (B.7 cluster model): refuse a
                # match whose normalized boundaries fall *inside* a
                # base+combining-mark cluster. Splicing a partial cluster
                # would corrupt the glyph run and break
                # ``_assert_match_addressable``, so we skip rather than
                # return an unaddressable match. A boundary is clean when
                # the cluster identity (its origin range) changes across it.
                starts_on_boundary = nidx == 0 or origin_first[nidx] != origin_first[nidx - 1]
                ends_on_boundary = (
                    nend == len(origin_last) or origin_last[nend - 1] != origin_last[nend]
                )
                if not (starts_on_boundary and ends_on_boundary):
                    continue

                # Map the normalized match span back to the original
                # char_map index range, covering each touched cluster in
                # full: start at the FIRST original index of the start
                # cluster, end after the LAST original index of the end
                # cluster.
                orig_start = origin_first[nidx]
                orig_end = origin_last[nend - 1] + 1

                matched_chars = [
                    char_map[i] for i in range(orig_start, orig_end) if char_map[i] is not None
                ]
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
                        matched_text=flat[orig_start:orig_end],
                        page_number=page_num,
                        bounding_box=(x0, y0, x1, y1),
                        characters=real_chars,
                        font_info=font_info,
                        operator_refs=operator_refs,
                    )
                )

    return matches


def get_text(pdf_path: str, *, page: int | None = None, password: str | bytes | None = None) -> str:
    """Extract all text from a PDF or a specific page.

    Args:
        pdf_path: Path to the PDF file.
        page: Specific page to extract (0-indexed). None extracts all pages.
        password: Optional password to open an encrypted PDF (A2.3 / INV-W-5).

    Returns:
        Extracted text content.
    """
    path = Path(pdf_path)
    resolved = str(path.resolve())
    with open_pdf(path, password=password) as pdf:
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


def get_fonts(
    pdf_path: str, *, page: int | None = None, password: str | bytes | None = None
) -> list[FontInfo]:
    """List all fonts used in a PDF or a specific page.

    Args:
        pdf_path: Path to the PDF file.
        page: Specific page to analyze (0-indexed). None analyzes all pages.
        password: Optional password to open an encrypted PDF (A2.3 / INV-W-5).

    Returns:
        List of FontInfo objects describing each font.
    """
    path = Path(pdf_path)
    with open_pdf(path, password=password) as pdf:
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


def get_text_layout(
    pdf_path: str, page: int | None = None, *, password: str | bytes | None = None
) -> list[TextBlock]:
    """Return text blocks with their positions, fonts, and sizes.

    Each TextBlock represents a contiguous text element (one TJ/Tj
    operator's output) with its rendered position and font info.

    Args:
        pdf_path: Path to the PDF file.
        page: Optional page number (0-indexed). If None, returns all pages.
        password: Optional password to open an encrypted PDF (A2.3 / INV-W-5).

    Returns:
        List of TextBlock objects sorted by page, then y (top to bottom),
        then x (left to right).
    """
    path = str(Path(pdf_path).resolve())
    blocks: list[TextBlock] = []
    with open_pdf(path, password=password) as pdf:
        pages = _resolve_pages(pdf, page)
        for page_num, page_obj in pages:
            elements = _build_index(page_obj, page_num, pdf_path=path)
            for elem in elements:
                if elem.type != "text" or not elem.text_content:
                    continue
                x0, y0, x1, _y1 = elem.bbox
                width = x1 - x0
                if width <= 0 and elem.characters:
                    width = sum(ch.width for ch in elem.characters)
                gs = elem.graphics_state
                f_name = gs.font_name or ""
                f_size = gs.font_size or 0.0
                blocks.append(
                    TextBlock(
                        text=elem.text_content,
                        x=x0,
                        y=y0,
                        width=width,
                        height=f_size,
                        font_name=f_name,
                        font_size=f_size,
                        page=elem.page,
                    )
                )
    blocks.sort(key=lambda b: (b.page, -b.y, b.x))
    return blocks


def extract_bbox_text(
    pdf_path: str,
    *,
    bbox: tuple[float, float, float, float],
    page: int,
    tolerance: float = 2.0,
    password: str | bytes | None = None,
) -> str:
    """Extract text from a bounding box region with gap-aware joining.

    Uses the same gap detection logic as :func:`get_text` to avoid
    inserting spurious spaces between adjacent text runs (e.g., "monthly"
    stays "monthly", not "month ly").

    Args:
        pdf_path: Path to the PDF file.
        bbox: Target region ``(x0, y0, x1, y1)`` in PDF coordinates.
        page: 0-indexed page number.
        tolerance: Extra margin (in points) for bbox overlap matching.
        password: Optional password to open an encrypted PDF (A2.3 / INV-W-5).

    Returns:
        Extracted text with lines separated by newlines.
    """
    x0, y0, x1, y1 = bbox
    path = str(Path(pdf_path).resolve())
    with open_pdf(path, password=password) as pdf:
        pages = _resolve_pages(pdf, page)
        if not pages:
            return ""
        _, page_obj = pages[0]
        elements = _build_index(page_obj, page, pdf_path=path)
        text_elements = [e for e in elements if e.type == "text" and e.text_content]

        # Filter to elements overlapping the bbox (with tolerance)
        hits: list[ContentElement] = []
        for elem in text_elements:
            ex0, ey0, ex1, ey1 = elem.bbox
            if (
                ex0 < x1 + tolerance
                and ex1 > x0 - tolerance
                and ey0 < y1 + tolerance
                and ey1 > y0 - tolerance
            ):
                hits.append(elem)

        # Sort by reading order: y descending (top first), x ascending
        hits.sort(key=lambda e: (-e.bbox[3], e.bbox[0]))

        # Reuse the gap-aware line grouping
        lines = _group_into_lines(hits)
        return "\n".join(lines)
