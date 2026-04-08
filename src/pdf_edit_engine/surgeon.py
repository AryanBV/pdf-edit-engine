"""OperatorSurgeon module — modify PDF content stream operators."""

from __future__ import annotations

import logging
import pathlib
from collections import defaultdict
from typing import Any

import pikepdf

from pdf_edit_engine._pathutil import validate_output_path
from pdf_edit_engine.encoding import FontResolver, FontResolverCache
from pdf_edit_engine.errors import (
    EncodingError,
    FontNotFoundError,
    OperatorError,
    PDFEditError,
    ReflowError,
)
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

# ── Module-level caches ─────────────────────────────────────────────────
#
# Singletons that persist across calls for same-PDF performance.
# Cleared automatically when the public API detects a different PDF path.

_width_cache = GlyphWidthCache()
_resolver_cache = FontResolverCache()
_cached_pdf_path: str | None = None


def _ensure_caches_for_pdf(pdf_path: str) -> None:
    """Clear module-level caches if the PDF path has changed.

    Prevents cross-PDF leaks: GlyphWidthCache keys on font resource
    names (e.g. ``"F1"``) which are PDF-local — different PDFs reuse
    the same names for different fonts.
    """
    global _cached_pdf_path  # noqa: PLW0603
    resolved = str(pathlib.Path(pdf_path).resolve())
    if resolved != _cached_pdf_path:
        _cached_pdf_path = resolved
        _width_cache.clear()
        _resolver_cache.clear()


# ── Private helpers ──────────────────────────────────────────────────────


def _get_font_resolver(page: pikepdf.Page, font_name: str) -> FontResolver:
    """Build a FontResolver for the given font on the page."""
    return _resolver_cache.get_resolver(page, font_name)


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


def _encode_with_kerning(
    text: str,
    original_width_page: float,
    font_size: float,
    resolver: FontResolver,
    width_cache: GlyphWidthCache,
    page: pikepdf.Page,
    font_name: str,
) -> list[object]:
    """Encode text into TJ items with kerning to match original width.

    Produces a list of pikepdf.String and numeric kerning values that,
    when rendered, occupy the same total width as the original match.

    Args:
        text: Replacement text to encode.
        original_width_page: Original match width in page-space units.
        font_size: Font size in points.
        resolver: FontResolver for encoding characters.
        width_cache: Glyph width cache.
        page: PDF page for width lookup.
        font_name: Font resource name.

    Returns:
        List of TJ array items (pikepdf.String and int kerning values).
    """
    if not text:
        return []

    bw = resolver.byte_width
    glyph_items: list[tuple[bytes, float]] = []  # (encoded_bytes, width_font_units)
    full_encoded = resolver.encode(text)
    for i in range(0, len(full_encoded), bw):
        glyph_bytes = full_encoded[i : i + bw]
        char_code = (glyph_bytes[0] << 8) | glyph_bytes[1] if bw == 2 else glyph_bytes[0]
        w = width_cache.get_width(page, font_name, char_code)
        glyph_items.append((glyph_bytes, w))

    if len(glyph_items) <= 1:
        # Single glyph — no gaps to distribute kerning
        return [pikepdf.String(glyph_items[0][0])] if glyph_items else []

    # Fallback: emit flat string when width info is unusable
    if original_width_page <= 0 or font_size <= 0:
        flat = b"".join(enc for enc, _ in glyph_items)
        return [pikepdf.String(flat)]

    # Compute everything in font units (/W scale) to avoid page-space round-trip
    original_fu = original_width_page * 1000.0 / font_size
    replacement_fu = sum(w for _, w in glyph_items)

    # TJ kerning: positive = move left (tighten), negative = move right (widen)
    # When replacement is wider (repl > orig), total_kern > 0 → tighten to fit
    # When replacement is narrower (repl < orig), total_kern < 0 → widen to fill
    total_kern = replacement_fu - original_fu
    num_gaps = len(glyph_items) - 1

    if abs(total_kern) > 0.5 * original_fu and original_fu > 0:
        # Width delta too large for kerning — return flat unkerned string
        flat = b"".join(enc for enc, _ in glyph_items)
        return [pikepdf.String(flat)]

    # Distribute kerning PROPORTIONALLY to each glyph's width.
    # Uniform distribution causes narrow chars (i, l, t) to overlap because
    # they absorb the same tightening as wide chars (W, M).  Proportional
    # distribution gives each glyph kerning proportional to its width, so
    # the advance ratio (width - kern) / width is constant across all glyphs.
    kern_values: list[int] = []
    if replacement_fu > 0 and abs(total_kern) > 0.5:
        accumulated = 0.0
        for i in range(num_gaps):
            w_i = glyph_items[i][1]
            ideal = total_kern * (w_i / replacement_fu)
            accumulated += ideal
            kern_int = round(accumulated) - sum(kern_values)
            kern_values.append(kern_int)
    else:
        kern_values = [0] * num_gaps

    # If no kerning needed, emit flat string
    if all(k == 0 for k in kern_values):
        flat = b"".join(enc for enc, _ in glyph_items)
        return [pikepdf.String(flat)]

    # Build TJ items: [glyph0, kern0, glyph1, kern1, ..., glyphN-1]
    result: list[object] = []
    for i, (encoded, _) in enumerate(glyph_items):
        result.append(pikepdf.String(encoded))
        if i < num_gaps and kern_values[i] != 0:
            result.append(kern_values[i])

    return result


