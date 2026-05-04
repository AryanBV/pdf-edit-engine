"""ReflowEngine module — paragraph detection, line breaking, and content stream rewriting."""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pikepdf
from fontTools.ttLib import TTLibError  # type: ignore[import-untyped]

from pdf_edit_engine._pathutil import open_pdf
from pdf_edit_engine.errors import EncodingError, FontNotFoundError, ReflowError
from pdf_edit_engine.models import (
    ContentElement,
    Degradation,
    EditResult,
    FidelityReport,
    Paragraph,
    TextMatch,
)
from pdf_edit_engine.widths import (
    DEFAULT_WIDTH,
    parse_cid_widths,
    parse_simple_widths,
)

if TYPE_CHECKING:
    from pdf_edit_engine.encoding import FontResolver, FontResolverCache

logger = logging.getLogger(__name__)

# Exception tuple for font-extension failures that should degrade to an
# EditResult failure instead of propagating. Kept as a single constant so
# all three call sites (reflow_paragraph, structural._extend_font,
# structural.insert_text_block) stay aligned — the prior asymmetry where
# reflow caught OSError but structural caught OperatorError was an
# inconsistency that let a deleted/permission-denied system font take
# down replace_block while degrading gracefully in reflow (ultrareview
# bug_002).
_FONT_EXTEND_FAIL_EXCS = (FontNotFoundError, EncodingError, OSError, TTLibError)

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
    #
    # Threshold history (ARY-277): the original value was
    # ``font_size * 0.25`` with a comment claiming "half-space
    # threshold". For a typical Western font a full space is ~250
    # font units of 1000-em = ~0.25 * font_size. So 0.25 * font_size
    # was actually one FULL space width, which allowed normal-width
    # glyph-side-bearing gaps (e.g. a comma's ~0.15 * font_size
    # offset from the preceding word) to exceed the threshold and
    # emit a phantom space token. When ``reflow_paragraph`` then
    # tokenised on spaces, the comma became its own token and could
    # be orphaned on the next line.
    #
    # Tightening to ``font_size * 0.125`` (≈ half of typical space
    # width) keeps word-boundary gaps above threshold (real spaces
    # in content streams render as ≥ space-width gaps between
    # elements) while keeping punctuation-adjacency gaps below it.
    space_width = font_size * 0.125
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


# ── v0.1.3 (Phase 3) S5 low-confidence paragraph signal (ARY-292 surfacing) ──
# Locked thresholds per docs/v0.1.3-implementation-design.md §2 and
# experiments/v013_detector_calibration/fpr_table.md "Locked decision: S5".
# FPR=0%, recall=64% on the labeled corpus (TP=7, FN=4, FP=0).
S1_MIN: float = 0.50  # paragraph_width / page_width threshold
S2_MAX: float = 0.55  # avg row stub coverage (above = natural flow)
S3_MIN: int = 2  # x-cluster count of element starts
Y_BUCKET: float = 4.0  # pt — line clustering granularity
X_TOL: float = 8.0  # pt — x-cluster tolerance


def _low_confidence_diagnostics(
    paragraph: Paragraph, page_width: float
) -> tuple[float, float, int]:
    """Compute S1, S2, S3 for the S5 low-confidence signal.

    Pure function; no PDF state mutation. Returns the three signal
    components so callers can populate Degradation.detail with the
    actual measurements (e.g. "width=0.62,cov=0.41,cols=3").
    """
    if page_width <= 0 or paragraph.paragraph_width <= 0:
        return 0.0, 1.0, 0

    s1 = paragraph.paragraph_width / page_width

    # S2: avg over y-buckets of (sum of element-line-widths / paragraph_width).
    lines: dict[int, float] = {}
    for e in paragraph.elements:
        y_bucket = round(e.bbox[1] / Y_BUCKET) * int(Y_BUCKET)
        line_w = e.bbox[2] - e.bbox[0]
        lines[y_bucket] = lines.get(y_bucket, 0.0) + line_w
    s2 = sum(w / paragraph.paragraph_width for w in lines.values()) / len(lines) if lines else 1.0

    # S3: distinct x-clusters of element x-starts within tol.
    xs = sorted(e.bbox[0] for e in paragraph.elements)
    if not xs:
        return s1, s2, 0
    s3 = 1
    last = xs[0]
    for x in xs[1:]:
        if x - last > X_TOL:
            s3 += 1
        last = x

    return s1, s2, s3


