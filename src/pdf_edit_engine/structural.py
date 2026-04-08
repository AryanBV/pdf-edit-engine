"""Structural editing module — block-level PDF content operations.

Provides bbox-based operations: replace_block, delete_block,
insert_text_block, and shift_content_below.  These work on spatial
regions rather than text matches, enabling paragraph-level editing.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

import pikepdf

from pdf_edit_engine._pathutil import validate_output_path
from pdf_edit_engine.encoding import FontResolverCache
from pdf_edit_engine.errors import OperatorError
from pdf_edit_engine.locator import _build_index, _resolve_pages
from pdf_edit_engine.models import ContentElement, EditResult, FidelityReport
from pdf_edit_engine.reflow import (
    _build_replacement_ops,
    _expand_to_bt_et,
    _find_bt_et_blocks,
    break_into_lines,
)

logger = logging.getLogger(__name__)

_Ops = list[Any]

_resolver_cache = FontResolverCache()

# Path construction operators whose y-coordinates need shifting
_PATH_Y_OPS = frozenset({"m", "l", "c", "v", "y", "re"})


# ── Helpers ──────────────────────────────────────────────────────────────


def _bbox_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    """Test if two (x0, y0, x1, y1) bboxes overlap."""
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def _collect_elements_in_bbox(
    elements: list[ContentElement],
    bbox: tuple[float, float, float, float],
) -> tuple[list[ContentElement], set[int]]:
    """Find elements overlapping a bbox and collect their operator indices.

    Args:
        elements: Content element index from _build_index.
        bbox: Target region (x0, y0, x1, y1).

    Returns:
        Tuple of (matching elements, set of operator indices).
    """
    matched: list[ContentElement] = []
    op_indices: set[int] = set()
    for elem in elements:
        if _bbox_overlap(elem.bbox, bbox):
            matched.append(elem)
            start, end = elem.operator_range
            for i in range(start, end):
                op_indices.add(i)
    return matched, op_indices


def _get_op_str(inst: Any) -> str:
    """Get operator string from a content stream instruction or tuple."""
    if hasattr(inst, "operator"):
        return str(inst.operator)
    return str(inst[1])


def _get_operands(inst: Any) -> list[Any]:
    """Get operands list from a content stream instruction or tuple."""
    if hasattr(inst, "operands"):
        return list(inst.operands)
    return list(inst[0])


def _invalidate_caches() -> None:
    """Clear locator and local caches after structural edits."""
    from pdf_edit_engine import locator

    locator._cached_path = None  # noqa: SLF001
    locator._cached_elements = {}  # noqa: SLF001
    _resolver_cache.clear()


def _detect_font_from_elements(
    elements: list[ContentElement],
) -> tuple[str, float, tuple[float, ...] | None]:
    """Auto-detect font name, size, and fill color from text elements.

    Args:
        elements: Content elements (should include text elements).

    Returns:
        Tuple of (font_name, font_size, fill_color).

    Raises:
        OperatorError: If no text elements found.
    """
    for elem in elements:
        if elem.type == "text" and elem.graphics_state.font_name:
            return (
                elem.graphics_state.font_name,
                elem.graphics_state.font_size or 12.0,
                elem.graphics_state.fill_color,
            )
    raise OperatorError("No text elements found in region for font detection")


def _check_page_overflow(
    elements: list[ContentElement],
    page_obj: pikepdf.Page,
    delta_y: float,
    y_threshold: float,
) -> list[str]:
    """Check if shifted elements exceed page boundaries.

    Args:
        elements: All content elements on the page.
        page_obj: The pikepdf page object.
        delta_y: Shift amount (positive = down in visual terms).
        y_threshold: Only elements below this were shifted.

    Returns:
        List of warning strings.
    """
    warnings: list[str] = []
    mediabox = page_obj.get("/MediaBox")
    if mediabox is None:
        return warnings
    page_bottom = float(mediabox[1])

    for elem in elements:
        elem_bottom = elem.bbox[1]
        if elem_bottom < y_threshold:
            new_bottom = elem_bottom - delta_y
            if new_bottom < page_bottom:
                overshoot = page_bottom - new_bottom
                warnings.append(
                    f"Content shifted below page boundary by {overshoot:.1f}pt"
                )
                break
    return warnings


# ── Core: shift content below ────────────────────────────────────────────


def _shift_content_below_inplace(
    pdf: pikepdf.Pdf,
    page_obj: pikepdf.Page,
    page_num: int,
    y_threshold: float,
    delta_y: float,
) -> list[str]:
    """Shift all content below y_threshold by delta_y on an open page.

    Modifies the content stream and annotations in-place.

    Convention: positive delta_y shifts content DOWN (decreases y-values
    in PDF coordinates where y=0 is page bottom).  Negative delta_y
    shifts content UP (increases y-values, closing gaps).

    Internally: ``y_new = y_old - delta_y``

    Args:
        pdf: Open pikepdf.Pdf object.
        page_obj: Page to modify.
        page_num: 0-indexed page number (for element index).
        y_threshold: Only elements with y < y_threshold are shifted.
        delta_y: Shift amount.  Positive = down, negative = up.

    Returns:
        List of warning strings (e.g. overflow warnings).
    """
    if delta_y == 0.0:
        return []

    ops: _Ops = list(pikepdf.parse_content_stream(page_obj))

    # ── Text: walk ops tracking BT/ET context ────────────────────────
    #
    # For each BT/ET block, find the FIRST positioning op (Tm or Td).
    #   - Tm is absolute: if ty < y_threshold, shift it.
    #   - First Td (no preceding Tm in block): ty is absolute (BT resets
    #     text matrix to identity).  If ty < y_threshold, shift it.
    #   - Subsequent Td/TD: relative line spacing — do NOT modify.
    #     Shifting the first op cascades to all lines in the block.
    in_bt = False
    seen_positioning_in_block = False

    for i in range(len(ops)):
        inst = ops[i]
        op_str = _get_op_str(inst)

        if op_str == "BT":
            in_bt = True
            seen_positioning_in_block = False
            continue
        if op_str == "ET":
            in_bt = False
            continue

        if in_bt and op_str == "Tm":
            operands = _get_operands(inst)
            if len(operands) >= 6:
                ty = float(operands[5])
                if ty < y_threshold:
                    new_operands = list(operands)
                    new_operands[5] = pikepdf.Object.parse(
                        str(ty - delta_y).encode()
                    )
                    operator = inst.operator if hasattr(inst, "operator") else inst[1]
                    ops[i] = (new_operands, operator)
            seen_positioning_in_block = True
            continue

        if in_bt and op_str in ("Td", "TD") and not seen_positioning_in_block:
            # First Td/TD in block with no preceding Tm — absolute y
            operands = _get_operands(inst)
            if len(operands) >= 2:
                ty = float(operands[1])
                if ty < y_threshold:
                    operator = inst.operator if hasattr(inst, "operator") else inst[1]
                    ops[i] = (
                        [operands[0], pikepdf.Object.parse(str(ty - delta_y).encode())],
                        operator,
                    )
            seen_positioning_in_block = True
            continue

    # ── Paths: shift y-coordinates directly ──────────────────────────
    for i in range(len(ops)):
        inst = ops[i]
        op_str = _get_op_str(inst)
        operands = _get_operands(inst)
        operator = inst.operator if hasattr(inst, "operator") else inst[1]

        if op_str in ("m", "l") and len(operands) >= 2:
            y_val = float(operands[1])
            if y_val < y_threshold:
                ops[i] = (
                    [operands[0], pikepdf.Object.parse(str(y_val - delta_y).encode())],
                    operator,
                )

        elif op_str == "c" and len(operands) >= 6:
            # Check if any control point is below threshold
            ys = [float(operands[j]) for j in (1, 3, 5)]
            if any(y < y_threshold for y in ys):
                new_operands = list(operands)
                for yi in (1, 3, 5):
                    val = float(operands[yi])
                    if val < y_threshold:
                        new_operands[yi] = pikepdf.Object.parse(
                            str(val - delta_y).encode()
                        )
                ops[i] = (new_operands, operator)

        elif op_str in ("v", "y") and len(operands) >= 4:
            ys = [float(operands[j]) for j in (1, 3)]
            if any(y < y_threshold for y in ys):
                new_operands = list(operands)
                for yi in (1, 3):
                    val = float(operands[yi])
                    if val < y_threshold:
                        new_operands[yi] = pikepdf.Object.parse(
                            str(val - delta_y).encode()
                        )
                ops[i] = (new_operands, operator)

        elif op_str == "re" and len(operands) >= 4:
            y_val = float(operands[1])
            if y_val < y_threshold:
                ops[i] = (
                    [
                        operands[0],
                        pikepdf.Object.parse(str(y_val - delta_y).encode()),
                        operands[2],
                        operands[3],
                    ],
                    operator,
                )

        elif op_str == "cm" and len(operands) >= 6:
            # Only shift cm if it precedes a Do (image placement).
            # General CTM setups (identity, page-level transforms)
            # must NOT be shifted — they'd corrupt all subsequent content.
            next_is_do = False
            for j in range(i + 1, min(i + 4, len(ops))):
                nop = _get_op_str(ops[j])
                if nop == "Do":
                    next_is_do = True
                    break
                if nop not in ("q", "Q", "gs"):
                    break
            if next_is_do:
                ty = float(operands[5])
                if ty < y_threshold:
                    new_operands = list(operands)
                    new_operands[5] = pikepdf.Object.parse(
                        str(ty - delta_y).encode()
                    )
                    ops[i] = (new_operands, operator)

    # Write back content stream
    new_stream = pikepdf.unparse_content_stream(ops)
    page_obj.Contents = pdf.make_stream(new_stream)

    # ── Annotations: shift rects below threshold ─────────────────────
    annots_key = pikepdf.Name("/Annots")
    if annots_key in page_obj:
        try:
            annots: list[Any] = list(page_obj[annots_key])  # type: ignore[call-overload]
            for annot_ref in annots:
                annot = (
                    annot_ref.resolve()
                    if hasattr(annot_ref, "resolve")
                    else annot_ref
                )
                rect_key = pikepdf.Name("/Rect")
                if rect_key in annot:
                    rect = annot[rect_key]
                    rect_y1 = float(rect[3])
                    if rect_y1 < y_threshold:
                        annot[rect_key] = pikepdf.Array([
                            float(rect[0]),
                            float(rect[1]) - delta_y,
                            float(rect[2]),
                            rect_y1 - delta_y,
                        ])
        except (KeyError, TypeError, IndexError):
            logger.warning("Failed to shift annotations on page %d", page_num)

    # ── Overflow check ───────────────────────────────────────────────
    mediabox = page_obj.get("/MediaBox")
    warnings: list[str] = []
    if mediabox is not None:
        page_bottom = float(mediabox[1])
        # Re-parse to check final positions
        elements = _build_index(page_obj, page_num)
        for elem in elements:
            if elem.bbox[1] < page_bottom:
                overshoot = page_bottom - elem.bbox[1]
                warnings.append(
                    f"Content shifted below page boundary by {overshoot:.1f}pt"
                )
                break
    return warnings


# ── Public API ───────────────────────────────────────────────────────────


def shift_content_below(
    pdf_path: str,
    page_number: int,
    y_threshold: float,
    delta_y: float,
    output_path: str,
) -> EditResult:
    """Shift all content below a y-threshold on a page.

    Convention: positive delta_y shifts content DOWN the page (decreases
    y-values in PDF coordinates where y=0 is page bottom).  Negative
    delta_y shifts content UP (increases y-values, closing gaps).

    Example: to make 20pt of room above y=400, call
    ``shift_content_below(pdf, 0, 400, 20, out)`` — all elements with
    y < 400 move down by 20pt.

    Args:
        pdf_path: Path to the input PDF.
        page_number: 0-indexed page number.
        y_threshold: Only elements with y < y_threshold are shifted.
        delta_y: Shift amount.  Positive = down, negative = up.
        output_path: Path for the output PDF.

    Returns:
        EditResult with fidelity information.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    try:
        pages = _resolve_pages(pdf, page_number)
        _, page_obj = pages[0]

        warnings = _shift_content_below_inplace(
            pdf, page_obj, page_number, y_threshold, delta_y,
        )

        overflow = any("below page boundary" in w for w in warnings)
        pdf.save(output_path)
        _invalidate_caches()

        return EditResult(
            success=True,
            original_text="",
            new_text="",
            font_action="kept",
            warnings=warnings,
            fidelity_report=FidelityReport(
                font_preserved=True,
                font_substituted=None,
                overflow_detected=overflow,
                reflow_applied=False,
                glyphs_missing=[],
            ),
        )
    finally:
        pdf.close()


