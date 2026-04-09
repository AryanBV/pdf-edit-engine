"""ReflowEngine module — paragraph detection, line breaking, and content stream rewriting."""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pikepdf

from pdf_edit_engine.encoding import FontResolver, FontResolverCache
from pdf_edit_engine.errors import EncodingError, FontNotFoundError, ReflowError
from pdf_edit_engine.models import (
    ContentElement,
    EditResult,
    FidelityReport,
    Paragraph,
    TextMatch,
)
from pdf_edit_engine.widths import (
    DEFAULT_WIDTH,
    GlyphWidthCache,
    parse_cid_widths,
    parse_simple_widths,
)

logger = logging.getLogger(__name__)

# Parsed content stream ops — using Any mirrors surgeon.py convention.
_Ops = list[Any]

_TEXT_OPS = frozenset({"Tj", "TJ", "'", '"'})

# Bullet/list-item markers — matches lines starting with •, -, *, or numbered lists (1., 2), etc.)
_BULLET_RE = re.compile(r"^\s*([•\-\*]|\d+[.\)])\s")


# ── Width helpers ─────────────────────────────────────────────────────


def _load_widths_from_ref(font_ref: pikepdf.Object) -> dict[int, float]:
    """Parse glyph widths from a raw font reference object.

    Args:
        font_ref: Raw font dictionary from page Resources.

    Returns:
        Dict mapping character/CID codes to widths in font units.
    """
    subtype_obj = font_ref.get("/Subtype")
    subtype = str(subtype_obj) if subtype_obj is not None else ""
    if subtype == "/Type0":
        try:
            cid_font = font_ref["/DescendantFonts"][0]
            return parse_cid_widths(
                pikepdf.Dictionary(cid_font),  # type: ignore[arg-type]
            )
        except (KeyError, IndexError):
            return {}
    return parse_simple_widths(
        pikepdf.Dictionary(font_ref),  # type: ignore[arg-type]
    )


def _get_space_width(
    resolver: FontResolver,
    widths: dict[int, float],
    font_size: float,
    horizontal_scaling: float,
    word_spacing: float,
) -> float:
    """Get the width of a space character with fallback.

    Args:
        resolver: Font resolver for encoding.
        widths: Parsed width table.
        font_size: Current font size in points.
        horizontal_scaling: Horizontal scaling factor.
        word_spacing: Additional word spacing.

    Returns:
        Space width in page-space units.
    """
    byte_width = resolver.byte_width
    try:
        encoded = resolver.encode(" ")
        if byte_width == 2 and len(encoded) >= 2:
            char_code = (encoded[0] << 8) | encoded[1]
        elif encoded:
            char_code = encoded[0]
        else:
            return font_size * 0.25 * horizontal_scaling + word_spacing
        w = widths.get(char_code, DEFAULT_WIDTH)
        return (w / 1000.0) * font_size * horizontal_scaling + word_spacing
    except KeyError:
        return font_size * 0.25 * horizontal_scaling + word_spacing


def _measure_word(
    word: str,
    resolver: FontResolver,
    widths: dict[int, float],
    font_size: float,
    horizontal_scaling: float,
    char_spacing: float,
) -> float:
    """Calculate width of a word in page-space units.

    Args:
        word: The word to measure.
        resolver: Font resolver for encoding.
        widths: Parsed width table.
        font_size: Current font size in points.
        horizontal_scaling: Horizontal scaling factor.
        char_spacing: Extra spacing per character.

    Returns:
        Total word width in page-space units.
    """
    byte_width = resolver.byte_width
    try:
        encoded = resolver.encode(word)
    except KeyError:
        # Fallback estimate for unencodable words
        return len(word) * font_size * 0.5

    total = 0.0
    n_chars = len(encoded) // byte_width if byte_width > 0 else 0
    for i in range(0, len(encoded), byte_width):
        if byte_width == 2 and i + 1 < len(encoded):
            char_code = (encoded[i] << 8) | encoded[i + 1]
        else:
            char_code = encoded[i]
        w = widths.get(char_code, DEFAULT_WIDTH)
        total += (w / 1000.0) * font_size * horizontal_scaling + char_spacing
    # Remove trailing char_spacing (applied per-char but not after last)
    if n_chars > 0:
        total -= char_spacing
    return total


# ── Paragraph detection helpers ───────────────────────────────────────


