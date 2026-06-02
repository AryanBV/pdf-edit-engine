"""ReflowEngine module — paragraph detection, line breaking, and content stream rewriting."""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pikepdf

from pdf_edit_engine._pathutil import (
    _parse_content_stream,
    _unparse_content_stream,
    open_pdf,
)
from pdf_edit_engine.errors import FontStreamTooLargeError, ReflowError
from pdf_edit_engine.fonts import _FONT_EXTEND_FAIL_EXCS
from pdf_edit_engine.linebreak import (
    break_opportunities,
    grouping_boundary_separator,
    has_break_opportunity,
    is_latin_simple,
    is_space_delimited_script,
    recorded_separators,
    segment_by_opportunities,
)
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

# Parsed content stream ops — using Any mirrors surgeon.py convention.
_Ops = list[Any]

_TEXT_OPS = frozenset({"Tj", "TJ", "'", '"'})

# E.2 (v0.2.0): indent-classification noise floor, as a fraction of font size.
# At 11pt this is 6.6pt — below a typical paragraph indent (~36pt) and above
# sub-pixel positioning jitter. Governs the x-indent axis only; does not
# collide with the font_size*0.5 y-clustering threshold in
# _group_elements_into_lines (different axis).
MIN_INDENT_FACTOR: float = 0.6

# Bullet/list-item markers — matches lines starting with •, -, *, or numbered lists (1., 2), etc.)
_BULLET_RE = re.compile(r"^\s*([•\-\*]|\d+[.\)])\s")


def _normalize_color_operand(operand: Any) -> tuple[str, Any]:
    """Normalize one fill-color operand for value-based (not string) keying.

    pikepdf does NOT normalize numeric literals, so ``1 0 0 rg`` and
    ``1.0 0.0 0.0 rg`` stringify differently (``'1'`` vs ``'1.0'``) even though
    they render the identical color. Keying color operands by ``str()`` therefore
    splits a single-color paragraph written at mixed precision into multiple
    "distinct" colors and falsely emits ``color_space_approximated``.

    A numeric operand keys as ``("n", round(float(o), 6))`` so ``1 == 1.0 == 1.00``;
    a non-numeric operand (e.g. a ``pikepdf.Name`` like ``/CS0``) keys as
    ``("s", str(o))`` so it stays distinct from a number and from other names.
    The leading tag keeps the two domains from colliding.
    """
    try:
        return ("n", round(float(operand), 6))
    except (TypeError, ValueError):
        return ("s", str(operand))


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


def _median(values: list[float]) -> float:
    """Return the median of a non-empty list of floats.

    Inline to avoid importing ``statistics`` (dep-boundary hygiene — reflow's
    third-party surface is pikepdf + fonttools only).
    """
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _detect_indent_style(
    x_starts: list[float],
    font_size: float,
) -> tuple[Literal["first_line", "hanging", "flush"], float, float]:
    """Classify a paragraph's per-line indent style from its source x-starts.

    Pure helper — reports the indent *style* and its magnitudes ONLY; it
    performs NO positioning math (the caller decides the anchor margin and the
    existing absolute-Tm / relative-Td primitive in ``_build_replacement_ops``
    does the emission). The classifier is DEFAULT-FLUSH-BIASED: on ANY
    ambiguity it returns ``("flush", 0.0, 0.0)`` and the caller surfaces a
    typed ``indent_flattened`` Degradation. A genuinely flush multi-line
    paragraph (all x_starts equal within the noise floor) ALSO returns
    ``("flush", 0.0, 0.0)`` — but the caller distinguishes that CLEAN flush
    (no degradation) from the DEGRADED flush via the same predicates used
    here (see ``_is_degraded_flush``).

    ``MIN_INDENT = font_size * 0.6`` (6.6pt at 11pt) is the noise floor: well
    below the typical paragraph indent (e.g. 36pt) and well above sub-pixel
    positioning jitter. It governs the x-indent axis only and does not collide
    with the ``font_size * 0.5`` y-clustering threshold in
    ``_group_elements_into_lines`` (different axis).

    Args:
        x_starts: Per-line source x-start positions (line 0 first).
        font_size: Body font size, used to scale the noise floor.

    Returns:
        ``(style, first_line_indent, hanging_indent)``. For ``"first_line"``
        the first-line indent is the +delta of line 0 above the continuation
        margin; for ``"hanging"`` the hanging indent is the +delta the
        continuations sit to the right of line 0. For ``"flush"`` both are
        ``0.0``.
    """
    min_indent = font_size * MIN_INDENT_FACTOR

    # AMBIGUITY GUARD — single-line paragraphs carry no continuation block to
    # classify against, so no clean first-line/hanging shape can be inferred.
    if len(x_starts) < 2:
        return ("flush", 0.0, 0.0)

    line0 = x_starts[0]
    rest = x_starts[1:]

    # Continuations must be mutually consistent (all within MIN_INDENT of the
    # block min) for a clean first-line/hanging shape. A non-monotone /
    # multi-margin continuation block is ambiguous -> flush (degraded).
    rest_min = min(rest)
    if any(abs(x - rest_min) > min_indent for x in rest):
        return ("flush", 0.0, 0.0)

    rest_median = _median(rest)

    # FIRST_LINE: line 0 sits to the right of a flush continuation block.
    if line0 > rest_min + min_indent:
        return ("first_line", line0 - rest_min, 0.0)

    # HANGING: line 0 sits to the left of an indented continuation block.
    if line0 < rest_median - min_indent:
        return ("hanging", 0.0, rest_median - line0)

    # CLEAN flush — all lines equal within the noise floor.
    return ("flush", 0.0, 0.0)


