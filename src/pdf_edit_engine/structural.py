"""Structural editing module — block-level PDF content operations.

Provides bbox-based operations: replace_block, delete_block,
insert_text_block, and shift_content_below.  These work on spatial
regions rather than text matches, enabling paragraph-level editing.
"""

from __future__ import annotations

import contextlib
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Literal

import pikepdf

from pdf_edit_engine._pathutil import (
    _parse_content_stream,
    _save_pdf,
    _unparse_content_stream,
    open_pdf,
    validate_output_path,
)
from pdf_edit_engine.encoding import FontResolverCache
from pdf_edit_engine.errors import FontStreamTooLargeError, OperatorError
from pdf_edit_engine.fonts import _FONT_EXTEND_FAIL_EXCS
from pdf_edit_engine.locator import _build_index, _resolve_pages
from pdf_edit_engine.models import ContentElement, Degradation, EditResult, FidelityReport
from pdf_edit_engine.reflow import (
    _build_replacement_ops,
    _expand_to_bt_et,
    _find_bt_et_blocks,
    break_into_lines,
)

logger = logging.getLogger(__name__)

_Ops = list[Any]

# Cache ownership (ARY-283): each public entrypoint constructs its own
# FontResolverCache at entry and threads it through helpers. No module-
# level cache here — surgeon.py follows the same policy.


def _surface_linearization_dropped(
    results: list[EditResult] | EditResult, linearization_log: list[str]
) -> None:
    """Append a ``linearization_dropped`` Degradation when re-linearization failed.

    A2.2 / INV-W-3: ``_pathutil._save_pdf`` appends a marker to
    ``linearization_log`` only on the fallback path (a linearized input could
    not be re-linearized and was saved non-linearized). When that happened,
    surface the loss as a typed, info-severity, non-font-affecting
    ``Degradation`` on the edit result(s) so the caller sees the dropped Fast
    Web View layout instead of a silent down-conversion. A no-op when the log
    is empty (preservation succeeded, or the input was not linearized).

    Args:
        results: A single ``EditResult`` or a list of them. For multi-result
            verbs the file-level drop is surfaced on every successful result.
        linearization_log: The list threaded into ``_save_pdf``; non-empty iff
            the fallback fired.
    """
    if not linearization_log:
        return
    detail = linearization_log[0]
    targets = results if isinstance(results, list) else [results]
    for res in targets:
        if res.success:
            res.fidelity_report.degradations.append(
                Degradation(
                    kind="linearization_dropped",
                    detail=detail,
                    severity="info",
                )
            )


def _surface_encryption_dropped(
    results: list[EditResult] | EditResult, encryption_log: list[str]
) -> None:
    """Append an ``encryption_dropped`` Degradation when re-encryption failed.

    A2.3 / INV-W-5: ``_pathutil._save_pdf`` appends a marker to
    ``encryption_log`` only on the fallback path (an encrypted input could not
    be re-encrypted and was saved unencrypted). When that happened, surface the
    loss as a typed, warning-severity, non-font-affecting ``Degradation`` on
    the edit result(s) so the caller sees the dropped encryption instead of a
    silent down-conversion to plaintext. A no-op when the log is empty
    (preservation succeeded, or the input was not encrypted). Severity
    ``"warning"`` (a dropped encryption is a more serious fidelity/security
    loss than a dropped Fast-Web-View layout).

    Args:
        results: A single ``EditResult`` or a list of them. For multi-result
            verbs the file-level drop is surfaced on every successful result.
        encryption_log: The list threaded into ``_save_pdf``; non-empty iff
            the fallback fired.
    """
    if not encryption_log:
        return
    detail = encryption_log[0]
    targets = results if isinstance(results, list) else [results]
    for res in targets:
        if res.success:
            res.fidelity_report.degradations.append(
                Degradation(
                    kind="encryption_dropped",
                    detail=detail,
                    severity="warning",
                )
            )


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


_SHOW_TEXT_OPS: frozenset[str] = frozenset({"Tj", "TJ", "'", '"'})
_INLINE_IMAGE_OPS: frozenset[str] = frozenset({"INLINE IMAGE", "BI", "ID", "EI"})


def _bbox_region_text(
    elements: list[ContentElement],
    bbox: tuple[float, float, float, float],
    tolerance: float = 2.0,
) -> str:
    """Extract the text rendered in a bbox region (extract_bbox_text semantics).

    B.11 / INV-B-10. Pure helper that mirrors
    :func:`locator.extract_bbox_text`'s tolerance-overlap filter + gap-aware
    line grouping, but operates on an in-memory element list (so the deletion
    residue predicate can run on the POST-emptying index without a disk
    round-trip). Used to compare the bbox-region text before vs after the
    keep-slot emptying.

    Args:
        elements: Content element index (e.g. from ``_build_index``).
        bbox: Target region ``(x0, y0, x1, y1)``.
        tolerance: Extra margin (points) for overlap matching.

    Returns:
        The text rendered within the bbox region, lines joined by newlines.
    """
    from pdf_edit_engine.locator import _group_into_lines

    x0, y0, x1, y1 = bbox
    hits: list[ContentElement] = []
    for elem in elements:
        if elem.type != "text" or not elem.text_content:
            continue
        ex0, ey0, ex1, ey1 = elem.bbox
        if (
            ex0 < x1 + tolerance
            and ex1 > x0 - tolerance
            and ey0 < y1 + tolerance
            and ey1 > y0 - tolerance
        ):
            hits.append(elem)
    hits.sort(key=lambda e: (-e.bbox[3], e.bbox[0]))
    return "\n".join(_group_into_lines(hits))


def _missed_co_rendered_residue(
    post_elements: list[ContentElement],
    bbox: tuple[float, float, float, float],
    matched_elems: list[ContentElement],
    removal_set: set[int],
    tolerance: float = 2.0,
) -> bool:
    """Detect a co-rendered show-text op the bbox collection MISSED.

    B.11 / INV-B-10, over-fire root-fix. Residue means a show-text op that
    co-renders the SAME deleted target into the bbox region but was NOT in the
    keep-slot ``removal_set`` (the strict-AABB collection missed it), so it
    still renders after the emptying. This is a TARGETED-op check: it looks for
    an un-emptied op whose rendered ``text_content`` MATCHES a COLLECTED target
    element's ``text_content`` (a co-rendered duplicate of what was deleted) and
    that overlaps the bbox within tolerance.

    This replaces the prior ``any(target_token in region_text_after)`` substring
    check, which mis-fired on an ADJACENT untouched line that merely SHARED a
    >=2-char token with the deleted heading: the ~2pt tolerance pulled the
    neighbour into the re-extracted region, and the shared token re-appearing
    there falsely flipped ``success=False`` even though the heading deletion was
    correct. Matching on the FULL collected-target text (a missed co-rendered
    duplicate), not a shared sub-token, keeps an adjacent DIFFERENT line that
    happens to share a word from triggering residue, while still catching a
    genuinely missed duplicate of the target (the strict-AABB ``< vs <=``
    boundary case the INV-B-10 missed-bbox fixture pins).

    Pure function of the post-emptying element index, the bbox, the collected
    targets, and the removal set — computed before any close_gap shift, so the
    verdict is stable.

    Args:
        post_elements: Content element index re-built AFTER the keep-slot
            emptying (so emptied ops carry no text).
        bbox: Target region ``(x0, y0, x1, y1)``.
        matched_elems: The elements the bbox collection captured as targets.
        removal_set: Operator indices the collection emptied.
        tolerance: Overlap margin (points) — matches ``_bbox_region_text``.

    Returns:
        True iff an un-emptied, overlapping op still renders a collected
        target's exact text.
    """
    target_texts = {
        elem.text_content
        for elem in matched_elems
        if elem.text_content and elem.text_content.strip()
    }
    if not target_texts:
        return False
    x0, y0, x1, y1 = bbox
    for elem in post_elements:
        if elem.type != "text" or not elem.text_content:
            continue
        start, _end = elem.operator_range
        if start in removal_set:
            continue  # this op WAS emptied (its post-emptying text is gone anyway)
        ex0, ey0, ex1, ey1 = elem.bbox
        overlaps = (
            ex0 < x1 + tolerance
            and ex1 > x0 - tolerance
            and ey0 < y1 + tolerance
            and ey1 > y0 - tolerance
        )
        if overlaps and elem.text_content in target_texts:
            return True
    return False


def _empty_show_text_in_place(ops: _Ops, removal_set: set[int]) -> None:
    """Empty the operand of each show-text op in ``removal_set``; KEEP every slot.

    B.11 / INV-B-10. Replaces the pre-B.11 tuple-removal
    (``[op for i, op in enumerate(ops) if i not in removal_set]``), which
    renumbered every downstream operator and invalidated sibling
    ``operator_refs`` in a batch (the index-shift root cause). Here the ops
    list length and every index are held stable: a ``Tj``/``'``/``"`` gets an
    empty string literal ``()`` and a ``TJ`` gets an empty array ``[]`` (both
    advance the text position by 0 and render no glyph); non-show-text ops
    (``BT``/``ET``/``Tf``/``Tm``/``Td``/positioning) are left untouched.

    Args:
        ops: Parsed content-stream instruction list (mutated in place).
        removal_set: Operator indices the bbox collection marked for deletion.
    """
    for i in removal_set:
        if not (0 <= i < len(ops)):
            continue
        inst = ops[i]
        op_str = str(inst.operator) if hasattr(inst, "operator") else str(inst[1])
        if op_str not in _SHOW_TEXT_OPS:
            continue
        operator = inst.operator if hasattr(inst, "operator") else inst[1]
        if op_str == "TJ":
            ops[i] = ([pikepdf.Array([])], operator)
        else:
            ops[i] = ([pikepdf.String(b"")], operator)