def _compute_x_mode(x_values: list[float]) -> float:
    """Compute the mode of x-positions rounded to nearest integer.

    Args:
        x_values: List of x-start positions.

    Returns:
        The most common x-start value (as float).
    """
    if not x_values:
        return 0.0
    rounded = [round(x) for x in x_values]
    counter = Counter(rounded)
    mode_val = counter.most_common(1)[0][0]
    return float(mode_val)


def _group_elements_into_lines(
    elements: list[ContentElement],
    font_size: float,
) -> list[list[ContentElement]]:
    """Group elements into visual lines based on y-position proximity.

    Args:
        elements: Text elements sorted by reading order (y desc, x asc).
        font_size: Font size for threshold calculation.

    Returns:
        List of lines, each a list of elements. Lines sorted top-to-bottom,
        elements within each line sorted left-to-right.
    """
    if not elements:
        return []

    threshold = font_size * 0.5
    lines: list[list[ContentElement]] = [[elements[0]]]

    for elem in elements[1:]:
        prev_y = lines[-1][0].characters[0].page_y  # type: ignore[index]
        curr_y = elem.characters[0].page_y  # type: ignore[index]
        if abs(prev_y - curr_y) <= threshold:
            lines[-1].append(elem)
        else:
            lines.append([elem])

    # Sort each line by x
    for line in lines:
        line.sort(key=lambda e: e.bbox[0])

    return lines


def _build_paragraph(
    elements: list[ContentElement],
) -> Paragraph:
    """Build a Paragraph object from a group of related text elements.

    Args:
        elements: Text elements belonging to this paragraph,
                  sorted by reading order.

    Returns:
        Paragraph with computed metrics.
    """
    chars0 = elements[0].characters
    assert chars0 is not None
    font_size = chars0[0].font_size
    font_name = chars0[0].font_name

    lines = _group_elements_into_lines(elements, font_size)

    # Left margin: mode of line-start x-positions
    x_starts = [
        line[0].characters[0].page_x  # type: ignore[index]
        for line in lines
    ]
    left_margin = _compute_x_mode(x_starts)

    # Right margin: rightmost extent of any element
    right_margin = max(e.bbox[2] for e in elements)

    paragraph_width = right_margin - left_margin
    if paragraph_width < 1.0:
        paragraph_width = 1.0

    # Line height: average y-gap between consecutive lines
    if len(lines) > 1:
        y_positions = [
            line[0].characters[0].page_y  # type: ignore[index]
            for line in lines
        ]
        gaps = [y_positions[i] - y_positions[i + 1] for i in range(len(y_positions) - 1)]
        positive_gaps = [g for g in gaps if g > 0]
        line_height = sum(positive_gaps) / len(positive_gaps) if positive_gaps else font_size * 1.2
    else:
        line_height = font_size * 1.2

    # Full text: join elements using position-aware spacing so that
    # adjacent elements (gap < half a space width) are joined without
    # extra space, matching pdfminer's extraction output.
    space_width = font_size * 0.25  # approximate half-space threshold
    line_texts: list[str] = []
    for line in lines:
        text_parts: list[str] = []
        prev_end_x: float | None = None
        for e in line:
            if not e.text_content or not e.characters:
                continue
            curr_start_x = e.characters[0].page_x
            if prev_end_x is not None and (curr_start_x - prev_end_x) > space_width:
                text_parts.append(" ")
            text_parts.append(e.text_content)
            prev_end_x = e.characters[-1].page_x + e.characters[-1].width
        line_texts.append("".join(text_parts))
    full_text = "\n".join(line_texts)

    # Operator indices: specific indices, not a range
    op_indices = [e.operator_range[0] for e in elements]

    first_line_y = chars0[0].page_y

    return Paragraph(
        elements=elements,
        full_text=full_text,
        left_margin=left_margin,
        right_margin=right_margin,
        paragraph_width=paragraph_width,
        line_height=line_height,
        font_name=font_name,
        font_size=font_size,
        first_line_y=first_line_y,
        line_count=len(lines),
        operator_indices=op_indices,
    )