def _is_degraded_flush(x_starts: list[float], font_size: float) -> bool:
    """True when ``_detect_indent_style`` returns flush for a GENUINE-but-unclassifiable indent.

    Distinguishes the DEGRADED flush — a MULTI-LINE paragraph whose continuation
    block carries a real indent signal that could not be confidently classified
    as first-line or hanging (non-monotone / mutually-inconsistent continuation
    x-starts) — from the CLEAN flush (all lines genuinely equal). Only the
    degraded case surfaces ``indent_flattened``; the clean case must NOT (false
    positive).

    A PLAIN SINGLE-LINE paragraph (``len(x_starts) < 2``) has NO multi-line
    indent structure to flatten — there is no continuation block whose shape was
    lost — so it is just FLUSH and returns ``False``. This is the common reflow
    case (a single line widening through re-wrap); emitting ``indent_flattened``
    there is info-channel noise / false honesty, so it is suppressed.

    Mirrors the guards in ``_detect_indent_style`` so the two stay in lockstep.
    """
    min_indent = font_size * MIN_INDENT_FACTOR
    # Single-line: no continuation block exists, so nothing could be flattened.
    if len(x_starts) < 2:
        return False
    rest = x_starts[1:]
    rest_min = min(rest)
    # Degraded iff the continuations are non-monotone / multi-margin (disagree by
    # more than the noise floor) — a real indent signal the classifier could not
    # resolve to first_line/hanging. With CONSISTENT continuations any supra-noise
    # line-0 delta is already resolved to first_line/hanging, so a flush there is
    # a CLEAN flush (line 0 within the noise floor of the continuation block).
    return any(abs(x - rest_min) > min_indent for x in rest)


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

    # E.2: classify the per-line indent style from the source x_starts. The
    # classifier reports style + magnitudes only; the caller anchors the
    # emission. For a HANGING indent the continuation block (which is the
    # indented body) forms the mode, so the mode margin is the WRONG anchor
    # for line 0 — store the true line-0 x so first_line_y/margin anchors the
    # actual line-0 origin. For first_line/flush the mode IS the continuation
    # margin, so keep it.
    indent_style, first_line_indent, hanging_indent = _detect_indent_style(x_starts, font_size)
    if indent_style == "hanging":
        left_margin = x_starts[0]

    # Right margin: rightmost extent of any element
    right_margin = max(e.bbox[2] for e in elements)

    # E.2 geometry safety (INV-G-7): wrap to the NARROWEST available width so
    # the widest-STARTING line still fits within the source right margin.
    # ``break_into_lines`` wraps every line to one ``paragraph_width``, but an
    # indented paragraph RENDERS its lines at different x-origins (first_line:
    # line 0 at ``left_margin + first_line_indent``; hanging: continuations at
    # ``left_margin + hanging_indent`` — note ``left_margin`` is line 0's x for
    # hanging). A line measured against the flush margin but rendered at an
    # indented x would overrun ``right_margin`` by up to the indent magnitude
    # (the vertical-overflow logic never sees horizontal overrun). Subtracting
    # the max indent from the available width makes every rendered line fit.
    # For FLUSH both indents are 0.0, so this is byte-identical to the pre-E.2
    # width and the flush path stays unchanged.
    available_width = right_margin - left_margin - max(first_line_indent, hanging_indent)
    paragraph_width = available_width
    if paragraph_width < 1.0:
        paragraph_width = 1.0

    # Line height precedence ladder (E.3 declared-leading rung first):
    #
    # 1. AUTHORITATIVE declared leading. When the source genuinely declared a
    #    line leading via TL/TD in its own q-scope (leading_authoritative), the
    #    document's intended line advance is that declared value — honour it even
    #    when reflow must SYNTHESIZE advances for newly-wrapped lines (e.g. a
    #    single source line re-wrapped onto several, where there is no measurable
    #    y-gap). This is the only case E.3 changes; every non-declaring paragraph
    #    (leading_authoritative False) skips this rung and is byte-identical.
    # 2. Measured average y-gap between consecutive source lines (>= 2 lines).
    # 3. font_size * 1.2 proxy (single line, no declared leading).
    declared_leading = elements[0].graphics_state.leading
    declared_authoritative = elements[0].graphics_state.leading_authoritative
    if declared_authoritative and declared_leading is not None and declared_leading > 0:
        line_height = declared_leading
    elif len(lines) > 1:
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
        indent_style=indent_style,
        first_line_indent=first_line_indent,
        hanging_indent=hanging_indent,
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