def replace_block(
    pdf_path: str,
    page_number: int,
    bbox: tuple[float, float, float, float],
    new_text: str,
    output_path: str,
    font_name: str | None = None,
    font_size: float | None = None,
) -> EditResult:
    """Replace all content within a bounding box with new reflowed text.

    Identifies all content stream operators within the bbox, removes them,
    and writes new text using the same (or specified) font and size.

    Args:
        pdf_path: Path to the input PDF.
        page_number: 0-indexed page number.
        bbox: Target region (x0, y0, x1, y1) in PDF coordinates.
        new_text: Replacement text.
        output_path: Path for the output PDF.
        font_name: Font resource name (e.g. 'F1').  Auto-detected if None.
        font_size: Font size in points.  Auto-detected if None.

    Returns:
        EditResult with fidelity information.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    try:
        pages = _resolve_pages(pdf, page_number)
        _, page_obj = pages[0]

        # Build element index and find elements in bbox
        elements = _build_index(page_obj, page_number)
        matched_elems, op_indices = _collect_elements_in_bbox(elements, bbox)

        if not matched_elems:
            return EditResult(
                success=False,
                original_text="",
                new_text=new_text,
                font_action="kept",
                warnings=["No content found in specified bounding box"],
            )

        # Detect font if not specified
        det_name, det_size, fill_color = _detect_font_from_elements(matched_elems)
        if font_name is None:
            font_name = det_name
        if font_size is None:
            font_size = det_size

        # Collect original text for the result
        original_parts: list[str] = []
        for elem in matched_elems:
            if elem.text_content:
                original_parts.append(elem.text_content)
        original_text = " ".join(original_parts)

        # Parse content stream and compute removal set
        ops: _Ops = list(pikepdf.parse_content_stream(page_obj))
        blocks = _find_bt_et_blocks(ops)
        removal_indices = _expand_to_bt_et(sorted(op_indices), blocks)
        removal_set = set(removal_indices)

        # Resolve font for encoding
        font_key = font_name if font_name.startswith("/") else f"/{font_name}"
        clean_name = font_name.lstrip("/")
        try:
            font_ref = page_obj["/Resources"]["/Font"][font_key]
        except (KeyError, TypeError) as exc:
            raise OperatorError(
                f"Font {font_name} not found in page resources"
            ) from exc

        resolver = _resolver_cache.get_resolver(page_obj, clean_name)

        # Check encodability — extend font subset if needed
        can_enc, missing = resolver.can_encode(new_text)
        font_action_str = "kept"
        if not can_enc:
            try:
                from pdf_edit_engine.fonts import extend_subset

                extend_subset(pdf, page_obj, clean_name, "".join(missing))
                _resolver_cache.evict(page_obj, clean_name)
                resolver = _resolver_cache.get_resolver(page_obj, clean_name)
                can_enc_after, still_missing = resolver.can_encode(new_text)
                if not can_enc_after:
                    return EditResult(
                        success=False,
                        original_text=" ".join(original_parts),
                        new_text=new_text,
                        font_action="failed",
                        warnings=[f"Cannot encode: {''.join(still_missing)}"],
                        fidelity_report=FidelityReport(
                            font_preserved=True,
                            font_substituted=None,
                            overflow_detected=False,
                            reflow_applied=False,
                            glyphs_missing=still_missing,
                        ),
                    )
                font_action_str = "extended"
            except Exception:
                logger.warning("Font extension failed", exc_info=True)
                return EditResult(
                    success=False,
                    original_text=" ".join(original_parts),
                    new_text=new_text,
                    font_action="failed",
                    warnings=["Font extension failed"],
                )

        # Break text into lines for the bbox width
        bbox_width = bbox[2] - bbox[0]
        lines = break_into_lines(
            new_text,
            bbox_width,
            resolver,
            font_ref,
            font_size,
        )

        # Line height from detected elements or default
        line_height = font_size * 1.2

        # Build replacement BT/ET block
        replacement = _build_replacement_ops(
            lines=lines,
            font_name=clean_name,
            font_size=font_size,
            fill_color=fill_color,
            left_margin=bbox[0],
            first_line_y=bbox[3] - font_size,
            line_height=line_height,
            resolver=resolver,
        )

        # Splice: remove old operators, insert replacement
        insert_pos = min(removal_set)
        new_ops: _Ops = []
        inserted = False
        for i, op in enumerate(ops):
            if i == insert_pos and not inserted:
                new_ops.extend(replacement)
                inserted = True
            if i not in removal_set:
                new_ops.append(op)

        # Write back
        new_stream = pikepdf.unparse_content_stream(new_ops)
        page_obj.Contents = pdf.make_stream(new_stream)
        pdf.save(output_path)
        _invalidate_caches()

        # Overflow detection: does text extend below bbox bottom?
        text_height = len(lines) * line_height
        overflow = (bbox[3] - font_size - text_height + line_height) < bbox[1]

        return EditResult(
            success=True,
            original_text=original_text,
            new_text=new_text,
            font_action=font_action_str,  # type: ignore[arg-type]
            fidelity_report=FidelityReport(
                font_preserved=font_action_str == "kept",
                font_substituted=None,
                overflow_detected=overflow,
                reflow_applied=True,
                glyphs_missing=[],
            ),
        )
    finally:
        pdf.close()


def insert_text_block(
    pdf_path: str,
    page_number: int,
    x: float,
    y: float,
    text: str,
    output_path: str,
    *,
    font_name: str | None = None,
    font_size: float = 12.0,
    max_width: float | None = None,
) -> EditResult:
    """Insert a new text block at a position, shifting existing content down.

    Shifts all content below the insertion point to make room, then
    appends new text operators to the content stream.

    Args:
        pdf_path: Path to the input PDF.
        page_number: 0-indexed page number.
        x: X-position for the left edge of new text.
        y: Y-position for the first line of new text.
        text: Text to insert.
        output_path: Path for the output PDF.
        font_name: Font resource name (e.g. 'F1').  Uses most common
            font on the page if None.
        font_size: Font size in points (default 12.0).
        max_width: Maximum line width.  Defaults to page width minus x
            minus a 36pt right margin.

    Returns:
        EditResult with fidelity information.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    try:
        pages = _resolve_pages(pdf, page_number)
        _, page_obj = pages[0]

        # Auto-detect font if needed
        elements = _build_index(page_obj, page_number)
        if font_name is None:
            # Use the most common font on the page
            font_counts: Counter[str] = Counter()
            for elem in elements:
                if elem.type == "text" and elem.graphics_state.font_name:
                    font_counts[elem.graphics_state.font_name] += 1
            if font_counts:
                font_name = font_counts.most_common(1)[0][0]
            else:
                raise OperatorError("No fonts found on page for insertion")

        # Determine fill color from first text element
        fill_color: tuple[float, ...] | None = None
        for elem in elements:
            if elem.type == "text":
                fill_color = elem.graphics_state.fill_color
                break

        # Resolve font
        font_key = font_name if font_name.startswith("/") else f"/{font_name}"
        clean_name = font_name.lstrip("/")
        try:
            font_ref = page_obj["/Resources"]["/Font"][font_key]
        except (KeyError, TypeError) as exc:
            raise OperatorError(
                f"Font {font_name} not found in page resources"
            ) from exc

        resolver = _resolver_cache.get_resolver(page_obj, clean_name)

        # Check encodability — extend font if needed
        can_enc, missing = resolver.can_encode(text)
        if not can_enc:
            try:
                from pdf_edit_engine.fonts import extend_subset

                extend_subset(pdf, page_obj, clean_name, "".join(missing))
                _resolver_cache.evict(page_obj, clean_name)
                resolver = _resolver_cache.get_resolver(page_obj, clean_name)
            except Exception:
                logger.warning("Font extension failed for insert", exc_info=True)
                return EditResult(
                    success=False,
                    original_text="",
                    new_text=text,
                    font_action="failed",
                    warnings=["Font extension failed"],
                )

        # Determine max width
        if max_width is None:
            mediabox = page_obj.get("/MediaBox")
            if mediabox:
                page_width = float(mediabox[2]) - float(mediabox[0])
                max_width = page_width - x - 36.0  # 36pt right margin
            else:
                max_width = 500.0  # reasonable default

        # Break text into lines
        lines = break_into_lines(text, max_width, resolver, font_ref, font_size)
        line_height = font_size * 1.2
        text_height = len(lines) * line_height

        # Shift existing content down to make room.
        # Use y + 0.5 as threshold so elements AT the insertion y also shift.
        shift_warnings = _shift_content_below_inplace(
            pdf, page_obj, page_number, y + 0.5, text_height,
        )

        # Re-parse content stream after shift
        ops: _Ops = list(pikepdf.parse_content_stream(page_obj))

        # Build new BT/ET block
        new_block = _build_replacement_ops(
            lines=lines,
            font_name=clean_name,
            font_size=font_size,
            fill_color=fill_color,
            left_margin=x,
            first_line_y=y,
            line_height=line_height,
            resolver=resolver,
        )

        # Append to end of content stream
        ops.extend(new_block)

        # Write back
        new_stream = pikepdf.unparse_content_stream(ops)
        page_obj.Contents = pdf.make_stream(new_stream)
        pdf.save(output_path)
        _invalidate_caches()

        overflow = any("below page boundary" in w for w in shift_warnings)
        return EditResult(
            success=True,
            original_text="",
            new_text=text,
            font_action="kept",
            warnings=shift_warnings,
            fidelity_report=FidelityReport(
                font_preserved=True,
                font_substituted=None,
                overflow_detected=overflow,
                reflow_applied=True,
                glyphs_missing=[],
            ),
        )
    finally:
        pdf.close()


