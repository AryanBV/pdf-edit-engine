"""OperatorSurgeon module — modify PDF content stream operators."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import pikepdf

from pdf_edit_engine._pathutil import validate_output_path
from pdf_edit_engine.encoding import FontResolver, FontResolverCache
from pdf_edit_engine.errors import FontNotFoundError, OperatorError, PDFEditError
from pdf_edit_engine.models import (
    Edit,
    EditResult,
    FidelityReport,
    TextCharacter,
    TextMatch,
)
from pdf_edit_engine.widths import GlyphWidthCache

logger = logging.getLogger(__name__)

# Parsed content stream ops: list of ContentStreamInstruction, but we also
# replace entries with (operands, operator) tuples during surgery.
# Using Any avoids fighting pikepdf's pybind11 typing.
_Ops = list[Any]


# ── Private helpers ──────────────────────────────────────────────────────


def _get_font_resolver(page: pikepdf.Page, font_name: str) -> FontResolver:
    """Build a FontResolver for the given font on the page."""
    cache = FontResolverCache()
    return cache.get_resolver(page, font_name)


def _nth_string_index(tj_items: list[object], frag_idx: int) -> int:
    """Map sequential fragment index to actual TJ array position.

    Args:
        tj_items: Items from the TJ array operand.
        frag_idx: Sequential count of string elements (0, 1, 2...).

    Returns:
        The actual array index of the frag_idx-th string element.
    """
    count = 0
    for i, item in enumerate(tj_items):
        if isinstance(item, pikepdf.String):
            if count == frag_idx:
                return i
            count += 1
    msg = f"Fragment index {frag_idx} not found in TJ array with {count} strings"
    raise OperatorError(msg)


def _splice_bytes(
    raw: bytes,
    replacements: list[tuple[int, bytes]],
    byte_width: int,
) -> bytes:
    """Splice replacement bytes into raw string data at given positions.

    Args:
        raw: Original raw bytes from the string operand.
        replacements: List of (byte_position, new_bytes) pairs.
        byte_width: Bytes per character (1 for WinAnsi, 2 for CIDFont).

    Returns:
        New raw bytes with replacements applied.
    """
    buf = bytearray(raw)
    for pos, new_bytes in replacements:
        buf[pos : pos + byte_width] = new_bytes
    return bytes(buf)


def _rebuild_tj_array(
    tj_items: list[object],
    match_chars: list[TextCharacter],
    encoded_replacement: bytes,
) -> pikepdf.Array:
    """Rebuild a TJ array replacing the matched span with new encoded bytes.

    Fragments before the match are preserved. The matched span becomes a
    single new string element. Fragments after the match are preserved.
    Kerning within the match is dropped; kerning outside is kept.

    Args:
        tj_items: Original TJ array items.
        match_chars: Characters from the match that fall in this operator.
        encoded_replacement: Pre-encoded replacement bytes.

    Returns:
        New pikepdf.Array for the TJ operand.
    """
    frag_indices = {ch.tj_fragment_index for ch in match_chars if ch.tj_fragment_index is not None}
    if not frag_indices:
        return pikepdf.Array(tj_items)

    min_frag = min(frag_indices)
    max_frag = max(frag_indices)

    # Find actual array positions for the fragment range
    min_arr_idx = _nth_string_index(tj_items, min_frag)
    max_arr_idx = _nth_string_index(tj_items, max_frag)

    # Compute partial prefix/suffix for boundary fragments
    chars_by_frag: dict[int, list[TextCharacter]] = defaultdict(list)
    for ch in match_chars:
        if ch.tj_fragment_index is not None:
            chars_by_frag[ch.tj_fragment_index].append(ch)

    # Prefix: bytes in the first fragment before the match
    first_frag_raw = bytes(tj_items[min_arr_idx])  # type: ignore[call-overload]
    first_match_start = min(ch.byte_position for ch in chars_by_frag[min_frag])
    prefix_bytes = first_frag_raw[:first_match_start] if first_match_start > 0 else b""

    # Suffix: bytes in the last fragment after the match
    last_frag_raw = bytes(tj_items[max_arr_idx])  # type: ignore[call-overload]
    last_chars = chars_by_frag[max_frag]
    # Determine byte_width from character data
    if len(last_frag_raw) > 0 and last_chars:
        inferred_bw = _infer_byte_width(last_frag_raw, last_chars)
        max_byte_end = max(ch.byte_position for ch in last_chars) + inferred_bw
    else:
        max_byte_end = len(last_frag_raw)
    suffix_bytes = last_frag_raw[max_byte_end:] if max_byte_end < len(last_frag_raw) else b""

    # Build new array
    new_items: list[object] = []

    # Elements before the match span
    for item in tj_items[:min_arr_idx]:
        new_items.append(item)

    # Add prefix if present
    if prefix_bytes:
        new_items.append(pikepdf.String(prefix_bytes))

    # Add the replacement string
    if encoded_replacement:
        new_items.append(pikepdf.String(encoded_replacement))

    # Add suffix if present
    if suffix_bytes:
        new_items.append(pikepdf.String(suffix_bytes))

    # Elements after the match span
    for item in tj_items[max_arr_idx + 1 :]:
        new_items.append(item)

    return pikepdf.Array(new_items)


def _infer_byte_width(raw: bytes, chars: list[TextCharacter]) -> int:
    """Infer byte width from character positions in a fragment."""
    if len(chars) < 2:
        # Single char — check if fragment is 2 bytes (CID) or 1 byte
        if len(raw) >= 2 and chars[0].byte_position == 0:
            # Could be 1 or 2 bytes. Use the fragment length / char count heuristic.
            return len(raw) // max(len(chars), 1)
        return 1
    # Use distance between consecutive byte_positions
    positions = sorted(ch.byte_position for ch in chars)
    if len(positions) >= 2:
        return positions[1] - positions[0]
    return 1


def _calculate_new_width(
    new_text: str,
    page: pikepdf.Page,
    font_name: str,
    font_size: float,
    resolver: FontResolver,
    width_cache: GlyphWidthCache,
) -> float:
    """Calculate total width of replacement text in page space.

    Args:
        new_text: The replacement text.
        page: PDF page for width lookup.
        font_name: Font resource name.
        font_size: Font size in points.
        resolver: FontResolver for encoding characters.
        width_cache: Glyph width cache.

    Returns:
        Total width in page-space units.
    """
    total = 0.0
    encoded = resolver.encode(new_text)
    bw = resolver.byte_width
    for i in range(0, len(encoded), bw):
        if bw == 2 and i + 1 < len(encoded):
            char_code = (encoded[i] << 8) | encoded[i + 1]
        else:
            char_code = encoded[i]
        w = width_cache.get_width(page, font_name, char_code)
        total += (w / 1000.0) * font_size
    return total


def _adjust_subsequent_positioning(
    ops: _Ops,
    last_op_index: int,
    width_delta: float,
    match_y: float,
    font_size: float,
    y_tolerance: float = 2.0,
) -> None:
    """Adjust positioning of subsequent text to compensate for width change.

    Scans forward from the last matched operator for a Td/TD operator
    on the same line, adjusting its x-offset by -width_delta.

    Args:
        ops: Parsed content stream instruction list.
        last_op_index: Index of the last operator in the match.
        width_delta: Width difference (positive = text got wider).
        match_y: Y-coordinate of the match for same-line detection.
        font_size: Current font size for TJ unit conversion.
        y_tolerance: Maximum y-difference for same-line detection.
    """
    for i in range(last_op_index + 1, len(ops)):
        inst = ops[i]
        op_str = str(inst.operator) if hasattr(inst, "operator") else str(inst[1])

        if op_str in ("Td", "TD"):
            operands = inst.operands if hasattr(inst, "operands") else inst[0]
            if len(operands) >= 2:
                # Td operands are (tx, ty) — relative move
                # Only adjust if on the same line (ty == 0 or very small)
                ty = float(operands[1])
                if abs(ty) < y_tolerance:
                    tx = float(operands[0])
                    new_tx = tx - width_delta
                    new_tx_obj = pikepdf.Object.parse(str(new_tx).encode())
                    ops[i] = ([new_tx_obj, operands[1]], inst.operator)
                return
        elif op_str == "Tm":
            # Absolute text matrix — check y position
            operands = inst.operands if hasattr(inst, "operands") else inst[0]
            if len(operands) >= 6:
                tm_y = float(operands[5])
                if abs(tm_y - match_y) < y_tolerance:
                    tm_x = float(operands[4])
                    new_x = tm_x - width_delta
                    new_operands = list(operands)
                    new_operands[4] = pikepdf.Object.parse(str(new_x).encode())
                    ops[i] = (new_operands, inst.operator)
                return
        elif op_str in ("BT", "ET"):
            # Hit a text block boundary — stop looking
            return


def _apply_single_replacement(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    ops: _Ops,
    match: TextMatch,
    new_text: str,
    resolver: FontResolver,
    width_cache: GlyphWidthCache,
    dry_run: bool,
) -> tuple[EditResult, FontResolver]:
    """Core replacement logic shared by replace() and replace_all().

    Modifies ops in-place when not dry_run. If encoding fails, attempts
    automatic font extension before returning failure.

    Args:
        pdf: The open PDF document.
        page: The page being modified.
        ops: Parsed content stream instructions (mutated in place).
        match: The text match to replace.
        new_text: Replacement text.
        resolver: FontResolver for the match's font.
        width_cache: Glyph width cache.
        dry_run: If True, skip actual modifications.

    Returns:
        Tuple of (EditResult, FontResolver). The resolver may be refreshed
        after font extension — callers should use the returned resolver
        for subsequent operations.
    """
    from typing import Literal as Lit

    # Check encodability
    can_enc, missing = resolver.can_encode(new_text)
    font_action: Lit["kept", "extended", "substituted", "failed"] = "kept"

    if not can_enc:
        # Attempt automatic font extension
        try:
            from pdf_edit_engine.fonts import extend_subset

            font_name = match.characters[0].font_name
            tier = extend_subset(pdf, page, font_name, "".join(missing))
            # Get fresh resolver from updated font dicts
            resolver = _get_font_resolver(page, font_name)
            can_enc_after, still_missing = resolver.can_encode(new_text)
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
                        reflow_applied=False,
                        glyphs_missing=still_missing,
                    ),
                ), resolver
            font_action = "extended"
            logger.info(
                "Font extension (%s) succeeded for %d missing chars",
                tier,
                len(missing),
            )
        except (FontNotFoundError, PDFEditError):
            return EditResult(
                success=False,
                original_text=match.matched_text,
                new_text=new_text,
                font_action="failed",
                fidelity_report=FidelityReport(
                    font_preserved=True,
                    font_substituted=None,
                    overflow_detected=False,
                    reflow_applied=False,
                    glyphs_missing=missing,
                ),
            ), resolver

    # Validate operator refs
    for ref in match.operator_refs:
        if ref >= len(ops):
            msg = f"Operator index {ref} out of bounds (content stream has {len(ops)} operators)"
            raise OperatorError(msg)

    byte_width = resolver.byte_width
    same_length = len(new_text) == len(match.matched_text)

    # Group match characters by operator_index
    chars_by_op: dict[int, list[TextCharacter]] = defaultdict(list)
    for ch in match.characters:
        chars_by_op[ch.operator_index].append(ch)

    # Determine character mapping for multi-operator same-length
    # Build a map: for each operator, which replacement characters go there
    op_replacement_map: dict[int, str] = {}
    if same_length:
        idx = 0
        for op_idx in sorted(chars_by_op.keys()):
            n = len(chars_by_op[op_idx])
            op_replacement_map[op_idx] = new_text[idx : idx + n]
            idx += n
    else:
        # Different-length: first operator gets all replacement text,
        # subsequent operators get empty string
        sorted_ops = sorted(chars_by_op.keys())
        if sorted_ops:
            op_replacement_map[sorted_ops[0]] = new_text
            for op_idx in sorted_ops[1:]:
                op_replacement_map[op_idx] = ""

    if not dry_run:
        for op_idx in sorted(chars_by_op.keys()):
            inst = ops[op_idx]
            op_str = str(inst.operator) if hasattr(inst, "operator") else str(inst[1])
            op_chars = chars_by_op[op_idx]
            replacement_text = op_replacement_map.get(op_idx, "")

            if op_str in ("TJ",):
                _modify_tj_operator(
                    ops,
                    op_idx,
                    op_chars,
                    replacement_text,
                    resolver,
                    byte_width,
                    same_length,
                )
            elif op_str in ("Tj", "'"):
                _modify_tj_single_operator(
                    ops,
                    op_idx,
                    op_chars,
                    replacement_text,
                    resolver,
                    byte_width,
                    same_length,
                )

    # Calculate widths
    old_width = sum(ch.width for ch in match.characters)
    new_width = _calculate_new_width(
        new_text,
        page,
        match.characters[0].font_name,
        match.characters[0].font_size,
        resolver,
        width_cache,
    )
    width_delta = new_width - old_width

    # Adjust subsequent positioning if needed
    if not dry_run and abs(width_delta) > 0.5:
        last_op_idx = max(match.operator_refs)
        match_y = match.characters[0].page_y
        _adjust_subsequent_positioning(
            ops,
            last_op_idx,
            width_delta,
            match_y,
            match.characters[0].font_size,
        )

    # Overflow detection
    page_width = float(page.MediaBox[2]) if page.MediaBox else 612.0
    overflow = (match.bounding_box[0] + new_width) > page_width

    return EditResult(
        success=True,
        original_text=match.matched_text,
        new_text=new_text,
        font_action=font_action,
        fidelity_report=FidelityReport(
            font_preserved=True,
            font_substituted=None,
            overflow_detected=overflow,
            reflow_applied=False,
            glyphs_missing=[],
        ),
    ), resolver


def _modify_tj_operator(
    ops: _Ops,
    op_idx: int,
    op_chars: list[TextCharacter],
    replacement_text: str,
    resolver: FontResolver,
    byte_width: int,
    same_length: bool,
) -> None:
    """Modify a TJ operator's array to apply replacement text."""
    inst = ops[op_idx]
    operands = inst.operands if hasattr(inst, "operands") else inst[0]
    operator = inst.operator if hasattr(inst, "operator") else inst[1]
    tj_items = list(operands[0])

    if same_length and replacement_text:
        # Same-length: splice bytes per fragment, preserving kerning
        chars_by_frag: dict[int, list[tuple[TextCharacter, str]]] = defaultdict(list)
        for i, ch in enumerate(op_chars):
            if ch.tj_fragment_index is not None:
                chars_by_frag[ch.tj_fragment_index].append((ch, replacement_text[i]))

        for frag_idx, char_pairs in chars_by_frag.items():
            arr_idx = _nth_string_index(tj_items, frag_idx)
            raw = bytes(tj_items[arr_idx])
            replacements: list[tuple[int, bytes]] = []
            for ch, new_char in char_pairs:
                encoded_char = resolver.encode(new_char)
                replacements.append((ch.byte_position, encoded_char))
            new_raw = _splice_bytes(raw, replacements, byte_width)
            tj_items[arr_idx] = pikepdf.String(new_raw)

        ops[op_idx] = ([pikepdf.Array(tj_items)], operator)
    else:
        # Different-length or empty: rebuild the TJ array
        encoded = resolver.encode(replacement_text) if replacement_text else b""
        new_array = _rebuild_tj_array(tj_items, op_chars, encoded)
        ops[op_idx] = ([new_array], operator)