def _inline_image_in_indices(ops: _Ops, removal_set: set[int]) -> bool:
    """Detect a ``BI``/``ID``/``EI`` inline image within a deletion's op-index span.

    B.11 / INV-B-10. Advisory only — A1.4 collapses ``BI``/``ID``/``EI`` to one
    stable ``INLINE IMAGE`` slot, so the deletion proceeds; the caller surfaces
    an ``inline_image_present`` (info) Degradation. The span is the contiguous
    operator range ``[min(removal_set), max(removal_set)]`` (the
    ``_expand_to_bt_et``-expanded block range), so an inline image inside the
    deleted block is caught.

    Args:
        ops: Parsed content-stream instruction list.
        removal_set: Operator indices marked for deletion.

    Returns:
        True iff an inline-image operator lies in the span.
    """
    if not removal_set:
        return False
    lo, hi = min(removal_set), max(removal_set)
    for i in range(lo, min(hi, len(ops) - 1) + 1):
        op_str = str(ops[i].operator) if hasattr(ops[i], "operator") else str(ops[i][1])
        if op_str in _INLINE_IMAGE_OPS:
            return True
    return False


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


def _invalidate_locator_cache() -> None:
    """Clear the locator's content-element cache after a content-stream edit."""
    from pdf_edit_engine import locator

    locator._cached_path = None  # noqa: SLF001
    locator._cached_elements = {}  # noqa: SLF001