def _rebuild_tj_array(
    tj_items: list[object],
    match_chars: list[TextCharacter],
    replacement_items: list[object],
) -> pikepdf.Array:
    """Rebuild a TJ array replacing the matched span with new items.

    Fragments before the match are preserved. The matched span is replaced
    by the provided items (strings + optional kerning values). Fragments
    after the match are preserved.

    Args:
        tj_items: Original TJ array items.
        match_chars: Characters from the match that fall in this operator.
        replacement_items: Pre-built TJ items (pikepdf.String and/or numeric
            kerning values) to insert in place of the matched span.

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

    # Add the replacement items (strings + optional kerning values)
    for item in replacement_items:
        new_items.append(item)

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
            # Evict stale resolver so _get_font_resolver re-parses
            _resolver_cache.evict(page, font_name)
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

    # Detect ligature CIDs: when a single CID decodes to multiple Unicode
    # characters (e.g., "tf" ligature), the sub-characters share the same
    # (operator_index, tj_fragment_index, byte_position).  The splice path
    # assumes 1 char = 1 CID slot and would overwrite the same position
    # twice, losing a character.  Force the rebuild path in that case.
    cid_slots = len(
        {(ch.operator_index, ch.tj_fragment_index, ch.byte_position) for ch in match.characters}
    )
    has_ligatures = cid_slots != len(match.matched_text)
    same_length = (not has_ligatures) and len(new_text) == len(match.matched_text)

    # Group match characters by operator_index
    chars_by_op: dict[int, list[TextCharacter]] = defaultdict(list)
    for ch in match.characters:
        chars_by_op[ch.operator_index].append(ch)

    # Build a map: for each operator, which replacement characters go there
    op_replacement_map: dict[int, str] = {}
    sorted_ops = sorted(chars_by_op.keys())
    if len(new_text) == len(match.matched_text):
        # Same char count: distribute by character count per operator
        idx = 0
        for op_idx in sorted_ops:
            n = len(chars_by_op[op_idx])
            op_replacement_map[op_idx] = new_text[idx : idx + n]
            idx += n
    elif sorted_ops:
        # Different length: distribute proportionally by original char count
        total_orig_chars = sum(len(chars_by_op[op]) for op in sorted_ops)
        idx = 0
        for i, op_idx in enumerate(sorted_ops):
            n = len(chars_by_op[op_idx])
            if i < len(sorted_ops) - 1:
                share = round(len(new_text) * n / total_orig_chars) if total_orig_chars > 0 else 0
            else:
                share = len(new_text) - idx  # last op gets remainder
            op_replacement_map[op_idx] = new_text[idx : idx + share]
            idx += share

    # Merge narrow operators (1-2 chars) into adjacent wide operators.
    # Narrow operators (em-dashes, spaces, hyphens, "| ") have fixed Tm positions
    # sized for the original character(s). When replacement text assigns different
    # characters to that slot, the inter-operator gap causes visible artifacts.
    # Merging lets the wide operator's text flow naturally past its boundary
    # (PDF does not clip text at operator boundaries).
    _MERGE_THRESHOLD = 2  # merge operators with <= this many chars
    merged_width_bonus: dict[int, float] = {}
    if len(sorted_ops) > 1:
        last_multi: int | None = None
        deferred: list[int] = []
        for op_idx in sorted_ops:
            n = len(chars_by_op[op_idx])
            if n > _MERGE_THRESHOLD:
                # Absorb any deferred leading single-char ops (prepend)
                if deferred:
                    prefix = "".join(op_replacement_map.get(s, "") for s in deferred)
                    op_replacement_map[op_idx] = prefix + op_replacement_map.get(op_idx, "")
                    for s in deferred:
                        bonus = sum(ch.width for ch in chars_by_op[s])
                        merged_width_bonus[op_idx] = merged_width_bonus.get(op_idx, 0.0) + bonus
                        op_replacement_map[s] = ""
                    deferred = []
                last_multi = op_idx
            elif n <= _MERGE_THRESHOLD and last_multi is not None:
                # Append to preceding wide operator
                op_replacement_map[last_multi] += op_replacement_map.get(op_idx, "")
                bonus = sum(ch.width for ch in chars_by_op[op_idx])
                merged_width_bonus[last_multi] = merged_width_bonus.get(last_multi, 0.0) + bonus
                op_replacement_map[op_idx] = ""
            elif n <= _MERGE_THRESHOLD:
                deferred.append(op_idx)

    # For merged operators, compute the target width as the Tm gap to the next
    # non-empty operator.  sum(ch.width) misses inter-operator spacing that the
    # original Tm positions encode.  Using the Tm gap ensures the replacement
    # text fills exactly the visual space between operators.
    active_ops = sorted(op_idx for op_idx in chars_by_op if op_replacement_map.get(op_idx, ""))

    if not dry_run:
        for op_idx in sorted(chars_by_op.keys()):
            inst = ops[op_idx]
            op_str = str(inst.operator) if hasattr(inst, "operator") else str(inst[1])
            op_chars = chars_by_op[op_idx]
            replacement_text = op_replacement_map.get(op_idx, "")
            # Per-operator same_length: merged operators have more chars than
            # original, so they must use the rebuild path with kerning
            op_same_length = same_length and len(replacement_text) == len(op_chars)

            # Compute width_bonus: use Tm position gap for merged operators
            wb = 0.0
            if op_idx in merged_width_bonus and replacement_text:
                # Find the next non-empty operator's first character position
                active_pos = active_ops.index(op_idx) if op_idx in active_ops else -1
                if active_pos >= 0 and active_pos + 1 < len(active_ops):
                    next_op = active_ops[active_pos + 1]
                    next_chars = chars_by_op[next_op]
                    if next_chars and op_chars:
                        first_x = op_chars[0].page_x
                        next_x = next_chars[0].page_x
                        glyph_width = sum(ch.width for ch in op_chars)
                        wb = max(0.0, (next_x - first_x) - glyph_width)
                if wb == 0.0:
                    wb = merged_width_bonus[op_idx]

            if op_str in ("TJ",):
                _modify_tj_operator(
                    ops,
                    op_idx,
                    op_chars,
                    replacement_text,
                    resolver,
                    byte_width,
                    op_same_length,
                    width_cache=width_cache,
                    page=page,
                    font_name=match.characters[0].font_name,
                    font_size=match.characters[0].font_size,
                    width_bonus=wb,
                )
            elif op_str in ("Tj", "'"):
                _modify_tj_single_operator(
                    ops,
                    op_idx,
                    op_chars,
                    replacement_text,
                    resolver,
                    byte_width,
                    op_same_length,
                    width_cache=width_cache,
                    page=page,
                    font_name=match.characters[0].font_name,
                    font_size=match.characters[0].font_size,
                    width_bonus=wb,
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
    width_cache: GlyphWidthCache | None = None,
    page: pikepdf.Page | None = None,
    font_name: str | None = None,
    font_size: float | None = None,
    width_bonus: float = 0.0,
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
        # Different-length or empty: rebuild the TJ array with kerning
        if (
            replacement_text
            and width_cache is not None
            and page is not None
            and font_name
            and font_size
        ):
            op_original_width = sum(ch.width for ch in op_chars) + width_bonus
            replacement_items = _encode_with_kerning(
                replacement_text,
                op_original_width,
                font_size,
                resolver,
                width_cache,
                page,
                font_name,
            )
        elif replacement_text:
            replacement_items = [pikepdf.String(resolver.encode(replacement_text))]
        else:
            replacement_items = []
        new_array = _rebuild_tj_array(tj_items, op_chars, replacement_items)
        ops[op_idx] = ([new_array], operator)


def _modify_tj_single_operator(
    ops: _Ops,
    op_idx: int,
    op_chars: list[TextCharacter],
    replacement_text: str,
    resolver: FontResolver,
    byte_width: int,
    same_length: bool,
    width_cache: GlyphWidthCache | None = None,
    page: pikepdf.Page | None = None,
    font_name: str | None = None,
    font_size: float | None = None,
    width_bonus: float = 0.0,
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
        ops[op_idx] = ([pikepdf.String(new_raw)], operator)
    elif replacement_text:
        # Different-length: use kerning if available, convert to TJ
        if width_cache is not None and page is not None and font_name and font_size:
            min_pos = min(ch.byte_position for ch in op_chars)
            max_pos = max(ch.byte_position for ch in op_chars) + byte_width
            prefix_bytes = raw[:min_pos]
            suffix_bytes = raw[max_pos:]
            op_original_width = sum(ch.width for ch in op_chars) + width_bonus
            kerned_items = _encode_with_kerning(
                replacement_text,
                op_original_width,
                font_size,
                resolver,
                width_cache,
                page,
                font_name,
            )
            tj_items: list[object] = []
            if prefix_bytes:
                tj_items.append(pikepdf.String(prefix_bytes))
            tj_items.extend(kerned_items)
            if suffix_bytes:
                tj_items.append(pikepdf.String(suffix_bytes))
            ops[op_idx] = ([pikepdf.Array(tj_items)], pikepdf.Operator("TJ"))
        else:
            min_pos = min(ch.byte_position for ch in op_chars)
            max_pos = max(ch.byte_position for ch in op_chars) + byte_width
            encoded = resolver.encode(replacement_text)
            new_raw = raw[:min_pos] + encoded + raw[max_pos:]
            ops[op_idx] = ([pikepdf.String(new_raw)], operator)
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
    _ensure_caches_for_pdf(pdf_path)
    if not dry_run:
        validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    try:
        if pdf.is_encrypted:
            raise PDFEditError("Cannot edit encrypted PDF")

        if match.page_number >= len(pdf.pages):
            raise OperatorError(
                f"Page {match.page_number} out of range (PDF has {len(pdf.pages)} pages)"
            )

        page = pdf.pages[match.page_number]
        font_name = match.characters[0].font_name
        resolver = _get_font_resolver(page, font_name)

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
                    _width_cache,
                )
                # Only reflow if meaningfully wider (>1pt avoids trivial diffs)
                needs_reflow = new_width > old_width + 1.0
            except (KeyError, EncodingError, FontNotFoundError):
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
                        _invalidate_locator_cache()
                        return result
                except (ReflowError, OperatorError, EncodingError, KeyError, ValueError):
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
            _width_cache,
            dry_run,
        )

        if result.success and not dry_run:
            new_stream = pikepdf.unparse_content_stream(ops)
            page.Contents = pdf.make_stream(new_stream)
            pdf.save(output_path)

        # Invalidate locator cache since PDF content changed
        _invalidate_locator_cache()
        return result
    finally:
        pdf.close()


def _try_reflow_match(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    page_num: int,
    match: TextMatch,
    new_text: str,
) -> EditResult | None:
    """Attempt reflow for a single match.  Returns EditResult on success, None on failure."""
    try:
        font_name = match.characters[0].font_name
        resolver = _get_font_resolver(page, font_name)
        old_width = sum(ch.width for ch in match.characters)
        new_width = _calculate_new_width(
            new_text, page, font_name,
            match.characters[0].font_size, resolver, _width_cache,
        )
        if new_width <= old_width + 1.0:
            return None  # not meaningfully wider

        from pdf_edit_engine.locator import _build_index
        from pdf_edit_engine.reflow import (
            _detect_paragraphs_from_index,
            find_paragraph_for_match,
            reflow_paragraph,
        )

        elements = _build_index(page, page_num)
        page_width = float(page.MediaBox[2]) if page.MediaBox else 612.0
        paragraphs = _detect_paragraphs_from_index(elements, page_width)
        para = find_paragraph_for_match(paragraphs, match)
        if para is None:
            return None

        font_key = font_name if font_name.startswith("/") else f"/{font_name}"
        font_ref = page["/Resources"]["/Font"][font_key]
        result = reflow_paragraph(pdf, page, para, match, new_text, resolver, font_ref)
        return result if result.success else None
    except (ReflowError, OperatorError, EncodingError, FontNotFoundError,
            KeyError, ValueError):
        logger.warning("Reflow failed, falling back to simple replacement", exc_info=True)
        return None


def replace_all(
    pdf_path: str,
    search: str,
    replacement: str,
    output_path: str,
    *,
    dry_run: bool = False,
    reflow: bool = True,
) -> list[EditResult]:
    """Find and replace all occurrences of text in a PDF.

    Args:
        pdf_path: Path to the input PDF file.
        search: Text to find.
        replacement: Replacement text.
        output_path: Path for the output PDF.
        dry_run: If True, simulate edits without writing output.
        reflow: If True and replacement is wider, attempt paragraph reflow.

    Returns:
        List of EditResult objects, one per match.
    """
    _ensure_caches_for_pdf(pdf_path)
    if not dry_run:
        validate_output_path(output_path)
    from pdf_edit_engine.locator import find

    matches = find(pdf_path, search)
    if not matches:
        return []

    pdf = pikepdf.Pdf.open(pdf_path)
    try:
        if pdf.is_encrypted:
            raise PDFEditError("Cannot edit encrypted PDF")

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
            page_reflowed = False
            simple_success = False
            for m in page_matches:
                # Attempt reflow for the first qualifying match per page
                if reflow and not dry_run and not page_reflowed:
                    reflow_result = _try_reflow_match(
                        pdf, page, page_num, m, replacement,
                    )
                    if reflow_result is not None:
                        page_results.append(reflow_result)
                        any_success = True
                        page_reflowed = True
                        # Re-parse ops since reflow wrote to page directly
                        ops = list(pikepdf.parse_content_stream(page))
                        continue

                try:
                    result, resolver = _apply_single_replacement(
                        pdf, page, ops, m, replacement,
                        resolver, _width_cache, dry_run,
                    )
                except OperatorError:
                    result = EditResult(
                        success=False,
                        original_text=m.matched_text,
                        new_text=replacement,
                        font_action="kept",
                        warnings=["Skipped: operator indices invalidated by prior reflow"],
                    )
                page_results.append(result)
                if result.success:
                    any_success = True
                    simple_success = True

            # Write modified content stream for this page
            if simple_success and not dry_run:
                new_stream = pikepdf.unparse_content_stream(ops)
                page.Contents = pdf.make_stream(new_stream)

            # Reverse back to original order (we processed in reverse)
            page_results.reverse()
            results.extend(page_results)

        if any_success and not dry_run:
            pdf.save(output_path)

        _invalidate_locator_cache()
        return results
    finally:
        pdf.close()


def batch_replace(
    pdf_path: str,
    edits: list[Edit],
    output_path: str,
    *,
    dry_run: bool = False,
    reflow: bool = True,
) -> list[EditResult]:
    """Apply multiple find-and-replace operations to a PDF in a single pass.

    Args:
        pdf_path: Path to the input PDF file.
        edits: List of Edit objects with find/replace pairs.
        output_path: Path for the output PDF.
        dry_run: If True, simulate edits without writing output.
        reflow: If True and replacement is wider, attempt paragraph reflow.

    Returns:
        List of EditResult objects, one per edit.
    """
    _ensure_caches_for_pdf(pdf_path)
    if not dry_run:
        validate_output_path(output_path)
    from pdf_edit_engine.locator import find

    pdf = pikepdf.Pdf.open(pdf_path)
    try:
        if pdf.is_encrypted:
            raise PDFEditError("Cannot edit encrypted PDF")

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
            page_reflowed = False
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

                # Attempt reflow for the first qualifying match per page
                if reflow and not dry_run and not page_reflowed:
                    reflow_result = _try_reflow_match(pdf, page, page_num, m, repl)
                    if reflow_result is not None:
                        edit_results[edit_idx].append(reflow_result)
                        used_ops_by_page[page_num].update(m.operator_refs)
                        any_success = True
                        page_reflowed = True
                        ops = list(pikepdf.parse_content_stream(page))
                        continue

                resolver = _get_font_resolver(page, m.characters[0].font_name)
                try:
                    result, resolver = _apply_single_replacement(
                        pdf, page, ops, m, repl,
                        resolver, _width_cache, dry_run,
                    )
                except OperatorError:
                    result = EditResult(
                        success=False,
                        original_text=m.matched_text,
                        new_text=repl,
                        font_action="kept",
                        warnings=["Skipped: operator indices invalidated by prior reflow"],
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
    finally:
        pdf.close()


def _invalidate_locator_cache() -> None:
    """Clear the locator module's content element cache and surgeon caches."""
    global _cached_pdf_path  # noqa: PLW0603
    from pdf_edit_engine import locator

    locator._cached_path = None  # noqa: SLF001
    locator._cached_elements = {}  # noqa: SLF001
    _cached_pdf_path = None
    _width_cache.clear()
    _resolver_cache.clear()