def delete_block(
    pdf_path: str,
    page_number: int,
    bbox: tuple[float, float, float, float],
    output_path: str,
    *,
    close_gap: bool = True,
) -> EditResult:
    """Delete all content within a bounding box.

    Optionally shifts content below the deleted region up to close the gap.

    Args:
        pdf_path: Path to the input PDF.
        page_number: 0-indexed page number.
        bbox: Region to delete (x0, y0, x1, y1) in PDF coordinates.
        output_path: Path for the output PDF.
        close_gap: If True, shift content below the deleted region up
            to close the gap (default True).

    Returns:
        EditResult with fidelity information.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    try:
        pages = _resolve_pages(pdf, page_number)
        _, page_obj = pages[0]

        # Build element index and find elements in bbox
        elements = _build_index(page_obj, page_number)
        matched_elems, op_indices = _collect_elements_in_bbox(elements, bbox)

        if not matched_elems:
            pdf.save(output_path)
            return EditResult(
                success=True,
                original_text="",
                new_text="",
                font_action="kept",
                warnings=["No content found in specified bounding box"],
            )

        # Collect original text
        original_parts: list[str] = []
        for elem in matched_elems:
            if elem.text_content:
                original_parts.append(elem.text_content)
        original_text = " ".join(original_parts)

        # Parse content stream and compute removal set
        ops: _Ops = list(pikepdf.parse_content_stream(page_obj))
        blocks = _find_bt_et_blocks(ops)
        removal_indices = _expand_to_bt_et(sorted(op_indices), blocks)
        removal_set = set(removal_indices)

        # Remove operators
        new_ops = [op for i, op in enumerate(ops) if i not in removal_set]

        # Write back content stream
        new_stream = pikepdf.unparse_content_stream(new_ops)
        page_obj.Contents = pdf.make_stream(new_stream)

        # Delete annotations overlapping the bbox
        annots_key = pikepdf.Name("/Annots")
        if annots_key in page_obj:
            try:
                annots: list[Any] = list(page_obj[annots_key])  # type: ignore[call-overload]
                kept: list[Any] = []
                for annot_ref in annots:
                    annot = (
                        annot_ref.resolve()
                        if hasattr(annot_ref, "resolve")
                        else annot_ref
                    )
                    rect_key = pikepdf.Name("/Rect")
                    if rect_key in annot:
                        rect = annot[rect_key]
                        annot_bbox = (
                            float(rect[0]),
                            float(rect[1]),
                            float(rect[2]),
                            float(rect[3]),
                        )
                        if _bbox_overlap(annot_bbox, bbox):
                            continue  # remove this annotation
                    kept.append(annot_ref)
                if len(kept) != len(annots):
                    page_obj[annots_key] = pdf.make_indirect(
                        pikepdf.Array(kept)
                    )
            except (KeyError, TypeError, IndexError):
                logger.warning("Failed to clean annotations on page %d", page_number)

        # Shift content up to close the gap
        warnings: list[str] = []
        deleted_height = bbox[3] - bbox[1]
        if close_gap and deleted_height > 0:
            # Shift content below the deleted region UP
            # y_threshold = bbox[1] (bottom of deleted region)
            # delta_y = -deleted_height (negative = shift up)
            warnings = _shift_content_below_inplace(
                pdf, page_obj, page_number, bbox[1], -deleted_height,
            )

        pdf.save(output_path)
        _invalidate_caches()

        overflow = any("below page boundary" in w for w in warnings)
        return EditResult(
            success=True,
            original_text=original_text,
            new_text="",
            font_action="kept",
            warnings=warnings,
            fidelity_report=FidelityReport(
                font_preserved=True,
                font_substituted=None,
                overflow_detected=overflow,
                reflow_applied=False,
                glyphs_missing=[],
            ),
        )
    finally:
        pdf.close()