def _detect_line_height(
    elements: list[ContentElement],
    font_size: float,
) -> float:
    """Detect line height from y-positions of text elements.

    Returns the median gap between consecutive text lines.  Falls back
    to ``font_size * 1.2`` when fewer than two distinct lines exist.
    """
    text_elems = [e for e in elements if e.type == "text" and e.characters]
    if len(text_elems) < 2:
        return font_size * 1.2
    y_positions = sorted(
        {round(e.bbox[3] * 2) / 2 for e in text_elems},
        reverse=True,
    )
    if len(y_positions) < 2:
        return font_size * 1.2
    gaps = [y_positions[i] - y_positions[i + 1] for i in range(len(y_positions) - 1)]
    positive = sorted(g for g in gaps if 0 < g < font_size * 3)
    if not positive:
        return font_size * 1.2
    return positive[len(positive) // 2]


@dataclass
class _StylePalette:
    """Typographic roles detected from original content in a bbox region.

    Derived entirely from the data — no document-type assumptions.

    Attributes:
        heading_font: Font of the first visual line if ≠ body.  None otherwise.
        body_font: Most common font in the region.
        body_size: Font size of the body font.
        body_color: Fill color of the body font (or None).
        marker_fonts: ``{char: font_name}`` for line-initial single-character
            elements that use a non-body, non-heading font (e.g. bullet "•").
        marker_x: Absolute x-position for marker characters (0 = no indent).
        body_after_marker_x: Absolute x-position for body text after markers.
    """

    heading_font: str | None
    body_font: str
    body_size: float
    body_color: tuple[float, ...] | None
    marker_fonts: dict[str, str]
    marker_x: float = 0.0
    body_after_marker_x: float = 0.0


def _build_style_palette(
    elements: list[ContentElement],
    body_font: str,
    body_size: float,
    body_color: tuple[float, ...] | None,
) -> _StylePalette:
    """Build a style palette from the original content in a bbox.

    Groups text elements by y-position into visual lines, then extracts:
    - **heading_font**: dominant font of the topmost line, if ≠ body_font.
    - **marker_fonts**: for each visual line's leftmost element that is a
      single non-whitespace character in a non-body, non-heading font,
      record ``{char: font_name}``.

    This is universal — it works for any document type because it derives
    styles from what the original content actually used.
    """
    text_elems = [e for e in elements if e.type == "text" and e.graphics_state.font_name]
    if not text_elems:
        return _StylePalette(None, body_font, body_size, body_color, {})

    # Group elements by visual line (y-position, rounded to 0.5pt)
    lines_by_y: dict[float, list[ContentElement]] = defaultdict(list)
    for e in text_elems:
        y_key = round(e.bbox[3] * 2) / 2
        lines_by_y[y_key].append(e)

    sorted_ys = sorted(lines_by_y, reverse=True)  # top to bottom

    # ── Heading font: dominant non-body font in the top visual lines ──
    # Scan the top 2 lines (not just the topmost) to handle stray elements
    # that leak into the bbox from adjacent sections during batch processing.
    # Only consider elements with substantial text (>1 visible char) to
    # avoid misidentifying single-char markers (bullets, dashes).
    heading_font: str | None = None
    for y_key in sorted_ys[:2]:
        line = lines_by_y[y_key]
        significant = [e for e in line if e.text_content and len(e.text_content.strip()) > 1]
        if not significant:
            continue
        font_counts: Counter[str] = Counter()
        for e in significant:
            name = e.graphics_state.font_name
            if name is None:
                continue
            font_counts[name] += 1
        if not font_counts:
            continue
        dominant = font_counts.most_common(1)[0][0]
        if dominant != body_font:
            heading_font = dominant
            break

    # ── Marker fonts: line-initial single non-ws chars ───────────
    marker_fonts: dict[str, str] = {}
    for y_key in sorted_ys:
        line_elems = lines_by_y[y_key]
        # Find leftmost element on this line
        leftmost = min(line_elems, key=lambda e: e.bbox[0])
        text = leftmost.text_content
        if not text:
            continue
        stripped = text.strip()
        leftmost_font_name = leftmost.graphics_state.font_name
        if (
            len(stripped) == 1
            and not stripped.isspace()
            and leftmost_font_name is not None
            and leftmost_font_name != body_font
            and leftmost_font_name != heading_font
            and stripped not in marker_fonts
        ):
            marker_fonts[stripped] = leftmost_font_name

    # ── Marker indentation: x-positions of markers and body after markers ─
    marker_x = 0.0
    body_after_marker_x = 0.0
    if marker_fonts:
        # Find x-positions from lines that have markers
        for y_key in sorted_ys:
            line_elems = sorted(lines_by_y[y_key], key=lambda e: e.bbox[0])
            leftmost = line_elems[0]
            text = leftmost.text_content
            if text and text.strip() in marker_fonts:
                marker_x = leftmost.bbox[0]
                # Find the first non-marker, non-space element on the same line
                for e in line_elems[1:]:
                    if (
                        e.text_content
                        and e.text_content.strip()
                        and e.graphics_state.font_name != leftmost.graphics_state.font_name
                    ):
                        body_after_marker_x = e.bbox[0]
                        break
                if body_after_marker_x > 0:
                    break  # found both positions

    return _StylePalette(
        heading_font=heading_font,
        body_font=body_font,
        body_size=body_size,
        body_color=body_color,
        marker_fonts=marker_fonts,
        marker_x=marker_x,
        body_after_marker_x=body_after_marker_x,
    )


def _detect_font_from_elements(
    elements: list[ContentElement],
) -> tuple[str, float, tuple[float, ...] | None]:
    """Auto-detect font name, size, and fill color from text elements.

    Uses the most common font in the region so that body text fonts
    (e.g. regular-weight F3) are preferred over title fonts (bold F1)
    that appear only once or twice.

    Args:
        elements: Content elements (should include text elements).

    Returns:
        Tuple of (font_name, font_size, fill_color).

    Raises:
        OperatorError: If no text elements found.
    """
    font_counts: Counter[str] = Counter()
    font_info: dict[str, tuple[float, tuple[float, ...] | None]] = {}
    for elem in elements:
        if elem.type == "text" and elem.graphics_state.font_name:
            name = elem.graphics_state.font_name
            font_counts[name] += 1
            if name not in font_info:
                font_info[name] = (
                    elem.graphics_state.font_size or 12.0,
                    elem.graphics_state.fill_color,
                )
    if not font_counts:
        raise OperatorError("No text elements found in region for font detection")
    most_common = font_counts.most_common(1)[0][0]
    size, color = font_info[most_common]
    return most_common, size, color


def _find_cid_font_in_elements(
    elements: list[ContentElement],
    page_obj: pikepdf.Page,
    resolver_cache: FontResolverCache,
) -> tuple[str, float] | None:
    """Find the first CID/Identity-H font among text elements in a region.

    Used as a fallback when the auto-detected font is a non-extensible
    WinAnsi TrueType font.  CID fonts support extend_subset().

    Args:
        elements: Content elements to scan.
        page_obj: Page for font resolution.
        resolver_cache: Caller-owned font resolver cache.

    Returns:
        Tuple of (font_name, font_size) for the first CID font, or None.
    """
    seen: set[str] = set()
    for elem in elements:
        if elem.type == "text" and elem.graphics_state.font_name:
            name = elem.graphics_state.font_name
            if name in seen:
                continue
            seen.add(name)
            resolver = resolver_cache.get_resolver(page_obj, name.lstrip("/"))
            if resolver.is_cid_font:
                return (name, elem.graphics_state.font_size or 12.0)
    return None


# ── Core: annotation sync ───────────────────────────────────────────────


def _is_annotation_orphaned(annot: pikepdf.Object, new_lower: str) -> bool:
    """Decide whether an annotation's URI is orphaned by the new text.

    An orphan is a hyperlink whose URI references keywords (the last
    path segment, split on '-'/'_') of which **none** appear in the
    new text. A blank ``new_text`` keeps the annotation by convention
    (the caller may be deleting content; orphan-detection then has no
    text to compare against).
    """
    if not new_lower:
        return False
    a_dict = annot.get("/A")
    uri = str(a_dict.get("/URI", "")) if a_dict else ""  # type: ignore[call-overload]
    if not uri:
        return False
    path = uri.rstrip("/").rsplit("/", 1)[-1]
    keywords = [
        kw for kw in path.replace("-", " ").replace("_", " ").lower().split() if len(kw) > 2
    ]
    return bool(keywords) and not any(kw in new_lower for kw in keywords)


def _remove_orphaned_annotations(
    page_obj: pikepdf.Page,
    bbox: tuple[float, float, float, float],
    new_text: str,
) -> None:
    """Remove hyperlink annotations whose URI keywords don't match *new_text*.

    INV-W0-7 contract: orphan detection MUST run on every replace_block /
    delete_block / batch_replace_block call regardless of whether the
    replacement triggers a vertical shift. Splitting this from the shift
    pass guarantees the two concerns can never silently couple again.

    Writes back via ``page_obj["/Annots"]`` because deleting from a
    locally-bound ``list(annots_obj)`` only mutates the Python list, not
    the PDF's ``/Annots`` array — a latent bug in the prior monolithic
    ``_sync_annotations_in_bbox`` that hid the orphan removal entirely.
    """
    annots_obj = page_obj.get("/Annots")
    if not annots_obj:
        return
    annots: list[pikepdf.Object] = list(annots_obj)  # type: ignore[call-overload]
    new_lower = new_text.lower()
    kept: list[pikepdf.Object] = []
    removed = False
    for annot in annots:
        try:
            rect = [float(r) for r in annot["/Rect"]]  # type: ignore[attr-defined]
        except (KeyError, TypeError):
            kept.append(annot)
            continue
        # Vertical overlap with bbox
        if not (rect[1] < bbox[3] and rect[3] > bbox[1]):
            kept.append(annot)
            continue
        if _is_annotation_orphaned(annot, new_lower):
            removed = True
            continue
        kept.append(annot)
    if removed:
        page_obj["/Annots"] = pikepdf.Array(kept)


def _sync_annotations_in_bbox(
    page_obj: pikepdf.Page,
    bbox: tuple[float, float, float, float],
    delta_y: float,
    new_text: str = "",
) -> None:
    """Shift relevant annotations in *bbox* by *delta_y* and remove orphans.

    Relevant annotations (URI keywords appear in *new_text*) get their
    /Rect shifted and any zeroed /BS underline restored. Orphan removal
    is delegated to :func:`_remove_orphaned_annotations` so callers that
    don't shift can still drop dangling links.

    Args:
        page_obj: The page whose annotations to adjust.
        bbox: The replaced region (x0, y0, x1, y1).
        delta_y: Vertical shift to apply (positive = up in PDF coords).
        new_text: The replacement text (used to detect orphans).
    """
    # First pass: drop orphans. After this, the only annotations left
    # overlapping bbox are relevant ones that should follow the shift.
    _remove_orphaned_annotations(page_obj, bbox, new_text)

    annots_obj = page_obj.get("/Annots")
    if not annots_obj:
        return
    for annot in list(annots_obj):  # type: ignore[call-overload]
        try:
            rect = [float(r) for r in annot["/Rect"]]
        except (KeyError, TypeError):
            continue
        if not (rect[1] < bbox[3] and rect[3] > bbox[1]):
            continue
        annot["/Rect"] = pikepdf.Array(
            [
                rect[0],
                rect[1] + delta_y,
                rect[2],
                rect[3] + delta_y,
            ]
        )
        bs = annot.get("/BS")
        if bs is not None and float(bs.get("/W", 1)) == 0:
            annot["/BS"] = pikepdf.Dictionary(
                {
                    "/W": 0.5,
                    "/S": pikepdf.Name("/U"),
                }
            )


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

    ops: _Ops = _parse_content_stream(page_obj, context="structural._shift_content_below_inplace")

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
                    new_operands[5] = pikepdf.Object.parse(str(ty - delta_y).encode())
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
                        new_operands[yi] = pikepdf.Object.parse(str(val - delta_y).encode())
                ops[i] = (new_operands, operator)

        elif op_str in ("v", "y") and len(operands) >= 4:
            ys = [float(operands[j]) for j in (1, 3)]
            if any(y < y_threshold for y in ys):
                new_operands = list(operands)
                for yi in (1, 3):
                    val = float(operands[yi])
                    if val < y_threshold:
                        new_operands[yi] = pikepdf.Object.parse(str(val - delta_y).encode())
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
                    new_operands[5] = pikepdf.Object.parse(str(ty - delta_y).encode())
                    ops[i] = (new_operands, operator)

    # Write back content stream
    new_stream = _unparse_content_stream(ops, context="structural._shift_content_below_inplace")
    page_obj.Contents = pdf.make_stream(new_stream)

    # ── Annotations: shift rects below threshold ─────────────────────
    annots_key = pikepdf.Name("/Annots")
    if annots_key in page_obj:
        try:
            annots: list[Any] = list(page_obj[annots_key])  # type: ignore[call-overload]
            for annot_ref in annots:
                annot = annot_ref.resolve() if hasattr(annot_ref, "resolve") else annot_ref
                rect_key = pikepdf.Name("/Rect")
                if rect_key in annot:
                    rect = annot[rect_key]
                    rect_y1 = float(rect[3])
                    if rect_y1 < y_threshold:
                        annot[rect_key] = pikepdf.Array(
                            [
                                float(rect[0]),
                                float(rect[1]) - delta_y,
                                float(rect[2]),
                                rect_y1 - delta_y,
                            ]
                        )
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
                warnings.append(f"Content shifted below page boundary by {overshoot:.1f}pt")
                break
    return warnings


# ── Public API ───────────────────────────────────────────────────────────


def shift_content_below(
    pdf_path: str,
    page_number: int,
    y_threshold: float,
    delta_y: float,
    output_path: str,
    *,
    password: str | bytes | None = None,
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
        password: Optional password to open an encrypted input; the output is
            RE-ENCRYPTED with the same password (A2.3 / INV-W-5).

    Returns:
        EditResult with fidelity information.
    """
    validate_output_path(output_path)
    pdf = open_pdf(pdf_path, password=password)
    try:
        pages = _resolve_pages(pdf, page_number)
        _, page_obj = pages[0]

        warnings = _shift_content_below_inplace(
            pdf,
            page_obj,
            page_number,
            y_threshold,
            delta_y,
        )

        overflow = any("below page boundary" in w for w in warnings)
        lin_log: list[str] = []
        enc_log: list[str] = []
        _save_pdf(
            pdf,
            output_path,
            linearization_log=lin_log,
            reencrypt_password=password,
            encryption_log=enc_log,
        )
        _invalidate_locator_cache()

        result = EditResult(
            success=True,
            original_text="",
            new_text="",
            font_action="kept",
            warnings=warnings,
            fidelity_report=FidelityReport(
                font_substituted=None,
                overflow_detected=overflow,
                reflow_applied=False,
                glyphs_missing=[],
            ),
        )
        _surface_linearization_dropped(result, lin_log)
        _surface_encryption_dropped(result, enc_log)
        return result
    finally:
        pdf.close()


def compute_uniform_layout(
    region_height: float,
    line_counts: list[int],
    font_size: float = 10.0,
    original_gap: float = 27.0,
) -> tuple[float, float]:
    """Compute uniform line_height and section_gap for N sections.

    Uses a cascade: first reduces inter-section gaps, then (if still
    insufficient) reduces line spacing.  Never compresses line_height
    below ``font_size * 1.05``.

    This is a pure computation with no side effects — it does not modify
    any PDF.  The caller passes the result as ``line_height`` to
    :func:`batch_replace_block`.

    Args:
        region_height: Total vertical space available for all sections
            (from the top of the first section to the bottom of the last).
        line_counts: Number of text lines in each section.
        font_size: Base font size (used for minimum spacing calculation).
        original_gap: Desired inter-section gap (from the original document).

    Returns:
        ``(line_height, section_gap)`` — both in PDF points.
    """
    n_sections = len(line_counts)
    total_inter_line_gaps = sum(lc - 1 for lc in line_counts)
    if total_inter_line_gaps <= 0:
        return (font_size * 1.2, original_gap)

    section_gap = original_gap
    min_line_height = font_size * 1.05

    while section_gap >= 0:
        available = region_height - n_sections * section_gap
        line_height = available / total_inter_line_gaps
        if line_height >= min_line_height:
            return (round(line_height, 2), round(section_gap, 2))
        section_gap -= 0.5

    # Even with zero gaps, content may be too large — clamp to minimum
    line_height = region_height / total_inter_line_gaps
    return (max(round(line_height, 2), font_size), 0.0)


def compute_uniform_layout_detailed(
    region_height: float,
    line_counts: list[int],
    font_size: float = 10.0,
    original_gap: float = 27.0,
) -> tuple[float, float, float]:
    """Pure sibling of :func:`compute_uniform_layout` that also reports the
    natural single-line ratio.

    Additive, net-new symbol — does NOT touch :func:`compute_uniform_layout`'s
    2-tuple return (INV-F-5 pins that). Delegates the line-height/section-gap
    computation to it verbatim, then appends ``natural_line_height`` (the
    documented natural single-line ratio ``font_size * 1.2``) so the batch
    caller (:func:`_auto_compute_layout` → :func:`batch_replace_block`) can
    surface a ``line_height_compressed`` Degradation (E.6) when the computed
    applied line height falls below the natural ratio.

    Args:
        region_height: Total vertical space available for all sections.
        line_counts: Number of text lines in each section.
        font_size: Base font size.
        original_gap: Desired inter-section gap.

    Returns:
        ``(line_height, section_gap, natural_line_height)`` — all in PDF points.
    """
    line_height, section_gap = compute_uniform_layout(
        region_height,
        line_counts,
        font_size=font_size,
        original_gap=original_gap,
    )
    natural_line_height = font_size * 1.2
    return (line_height, section_gap, natural_line_height)


def _auto_compute_layout(
    page_obj: pikepdf.Page,
    page_number: int,
    replacements: list[tuple[tuple[float, float, float, float], str]],
    resolver_cache: FontResolverCache,
) -> tuple[float, float, float]:
    """Auto-detect layout params from original content and replacement text.

    Analyzes the original sections to measure font size and section gaps,
    then computes optimal ``(line_height, section_gap, natural_line_height)``
    via :func:`compute_uniform_layout_detailed`.  Called internally by
    :func:`batch_replace_block` when the caller omits layout parameters.
    The third element (``natural_line_height``) lets the batch caller surface
    a ``line_height_compressed`` Degradation (E.6) when the computed applied
    line height fell below the natural single-line ratio.
    """
    # Collect all text elements across all bboxes for font detection
    elements = _build_index(page_obj, page_number)
    all_matched: list[ContentElement] = []
    for bbox, _ in replacements:
        matched, _ = _collect_elements_in_bbox(elements, bbox)
        all_matched.extend(matched)

    if not all_matched:
        return (12.0, 27.0, 12.0)  # safe fallback (natural == applied)

    det_name, font_size, _ = _detect_font_from_elements(all_matched)
    clean = det_name.lstrip("/")

    # Measure original section gap: median y-distance between adjacent bboxes
    sorted_bboxes = sorted(
        [bb for bb, _ in replacements],
        key=lambda b: -b[3],
    )
    gaps: list[float] = []
    for i in range(len(sorted_bboxes) - 1):
        gap = sorted_bboxes[i][1] - sorted_bboxes[i + 1][3]
        if gap > 0:
            gaps.append(gap)
    original_gap = sorted(gaps)[len(gaps) // 2] if gaps else 27.0

    # Count lines for each replacement text at the detected font/width
    try:
        resolver = resolver_cache.get_resolver(page_obj, clean)
        font_ref = page_obj["/Resources"]["/Font"]["/" + clean]
    except (KeyError, TypeError):
        return (font_size * 1.2, original_gap, font_size * 1.2)

    bbox_width = sorted_bboxes[0][2] - sorted_bboxes[0][0]
    line_counts: list[int] = []
    for bbox, text in replacements:
        # Build style palette for this bbox (same as _replace_block_on_page)
        # to get accurate indent-aware, continuation-joined line counts.
        matched, _ = _collect_elements_in_bbox(elements, bbox)
        if matched:
            body_color: tuple[float, ...] | None = None
            palette = _build_style_palette(matched, clean, font_size, body_color)
        else:
            palette = _StylePalette(None, clean, font_size, None, {})

        if palette.body_after_marker_x > 0:
            marker_indent = palette.body_after_marker_x - bbox[0]
            indented_width = bbox_width - marker_indent
            raw_segs = text.split("\n")
            segs: list[str] = []
            for seg in raw_segs:
                stripped = seg.lstrip()
                if stripped[:1] in palette.marker_fonts:
                    segs.append(seg)
                elif segs and segs[-1].lstrip()[:1] in palette.marker_fonts:
                    segs[-1] = segs[-1].rstrip() + " " + seg.lstrip()
                else:
                    segs.append(seg)
            count = 0
            for seg in segs:
                stripped = seg.lstrip()
                w = indented_width if stripped[:1] in palette.marker_fonts else bbox_width
                count += len(break_into_lines(seg, w, resolver, font_ref, font_size))
        else:
            count = len(break_into_lines(text, bbox_width, resolver, font_ref, font_size))
        line_counts.append(count)

    region_top = max(bb[3] for bb, _ in replacements)
    region_bottom = min(bb[1] for bb, _ in replacements)
    return compute_uniform_layout_detailed(
        region_top - region_bottom,
        line_counts,
        font_size=font_size,
        original_gap=original_gap,
    )


def _extend_font(
    pdf: pikepdf.Pdf,
    page_obj: pikepdf.Page,
    font_name: str,
    text: str,
    resolver_cache: FontResolverCache,
    *,
    substitution_log: list[str] | None = None,
    coverage_tier_log: list[tuple[str, list[str]]] | None = None,
    stream_too_large: list[bool] | None = None,
) -> bool:
    """Extend a font's subset to encode *text*.  Returns True on success.

    The optional *substitution_log* (kw-only) captures metric-equivalent
    substitution events from ``extend_subset`` so the calling
    ``_replace_block_on_page`` / ``insert_text_block`` can surface them
    through ``FidelityReport.font_substituted`` (INV-C-4).

    v0.1.3 (Phase 5): the optional *coverage_tier_log* captures
    ``(tier, missing_chars)`` on successful extension so callers can emit
    ``font_coverage_extended`` (Tier 1) or ``font_coverage_substituted``
    (Tier 1.5) Degradations. Unlike substitution_log this carries the
    tier even when no metric-equivalent fallback fires.

    A1.3 / INV-W-4: the optional *stream_too_large* out-param (mirrors
    *substitution_log*) records — by appending ``True`` — when the extension
    failed specifically because an embedded font / CMap stream exceeded the
    decoded-size bound (``FontStreamTooLargeError``). The helper still returns
    ``False`` on that failure (graceful degrade); the caller inspects the
    out-param to surface the specific-cause ``font_stream_too_large``
    Degradation alongside ``font_extension_failed``.
    """
    from pdf_edit_engine.fonts import extend_subset

    clean = font_name.lstrip("/")
    r = resolver_cache.get_resolver(page_obj, clean)
    can, missing = r.can_encode(text)
    if can:
        return True
    # v0.1.3 Phase 13: simple (non-CID) fonts now extend via _extend_simple_tier_one_five
    # in fonts.extend_subset()'s subtype dispatcher. Falls through unconditionally;
    # the dispatcher itself rejects unsupported subtypes (Type1, /FontFile3) with
    # FontNotFoundError, which the _FONT_EXTEND_FAIL_EXCS catch below converts to
    # a clean return-False + logger.warning.
    try:
        tier = extend_subset(
            pdf,
            page_obj,
            clean,
            "".join(missing),
            substitution_log=substitution_log,
        )
        resolver_cache.evict(page_obj, clean)
        r2 = resolver_cache.get_resolver(page_obj, clean)
        ok, _ = r2.can_encode(text)
        if ok and coverage_tier_log is not None:
            coverage_tier_log.append((tier, list(missing)))
        return ok
    except _FONT_EXTEND_FAIL_EXCS as exc:
        logger.warning("Font extension failed for %s", font_name, exc_info=True)
        if isinstance(exc, FontStreamTooLargeError) and stream_too_large is not None:
            stream_too_large.append(True)
        return False


def _replace_block_on_page(
    pdf: pikepdf.Pdf,
    page_obj: pikepdf.Page,
    page_number: int,
    bbox: tuple[float, float, float, float],
    new_text: str,
    resolver_cache: FontResolverCache,
    font_name: str | None = None,
    font_size: float | None = None,
    line_height: float | None = None,
    first_line_y_override: float | None = None,
    skip_vertical_shift: bool = False,
    *,
    fit: Literal["none", "shrink"] = "none",
) -> tuple[EditResult, float, float]:
    """Core replace_block logic operating on an open PDF page.

    Pipeline order (each step uses outputs of previous steps):
      1. Collect elements → matched_elems
      2. Detect body font → body_font, body_size, body_color
      3. Build style palette → heading/marker fonts, indentation
      4. Extend ALL palette fonts (body + heading + markers)
      5. Break text into lines (indent-aware widths)
      6. Detect line height from original content
      7. Handle overflow (shift down) / record underflow
      8. Render replacement ops (styled, indented)
      9. Splice into content stream
     10. Post-splice underflow collapse (shift up)

    Does NOT save the PDF — caller is responsible for saving.

    Returns:
        Tuple of (EditResult, overflow_delta).  Positive when replacement
        extends below the bbox, negative when it is shorter (underflow).
    """
    from pdf_edit_engine.encoding import FontResolver

    # INV-C-4 wiring: capture metric-equivalent substitutions
    # (e.g. Carlito for Calibri) from extend_subset so the
    # FidelityReport can surface them. Empty list → no substitution
    # observed; first entry → name of the substitute font.
    substitution_log: list[str] = []
    # v0.1.3 (Phase 5) coverage tier captures from extend_subset.
    coverage_tier_log: list[tuple[str, list[str]]] = []
    # A1.3 / INV-W-4: records a bombed-stream extension failure on the body
    # font so the _extend_font_returned_false branch can surface the
    # specific-cause font_stream_too_large Degradation.
    stl: list[bool] = []

    # ── Phase 1: Analyze ──────────────────────────────────────────────
    elements = _build_index(page_obj, page_number)
    matched_elems, op_indices = _collect_elements_in_bbox(elements, bbox)

    if not matched_elems:
        return (
            EditResult(
                success=False,
                original_text="",
                new_text=new_text,
                font_action="kept",
                warnings=["No content found in specified bounding box"],
            ),
            0.0,
            bbox[3],
        )

    # Detect body font (most common in region)
    font_size_was_auto = font_size is None
    det_name, det_size, fill_color = _detect_font_from_elements(matched_elems)
    if font_name is None:
        font_name = det_name
    if font_size is None:
        font_size = det_size

    font_key = font_name if font_name.startswith("/") else f"/{font_name}"
    clean_name = font_name.lstrip("/")

    # Collect original text
    original_parts = [e.text_content for e in matched_elems if e.text_content]
    original_text = " ".join(original_parts)

    # Build style palette — BEFORE font extension and line breaking
    palette = _build_style_palette(matched_elems, clean_name, font_size, fill_color)

    # Compute removal set
    ops: _Ops = _parse_content_stream(page_obj, context="structural._replace_block_on_page")
    blocks = _find_bt_et_blocks(ops)
    removal_indices = _expand_to_bt_et(sorted(op_indices), blocks)
    removal_set = set(removal_indices)

    # ── Phase 2: Extend ALL palette fonts ─────────────────────────────
    try:
        font_ref = page_obj["/Resources"]["/Font"][font_key]
    except (KeyError, TypeError) as exc:
        raise OperatorError(f"Font {font_name} not found in page resources") from exc

    resolver = resolver_cache.get_resolver(page_obj, clean_name)
    encodable_text = "".join(ch for ch in new_text if ch >= " ")
    font_action_str = "kept"
    font_switched = False

    # CID font fallback for non-CID body font
    can_enc, missing = resolver.can_encode(encodable_text)
    if not can_enc and not resolver.is_cid_font:
        cid_alt = _find_cid_font_in_elements(matched_elems, page_obj, resolver_cache)
        if cid_alt is not None:
            alt_name, alt_size = cid_alt
            font_name = alt_name
            clean_name = font_name.lstrip("/")
            font_key = font_name if font_name.startswith("/") else f"/{font_name}"
            font_ref = page_obj["/Resources"]["/Font"][font_key]
            resolver = resolver_cache.get_resolver(page_obj, clean_name)
            if font_size_was_auto:
                font_size = alt_size
            can_enc, missing = resolver.can_encode(encodable_text)
            font_switched = True

    # Extend body font
    if not can_enc:
        if not resolver.is_cid_font:
            # F-C-05 / INV-J-9: must carry a font-affecting Degradation
            # so font_preserved correctly resolves to False.
            return (
                EditResult(
                    success=False,
                    original_text=original_text,
                    new_text=new_text,
                    font_action="failed",
                    warnings=[
                        "Font cannot encode text and no extensible "
                        "(Type0/Identity-H) font available in bbox"
                    ],
                    fidelity_report=FidelityReport(
                        font_substituted=None,
                        overflow_detected=False,
                        reflow_applied=False,
                        glyphs_missing=list(missing),
                        degradations=[
                            Degradation(
                                kind="font_extension_failed",
                                detail="non_cid_body_font",
                                severity="error",
                            ),
                        ],
                    ),
                ),
                0.0,
                bbox[3],
            )
        if not _extend_font(
            pdf,
            page_obj,
            clean_name,
            encodable_text,
            resolver_cache,
            substitution_log=substitution_log,
            coverage_tier_log=coverage_tier_log,
            stream_too_large=stl,
        ):
            # F-C-05 / INV-J-9: must carry a font-affecting Degradation
            # so font_preserved correctly resolves to False.
            degradations = [
                Degradation(
                    kind="font_extension_failed",
                    detail="_extend_font_returned_false",
                    severity="error",
                ),
            ]
            # A1.3 / INV-W-4: surface the specific-cause sibling when the
            # extension failed because an embedded stream was a Flate bomb.
            if stl:
                degradations.append(
                    Degradation(
                        kind="font_stream_too_large",
                        detail="font_stream_too_large",
                        severity="warning",
                    )
                )
            return (
                EditResult(
                    success=False,
                    original_text=original_text,
                    new_text=new_text,
                    font_action="failed",
                    warnings=[
                        f"Font extension failed for body font '{clean_name}' (missing: {missing!r})"
                    ],
                    fidelity_report=FidelityReport(
                        font_substituted=None,
                        overflow_detected=False,
                        reflow_applied=False,
                        glyphs_missing=list(missing),
                        degradations=degradations,
                    ),
                ),
                0.0,
                bbox[3],
            )
        resolver = resolver_cache.get_resolver(page_obj, clean_name)
        font_ref = page_obj["/Resources"]["/Font"][font_key]
        font_action_str = "extended"

    # Extend heading font (for the first line)
    first_line = new_text.split("\n", 1)[0] if new_text else ""
    first_line_clean = "".join(ch for ch in first_line if ch >= " ")
    if palette.heading_font and palette.heading_font != clean_name:
        if _extend_font(
            pdf,
            page_obj,
            palette.heading_font,
            first_line_clean,
            resolver_cache,
            substitution_log=substitution_log,
        ):
            if font_action_str == "kept":
                font_action_str = "extended"
        else:
            palette = _StylePalette(  # degrade: drop heading font
                heading_font=None,
                body_font=palette.body_font,
                body_size=palette.body_size,
                body_color=palette.body_color,
                marker_fonts=palette.marker_fonts,
                marker_x=palette.marker_x,
                body_after_marker_x=palette.body_after_marker_x,
            )

    # Extend marker fonts
    for char, mfont in list(palette.marker_fonts.items()):
        if mfont != clean_name and not _extend_font(
            pdf,
            page_obj,
            mfont,
            char,
            resolver_cache,
            substitution_log=substitution_log,
        ):
            del palette.marker_fonts[char]  # degrade: drop this marker

    # Build resolvers for ALL palette fonts
    extra_resolvers: dict[str, FontResolver] = {}
    all_font_names: set[str | None] = {palette.heading_font, *palette.marker_fonts.values()}
    for fn in all_font_names - {None, clean_name}:
        if fn is None:
            continue  # narrow for mypy; set difference should have removed None
        with contextlib.suppress(KeyError, TypeError):
            extra_resolvers[fn] = resolver_cache.get_resolver(
                page_obj,
                fn.lstrip("/"),
            )

    # ── Phase 3: Break text into lines (indent-aware) ────────────────
    bbox_width = bbox[2] - bbox[0]

    def wrap_at(size: float) -> list[str]:
        """Break ``new_text`` into wrapped lines at the given font size.

        Indent-aware: bullet lines wrap against the post-marker width, body
        lines against the full bbox width. Factored out of Phase 3 so the
        E.8 shrink-to-fit binary search (Phase 2.5) and the final Phase 3
        line build share ONE wrap implementation — the search predicate and
        the rendered output can never diverge. ``size``-independent inputs
        (``bbox_width``, ``palette``, ``resolver``, ``font_ref``) are closed
        over; only the font size varies.
        """
        if palette.body_after_marker_x > 0:
            # Indent-aware breaking: bullet lines have less width.
            marker_indent = palette.body_after_marker_x - bbox[0]
            indented_width = bbox_width - marker_indent

            # Join continuation segments back into their bullet paragraph.
            # Extracted text preserves original visual line breaks as \n,
            # splitting "• retailer —\ncovering 500+" into two segments.
            # Merging non-marker segments after a marker into the marker's
            # paragraph produces optimal reflow and correct indented width.
            raw_segments = new_text.split("\n")
            segments: list[str] = []
            for seg in raw_segments:
                stripped = seg.lstrip()
                if stripped[:1] in palette.marker_fonts:
                    segments.append(seg)
                elif segments and segments[-1].lstrip()[:1] in palette.marker_fonts:
                    # Continuation of previous bullet — join with space
                    segments[-1] = segments[-1].rstrip() + " " + seg.lstrip()
                else:
                    segments.append(seg)

            all_lines: list[str] = []
            for seg in segments:
                stripped = seg.lstrip()
                if stripped[:1] in palette.marker_fonts:
                    seg_lines = break_into_lines(
                        seg,
                        indented_width,
                        resolver,
                        font_ref,
                        size,
                    )
                else:
                    seg_lines = break_into_lines(
                        seg,
                        bbox_width,
                        resolver,
                        font_ref,
                        size,
                    )
                all_lines.extend(seg_lines)
            return all_lines if all_lines else [""]
        return break_into_lines(new_text, bbox_width, resolver, font_ref, size)

    # ── Phase 2.5: Opt-in shrink-to-fit font-size search (E.8) ────────
    # When fit="shrink", binary-search the body font size DOWN until the
    # wrapped text height fits the FIXED-height bbox region, with a
    # min_pt = max(4.0, original * 0.5) floor. Guarded so fit="none" (the
    # default) skips this block entirely — font_size is never reassigned,
    # every downstream value matches the pre-E.8 code, and the saved bytes
    # are byte-identical. font_size_reduced is emitted iff the applied size
    # ends strictly below the original (including the floor-clamp case).
    bbox_height_for_fit = bbox[3] - bbox[1]
    original_font_size = font_size
    fit_shrink_degradations: list[Degradation] = []
    if fit == "shrink":
        floor_pt = max(4.0, original_font_size * 0.5)

        def _line_height_for(size: float) -> float:
            # Hold a caller-passed line_height FIXED (the search trades only
            # font size); otherwise use the natural size * 1.2 leading that
            # _detect_line_height falls back to for a single-line region.
            return line_height if line_height is not None else size * 1.2

        def _fits(size: float) -> bool:
            return len(wrap_at(size)) * _line_height_for(size) <= bbox_height_for_fit

        if not _fits(original_font_size):
            # Overflows at the original size → search for the largest size in
            # [floor, original] that fits. Predicate is a monotone down-set
            # (smaller size → fewer/equal lines AND smaller leading → smaller
            # text height), so bisection for the largest fitting size is valid.
            if not _fits(floor_pt):
                # Even the floor cannot fit → clamp AT the floor. Overflow is
                # surfaced honestly + separately by the existing machinery
                # below (it runs on the floor-sized wrap).
                font_size = floor_pt
            else:
                lo, hi = floor_pt, original_font_size
                best_fit = floor_pt
                for _ in range(24):
                    if hi - lo <= 0.05:
                        break
                    mid = (lo + hi) / 2.0
                    if _fits(mid):
                        best_fit = mid
                        lo = mid
                    else:
                        hi = mid
                font_size = round(best_fit, 2)
        # else: already fits at original → no shrink, font_size unchanged.

        if font_size < original_font_size - 1e-6:
            fit_shrink_degradations.append(
                Degradation(
                    kind="font_size_reduced",
                    detail=(
                        f"original={original_font_size:.2f},applied={font_size:.2f},"
                        f"floor={floor_pt:.2f},fit={bool(_fits(font_size))}"
                    ),
                    severity="info",
                )
            )

    lines = wrap_at(font_size)

    # ── Phase 4: Layout ──────────────────────────────────────────────
    caller_line_height = line_height is not None
    if not caller_line_height:
        line_height = _detect_line_height(matched_elems, font_size)
    assert line_height is not None  # narrowed by the branch above
    bbox_height = bbox[3] - bbox[1]
    text_height = len(lines) * line_height
    overflow_delta = text_height - bbox_height

    shift_warnings: list[str] = []
    # v0.1.3 Phase 6: emit-at-source overflow_shift Degradations parallel
    # to shift_warnings (INV-J-3 backward-compat preserved; v0.2 collapses).
    shift_degradations: list[Degradation] = []
    original_overflow = overflow_delta

    # In sequential mode (skip_vertical_shift), the batch caller handles
    # all vertical shifts at the end.  Bboxes are for removal only; text
    # is positioned via first_line_y_override.  Skipping per-section
    # shifts keeps content at original positions so subsequent bboxes
    # still find the right elements to remove.
    if not skip_vertical_shift and overflow_delta > 0:
        mediabox = page_obj.get("/MediaBox")
        if mediabox is not None:
            page_bottom = float(mediabox[1])
            below_ys = [e.bbox[1] for e in elements if e.bbox[1] < bbox[1]]
            if below_ys:
                lowest_y = min(below_ys)
                max_safe_shift = lowest_y - page_bottom
                if max_safe_shift <= 0:
                    # No room below — shift fully suppressed (silent in
                    # warnings list pre-v0.1.3; surfaced via Degradation).
                    shift_degradations.append(
                        Degradation(
                            kind="overflow_shift_suppressed",
                            detail=f"requested={original_overflow:.1f}pt,available=0pt",
                            severity="warning",
                        )
                    )
                    overflow_delta = 0.0
                elif overflow_delta > max_safe_shift:
                    shift_warnings.append(
                        f"Overflow shift clamped from {original_overflow:.1f}pt "
                        f"to {max_safe_shift:.1f}pt to keep content on-page",
                    )
                    shift_degradations.append(
                        Degradation(
                            kind="overflow_shift_clamped",
                            detail=f"requested={original_overflow:.1f}pt,clamped_to={max_safe_shift:.1f}pt",
                            severity="warning",
                        )
                    )
                    overflow_delta = max_safe_shift
            else:
                overflow_delta = 0.0

    if not skip_vertical_shift and overflow_delta > 0:
        shift_warnings.extend(
            _shift_content_below_inplace(
                pdf,
                page_obj,
                page_number,
                bbox[1],
                overflow_delta,
            )
        )
        ops = _parse_content_stream(page_obj, context="structural._replace_block_on_page")
        blocks = _find_bt_et_blocks(ops)
        removal_indices = _expand_to_bt_et(sorted(op_indices), blocks)
        removal_set = set(removal_indices)

    # If text still exceeds available space after clamped shift,
    # compress line_height so all lines fit without overlapping
    # content below.  Skip when the caller provided an explicit
    # line_height (they already computed uniform spacing).
    #
    # E.6 (v0.2.0): line_height_compressed honesty surfacing. When the
    # applied line height is pushed below the natural single-line ratio
    # (the value _detect_line_height produced), surface a typed
    # line_height_compressed Degradation from this caller. This does NOT
    # touch compute_uniform_layout's purity (INV-F-5) — natural and applied
    # are both locals here. Computed pre-save so dry_run parity holds.
    compression_degradations: list[Degradation] = []
    if not caller_line_height:
        natural_line_height = line_height  # from _detect_line_height (line 1179)
        available_height = bbox_height + max(0.0, overflow_delta)
        if text_height > available_height and len(lines) > 1:
            line_height = available_height / len(lines)
        text_height = available_height  # recalculate for underflow check
        # 0.95 == the same 5% deadzone the kerning Algo-A decision uses.
        if line_height < natural_line_height * 0.95:
            ratio = line_height / natural_line_height if natural_line_height else 0.0
            compression_degradations.append(
                Degradation(
                    kind="line_height_compressed",
                    detail=(
                        f"applied={line_height:.2f},natural={natural_line_height:.2f},"
                        f"ratio={ratio:.2f}"
                    ),
                    severity="info",
                )
            )

    # ── Phase 5: Render ──────────────────────────────────────────────
    actual_first_y = (
        first_line_y_override if first_line_y_override is not None else bbox[3] - font_size
    )
    replacement = _build_replacement_ops(
        lines=lines,
        font_name=clean_name,
        font_size=font_size,
        fill_color=fill_color,
        left_margin=bbox[0],
        first_line_y=actual_first_y,
        line_height=line_height,
        resolver=resolver,
        page=page_obj,
        style_palette=palette,
        extra_resolvers=extra_resolvers,
    )

    # ── Phase 6: Write ───────────────────────────────────────────────
    insert_pos = min(removal_set)
    new_ops: _Ops = []
    inserted = False
    for i, op in enumerate(ops):
        if i == insert_pos and not inserted:
            new_ops.extend(replacement)
            inserted = True
        if i not in removal_set:
            new_ops.append(op)

    new_stream = _unparse_content_stream(new_ops, context="structural._replace_block_on_page")
    page_obj.Contents = pdf.make_stream(new_stream)

    # Sync annotations: shift link rects to match new text position.
    # Orphan-removal also runs at the public-API entry points
    # (replace_block / batch_replace_block / delete_block) so even the
    # early-return branches in _replace_block_on_page (e.g. empty bbox)
    # still clean up dangling links.
    delta_y = actual_first_y - (bbox[3] - font_size)
    if abs(delta_y) > 0.5:
        _sync_annotations_in_bbox(page_obj, bbox, delta_y, new_text)

    # Post-splice underflow collapse: shift content below bbox UP.
    # Skipped in sequential mode (skip_vertical_shift) — the batch
    # caller handles one net shift at the end for the whole region.
    if not skip_vertical_shift and overflow_delta < 0:
        shift_warnings.extend(
            _shift_content_below_inplace(
                pdf,
                page_obj,
                page_number,
                bbox[1],
                overflow_delta,
            )
        )

    last_line_y = actual_first_y - max(0, len(lines) - 1) * line_height
    effective_delta = 0.0 if skip_vertical_shift else overflow_delta

    # INV-C-4: prefer the metric-equivalent substitution name (e.g.
    # "Carlito-Regular" when the system lacks Calibri) over the
    # CID-fallback alternative. Both are real substitutions, but the
    # metric-equivalent one signals a *fidelity* concern (different
    # outlines from the original); CID fallback merely picks a font
    # already shown on the page.
    substituted_name = (
        substitution_log[0] if substitution_log else (clean_name if font_switched else None)
    )

    # v0.1.3 (Phase 5) emit coverage Degradations from any extend_subset
    # events captured in coverage_tier_log. Pre-extension missing chars
    # populate glyphs_missing (audit-bundle finding #3).
    coverage_degradations: list[Degradation] = []
    pre_extension_missing: list[str] = []
    for tier, missing_chars in coverage_tier_log:
        pre_extension_missing.extend(missing_chars)
        chars_str = ",".join(missing_chars)
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

    result = EditResult(
        success=True,
        original_text=original_text,
        new_text=new_text,
        font_action=font_action_str,  # type: ignore[arg-type]
        warnings=shift_warnings,
        fidelity_report=FidelityReport(
            # font_preserved is now a computed @property on FidelityReport
            # (INV-J-8). It returns True iff font_substituted is None AND no
            # FONT_AFFECTING_KINDS Degradation was emitted. The pre-v0.1.3
            # expression here lied about font_action_str=="extended" success
            # (returned False even though extension preserves font identity);
            # the computed property fixes that. font_switched populates
            # substituted_name above (line 1252 fallback), so the new property
            # still detects body-font swaps via font_substituted is not None.
            font_substituted=substituted_name,
            overflow_detected=original_overflow > 0,
            reflow_applied=True,
            glyphs_missing=pre_extension_missing,
            degradations=[
                *coverage_degradations,
                *shift_degradations,
                *compression_degradations,
                *fit_shrink_degradations,
            ],
        ),
    )
    return result, effective_delta, last_line_y


def replace_block(
    pdf_path: str,
    page_number: int,
    bbox: tuple[float, float, float, float],
    new_text: str,
    output_path: str,
    font_name: str | None = None,
    font_size: float | None = None,
    line_height: float | None = None,
    *,
    fit: Literal["none", "shrink"] = "none",
    password: str | bytes | None = None,
) -> EditResult:
    """Replace all content within a bounding box with new reflowed text.

    Identifies all content stream operators within the bbox, removes them,
    and writes new text using the same (or specified) font and size.
    When replacement text overflows the bbox vertically, content below
    is automatically shifted downward to prevent interleaving.

    Args:
        pdf_path: Path to the input PDF.
        page_number: 0-indexed page number.
        bbox: Target region (x0, y0, x1, y1) in PDF coordinates.
        new_text: Replacement text.
        output_path: Path for the output PDF.
        font_name: Font resource name (e.g. 'F1').  Auto-detected if None.
        font_size: Font size in points.  Auto-detected if None.
        fit: Opt-in shrink-to-fit policy (E.8). ``"none"`` (the default)
            reproduces the legacy behaviour byte-for-byte — the font size is
            never reduced. ``"shrink"`` binary-searches the body font size
            DOWN until the replacement fits the FIXED-height bbox region,
            with a ``min_pt = max(4.0, original * 0.5)`` floor; emits a typed
            ``font_size_reduced`` (severity ``info``) Degradation whenever it
            shrinks. The clamp at the floor still surfaces overflow honestly.
        password: Optional password to open an encrypted input; the output is
            RE-ENCRYPTED with the same password (A2.3 / INV-W-5).

    Returns:
        EditResult with fidelity information.
    """
    validate_output_path(output_path)

    pdf = open_pdf(pdf_path, password=password)
    try:
        # Per-call cache (ARY-283); pdf threaded for ARY-349 cache key.
        resolver_cache = FontResolverCache()

        pages = _resolve_pages(pdf, page_number)
        _, page_obj = pages[0]
        result, _, _ = _replace_block_on_page(
            pdf,
            page_obj,
            page_number,
            bbox,
            new_text,
            resolver_cache,
            font_name,
            font_size,
            line_height=line_height,
            fit=fit,
        )
        # INV-W0-7: orphan-annotation cleanup must run regardless of
        # whether _replace_block_on_page found text to surgery. A bbox
        # may contain only annotations (no content stream text), and
        # those still go orphan when the user asks us to replace the
        # region with unrelated text.
        _remove_orphaned_annotations(page_obj, bbox, new_text)
        lin_log: list[str] = []
        enc_log: list[str] = []
        _save_pdf(
            pdf,
            output_path,
            linearization_log=lin_log,
            reencrypt_password=password,
            encryption_log=enc_log,
        )
        _surface_linearization_dropped(result, lin_log)
        _surface_encryption_dropped(result, enc_log)
        _invalidate_locator_cache()
        return result
    finally:
        pdf.close()


def batch_replace_block(
    pdf_path: str,
    page_number: int,
    replacements: list[tuple[tuple[float, float, float, float], str]],
    output_path: str,
    *,
    line_height: float | None = None,
    section_gap: float | None = None,
    fit: Literal["none", "shrink"] = "none",
    password: str | bytes | None = None,
) -> list[EditResult]:
    """Apply multiple bbox-based text replacements on a single page.

    Processes replacements top-to-bottom (highest y1 first).

    **Default mode** (no ``section_gap``): Each replacement's text is
    anchored to the top of its bbox.  Cumulative vertical shifts track
    overflow/underflow between replacements.

    **Sequential mode** (``section_gap`` provided with ``line_height``):
    Bboxes define what to *remove* only.  Text flows sequentially from
    the top of the region: each section starts at the previous section's
    last line minus ``section_gap``.  A single net vertical shift at the
    end adjusts content below the region.  This mode is designed for
    use with :func:`compute_uniform_layout`, which returns both
    ``line_height`` and ``section_gap`` as a coordinated pair.

    Args:
        pdf_path: Path to the input PDF.
        page_number: 0-indexed page number.
        replacements: List of (bbox, new_text) tuples.  Each bbox is
            (x0, y0, x1, y1) in PDF coordinates.
        output_path: Path for the output PDF.
        line_height: Uniform line spacing (optional).
        section_gap: Gap between sections in sequential mode (optional).
            Only effective when ``line_height`` is also provided.
        fit: Opt-in shrink-to-fit policy (E.8). ``"none"`` (the default)
            reproduces the legacy behaviour byte-for-byte. ``"shrink"``
            applies the per-section font-size shrink-to-fit ONLY in the
            DEFAULT (bbox-anchored) mode. In SEQUENTIAL mode
            (``section_gap`` + ``line_height``) it is advisory/no-op:
            ``compute_uniform_layout`` already owns vertical fit and a
            per-section shrink would fight its uniform line-height + cursor
            math.
        password: Optional password to open an encrypted input; the output is
            RE-ENCRYPTED with the same password (A2.3 / INV-W-5).

    Returns:
        List of EditResult, one per replacement (same order as input).
    """
    validate_output_path(output_path)
    if not replacements:
        return []

    pdf = open_pdf(pdf_path, password=password)
    try:
        # Per-call cache (ARY-283); pdf threaded for ARY-349 cache key.
        resolver_cache = FontResolverCache()

        pages = _resolve_pages(pdf, page_number)
        _, page_obj = pages[0]

        # Auto-layout: when multiple replacements are given without explicit
        # layout params, analyze the original content and compute optimal
        # spacing automatically.  This is the "brain" — the caller provides
        # only bboxes and text, the engine figures out the rest.
        #
        # E.6 (v0.2.0): the auto-layout caller is the only site that knows
        # both the computed (applied) line height and the natural single-line
        # ratio for the batch path (per-section _replace_block_on_page runs
        # with caller_line_height=True and so skips its own compression
        # branch). When the auto-computed line height fell below the natural
        # ratio, surface a line_height_compressed Degradation on every result.
        batch_natural_line_height: float | None = None
        if line_height is None and len(replacements) > 1:
            line_height, section_gap, batch_natural_line_height = _auto_compute_layout(
                page_obj,
                page_number,
                replacements,
                resolver_cache,
            )

        # Sort by y1 descending (topmost first) while preserving input order
        # for result mapping.
        indexed = list(enumerate(replacements))
        indexed.sort(key=lambda t: t[1][0][3], reverse=True)

        results: list[tuple[int, EditResult]] = []
        sequential = section_gap is not None and line_height is not None

        if sequential:
            # ── Sequential mode ──────────────────────────────────
            # Bboxes = removal only.  Text positioned by layout algorithm.
            assert section_gap is not None and line_height is not None
            prev_last_line_y: float | None = None

            for orig_idx, (bbox, new_text) in indexed:
                ffly = None if prev_last_line_y is None else prev_last_line_y - section_gap
                result, _delta, last_y = _replace_block_on_page(
                    pdf,
                    page_obj,
                    page_number,
                    bbox,
                    new_text,
                    resolver_cache,
                    line_height=line_height,
                    first_line_y_override=ffly,
                    skip_vertical_shift=True,
                )
                _invalidate_locator_cache()
                # Only update the running cursor on success — the failure
                # branches return last_y=bbox[3] (top of the failed region)
                # as a placeholder, which would mis-position subsequent
                # sections in sequential mode if propagated.
                if result.success:
                    prev_last_line_y = last_y
                results.append((orig_idx, result))
                # INV-W0-7: orphan-annotation cleanup per bbox.
                _remove_orphaned_annotations(page_obj, bbox, new_text)

            # Net shift: adjust content below the region.
            # Measure the ACTUAL gap from the last rendered line to the
            # first content element below the region.  If this gap exceeds
            # section_gap, shift content up to close the excess — keeping
            # the trailing gap proportional to inter-section gaps.
            region_bottom = min(bb[1] for bb, _ in replacements)
            if prev_last_line_y is not None:
                if prev_last_line_y < region_bottom:
                    # Overflow: text extends below region → shift down
                    _shift_content_below_inplace(
                        pdf,
                        page_obj,
                        page_number,
                        region_bottom,
                        region_bottom - prev_last_line_y,
                    )
                else:
                    # Measure actual gap to first content below region
                    _invalidate_locator_cache()
                    below_elems = [
                        e
                        for e in _build_index(page_obj, page_number)
                        if e.type == "text" and e.bbox[3] < region_bottom
                    ]
                    if below_elems and prev_last_line_y is not None:
                        next_y = max(e.bbox[3] for e in below_elems)
                        actual_gap = prev_last_line_y - next_y
                        if actual_gap > section_gap:
                            collapse = actual_gap - section_gap
                            _shift_content_below_inplace(
                                pdf,
                                page_obj,
                                page_number,
                                region_bottom,
                                -collapse,  # negative = shift up
                            )
        else:
            # ── Default mode (bbox-anchored) ─────────────────────
            cumulative_shift = 0.0

            for orig_idx, (bbox, new_text) in indexed:
                adjusted_bbox = (
                    bbox[0],
                    bbox[1] - cumulative_shift,
                    bbox[2],
                    bbox[3] - cumulative_shift,
                )
                result, overflow, _last_y = _replace_block_on_page(
                    pdf,
                    page_obj,
                    page_number,
                    adjusted_bbox,
                    new_text,
                    resolver_cache,
                    line_height=line_height,
                    fit=fit,
                )
                _invalidate_locator_cache()
                cumulative_shift += overflow
                results.append((orig_idx, result))
                # INV-W0-7: clean up orphan annotations regardless of
                # whether the per-bbox surgery hit content.
                _remove_orphaned_annotations(page_obj, adjusted_bbox, new_text)

        # E.6: surface the batch-path line-height compression on each result.
        # 0.95 == the same 5% deadzone the kerning Algo-A decision uses.
        if (
            batch_natural_line_height is not None
            and line_height is not None
            and line_height < batch_natural_line_height * 0.95
        ):
            ratio = line_height / batch_natural_line_height if batch_natural_line_height else 0.0
            for _, res in results:
                if res.success:
                    res.fidelity_report.degradations.append(
                        Degradation(
                            kind="line_height_compressed",
                            detail=(
                                f"applied={line_height:.2f},"
                                f"natural={batch_natural_line_height:.2f},ratio={ratio:.2f}"
                            ),
                            severity="info",
                        )
                    )

        lin_log: list[str] = []
        enc_log: list[str] = []
        _save_pdf(
            pdf,
            output_path,
            linearization_log=lin_log,
            reencrypt_password=password,
            encryption_log=enc_log,
        )
        _invalidate_locator_cache()

        # Return results in original input order
        results.sort(key=lambda t: t[0])
        ordered = [r for _, r in results]
        _surface_linearization_dropped(ordered, lin_log)
        _surface_encryption_dropped(ordered, enc_log)
        return ordered
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
    password: str | bytes | None = None,
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
        password: Optional password to open an encrypted input; the output is
            RE-ENCRYPTED with the same password (A2.3 / INV-W-5).

    Returns:
        EditResult with fidelity information.
    """
    validate_output_path(output_path)

    # CRIT-1: capture extension events for honest FidelityReport surfacing.
    # Mirrors the canonical pattern in surgeon._apply_single_replacement
    # (surgeon.py:579-657, 905-921) so insert_text_block produces the same
    # shape FidelityReport when Tier 1.5 metric-equivalent substitution runs.
    substitution_log: list[str] = []
    coverage_degradations: list[Degradation] = []
    pre_extension_missing: list[str] = []
    font_action: Literal["kept", "extended", "substituted", "failed"] = "kept"

    pdf = open_pdf(pdf_path, password=password)
    try:
        # Per-call cache (ARY-283); pdf threaded for ARY-349 cache key.
        resolver_cache = FontResolverCache()

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
            raise OperatorError(f"Font {font_name} not found in page resources") from exc

        resolver = resolver_cache.get_resolver(page_obj, clean_name)

        # Check encodability — extend font if needed
        can_enc, missing = resolver.can_encode(text)
        if not can_enc:
            try:
                from pdf_edit_engine.fonts import extend_subset

                tier = extend_subset(
                    pdf,
                    page_obj,
                    clean_name,
                    "".join(missing),
                    substitution_log=substitution_log,
                )
                font_action = "extended"
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
                resolver_cache.evict(page_obj, clean_name)
                resolver = resolver_cache.get_resolver(page_obj, clean_name)
            except _FONT_EXTEND_FAIL_EXCS as exc:
                # F-C-03 / INV-W0-9: drop {exc} from the user-visible
                # warnings list and Degradation.detail; the type name is
                # the stable signal across upstream version drift, and the
                # full traceback survives in this logger.warning.
                logger.warning(
                    "Font extension failed for insert (clean_name=%s missing=%r)",
                    clean_name,
                    missing,
                    exc_info=True,
                )
                # CRIT-1 expansion: failure path also surfaces a typed
                # Degradation. Pre-fix this branch returned an EditResult
                # without fidelity_report, so the default-factory
                # FidelityReport reported font_preserved=True even on
                # extension failure (the same lying-success-path shape).
                # A1.3 / INV-W-4: a Flate decompression bomb surfaces a
                # SECOND, specific-cause font_stream_too_large Degradation
                # (warning) alongside the font_extension_failed (error).
                degs = [
                    Degradation(
                        kind="font_extension_failed",
                        detail=type(exc).__name__,
                        severity="error",
                    )
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
                    original_text="",
                    new_text=text,
                    font_action="failed",
                    warnings=[
                        f"Font extension failed for '{clean_name}' "
                        f"(missing: {missing!r}): {type(exc).__name__}"
                    ],
                    fidelity_report=FidelityReport(
                        font_substituted=None,
                        overflow_detected=False,
                        reflow_applied=False,
                        glyphs_missing=list(missing),
                        degradations=degs,
                    ),
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
            pdf,
            page_obj,
            page_number,
            y + 0.5,
            text_height,
        )

        # Re-parse content stream after shift
        ops: _Ops = _parse_content_stream(page_obj, context="structural.insert_text_block")

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
            page=page_obj,
        )

        # Append to end of content stream
        ops.extend(new_block)

        # Write back
        new_stream = _unparse_content_stream(ops, context="structural.insert_text_block")
        page_obj.Contents = pdf.make_stream(new_stream)
        lin_log: list[str] = []
        enc_log: list[str] = []
        _save_pdf(
            pdf,
            output_path,
            linearization_log=lin_log,
            reencrypt_password=password,
            encryption_log=enc_log,
        )
        _invalidate_locator_cache()

        overflow = any("below page boundary" in w for w in shift_warnings)
        result = EditResult(
            success=True,
            original_text="",
            new_text=text,
            font_action=font_action,
            warnings=shift_warnings,
            fidelity_report=FidelityReport(
                font_substituted=substitution_log[0] if substitution_log else None,
                overflow_detected=overflow,
                reflow_applied=True,
                glyphs_missing=pre_extension_missing,
                degradations=list(coverage_degradations),
            ),
        )
        _surface_linearization_dropped(result, lin_log)
        _surface_encryption_dropped(result, enc_log)
        return result
    finally:
        pdf.close()


def delete_block(
    pdf_path: str,
    page_number: int,
    bbox: tuple[float, float, float, float],
    output_path: str,
    *,
    close_gap: bool = True,
    password: str | bytes | None = None,
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
        password: Optional password to open an encrypted input; the output is
            RE-ENCRYPTED with the same password (A2.3 / INV-W-5).

    Returns:
        EditResult with fidelity information.
    """
    validate_output_path(output_path)
    pdf = open_pdf(pdf_path, password=password)
    try:
        pages = _resolve_pages(pdf, page_number)
        _, page_obj = pages[0]

        # Build element index and find elements in bbox
        elements = _build_index(page_obj, page_number)
        matched_elems, op_indices = _collect_elements_in_bbox(elements, bbox)

        if not matched_elems:
            # Genuinely empty region (the strict-AABB collection captured no op
            # AND there is nothing to delete): keep the pre-B.11 success
            # contract byte-for-byte. (Residue is proven on the matched-elems
            # path below by comparing the COLLECTED targets against the
            # post-emptying bbox region — a target-specific check that does not
            # over-fire on unrelated text merely touching the bbox boundary.)
            lin_log: list[str] = []
            enc_log: list[str] = []
            _save_pdf(
                pdf,
                output_path,
                linearization_log=lin_log,
                reencrypt_password=password,
                encryption_log=enc_log,
            )
            empty_result = EditResult(
                success=True,
                original_text="",
                new_text="",
                font_action="kept",
                warnings=["No content found in specified bounding box"],
            )
            _surface_linearization_dropped(empty_result, lin_log)
            _surface_encryption_dropped(empty_result, enc_log)
            return empty_result

        # Collect original text
        original_parts: list[str] = []
        for elem in matched_elems:
            if elem.text_content:
                original_parts.append(elem.text_content)
        original_text = " ".join(original_parts)

        # Parse content stream and compute removal set
        ops: _Ops = _parse_content_stream(page_obj, context="structural.delete_block")
        blocks = _find_bt_et_blocks(ops)
        removal_indices = _expand_to_bt_et(sorted(op_indices), blocks)
        removal_set = set(removal_indices)

        # B.11 / INV-B-10: advisory inline-image signal (computed pre-mutation
        # over the deletion's op-index span). A1.4: the BI/ID/EI slot is stable,
        # so the deletion still proceeds.
        inline_image_present = _inline_image_in_indices(ops, removal_set)

        # B.11 / INV-B-10: KEEP-SLOT EMPTYING instead of tuple removal. Emptying
        # the show-text operands in place (and leaving every other op untouched)
        # holds the ops-list length and all downstream operator_index values
        # stable — the pre-B.11 list-comp filter renumbered every later op and
        # corrupted sibling operator_refs in a batch. Non-show-text ops in the
        # removal_set (BT/ET/Tf/Tm/Td) stay in place; they render nothing on
        # their own once the show-text operands are empty.
        _empty_show_text_in_place(ops, removal_set)

        # Write back content stream
        new_stream = _unparse_content_stream(ops, context="structural.delete_block")
        page_obj.Contents = pdf.make_stream(new_stream)

        # B.11 / INV-B-10: prove residue as a MISSED CO-RENDERED OP, not a
        # token re-appearing in the re-extracted region. Re-index the
        # just-written page and look for an un-emptied show-text op that (a)
        # overlaps the target bbox within tolerance AND (b) renders the EXACT
        # text of a COLLECTED target element — i.e. a co-rendered duplicate of
        # the deletion target that the strict-AABB collection missed (the
        # ``< vs <=`` boundary case). The prior check fired on any >=2-char
        # COLLECTED-target TOKEN re-appearing anywhere in the ~2pt-tolerant
        # region, which over-fired when an ADJACENT untouched line merely SHARED
        # a word (e.g. a body line sharing the heading's leading token): the
        # tolerance pulled the neighbour in and the shared token falsely flipped
        # success=False even though the deletion was correct. Matching the FULL
        # collected-target text (a missed duplicate), not a shared sub-token,
        # ignores an adjacent DIFFERENT line. Computed BEFORE the close_gap
        # shift so pulled-up content cannot masquerade as residue.
        _invalidate_locator_cache()
        post_elements = _build_index(page_obj, page_number)
        residue_proven = _missed_co_rendered_residue(
            post_elements, bbox, matched_elems, removal_set
        )

        # Delete annotations overlapping the bbox
        annots_key = pikepdf.Name("/Annots")
        if annots_key in page_obj:
            try:
                annots: list[Any] = list(page_obj[annots_key])  # type: ignore[call-overload]
                kept: list[Any] = []
                for annot_ref in annots:
                    annot = annot_ref.resolve() if hasattr(annot_ref, "resolve") else annot_ref
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
                    page_obj[annots_key] = pdf.make_indirect(pikepdf.Array(kept))
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
                pdf,
                page_obj,
                page_number,
                bbox[1],
                -deleted_height,
            )

        main_lin_log: list[str] = []
        main_enc_log: list[str] = []
        _save_pdf(
            pdf,
            output_path,
            linearization_log=main_lin_log,
            reencrypt_password=password,
            encryption_log=main_enc_log,
        )
        _invalidate_locator_cache()

        overflow = any("below page boundary" in w for w in warnings)
        # IMP-2: emit a typed Degradation when overflow is detected.
        # NOTE: this branch is currently unreachable from delete_block —
        # _shift_content_below_inplace's "below page boundary" warning
        # only fires for positive delta_y (content moving down), but
        # delete_block always passes delta_y = -deleted_height
        # (negative; content moving up to close the gap). The
        # Degradation is wired defensively in case a future caller
        # passes positive delta_y or the helper's overflow check is
        # extended; the corresponding regression-guard test asserts the
        # negative case (overflow=False ⇒ no overflow_shift_*
        # Degradation) so a regression to false-positive emission is
        # caught.
        overflow_degradations: list[Degradation] = []
        if overflow:
            overflow_degradations.append(
                Degradation(
                    kind="overflow_shift_clamped",
                    detail="vertical,defensive_unreachable_at_v0_1_3",
                    severity="warning",
                )
            )
        # B.11 / INV-B-10: deletion-cleanup surfacing. Provable residue flips
        # success=False with a deletion_residual_text (warning); an inline image
        # in the deletion span is an advisory inline_image_present (info) that
        # does NOT fail the edit (A1.4 stable slot). Both kinds are NOT in
        # FONT_AFFECTING_KINDS, so font_preserved stays True.
        deletion_degradations: list[Degradation] = []
        delete_warnings = list(warnings)
        success = True
        if residue_proven:
            success = False
            delete_warnings.append("Deletion left residual text in the bounding box region")
            deletion_degradations.append(
                Degradation(
                    kind="deletion_residual_text",
                    detail=(
                        "a co-rendered show-text op overlapping the bbox was missed; "
                        f"target {original_text!r} still renders in the region"
                    ),
                    severity="warning",
                )
            )
        if inline_image_present:
            deletion_degradations.append(
                Degradation(
                    kind="inline_image_present",
                    detail=f"inline image in deletion span {sorted(removal_set)[:1]}",
                    severity="info",
                )
            )

        delete_result = EditResult(
            success=success,
            original_text=original_text,
            new_text="",
            font_action="kept",
            warnings=delete_warnings,
            fidelity_report=FidelityReport(
                font_substituted=None,
                overflow_detected=overflow,
                reflow_applied=False,
                glyphs_missing=[],
                degradations=[*overflow_degradations, *deletion_degradations],
            ),
        )
        _surface_linearization_dropped(delete_result, main_lin_log)
        _surface_encryption_dropped(delete_result, main_enc_log)
        return delete_result
    finally:
        pdf.close()