def _detect_paragraphs_from_index(
    elements: list[ContentElement],
    page_width: float = 612.0,
) -> list[Paragraph]:
    """Detect paragraphs from a pre-built content element index.

    Args:
        elements: Full content element index for a page.
        page_width: Page width in points (for margin calculations).

    Returns:
        List of detected Paragraph objects.
    """
    # Filter text elements with actual content
    text_elems = [e for e in elements if e.type == "text" and e.text_content and e.characters]

    # Sort by reading order: y descending (top first), x ascending (left first)
    text_elems.sort(key=lambda e: (-e.bbox[3], e.bbox[0]))

    if not text_elems:
        return []

    paragraphs: list[Paragraph] = []
    current_group: list[ContentElement] = [text_elems[0]]

    for elem in text_elems[1:]:
        prev = current_group[-1]
        prev_chars = prev.characters
        curr_chars = elem.characters
        assert prev_chars is not None and curr_chars is not None

        prev_font = prev_chars[0].font_name
        prev_size = prev_chars[0].font_size
        curr_font = curr_chars[0].font_name
        curr_size = curr_chars[0].font_size
        prev_y = prev_chars[0].page_y
        curr_y = curr_chars[0].page_y
        curr_x = curr_chars[0].page_x
        # Compare x against group start, not last element — avoids false breaks
        # when multi-element lines end far right of where continuation starts.
        group_start_x = current_group[0].characters[0].page_x  # type: ignore[index]

        should_break = False

        # Font change
        if prev_font != curr_font or abs(prev_size - curr_size) > 0.5:
            should_break = True

        # Y-gap (elements sorted y-descending, so prev_y >= curr_y)
        y_gap = abs(prev_y - curr_y)
        if not should_break and y_gap > 2.5 * curr_size:
            should_break = True

        # X-start jump (only for elements on different lines)
        if not should_break and y_gap > curr_size * 0.5 and abs(curr_x - group_start_x) > 50.0:
            should_break = True

        # Bullet boundary — new bullet on a different line starts a new paragraph
        if not should_break and y_gap > curr_size * 0.3:
            curr_text = elem.text_content or ""
            if _BULLET_RE.match(curr_text):
                should_break = True

        if should_break:
            paragraphs.append(_build_paragraph(current_group))
            current_group = [elem]
        else:
            current_group.append(elem)

    # Finalize last group
    paragraphs.append(_build_paragraph(current_group))

    return paragraphs


# ── Public API: paragraph detection ───────────────────────────────────


def detect_paragraphs(
    pdf_path: str | Path,
    page: int = 0,
) -> list[Paragraph]:
    """Detect paragraph blocks on a page.

    Args:
        pdf_path: Path to the PDF file.
        page: 0-indexed page number.

    Returns:
        List of detected Paragraph objects.
    """
    from pdf_edit_engine.locator import _build_index

    resolved = str(Path(pdf_path).resolve())
    pdf = pikepdf.Pdf.open(resolved)
    try:
        if page >= len(pdf.pages):
            msg = f"Page {page} out of range (PDF has {len(pdf.pages)} pages)"
            raise ReflowError(msg)

        page_obj = pdf.pages[page]
        elements = _build_index(page_obj, page, resolved)
        page_width = float(page_obj.MediaBox[2]) if page_obj.MediaBox else 612.0
        return _detect_paragraphs_from_index(elements, page_width)
    finally:
        pdf.close()


def find_paragraph_for_match(
    paragraphs: list[Paragraph],
    match: TextMatch,
) -> Paragraph | None:
    """Find which paragraph contains the given TextMatch.

    Args:
        paragraphs: List of detected paragraphs.
        match: TextMatch to locate.

    Returns:
        The containing Paragraph, or None if not found.
    """
    match_ops = set(match.operator_refs)
    for para in paragraphs:
        if match_ops & set(para.operator_indices):
            return para
    return None


# ── Public API: line breaking ─────────────────────────────────────────