def _modify_tj_single_operator(
    ops: _Ops,
    op_idx: int,
    op_chars: list[TextCharacter],
    replacement_text: str,
    resolver: FontResolver,
    byte_width: int,
    same_length: bool,
) -> None:
    """Modify a Tj (or ') operator's string to apply replacement text."""
    inst = ops[op_idx]
    operands = inst.operands if hasattr(inst, "operands") else inst[0]
    operator = inst.operator if hasattr(inst, "operator") else inst[1]
    raw = bytes(operands[0])

    if same_length and replacement_text:
        # Splice bytes at each character's byte_position
        replacements: list[tuple[int, bytes]] = []
        for i, ch in enumerate(op_chars):
            encoded_char = resolver.encode(replacement_text[i])
            replacements.append((ch.byte_position, encoded_char))
        new_raw = _splice_bytes(raw, replacements, byte_width)
    elif replacement_text:
        # Different-length: replace the matched byte range
        min_pos = min(ch.byte_position for ch in op_chars)
        max_pos = max(ch.byte_position for ch in op_chars) + byte_width
        encoded = resolver.encode(replacement_text)
        new_raw = raw[:min_pos] + encoded + raw[max_pos:]
    else:
        # Empty replacement: remove matched bytes
        min_pos = min(ch.byte_position for ch in op_chars)
        max_pos = max(ch.byte_position for ch in op_chars) + byte_width
        new_raw = raw[:min_pos] + raw[max_pos:]

    ops[op_idx] = ([pikepdf.String(new_raw)], operator)