def is_low_confidence_paragraph(paragraph: Paragraph, page_width: float) -> bool:
    """S5 low-confidence signal per design doc §2 (locked).

    Returns True when the paragraph likely represents a misgrouped
    table-cell cluster: wide enough to span columns (S1), with
    significant white-space gaps per line (S2), AND multiple distinct
    x-start clusters indicating column boundaries (S3). All three must
    hold. False-positive rate on labeled corpus: 0% (FP=0/246).
    """
    s1, s2, s3 = _low_confidence_diagnostics(paragraph, page_width)
    return s1 >= S1_MIN and s2 < S2_MAX and s3 >= S3_MIN


def _detect_paragraphs_from_index(
    elements: list[ContentElement],
) -> list[Paragraph]:
    """Detect paragraphs from a pre-built content element index.

    Args:
        elements: Full content element index for a page.

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
    pdf = open_pdf(resolved)
    try:
        if page >= len(pdf.pages):
            msg = f"Page {page} out of range (PDF has {len(pdf.pages)} pages)"
            raise ReflowError(msg)

        page_obj = pdf.pages[page]
        elements = _build_index(page_obj, page, resolved)
        return _detect_paragraphs_from_index(elements)
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
            elif word.strip() and all(not c.isalnum() for c in word):
                # Punctuation-only word (em-dash "—", etc.) — keep with
                # previous line to avoid orphaning a lone dash on a new line.
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
) -> tuple[list[Any], Any]:
    """Encode a line of text into a content stream text operator.

    For CIDFonts: produces a TJ operator with per-glyph String items,
    matching how surgeon.py constructs replacement operators. This
    ensures PDF viewers advance each glyph individually using the
    font's /W table rather than interpreting one long byte string.

    For simple fonts: produces a flat Tj operator (single String).

    Args:
        line: Text for this line.
        resolver: FontResolver for encoding.

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
    *,
    style_palette: Any | None = None,
    extra_resolvers: dict[str, FontResolver] | None = None,
) -> list[tuple[list[Any], Any]]:
    """Build replacement content stream operators for a reflowed paragraph.

    Constructs a single BT/ET block with Tf, color, and positioning/text
    per line.  For CIDFonts, uses Tm (text matrix) for first-line
    positioning and TJ arrays with per-glyph strings.

    When *style_palette* is provided, per-line style preservation is applied:

    - The first replacement line uses the heading font (if one was detected
      in the original content) — typically a bold variant.
    - Lines starting with a character in ``style_palette.marker_fonts``
      render that character in its original font, then switch to the body
      font for the rest of the line.
    - All other lines use the body font.

    This is universal: the palette is built from whatever fonts the original
    content used, with no document-type assumptions.

    Args:
        lines: Text broken into lines by break_into_lines.
        font_name: Font resource name for body text (e.g., 'F3').
        font_size: Font size in points.
        fill_color: Fill color tuple (grayscale/RGB/CMYK) or None.
        left_margin: X-position for the left edge.
        first_line_y: Y-position for the first line.
        line_height: Vertical distance between lines.
        resolver: FontResolver for body text encoding.
        page: PDF page object (needed for CIDFont width lookups).
        style_palette: Optional _StylePalette with heading/marker fonts.
        extra_resolvers: ``{font_name: FontResolver}`` for non-body fonts.

    Returns:
        List of (operands, operator) tuples for the replacement block.
    """
    extra = extra_resolvers or {}
    body_font_ref = pikepdf.Name("/" + font_name)
    new_ops: list[tuple[list[Any], Any]] = []

    # Extract palette fields (avoid importing _StylePalette in reflow.py)
    heading_font: str | None = None
    marker_fonts: dict[str, str] = {}
    if style_palette is not None:
        heading_font = getattr(style_palette, "heading_font", None)
        marker_fonts = getattr(style_palette, "marker_fonts", {}) or {}

    # ── BT ────────────────────────────────────────────────────────
    new_ops.append(([], pikepdf.Operator("BT")))

    # Color
    if fill_color is not None:
        color_operands = [float(c) for c in fill_color]
        if len(fill_color) == 1:
            new_ops.append((color_operands, pikepdf.Operator("g")))
        elif len(fill_color) == 3:
            new_ops.append((color_operands, pikepdf.Operator("rg")))
        elif len(fill_color) == 4:
            new_ops.append((color_operands, pikepdf.Operator("k")))

    # Decide first-line font: heading if available and line doesn't start
    # with a marker character (markers get their own font handling).
    first_char = lines[0].lstrip()[:1] if lines else ""
    use_heading = (
        heading_font is not None and heading_font in extra and first_char not in marker_fonts
    )

    current_font: str
    current_resolver: FontResolver
    if use_heading and heading_font is not None:
        current_font = heading_font
        current_resolver = extra[heading_font]
    else:
        current_font = font_name
        current_resolver = resolver
    new_ops.append(
        ([pikepdf.Name("/" + current_font), font_size], pikepdf.Operator("Tf")),
    )

    # ── Positioning ───────────────────────────────────────────────
    if resolver.is_cid_font:
        new_ops.append(
            ([1, 0, 0, 1, left_margin, first_line_y], pikepdf.Operator("Tm")),
        )
    else:
        new_ops.append(
            ([left_margin, first_line_y], pikepdf.Operator("Td")),
        )

    # ── Encode first line ─────────────────────────────────────────
    # If the heading font can't encode the text, fall back to body font.
    if use_heading:
        can_enc, _ = current_resolver.can_encode(lines[0])
        if not can_enc:
            # Graceful degradation: render in body font instead
            current_font = font_name
            current_resolver = resolver
            new_ops[-2] = ([body_font_ref, font_size], pikepdf.Operator("Tf"))

    new_ops.append(
        _encode_line_as_tj(
            line=lines[0],
            resolver=current_resolver,
        ),
    )

    # Switch back to body if we used heading
    if current_font != font_name:
        current_font = font_name
        current_resolver = resolver
        new_ops.append(([body_font_ref, font_size], pikepdf.Operator("Tf")))

    # ── Subsequent lines ──────────────────────────────────────────
    # Track whether we're inside a bullet section for continuation line
    # indentation.  A bullet section starts when a marker character is
    # found and continues until the next marker or a non-indented segment.
    in_bullet_section = False

    # Extract indent positions from palette (constant across lines)
    pal_marker_x = getattr(style_palette, "marker_x", 0) if style_palette else 0
    pal_body_x = getattr(style_palette, "body_after_marker_x", 0) if style_palette else 0

    for line_idx, line in enumerate(lines[1:], start=1):
        new_ops.append(
            ([0.0, -line_height], pikepdf.Operator("Td")),
        )

        stripped = line.lstrip()
        marker_char = stripped[:1] if stripped else ""
        marker_font = marker_fonts.get(marker_char)
        marker_resolver = extra.get(marker_font) if marker_font else None

        if marker_font and marker_resolver and marker_resolver.can_encode(marker_char)[0]:
            # ── Indented marker line ──────────────────────────────
            in_bullet_section = True
            # Position marker at marker_x, body text at body_after_marker_x
            if pal_marker_x > 0:
                # Use absolute Tm for marker position
                current_y = first_line_y - line_idx * line_height
                new_ops.append(
                    ([1, 0, 0, 1, pal_marker_x, current_y], pikepdf.Operator("Tm")),
                )
            if current_font != marker_font:
                new_ops.append(
                    ([pikepdf.Name("/" + marker_font), font_size], pikepdf.Operator("Tf")),
                )
            new_ops.append(
                _encode_line_as_tj(
                    line=marker_char,
                    resolver=marker_resolver,
                ),
            )
            # Position body text after marker
            if marker_font != font_name:
                new_ops.append(
                    ([body_font_ref, font_size], pikepdf.Operator("Tf")),
                )
            if pal_body_x > 0:
                current_y = first_line_y - line_idx * line_height
                new_ops.append(
                    ([1, 0, 0, 1, pal_body_x, current_y], pikepdf.Operator("Tm")),
                )
            rest = stripped[1:]
            if rest:
                new_ops.append(
                    _encode_line_as_tj(
                        line=rest,
                        resolver=resolver,
                    ),
                )
            current_font = font_name
            current_resolver = resolver
        elif in_bullet_section and pal_body_x > 0:
            # ── Continuation of a bullet line ─────────────────────
            # Indent at body_after_marker_x to create hanging indent.
            if current_font != font_name:
                new_ops.append(
                    ([body_font_ref, font_size], pikepdf.Operator("Tf")),
                )
                current_font = font_name
                current_resolver = resolver
            current_y = first_line_y - line_idx * line_height
            new_ops.append(
                ([1, 0, 0, 1, pal_body_x, current_y], pikepdf.Operator("Tm")),
            )
            new_ops.append(
                _encode_line_as_tj(
                    line=line.lstrip(),
                    resolver=resolver,
                ),
            )
        else:
            # ── Regular body line ─────────────────────────────────
            in_bullet_section = False
            if current_font != font_name:
                new_ops.append(
                    ([body_font_ref, font_size], pikepdf.Operator("Tf")),
                )
                current_font = font_name
                current_resolver = resolver
            new_ops.append(
                _encode_line_as_tj(
                    line=line,
                    resolver=current_resolver,
                ),
            )

    # ── ET ────────────────────────────────────────────────────────
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
    resolver_cache: FontResolverCache | None = None,
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
        resolver_cache: Caller-owned cache. After font extension we evict and
            re-fetch through this cache so the caller sees the updated
            resolver state on subsequent matches (ARY-283). When ``None``
            (the backward-compatible default for pre-0.1.2 external
            callers), a fresh per-call cache is constructed internally —
            the per-call ownership invariant still holds, it just isn't
            visible to the caller.

    Returns:
        EditResult with fidelity report (reflow_applied=True).
    """
    # Preserve the 0.1.1 public signature. When the caller does not pass a
    # cache, construct one internally — per-call ownership is maintained.
    from pdf_edit_engine.encoding import FontResolverCache as _FontResolverCache

    if resolver_cache is None:
        resolver_cache = _FontResolverCache()

    # INV-B-3 contract: reflow_paragraph is a public API entry that
    # consumes a TextMatch. Refuse stale matches so the caller cannot
    # silently reflow with operator_refs that no longer point at
    # match.matched_text. The single-helper-per-entry pattern in
    # surgeon.py is replicated here for defense in depth.
    from pdf_edit_engine.surgeon import _assert_match_addressable

    _ops_for_validation = list(pikepdf.parse_content_stream(page))
    _assert_match_addressable(_ops_for_validation, match, font_resolver)

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
    font_action: Literal["kept", "extended", "substituted", "failed"] = "kept"
    can_enc, missing = font_resolver.can_encode(all_line_text)
    # INV-C-4 plumbing: capture metric-equivalent substitution events.
    substitution_log: list[str] = []

    if not can_enc:
        try:
            from pdf_edit_engine.fonts import extend_subset

            font_name = paragraph.font_name
            extend_subset(
                pdf,
                page,
                font_name,
                "".join(missing),
                substitution_log=substitution_log,
            )
            # Refresh resolver through the caller's cache: evict the stale
            # entry so the next caller (and our re-fetch below) sees the
            # post-extension font state. See ARY-283.
            resolver_cache.evict(page, font_name)
            font_resolver = resolver_cache.get_resolver(page, font_name)
            can_enc_after, still_missing = font_resolver.can_encode(all_line_text)
            if not can_enc_after:
                return EditResult(
                    success=False,
                    original_text=match.matched_text,
                    new_text=new_text,
                    font_action="failed",
                    fidelity_report=FidelityReport(
                        font_substituted=None,
                        overflow_detected=False,
                        reflow_applied=True,
                        glyphs_missing=still_missing,
                        degradations=[
                            Degradation(
                                kind="font_extension_failed",
                                detail="partial_fail",
                                severity="error",
                            ),
                        ],
                    ),
                )
            font_action = "extended"
            logger.info(
                "Font extension succeeded for %d missing chars during reflow",
                len(missing),
            )
        except _FONT_EXTEND_FAIL_EXCS as exc:
            logger.warning("Font extension failed during reflow", exc_info=True)
            return EditResult(
                success=False,
                original_text=match.matched_text,
                new_text=new_text,
                font_action="failed",
                fidelity_report=FidelityReport(
                    font_substituted=None,
                    overflow_detected=False,
                    reflow_applied=True,
                    glyphs_missing=missing,
                    degradations=[
                        Degradation(
                            kind="font_extension_failed",
                            detail=type(exc).__name__,
                            severity="error",
                        ),
                    ],
                ),
            )

    # 4. Parse content stream
    ops = list(pikepdf.parse_content_stream(page))

    # 5. Find BT/ET blocks and expand operator indices
    blocks = _find_bt_et_blocks(ops)
    removal_indices = _expand_to_bt_et(paragraph.operator_indices, blocks)

    # 6. Get fill color from paragraph's first element
    fill_color = paragraph.elements[0].graphics_state.fill_color

    # 7. Overflow shift — when the replacement produces more lines than
    # the original paragraph occupied, shift content below the paragraph
    # down to make room. Without this, the extra lines land on top of
    # whatever was already there and the visible output garbles (the
    # actual ARY-277 symptom: "Konstantinidis" ending up mid-paragraph
    # of unrelated text because its second reflow line overlapped
    # a different paragraph's y-band).
    extra_lines = len(lines) - paragraph.line_count
    overflow = extra_lines > 0
    shift_warnings: list[str] = []
    if overflow:
        # Import locally to avoid cross-module boundary noise at import
        # time. structural owns the shift primitive; we borrow it.
        from pdf_edit_engine.locator import _build_index as _reflow_build_index
        from pdf_edit_engine.structural import _shift_content_below_inplace

        requested_shift = extra_lines * paragraph.line_height
        shift_amount = requested_shift
        # y_threshold is the bottom edge of the paragraph; everything
        # below that is shifted.
        paragraph_bottom_y = (
            paragraph.first_line_y
            - (paragraph.line_count - 1) * paragraph.line_height
            - (paragraph.font_size * 0.25)
        )
        # Page-bottom clamp — mirrors structural._replace_block_on_page so
        # an overflow that would push content below MediaBox[1] is either
        # clamped (content sits at the page edge) or suppressed
        # (no room available). Either way the user gets a warning
        # instead of silently-lost content (ultrareview merged_bug_003).
        mediabox = page.get("/MediaBox")
        if mediabox is not None:
            page_bottom = float(mediabox[1])
            elements_below = [
                e
                for e in _reflow_build_index(page, paragraph.elements[0].page)
                if e.bbox[1] < paragraph_bottom_y
            ]
            if elements_below:
                lowest_y = min(e.bbox[1] for e in elements_below)
                max_safe_shift = lowest_y - page_bottom
                if max_safe_shift <= 0:
                    shift_warnings.append(
                        f"Overflow shift suppressed — no room below paragraph "
                        f"(wanted {requested_shift:.1f}pt, page has 0pt available)",
                    )
                    shift_amount = 0.0
                elif requested_shift > max_safe_shift:
                    shift_warnings.append(
                        f"Overflow shift clamped from {requested_shift:.1f}pt "
                        f"to {max_safe_shift:.1f}pt to keep content on-page",
                    )
                    shift_amount = max_safe_shift
            else:
                # Nothing below the paragraph to shift — no collision risk.
                shift_amount = 0.0

        if shift_amount > 0:
            shift_warnings.extend(
                _shift_content_below_inplace(
                    pdf,
                    page,
                    paragraph.elements[0].page,
                    paragraph_bottom_y,
                    shift_amount,
                )
            )
            # Re-parse ops after the shift mutated page.Contents. The shift
            # modifies operand values, not operator counts, so our
            # paragraph.operator_indices and removal_indices (derived from
            # the pre-shift build_index) stay valid.
            ops = list(pikepdf.parse_content_stream(page))

    # 8. Build replacement operators
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

    # 9. Splice: remove old operators, insert replacement
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

    # 10. Write back content stream
    new_stream = pikepdf.unparse_content_stream(new_ops)
    page.Contents = pdf.make_stream(new_stream)

    # 11. Warnings — propagate shift warnings from step 7 so callers see
    # page-boundary overflows and clamps instead of silently-lost content.
    warnings: list[str] = list(shift_warnings)
    fonts_in_para = {e.characters[0].font_name for e in paragraph.elements if e.characters}
    if len(fonts_in_para) > 1:
        warnings.append("Mixed-font paragraph: reflowed using single font")

    # v0.1.3 Phase 3: post-pass S5 low-confidence detector signal (ARY-292).
    # The detector grouping is unchanged in v0.1.3 — we only surface what's
    # misgrouped. Algorithm fix is v0.1.4 work.
    page_width = float(page.MediaBox[2]) - float(page.MediaBox[0]) if page.MediaBox else 612.0
    detector_degradations: list[Degradation] = []
    if is_low_confidence_paragraph(paragraph, page_width):
        s1, s2, s3 = _low_confidence_diagnostics(paragraph, page_width)
        detector_degradations.append(
            Degradation(
                kind="paragraph_detection_low_confidence",
                detail=f"width={s1:.2f},cov={s2:.2f},cols={s3}",
                severity="info",
            )
        )

    return EditResult(
        success=True,
        original_text=match.matched_text,
        new_text=new_text,
        font_action=font_action,
        warnings=warnings,
        fidelity_report=FidelityReport(
            # INV-C-4: surface metric-equivalent if any was used.
            font_substituted=substitution_log[0] if substitution_log else None,
            overflow_detected=overflow,
            reflow_applied=True,
            glyphs_missing=[],
            degradations=detector_degradations,
        ),
    )