def break_into_lines(
    text: str,
    paragraph_width: float,
    font_resolver: FontResolver,
    font_ref: pikepdf.Object,
    font_size: float,
    horizontal_scaling: float = 1.0,
    char_spacing: float = 0.0,
    word_spacing: float = 0.0,
) -> list[str]:
    """Break text into lines that fit within paragraph_width.

    Uses greedy word-wrapping with glyph-width-aware measurement.

    Args:
        text: Text to break into lines.
        paragraph_width: Available width in page-space units.
        font_resolver: Font resolver for encoding characters.
        font_ref: Raw font reference from page Resources (NOT a copy).
        font_size: Font size in points.
        horizontal_scaling: Horizontal scaling factor (default 1.0).
        char_spacing: Extra spacing per character (default 0.0).
        word_spacing: Extra spacing per space (default 0.0).

    Returns:
        List of line strings.
    """
    widths = _load_widths_from_ref(font_ref)

    space_w = _get_space_width(
        font_resolver,
        widths,
        font_size,
        horizontal_scaling,
        word_spacing,
    )

    # Split on hard newlines first
    segments = text.split("\n")

    all_lines: list[str] = []
    for segment in segments:
        words = segment.split(" ")
        words = [w for w in words if w]

        if not words:
            all_lines.append("")
            continue

        current_line_words: list[str] = []
        current_width = 0.0

        for word in words:
            word_w = _measure_word(
                word,
                font_resolver,
                widths,
                font_size,
                horizontal_scaling,
                char_spacing,
            )

            if not current_line_words:
                # First word on line — always add
                current_line_words.append(word)
                current_width = word_w
            elif current_width + space_w + word_w <= paragraph_width:
                # Fits on current line
                current_line_words.append(word)
                current_width += space_w + word_w
            else:
                # Start new line
                all_lines.append(" ".join(current_line_words))
                current_line_words = [word]
                current_width = word_w

        if current_line_words:
            all_lines.append(" ".join(current_line_words))

    return all_lines if all_lines else [""]


# ── Content stream operator helpers ───────────────────────────────────


def _find_bt_et_blocks(
    ops: _Ops,
) -> list[tuple[int, int, list[int]]]:
    """Find all BT/ET blocks and their text-showing operators.

    Args:
        ops: Parsed content stream operators.

    Returns:
        List of (bt_index, et_index, [text_op_indices]) tuples.
    """
    blocks: list[tuple[int, int, list[int]]] = []
    bt_idx: int | None = None
    text_ops: list[int] = []

    for i, inst in enumerate(ops):
        op_str = str(inst.operator) if hasattr(inst, "operator") else str(inst[1])
        if op_str == "BT":
            bt_idx = i
            text_ops = []
        elif op_str == "ET" and bt_idx is not None:
            blocks.append((bt_idx, i, text_ops))
            bt_idx = None
            text_ops = []
        elif op_str in _TEXT_OPS and bt_idx is not None:
            text_ops.append(i)

    return blocks


def _expand_to_bt_et(
    paragraph_indices: list[int],
    blocks: list[tuple[int, int, list[int]]],
) -> list[int]:
    """Expand paragraph operator indices to include enclosing BT/ET blocks.

    Applies BT/ET safety rule: only include the full block if ALL text
    operators within it belong to the paragraph. Otherwise, include only
    the paragraph's specific text operators.

    Args:
        paragraph_indices: Operator indices of the paragraph's text elements.
        blocks: BT/ET block info from _find_bt_et_blocks.

    Returns:
        Sorted list of all operator indices to remove.
    """
    para_set = set(paragraph_indices)
    removal: set[int] = set()

    for bt_idx, et_idx, text_ops in blocks:
        text_in_para = [t for t in text_ops if t in para_set]
        if not text_in_para:
            continue

        if len(text_in_para) == len(text_ops):
            # All text ops in this block belong to paragraph — claim whole block
            for i in range(bt_idx, et_idx + 1):
                removal.add(i)
        else:
            # Partial — only remove our text ops from this block
            removal.update(text_in_para)

    # Include any paragraph text ops that weren't in any BT/ET block
    removal.update(para_set)

    return sorted(removal)


def _encode_line_as_tj(
    line: str,
    resolver: FontResolver,
    width_cache: GlyphWidthCache | None,
    page: pikepdf.Page | None,
    font_name: str,
    font_size: float,
) -> tuple[list[Any], Any]:
    """Encode a line of text into a content stream text operator.

    For CIDFonts: produces a TJ operator with per-glyph String items,
    matching how surgeon.py constructs replacement operators.  This
    ensures PDF viewers advance each glyph individually using the
    font's /W table rather than interpreting one long byte string.

    For simple fonts: produces a flat Tj operator (single String).

    Args:
        line: Text for this line.
        resolver: FontResolver for encoding.
        width_cache: GlyphWidthCache for per-glyph width lookups (CID only).
        page: PDF page for width lookup (CID only).
        font_name: Font resource name.
        font_size: Font size in points.

    Returns:
        Tuple of (operands, operator) for a Tj or TJ instruction.
    """
    encoded = resolver.encode(line)

    if not resolver.is_cid_font:
        return ([pikepdf.String(encoded)], pikepdf.Operator("Tj"))

    # CIDFont: split into per-glyph 2-byte items for a TJ array
    bw = resolver.byte_width
    tj_items: list[object] = []
    for i in range(0, len(encoded), bw):
        glyph_bytes = encoded[i : i + bw]
        tj_items.append(pikepdf.String(glyph_bytes))

    return ([pikepdf.Array(tj_items)], pikepdf.Operator("TJ"))