# ── Public API ──────────────────────────────────────────────────────────


def replace(
    pdf_path: str,
    match: TextMatch,
    new_text: str,
    output_path: str,
    *,
    dry_run: bool = False,
    reflow: bool = True,
) -> EditResult:
    """Replace a single text match in a PDF.

    Args:
        pdf_path: Path to the input PDF file.
        match: TextMatch from locator.find() identifying the text to replace.
        new_text: Replacement text.
        output_path: Path for the output PDF.
        dry_run: If True, simulate the edit without writing output.
        reflow: If True and replacement is wider, reflow the paragraph.

    Returns:
        EditResult with fidelity report.

    Raises:
        PDFEditError: If the PDF is encrypted.
        OperatorError: If operator references are stale or invalid.
    """
    if not dry_run:
        validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    if pdf.is_encrypted:
        raise PDFEditError("Cannot edit encrypted PDF")

    if match.page_number >= len(pdf.pages):
        raise OperatorError(
            f"Page {match.page_number} out of range (PDF has {len(pdf.pages)} pages)"
        )

    page = pdf.pages[match.page_number]
    font_name = match.characters[0].font_name
    resolver = _get_font_resolver(page, font_name)
    width_cache = GlyphWidthCache()

    # Check if reflow is needed: replacement wider than original
    if reflow:
        try:
            old_width = sum(ch.width for ch in match.characters)
            new_width = _calculate_new_width(
                new_text,
                page,
                font_name,
                match.characters[0].font_size,
                resolver,
                width_cache,
            )
            # Only reflow if meaningfully wider (>1pt avoids trivial diffs)
            needs_reflow = new_width > old_width + 1.0
        except (KeyError, Exception):
            # Encoding failure — skip reflow, let simple replacement handle it
            needs_reflow = False
        if needs_reflow:
            try:
                from pdf_edit_engine.locator import _build_index
                from pdf_edit_engine.reflow import (
                    _detect_paragraphs_from_index,
                    find_paragraph_for_match,
                    reflow_paragraph,
                )

                elements = _build_index(page, match.page_number)
                page_width = float(page.MediaBox[2]) if page.MediaBox else 612.0
                paragraphs = _detect_paragraphs_from_index(
                    elements,
                    page_width,
                )
                para = find_paragraph_for_match(paragraphs, match)

                if para is not None:
                    font_key = font_name if font_name.startswith("/") else f"/{font_name}"
                    font_ref = page["/Resources"]["/Font"][font_key]
                    result = reflow_paragraph(
                        pdf,
                        page,
                        para,
                        match,
                        new_text,
                        resolver,
                        font_ref,
                    )
                    if result.success and not dry_run:
                        pdf.save(output_path)
                    pdf.close()
                    _invalidate_locator_cache()
                    return result
            except Exception:
                logger.warning(
                    "Reflow failed, falling back to simple replacement",
                    exc_info=True,
                )

    ops = list(pikepdf.parse_content_stream(page))

    result, _ = _apply_single_replacement(
        pdf,
        page,
        ops,
        match,
        new_text,
        resolver,
        width_cache,
        dry_run,
    )

    if result.success and not dry_run:
        new_stream = pikepdf.unparse_content_stream(ops)
        page.Contents = pdf.make_stream(new_stream)
        pdf.save(output_path)

    pdf.close()
    # Invalidate locator cache since PDF content changed
    _invalidate_locator_cache()
    return result