def _join_atoms(atoms: list[str], segment: str) -> str:
    """Re-join wrapped atoms with the BYTE-FAITHFUL RECORDED separator (E.7).

    The PURE-LATIN fast path is byte-identical to ``" ".join`` (INV-G-10 — it
    collapses runs of spaces to one); the NON-Latin-simple path (CJK / mixed
    Latin+CJK / Korean) re-emits the ACTUAL separator that was consumed at each
    gap in the ORIGINAL ``segment``, recovered by
    :func:`linebreak.recorded_separators`. A former-whitespace gap re-joins with
    a single space ``" "`` and a no-width ideographic gap (no whitespace in the
    original) re-joins with EMPTY ``""``. Crucially this distinguishes a real
    space between two ideographs (``"報告 売上"`` -> keep the space) from a
    genuinely-adjacent-ideograph gap (``"報告売上"`` -> empty) — class inference
    could not, because both are ID↔ID; and it preserves Korean word spacing
    (Hangul is ID-class but space-delimited).

    A pure-CJK segment has only no-whitespace gaps, so every separator is ``""``
    and the result is byte-identical to the prior ``"".join``; a mixed
    ``"report 報告 done"`` line keeps the spaces around ``報告``.

    Args:
        atoms: The atoms making up one wrapped line.
        segment: The source segment the atoms came from (selects the path and
            supplies the recorded-separator provenance).

    Returns:
        The joined line string.
    """
    if is_latin_simple(segment):
        return " ".join(atoms)
    if not atoms:
        return ""
    seps = recorded_separators(segment, atoms)
    parts: list[str] = [atoms[0]]
    for sep, cur in zip(seps, atoms[1:], strict=False):
        parts.append(sep)
        parts.append(cur)
    return "".join(parts)


def _join_line(entries: list[tuple[str, str]]) -> str:
    """Re-emit one wrapped line from ``(atom, recorded_separator_before)`` pairs.

    The companion to :func:`break_into_lines`'s wrap: each atom carries the
    BYTE-FAITHFUL separator recorded for the gap that precedes it WITHIN the
    segment (``""`` for the first atom on the line, since a line break consumed
    its leading separator). Emitting ``sep + atom`` therefore reproduces the
    original spacing verbatim — a recorded-space gap re-emits ``" "`` (preserving
    a real inter-ideograph or Korean word space) and a no-width gap re-emits
    ``""``. No class inference is involved; the separators came from the original
    text in :func:`break_into_lines`.

    Args:
        entries: ``(atom, separator_before)`` pairs for one line, in order.

    Returns:
        The joined line string.
    """
    return "".join(sep + atom for atom, sep in entries)


def _rejoin_newline_artifacts(text: str) -> str:
    """Replace element-grouping ``"\\n"`` artifacts with a per-boundary separator.

    The PURE-LATIN fast path is byte-identical to ``text.replace("\\n", " ")``
    (INV-G-10). For a non-Latin-simple paragraph each ``"\\n"`` is an
    element-GROUPING artifact (the locator inserted it between separately-grouped
    content elements; there is NO per-boundary original-text provenance to record),
    so it is resolved by :func:`linebreak.grouping_boundary_separator` using the
    PARAGRAPH-level space-delimited-ness as the decision input: if the combined
    paragraph text contains ANY ASCII space (a Latin / Korean / mixed /
    CJK-with-spaces paragraph) every ``"\\n"`` re-joins with a single space ``" "``
    (pre-E.7 parity — the load-bearing fix for the Korean cross-line regression,
    where a word space at a source-line boundary was silently deleted because
    Hangul is East-Asian-width W / ``"ID"`` class and char-level inference glued
    the ID|ID boundary). Only a PURE no-space ideograph paragraph (no space
    anywhere) re-joins a no-width ideographic boundary with EMPTY ``""`` (no
    spurious inter-line space — an improvement over the pre-E.7 blanket space). A
    bare leading/trailing ``"\\n"`` (no neighbour on one side) becomes a space.

    REAL spaces are unaffected — they live INSIDE a piece (not as ``"\\n"``) and
    flow untouched into :func:`break_into_lines`, where the recorded-separator
    model (:func:`linebreak.recorded_separators`) preserves them verbatim. So
    Korean word spaces and real inter-ideograph spaces in the inserted
    replacement text survive this step.

    RESIDUAL (documented in ``docs/decisions.md``): a CJK-with-internal-spaces
    paragraph whose source line broke EXACTLY at an ideograph-ideograph boundary
    gets a spurious ``" "`` at that ``"\\n"`` (same as pre-E.7, so NOT a
    regression); the perfect per-boundary fix needs the read-path provenance
    carved out to 0.3.0.

    Args:
        text: The paragraph text whose ``"\\n"`` grouping artifacts to re-join.

    Returns:
        The text with each ``"\\n"`` replaced by its per-boundary separator.
    """
    if "\n" not in text:
        return text
    if is_latin_simple(text):
        return text.replace("\n", " ")
    # Paragraph-level decision: a space anywhere in the combined source text — OR
    # any Hangul (Korean is a space-delimited script whose syllables are
    # East-Asian-width W, so a short fragment like "관리자" carries no literal
    # space yet a "\n" beside it is a word boundary) — means the run is
    # space-delimited (Latin / Korean / mixed / CJK-with-spaces), so a "\n"
    # grouping boundary is a likely word boundary -> " " (pre-E.7 parity). Only a
    # PURE Han/Kana run with no space stays empty-joined. The per-boundary "was a
    # space here" provenance was discarded at extraction (recovering it is a
    # read-path change carved out to 0.3.0).
    space_delimited = is_space_delimited_script(text)
    pieces = text.split("\n")
    out: list[str] = [pieces[0]]
    for left, right in zip(pieces, pieces[1:], strict=False):
        out.append(grouping_boundary_separator(left, right, space_delimited))
        out.append(right)
    return "".join(out)


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
        latin = is_latin_simple(segment)
        if latin:
            # LATIN PATH — byte-identical to pre-E.7 (INV-G-10 lock).
            words = segment.split(" ")
            words = [w for w in words if w]
            # Every gap in the Latin path is a former-whitespace gap.
            seps_before = ["" if i == 0 else " " for i in range(len(words))]
        else:
            # E.7 (v0.2.0): CJK / mixed-script path. Atomize at UAX#14 break
            # opportunities so spaceless ideographic runs wrap at ideograph
            # boundaries instead of overflowing the column.
            opps = break_opportunities(segment)
            words = [a for a in segment_by_opportunities(segment, opps) if a]
            # E.7 recorded-separator root fix: recover the ACTUAL separator
            # consumed at each gap from the ORIGINAL segment (NOT class
            # inference, which could not tell a real space between two
            # ideographs from a no-width ID↔ID break, and destroyed Korean word
            # spacing). Computed ONCE over the full atom list so a repeated atom
            # (e.g. "報告" appearing twice) cannot mis-resolve to the wrong gap.
            gap_seps = recorded_separators(segment, words)
            seps_before = ["", *gap_seps]

        if not words:
            all_lines.append("")
            continue

        # Each entry pairs an atom with the recorded separator that precedes it
        # within the segment (``""`` for the first atom). The wrap then charges
        # the recorded separator width and the per-line join re-emits the
        # recorded separator verbatim — predicate and render share one source.
        current_line: list[tuple[str, str]] = []
        current_width = 0.0

        for word, sep_before in zip(words, seps_before, strict=False):
            word_w = _measure_word(
                word,
                font_resolver,
                widths,
                font_size,
                horizontal_scaling,
                char_spacing,
            )

            # E.7 (v0.2.0): the per-atom budget adds the width of the separator
            # ACTUALLY rendered between this word and the previous one ON THE
            # SAME LINE — the recorded separator. A recorded-space gap charges
            # ``space_w``; a recorded-empty (no-width ideographic) gap charges
            # 0. The pure-Latin fast path has only space gaps, so ``sep_w ==
            # space_w`` always (byte-identical to pre-E.7); a pure-CJK segment
            # has only empty gaps, so ``sep_w == 0`` always (byte-identical to
            # the prior CJK budget). A mixed / Korean line charges ``space_w``
            # only at its genuine recorded word boundaries.
            sep_w = space_w if (current_line and sep_before == " ") else 0.0

            if not current_line:
                # First word on line — always add (no leading separator).
                current_line.append((word, ""))
                current_width = word_w
            elif current_width + sep_w + word_w <= paragraph_width:
                # Fits on current line
                current_line.append((word, sep_before))
                current_width += sep_w + word_w
            elif word.strip() and all(not c.isalnum() for c in word):
                # Punctuation-only word (em-dash "—", etc.) — keep with
                # previous line to avoid orphaning a lone dash on a new line.
                current_line.append((word, sep_before))
                current_width += sep_w + word_w
            else:
                # Start new line — a break at this gap CONSUMES its separator.
                all_lines.append(_join_line(current_line))
                current_line = [(word, "")]
                current_width = word_w

        if current_line:
            all_lines.append(_join_line(current_line))

    return all_lines if all_lines else [""]