def _build_replacement_ops(
    lines: list[str],
    font_name: str,
    font_size: float,
    fill_color: tuple[float, ...] | None,
    left_margin: float,
    first_line_y: float,
    line_height: float,
    resolver: FontResolver,
    page: pikepdf.Page | None = None,
) -> list[tuple[list[Any], Any]]:
    """Build replacement content stream operators for a reflowed paragraph.

    Constructs a single BT/ET block with Tf, color, and positioning/text
    per line.  For CIDFonts, uses Tm (text matrix) for first-line
    positioning and TJ arrays with per-glyph strings — matching the
    operator structure produced by surgeon.py and expected by PDF viewers.

    Args:
        lines: Text broken into lines by break_into_lines.
        font_name: Font resource name (e.g., 'F1').
        font_size: Font size in points.
        fill_color: Fill color tuple (grayscale/RGB/CMYK) or None.
        left_margin: X-position for the left edge.
        first_line_y: Y-position for the first line.
        line_height: Vertical distance between lines.
        resolver: FontResolver for encoding text.
        page: PDF page object (needed for CIDFont width lookups).

    Returns:
        List of (operands, operator) tuples for the replacement block.
    """
    width_cache: GlyphWidthCache | None = None
    if resolver.is_cid_font and page is not None:
        width_cache = GlyphWidthCache()

    new_ops: list[tuple[list[Any], Any]] = []

    # BT
    new_ops.append(([], pikepdf.Operator("BT")))

    # Tf — set font
    font_name_ref = pikepdf.Name("/" + font_name)
    new_ops.append(([font_name_ref, font_size], pikepdf.Operator("Tf")))

    # Color — set fill color
    if fill_color is not None:
        color_operands = [float(c) for c in fill_color]
        if len(fill_color) == 1:
            new_ops.append((color_operands, pikepdf.Operator("g")))
        elif len(fill_color) == 3:
            new_ops.append((color_operands, pikepdf.Operator("rg")))
        elif len(fill_color) == 4:
            new_ops.append((color_operands, pikepdf.Operator("k")))

    # First line positioning — use Tm for CID fonts (matches original
    # document structure), Td for simple fonts (backward compatible).
    if resolver.is_cid_font:
        new_ops.append(
            ([1, 0, 0, 1, left_margin, first_line_y], pikepdf.Operator("Tm")),
        )
    else:
        new_ops.append(
            ([left_margin, first_line_y], pikepdf.Operator("Td")),
        )

    # First line text
    new_ops.append(
        _encode_line_as_tj(line=lines[0], resolver=resolver,
                           width_cache=width_cache, page=page,
                           font_name=font_name, font_size=font_size),
    )

    # Subsequent lines
    for line in lines[1:]:
        new_ops.append(
            ([0.0, -line_height], pikepdf.Operator("Td")),
        )
        new_ops.append(
            _encode_line_as_tj(line=line, resolver=resolver,
                               width_cache=width_cache, page=page,
                               font_name=font_name, font_size=font_size),
        )

    # ET
    new_ops.append(([], pikepdf.Operator("ET")))

    return new_ops


# ── Public API: reflow ────────────────────────────────────────────────