def replace_all(
    pdf_path: str,
    search: str,
    replacement: str,
    output_path: str,
    *,
    dry_run: bool = False,
) -> list[EditResult]:
    """Find and replace all occurrences of text in a PDF.

    Args:
        pdf_path: Path to the input PDF file.
        search: Text to find.
        replacement: Replacement text.
        output_path: Path for the output PDF.
        dry_run: If True, simulate edits without writing output.

    Returns:
        List of EditResult objects, one per match.
    """
    if not dry_run:
        validate_output_path(output_path)
    from pdf_edit_engine.locator import find

    matches = find(pdf_path, search)
    if not matches:
        return []

    pdf = pikepdf.Pdf.open(pdf_path)
    if pdf.is_encrypted:
        raise PDFEditError("Cannot edit encrypted PDF")

    width_cache = GlyphWidthCache()
    results: list[EditResult] = []

    # Group matches by page
    matches_by_page: dict[int, list[TextMatch]] = defaultdict(list)
    for m in matches:
        matches_by_page[m.page_number].append(m)

    any_success = False

    for page_num in sorted(matches_by_page.keys()):
        page = pdf.pages[page_num]
        ops = list(pikepdf.parse_content_stream(page))
        resolver = _get_font_resolver(
            page,
            matches_by_page[page_num][0].characters[0].font_name,
        )

        # Sort matches in reverse operator order to preserve indices
        page_matches = sorted(
            matches_by_page[page_num],
            key=lambda m: max(m.operator_refs),
            reverse=True,
        )

        page_results: list[EditResult] = []
        for m in page_matches:
            result, resolver = _apply_single_replacement(
                pdf,
                page,
                ops,
                m,
                replacement,
                resolver,
                width_cache,
                dry_run,
            )
            page_results.append(result)
            if result.success:
                any_success = True

        # Write modified content stream for this page
        if any_success and not dry_run:
            new_stream = pikepdf.unparse_content_stream(ops)
            page.Contents = pdf.make_stream(new_stream)

        # Reverse back to original order (we processed in reverse)
        page_results.reverse()
        results.extend(page_results)

    if any_success and not dry_run:
        pdf.save(output_path)

    pdf.close()
    _invalidate_locator_cache()
    return results