# E.4 (v0.2.0): widow threshold. A final line holding a single word whose
# length is <= this is treated as a widow worth surfacing. ``break_into_lines``
# (the punctuation-keep rule above) already prevents a lone punctuation token
# from landing on its own final line, so this only fires for short real words.
_WIDOW_MAX_WORD_LEN = 4


def _is_widow(lines: list[str], max_word_len: int = _WIDOW_MAX_WORD_LEN) -> bool:
    """Return True when a re-wrapped line set ends in a widow.

    A *widow* (for E.4 surfacing purposes) is a final line that holds a
    single word no longer than ``max_word_len`` characters while the
    paragraph wrapped onto two or more lines. Detect-and-surface only:
    this predicate never mutates ``lines``.

    Args:
        lines: The re-wrapped line set from ``break_into_lines``.
        max_word_len: Inclusive upper bound on the lone final word's length.

    Returns:
        True iff the last line is a single short word and there are >= 2 lines.
    """
    if len(lines) < 2:
        return False
    last_words = lines[-1].split()
    if len(last_words) != 1:
        return False
    return len(last_words[0]) <= max_word_len


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
    observed: list[str] | None = None,
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
        observed: B.9 out-param threaded to ``encode`` — collects any source
            substring collapsed into a ligature CID so the caller can surface a
            ``ligature_substituted`` Degradation.

    Returns:
        Tuple of (operands, operator) for a Tj or TJ instruction.
    """
    encoded = resolver.encode(line, _observed=observed)

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
    degradation_log: list[Degradation] | None = None,
    fill_color_ops: list[tuple[list[Any], str]] | None = None,
    indent_style: Literal["first_line", "hanging", "flush"] = "flush",
    first_line_indent: float = 0.0,
    hanging_indent: float = 0.0,
    observed: list[str] | None = None,
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
        degradation_log: Optional out-param; typed Degradations emitted at
            source (heading/marker font drops, and Block F
            ``color_space_approximated``) are appended here.
        fill_color_ops: Block F CORE — the verbatim fill color-setting
            operator subsequence captured from the original run
            (``[(operands, op_name), ...]``). When non-empty it is replayed
            verbatim after BT, preserving non-device (Separation/DeviceN/
            ICCBased/Pattern) color-space identity. When None/empty the engine
            falls back to the lossy ``fill_color`` device length-guess and
            appends a ``color_space_approximated`` Degradation.
        indent_style: E.2 — classified source indent style. Defaults to
            ``"flush"``, which keeps the existing relative-``Td`` continuation
            path byte-for-byte (the indent emission below is gated entirely
            behind ``first_line``/``hanging``). The two structural.py call
            sites omit this and inherit the flush default, so structural
            output is byte-identical.
        first_line_indent: E.2 — for ``"first_line"``, the +delta line 0 sits
            to the right of the continuation margin (``left_margin``). Line 0
            is emitted at ``left_margin + first_line_indent`` and the first
            continuation returns x to ``left_margin``.
        hanging_indent: E.2 — for ``"hanging"``, the +delta the continuations
            sit to the right of line 0 (``left_margin`` is line 0's x here).
            Continuations are emitted at ``left_margin + hanging_indent``.
        observed: B.9 out-param threaded to every ``_encode_line_as_tj`` call —
            collects any source substring collapsed into a ligature CID so the
            caller (``reflow_paragraph``) can surface a ``ligature_substituted``
            Degradation. None on call sites that omit it.

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

    # ── Color (Block F CORE) ──────────────────────────────────────
    # The fill color is set once after BT and inherits for every line in this
    # single rebuilt BT/ET block (the per-line Tf/Tm/Td emits are
    # color-agnostic). Replay the captured verbatim fill color-setting
    # subsequence so a non-device color space (Separation/DeviceN/ICCBased/
    # Pattern) keeps its identity (``/CS0 cs 0.8 scn`` round-trips rather than
    # collapsing to ``0.8 g``). When no capture is available, fall back to the
    # lossy device length-guess and surface the loss honestly.
    if fill_color_ops:
        for operands, op_name in fill_color_ops:
            new_ops.append((list(operands), pikepdf.Operator(op_name)))
    elif fill_color is not None:
        color_operands = [float(c) for c in fill_color]
        if len(fill_color) == 1:
            new_ops.append((color_operands, pikepdf.Operator("g")))
        elif len(fill_color) == 3:
            new_ops.append((color_operands, pikepdf.Operator("rg")))
        elif len(fill_color) == 4:
            new_ops.append((color_operands, pikepdf.Operator("k")))
        if degradation_log is not None:
            degradation_log.append(
                Degradation(
                    kind="color_space_approximated",
                    detail=f"len={len(fill_color)}",
                    severity="warning",
                )
            )

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
    # E.2: derive the line-0 x and the continuation-block x from the
    # classified indent style. For FLUSH (the default) both equal
    # ``left_margin`` and the emission below is byte-for-byte the pre-E.2
    # path — first line at ``left_margin`` and relative ``Td [0, -lh]``
    # continuations (see the gate at the continuation loop). The indent
    # branch activates ONLY for a confident first_line/hanging.
    apply_indent = indent_style in ("first_line", "hanging")
    if indent_style == "first_line":
        line0_x = left_margin + first_line_indent
        continuation_x = left_margin
    elif indent_style == "hanging":
        line0_x = left_margin
        continuation_x = left_margin + hanging_indent
    else:
        line0_x = left_margin
        continuation_x = left_margin

    if resolver.is_cid_font:
        new_ops.append(
            ([1, 0, 0, 1, line0_x, first_line_y], pikepdf.Operator("Tm")),
        )
    else:
        new_ops.append(
            ([line0_x, first_line_y], pikepdf.Operator("Td")),
        )

    # ── Encode first line ─────────────────────────────────────────
    # If the heading font can't encode the text, fall back to body font.
    if use_heading:
        can_enc, _ = current_resolver.can_encode(lines[0])
        if not can_enc:
            # Graceful degradation: render in body font instead.
            # v0.1.3 Phase 6: emit-at-source heading_font_dropped Degradation.
            # heading_font_dropped IS in FONT_AFFECTING_KINDS (font literally
            # swapped) — caller's computed font_preserved property correctly
            # returns False.
            if degradation_log is not None and heading_font is not None:
                degradation_log.append(
                    Degradation(
                        kind="heading_font_dropped",
                        detail=f"original_font={heading_font},fallback={font_name}",
                        severity="warning",
                    )
                )
            current_font = font_name
            current_resolver = resolver
            new_ops[-2] = ([body_font_ref, font_size], pikepdf.Operator("Tf"))

    new_ops.append(
        _encode_line_as_tj(
            line=lines[0],
            resolver=current_resolver,
            observed=observed,
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
        # E.2: on a confident first_line/hanging classification the FIRST
        # continuation returns x from the line-0 origin (``line0_x``) to the
        # continuation block margin (``continuation_x``) via a single relative
        # ``Td [dx, -line_height]``; thereafter ``Td [0, -line_height]`` keeps
        # x at ``continuation_x``. This works identically for the non-CID
        # relative-``Td`` reconstruction and the CID ``Tm``-then-relative-``Td``
        # reconstruction (the line-0 ``Tm``/``Td`` sets the origin, the dx
        # offsets it). For FLUSH (default) ``apply_indent`` is False and dx is
        # 0.0, so this is byte-for-byte the pre-E.2 relative-``Td`` path.
        dx = continuation_x - line0_x if (apply_indent and line_idx == 1) else 0.0
        new_ops.append(
            ([dx, -line_height], pikepdf.Operator("Td")),
        )

        stripped = line.lstrip()
        marker_char = stripped[:1] if stripped else ""
        marker_font = marker_fonts.get(marker_char)
        marker_resolver = extra.get(marker_font) if marker_font else None

        # v0.1.3 Phase 6: marker_font_dropped fires when style-palette has a
        # marker font for this char but the marker resolver can't encode it
        # (e.g., subset doesn't include the bullet glyph). The else branch
        # below renders the marker in body font instead — kind is in
        # FONT_AFFECTING_KINDS so font_preserved correctly flips False.
        marker_drop = bool(
            marker_font and marker_resolver and not marker_resolver.can_encode(marker_char)[0]
        )
        if marker_drop and degradation_log is not None:
            degradation_log.append(
                Degradation(
                    kind="marker_font_dropped",
                    detail=f"marker={marker_char!r},original_font={marker_font},fallback={font_name}",
                    severity="warning",
                )
            )

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
                        observed=observed,
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
                    observed=observed,
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
                    observed=observed,
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
    # The module-level ``_FONTFILE2_CACHE`` referenced by earlier comments
    # was deleted in Phase 13.4 (ARY-348); ``font_has_codepoint`` now re-
    # parses ``/FontFile2`` on each call, so no PDF-keyed cache threading
    # is required.
    from pdf_edit_engine.encoding import FontResolverCache as _FontResolverCache

    if resolver_cache is None:
        resolver_cache = _FontResolverCache()

    # INV-B-3 contract: reflow_paragraph is a public API entry that
    # consumes a TextMatch. Refuse stale matches so the caller cannot
    # silently reflow with operator_refs that no longer point at
    # match.matched_text. The single-helper-per-entry pattern in
    # surgeon.py is replicated here for defense in depth.
    from pdf_edit_engine.surgeon import _assert_match_addressable

    _ops_for_validation = _parse_content_stream(page, context="reflow.reflow_paragraph")
    _assert_match_addressable(_ops_for_validation, match, font_resolver)

    # B.12 rotation gate (INV-B-8): paragraph reflow re-emits a FRESH identity
    # text matrix (``_build_replacement_ops`` writes ``Tm = [1 0 0 1 ...]``).
    # On a non-axis-aligned (rotated/sheared) run that silently flattens the
    # rotation, so refuse here — BEFORE any font extension, line-breaking, or
    # ops mutation (no partial mutation, dry_run-parity-safe). The reuse of
    # ``_governing_tm_linear`` + ``_matrix_is_axis_aligned`` mirrors the
    # surgeon import pattern just above and POS-GATE's decision. ``None`` from
    # ``_governing_tm_linear`` means the BT-reset identity matrix governs the
    # run → axis-aligned → proceed. The rotation-safe splice (same-length) and
    # Tz-kerning (length-change, no reflow) paths never reach here, so they
    # keep the original ``Tm`` and are NOT refused (INV-B-7).
    from pdf_edit_engine.surgeon import _governing_tm_linear, _matrix_is_axis_aligned

    _tm_linear = _governing_tm_linear(_ops_for_validation, min(match.operator_refs))
    if _tm_linear is not None and not _matrix_is_axis_aligned(*_tm_linear):
        _a, _b, _c, _d = _tm_linear
        return EditResult(
            success=False,
            original_text=match.matched_text,
            new_text=new_text,
            font_action="kept",
            warnings=[
                "Rotated/sheared text cannot be reflowed; edit refused to "
                "avoid silently flattening rotation.",
            ],
            fidelity_report=FidelityReport(
                font_substituted=None,
                overflow_detected=False,
                reflow_applied=False,
                glyphs_missing=[],
                degradations=[
                    Degradation(
                        kind="rotated_text_unsupported",
                        detail=f"tm=[{_a:.3f} {_b:.3f} {_c:.3f} {_d:.3f}]",
                        severity="warning",
                    ),
                ],
            ),
        )

    # 1. Substitute text in paragraph, then join lines for proper reflow.
    # The \n in full_text are artifacts of element grouping, not hard breaks.
    new_para_text = paragraph.full_text.replace(match.matched_text, new_text, 1)
    # E.7 (v0.2.0): element-grouping "\n" artifacts re-join with a per-boundary
    # separator. The PURE-LATIN fast path stays the blanket ``.replace("\n",
    # " ")`` (byte-identical to pre-E.7). For a non-Latin-simple paragraph each
    # "\n" is a provenance-less grouping artifact resolved structurally by
    # ``linebreak.grouping_boundary_separator``: a "\n" between two ideographs
    # (ID↔ID / CL→ID) collapses to EMPTY (a space there is spurious), while a
    # "\n" at a likely word boundary becomes a single space. REAL spaces in the
    # inserted replacement text are NOT touched here (they live inside a piece,
    # not as a "\n"); they flow into break_into_lines where the recorded-
    # separator model preserves them verbatim (Korean word spaces, real
    # inter-ideograph spaces).
    new_para_text = _rejoin_newline_artifacts(new_para_text)

    # E.7 (v0.2.0): scriptless (no-UAX#14-opportunity) honesty surfacing. A
    # spaceless run with no break opportunity (Thai / Lao / Khmer / Myanmar —
    # dictionary-segmented scripts the stdlib unicodedata classifier cannot
    # break) is left honestly UNWRAPPED; surface it rather than silently
    # overflow. Gated on _has_space (NOT is_latin_simple — a spaceless Thai run
    # is all-AL hence Latin-simple) so CJK (has ID↔ID opportunities) and Latin
    # (has spaces) never over-fire. Computed before any ops mutation -> dry_run
    # parity holds.
    # A single grapheme (``len <= 1`` after stripping) can never overflow its
    # column, so it is NOT a scriptless-wrap failure — gate it out to avoid a
    # false-fire on a lone ideograph (whose ``has_break_opportunity`` is False
    # only because there is no adjacent pair).
    scriptless_degradations: list[Degradation] = []
    _has_space = " " in new_para_text
    _stripped = new_para_text.strip()
    if (not _has_space) and (not has_break_opportunity(new_para_text)) and len(_stripped) > 1:
        scriptless_degradations.append(
            Degradation(
                kind="scriptless_reflow_unsupported",
                detail=f"chars={len(new_para_text)}",
                severity="info",
            )
        )

    # 2. Break into lines (re-wraps the continuous text to paragraph width)
    lines = break_into_lines(
        new_para_text,
        paragraph.paragraph_width,
        font_resolver,
        font_ref,
        paragraph.font_size,
    )

    # E.4 (v0.2.0): widow/orphan honesty surfacing. Evaluated on the wrapped
    # ``lines`` BEFORE any ops mutation (so dry_run parity holds — the
    # predicate is identical dry vs real and never depends on a side effect).
    # Detect-and-surface only: the output geometry is unchanged; we do NOT
    # attempt the risky pull-down repair (moving a word down from the
    # penultimate line, which can mis-join across the wrap boundary).
    widow_degradations: list[Degradation] = []
    if _is_widow(lines):
        widow_degradations.append(
            Degradation(
                kind="line_break_quality_degraded",
                detail=f"last_word={lines[-1]!r}",
                severity="info",
            )
        )

    # 3. Check encoding on the actual line content, extend font if needed.
    # E.7 (v0.2.0): join the wrapped lines for the coverage probe with the SAME
    # segmentation-aware separator the render uses (_join_atoms): a SPACE for
    # Latin (byte-identical to the prior `" ".join(lines)`) but EMPTY for CJK.
    # A pure-CJK subset carries no space glyph, so a space-joined probe would
    # spuriously report the space as missing and trigger a needless (and, for a
    # .ttc system font, failing) extension. Joining as actually rendered keeps
    # the probe honest for both scripts.
    all_line_text = _join_atoms(lines, new_para_text)
    font_action: Literal["kept", "extended", "substituted", "failed"] = "kept"
    can_enc, missing = font_resolver.can_encode(all_line_text)
    # INV-C-4 plumbing: capture metric-equivalent substitution events.
    substitution_log: list[str] = []
    # v0.1.3 (Phase 5) coverage degradations + pre-extension glyphs_missing.
    coverage_degradations: list[Degradation] = []
    pre_extension_missing: list[str] = []

    if not can_enc:
        try:
            from pdf_edit_engine.fonts import extend_subset

            font_name = paragraph.font_name
            tier = extend_subset(
                pdf,
                page,
                font_name,
                "".join(missing),
                substitution_log=substitution_log,
            )
            # v0.1.3 Phase 5: record extension as a typed Degradation.
            pre_extension_missing = list(missing)
            chars_str = ",".join(missing)
            if tier == "cmap_only":
                coverage_degradations.append(
                    Degradation(
                        kind="font_coverage_extended",
                        detail=f"tier=1,chars={chars_str}",
                        severity="info",
                    )
                )
            elif tier == "full_extension":
                source_suffix = f",source={substitution_log[0]}" if substitution_log else ""
                coverage_degradations.append(
                    Degradation(
                        kind="font_coverage_substituted",
                        detail=f"tier=1.5,chars={chars_str}{source_suffix}",
                        severity="warning",
                    )
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
            # A1.3 / INV-W-4: a Flate decompression bomb in the embedded
            # font / CMap surfaces a SECOND, specific-cause
            # font_stream_too_large Degradation (warning) alongside the
            # font_extension_failed (error) that already drives success=False.
            degs = [
                Degradation(
                    kind="font_extension_failed",
                    detail=type(exc).__name__,
                    severity="error",
                ),
            ]
            if isinstance(exc, FontStreamTooLargeError):
                degs.append(
                    Degradation(
                        kind="font_stream_too_large",
                        detail=type(exc).__name__,
                        severity="warning",
                    )
                )
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
                    degradations=degs,
                ),
            )

    # 4. Parse content stream
    ops = _parse_content_stream(page, context="reflow.reflow_paragraph")

    # 5. Find BT/ET blocks and expand operator indices
    blocks = _find_bt_et_blocks(ops)
    removal_indices = _expand_to_bt_et(paragraph.operator_indices, blocks)

    # 6. Get fill color from paragraph's first element. Block F CORE: also
    # source the captured verbatim fill color-setting subsequence so reflow can
    # replay non-device color spaces (Separation/DeviceN/ICCBased/Pattern)
    # rather than collapsing them to a device guess.
    fill_color = paragraph.elements[0].graphics_state.fill_color
    fill_color_ops = paragraph.elements[0].graphics_state.fill_color_ops

    # Multi-color honesty: the rebuilt paragraph is a single BT/ET block with
    # one color op that inherits for all lines. If the paragraph's runs carried
    # MORE THAN ONE GENUINELY distinct fill color-setting subsequence, element[0]'s
    # color is replayed verbatim for the whole block and the per-run distinction is
    # lost. Per first_element_bug_handling, surface that loss honestly via
    # color_space_approximated rather than dropping it silently. Full per-run
    # re-coloring across a re-wrap is scoped out (mis-color risk across moved
    # wrap boundaries — same class E.4 declines for the widow pull-down).
    #
    # Each element keys as (op_name, normalized-operands) per fill_color_op, with
    # operands normalized numerically via _normalize_color_operand so that the
    # SAME color written at different literal precision (1 0 0 rg vs 1.0 0.0 0.0
    # rg) collapses to one key — a mixed-precision single-color paragraph must
    # NOT false-emit a multi_color_run signal.
    multi_color_degradations: list[Degradation] = []
    _distinct_color_keys = {
        tuple(
            (name, tuple(_normalize_color_operand(o) for o in operands))
            for operands, name in (e.graphics_state.fill_color_ops or [])
        )
        for e in paragraph.elements
    }
    if len(_distinct_color_keys) > 1:
        multi_color_degradations.append(
            Degradation(
                kind="color_space_approximated",
                detail=f"multi_color_run,colors={len(_distinct_color_keys)}",
                severity="warning",
            )
        )

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
    # v0.1.3 Phase 6: emit-at-source Degradations for the same overflow events.
    # Coexists with shift_warnings (INV-J-3 backward-compat); v0.2 will collapse.
    shift_degradations: list[Degradation] = []
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
                    shift_degradations.append(
                        Degradation(
                            kind="overflow_shift_suppressed",
                            detail=f"requested={requested_shift:.1f}pt,available=0pt",
                            severity="warning",
                        )
                    )
                    shift_amount = 0.0
                elif requested_shift > max_safe_shift:
                    shift_warnings.append(
                        f"Overflow shift clamped from {requested_shift:.1f}pt "
                        f"to {max_safe_shift:.1f}pt to keep content on-page",
                    )
                    shift_degradations.append(
                        Degradation(
                            kind="overflow_shift_clamped",
                            detail=f"requested={requested_shift:.1f}pt,clamped_to={max_safe_shift:.1f}pt",
                            severity="warning",
                        )
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
            ops = _parse_content_stream(page, context="reflow.reflow_paragraph")

    # E.2: indent-style honesty surfacing. The classifier already ran in
    # _build_paragraph (paragraph.indent_style); when it fell back to FLUSH for
    # an AMBIGUOUS reason (single-line / sub-noise-floor / non-monotone
    # continuations — a real-but-unclassifiable indent that was flattened) we
    # surface a typed ``indent_flattened`` (info). A CLEAN flush (all lines
    # genuinely equal) does NOT emit it (false positive). The predicate is a
    # pure function of the SOURCE x_starts (identical dry vs real, no side
    # effect) and is computed BEFORE the ops mutation below, so dry_run parity
    # holds. ``indent_flattened`` is NOT in FONT_AFFECTING_KINDS, so it does
    # not flip ``font_preserved``.
    indent_degradations: list[Degradation] = []
    if paragraph.indent_style == "flush":
        _src_x_starts = [
            ln[0].characters[0].page_x  # type: ignore[index]
            for ln in _group_elements_into_lines(paragraph.elements, paragraph.font_size)
        ]
        if _is_degraded_flush(_src_x_starts, paragraph.font_size):
            indent_degradations.append(
                Degradation(
                    kind="indent_flattened",
                    detail=f"lines={len(_src_x_starts)}",
                    severity="info",
                )
            )

    # 8. Build replacement operators (v0.1.3 Phase 6: capture per-line
    # font-fallback Degradations via degradation_log out-param).
    # B.9 (INV-B-9): capture any source substring encode() collapsed into a
    # ligature CID via the observed out-param so the loss is surfaced honestly.
    style_degradations: list[Degradation] = []
    ligature_observed: list[str] = []
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
        degradation_log=style_degradations,
        fill_color_ops=fill_color_ops,
        indent_style=paragraph.indent_style,
        first_line_indent=paragraph.first_line_indent,
        hanging_indent=paragraph.hanging_indent,
        observed=ligature_observed,
    )

    # B.9 (INV-B-9): surface a ligature_substituted info Degradation when a
    # ligature CID was actually chosen during the re-encode (mandatory or
    # opted-in discretionary). Empty on the default typed-separate path.
    ligature_degradations: list[Degradation] = []
    if ligature_observed:
        ligature_degradations.append(
            Degradation(
                kind="ligature_substituted",
                severity="info",
                detail=f"applied ligature(s) {sorted(set(ligature_observed))} during reflow",
            )
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
    new_stream = _unparse_content_stream(new_ops, context="reflow.reflow_paragraph")
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
            # Audit-bundle finding #3: pre-extension state.
            glyphs_missing=pre_extension_missing,
            degradations=[
                *coverage_degradations,
                *detector_degradations,
                *style_degradations,
                *shift_degradations,
                *widow_degradations,
                *multi_color_degradations,
                *indent_degradations,
                *ligature_degradations,
                *scriptless_degradations,
            ],
        ),
    )