def reflow_paragraph(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    paragraph: Paragraph,
    match: TextMatch,
    new_text: str,
    font_resolver: FontResolver,
    font_ref: pikepdf.Object,
) -> EditResult:
    """Replace matched text within a paragraph, reflow, and rewrite operators.

    Orchestrates: text substitution → line breaking → operator construction →
    content stream splice. Operates on the open Pdf object (caller saves).

    Args:
        pdf: The open PDF document.
        page: The page containing the paragraph.
        paragraph: Detected paragraph containing the match.
        match: TextMatch identifying text to replace.
        new_text: Replacement text.
        font_resolver: FontResolver for the paragraph's font.
        font_ref: Raw font reference from page Resources.

    Returns:
        EditResult with fidelity report (reflow_applied=True).
    """
    from typing import Literal as Lit

    # 1. Substitute text in paragraph, then join lines for proper reflow.
    # The \n in full_text are artifacts of element grouping, not hard breaks.
    new_para_text = paragraph.full_text.replace(match.matched_text, new_text, 1)
    new_para_text = new_para_text.replace("\n", " ")

    # 2. Break into lines (re-wraps the continuous text to paragraph width)
    lines = break_into_lines(
        new_para_text,
        paragraph.paragraph_width,
        font_resolver,
        font_ref,
        paragraph.font_size,
    )

    # 3. Check encoding on the actual line content, extend font if needed
    all_line_text = " ".join(lines)
    font_action: Lit["kept", "extended", "substituted", "failed"] = "kept"
    can_enc, missing = font_resolver.can_encode(all_line_text)

    if not can_enc:
        try:
            from pdf_edit_engine.fonts import extend_subset

            font_name = paragraph.font_name
            extend_subset(pdf, page, font_name, "".join(missing))
            # Refresh resolver after extension
            cache = FontResolverCache()
            font_resolver = cache.get_resolver(page, font_name)
            can_enc_after, still_missing = font_resolver.can_encode(all_line_text)
            if not can_enc_after:
                return EditResult(
                    success=False,
                    original_text=match.matched_text,
                    new_text=new_text,
                    font_action="failed",
                    fidelity_report=FidelityReport(
                        font_preserved=True,
                        font_substituted=None,
                        overflow_detected=False,
                        reflow_applied=True,
                        glyphs_missing=still_missing,
                    ),
                )
            font_action = "extended"
            logger.info(
                "Font extension succeeded for %d missing chars during reflow",
                len(missing),
            )
        except (FontNotFoundError, EncodingError, OSError):
            logger.warning("Font extension failed during reflow", exc_info=True)
            return EditResult(
                success=False,
                original_text=match.matched_text,
                new_text=new_text,
                font_action="failed",
                fidelity_report=FidelityReport(
                    font_preserved=True,
                    font_substituted=None,
                    overflow_detected=False,
                    reflow_applied=True,
                    glyphs_missing=missing,
                ),
            )

    # 4. Parse content stream
    ops = list(pikepdf.parse_content_stream(page))

    # 5. Find BT/ET blocks and expand operator indices
    blocks = _find_bt_et_blocks(ops)
    removal_indices = _expand_to_bt_et(paragraph.operator_indices, blocks)

    # 6. Get fill color from paragraph's first element
    fill_color = paragraph.elements[0].graphics_state.fill_color

    # 7. Build replacement operators
    replacement = _build_replacement_ops(
        lines=lines,
        font_name=paragraph.font_name,
        font_size=paragraph.font_size,
        fill_color=fill_color,
        left_margin=paragraph.left_margin,
        first_line_y=paragraph.first_line_y,
        line_height=paragraph.line_height,
        resolver=font_resolver,
        page=page,
    )

    # 8. Splice: remove old operators, insert replacement
    removal_set = set(removal_indices)
    insert_pos = min(removal_set)
    new_ops: _Ops = []
    inserted = False
    for i, op in enumerate(ops):
        if i == insert_pos and not inserted:
            new_ops.extend(replacement)
            inserted = True
        if i not in removal_set:
            new_ops.append(op)

    # 9. Write back content stream
    new_stream = pikepdf.unparse_content_stream(new_ops)
    page.Contents = pdf.make_stream(new_stream)

    # 10. Overflow detection
    overflow = len(lines) > paragraph.line_count

    # 11. Warnings
    warnings: list[str] = []
    fonts_in_para = {e.characters[0].font_name for e in paragraph.elements if e.characters}
    if len(fonts_in_para) > 1:
        warnings.append("Mixed-font paragraph: reflowed using single font")

    return EditResult(
        success=True,
        original_text=match.matched_text,
        new_text=new_text,
        font_action=font_action,
        warnings=warnings,
        fidelity_report=FidelityReport(
            font_preserved=True,
            font_substituted=None,
            overflow_detected=overflow,
            reflow_applied=True,
            glyphs_missing=[],
        ),
    )