def batch_replace(
    pdf_path: str,
    edits: list[Edit],
    output_path: str,
    *,
    dry_run: bool = False,
) -> list[EditResult]:
    """Apply multiple find-and-replace operations to a PDF in a single pass.

    Args:
        pdf_path: Path to the input PDF file.
        edits: List of Edit objects with find/replace pairs.
        output_path: Path for the output PDF.
        dry_run: If True, simulate edits without writing output.

    Returns:
        List of EditResult objects, one per edit.
    """
    if not dry_run:
        validate_output_path(output_path)
    from pdf_edit_engine.locator import find

    pdf = pikepdf.Pdf.open(pdf_path)
    if pdf.is_encrypted:
        raise PDFEditError("Cannot edit encrypted PDF")

    width_cache = GlyphWidthCache()
    results: list[EditResult] = []
    used_ops_by_page: dict[int, set[int]] = defaultdict(set)
    any_success = False

    # Collect all (match, replacement) pairs with dedup
    all_pairs: list[tuple[TextMatch, str, int]] = []
    for edit_idx, edit in enumerate(edits):
        matches = find(pdf_path, edit.find)
        for m in matches:
            all_pairs.append((m, edit.replace, edit_idx))

    # Group by page
    pairs_by_page: dict[int, list[tuple[TextMatch, str, int]]] = defaultdict(list)
    for m, repl, edit_idx in all_pairs:
        pairs_by_page[m.page_number].append((m, repl, edit_idx))

    # Process each page
    edit_results: dict[int, list[EditResult]] = defaultdict(list)
    for page_num in sorted(pairs_by_page.keys()):
        page = pdf.pages[page_num]
        ops = list(pikepdf.parse_content_stream(page))

        # Sort in reverse operator order
        page_pairs = sorted(
            pairs_by_page[page_num],
            key=lambda p: max(p[0].operator_refs),
            reverse=True,
        )

        page_changed = False
        for m, repl, edit_idx in page_pairs:
            # Skip if operators overlap with already-processed match on same page
            op_set = set(m.operator_refs)
            if op_set & used_ops_by_page[page_num]:
                edit_results[edit_idx].append(
                    EditResult(
                        success=False,
                        original_text=m.matched_text,
                        new_text=repl,
                        font_action="kept",
                        warnings=["Skipped: overlapping with previous edit"],
                    )
                )
                continue

            resolver = _get_font_resolver(page, m.characters[0].font_name)
            result, resolver = _apply_single_replacement(
                pdf,
                page,
                ops,
                m,
                repl,
                resolver,
                width_cache,
                dry_run,
            )
            edit_results[edit_idx].append(result)
            if result.success:
                used_ops_by_page[page_num].update(m.operator_refs)
                any_success = True
                page_changed = True

        if page_changed and not dry_run:
            new_stream = pikepdf.unparse_content_stream(ops)
            page.Contents = pdf.make_stream(new_stream)

    if any_success and not dry_run:
        pdf.save(output_path)

    pdf.close()
    _invalidate_locator_cache()

    # Flatten results: one per edit (aggregate per edit_idx)
    for edit_idx in range(len(edits)):
        if edit_idx in edit_results:
            # Use the first result for this edit
            results.append(edit_results[edit_idx][0])
        else:
            results.append(
                EditResult(
                    success=False,
                    original_text=edits[edit_idx].find,
                    new_text=edits[edit_idx].replace,
                    font_action="kept",
                    warnings=["No matches found"],
                )
            )

    return results


def _invalidate_locator_cache() -> None:
    """Clear the locator module's content element cache."""
    from pdf_edit_engine import locator

    locator._cached_path = None  # noqa: SLF001
    locator._cached_elements = {}  # noqa: SLF001
