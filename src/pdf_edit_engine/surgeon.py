"""OperatorSurgeon module — modify PDF content stream operators."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

import pikepdf

from pdf_edit_engine._pathutil import open_pdf, validate_output_path
from pdf_edit_engine.encoding import FontResolver, FontResolverCache
from pdf_edit_engine.errors import (
    EncodingError,
    FontNotFoundError,
    OperatorError,
    PDFEditError,
    ReflowError,
)
from pdf_edit_engine.models import (
    Degradation,
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


# ── Cache ownership policy (ARY-283) ─────────────────────────────────────
#
# This module holds NO cache state. Each public entrypoint (`replace`,
# `replace_all`, `batch_replace`) constructs a fresh `FontResolverCache`
# and `GlyphWidthCache` at entry and threads both through all internal
# helpers as explicit parameters. Two reasons:
#
#   1. Coherency.  When `extend_subset` mutates a font, only the cache
#      that evicts sees a fresh resolver. Threading a single cache per
#      call avoids any cross-module staleness between `surgeon` and
#      `structural`.
#   2. Thread-safety.  Fresh per-call caches are trivially isolated.
#
# The ephemeral cost (one font-parse per public call) is negligible at
# typical edit volumes and was previously absorbed by invalidation
# boilerplate (`_ensure_caches_for_pdf`, `_cached_pdf_path`) anyway.


# ── Private helpers ──────────────────────────────────────────────────────


def _get_font_resolver(
    page: pikepdf.Page,
    font_name: str,
    resolver_cache: FontResolverCache,
) -> FontResolver:
    """Build a FontResolver for the given font on the page."""
    return resolver_cache.get_resolver(page, font_name)


def _assert_match_addressable(
    ops: _Ops,
    match: TextMatch,
    resolver: FontResolver,
) -> None:
    """INV-B-3 contract enforcement.

    A ``TextMatch`` returned from :func:`pdf_edit_engine.find` captures
    ``(operator_index, byte_position, tj_fragment_index)`` triples that
    point into the content-stream snapshot at the moment of the find.
    If the caller mutates the PDF (e.g. ``replace_all``) and then re-uses
    a previously-collected match against the new file, those indices
    silently address into operators whose text has changed — ``replace``
    would dutifully splice over the wrong bytes.

    This guard runs at every public-API entry point that consumes a
    ``TextMatch`` (``surgeon.replace``, ``reflow.reflow_paragraph``).
    On stale input it raises ``OperatorError`` with a re-run-find()
    instruction; on fresh input it is essentially free (a single op
    lookup + one byte-slice decode).

    Args:
        ops: Parsed content-stream instructions for the match's page.
        match: The ``TextMatch`` to validate.
        resolver: ``FontResolver`` for the match's font, used to decode
            the captured byte-slice back to its Unicode character.

    Raises:
        OperatorError: If any character in ``match`` no longer resolves
            to its recorded ``unicode_char`` against the current ops.
    """
    if not match.characters:
        return  # empty match cannot be addressable

    bw = resolver.byte_width
    first = match.characters[0]
    op_idx = first.operator_index
    if op_idx < 0 or op_idx >= len(ops):
        raise OperatorError(
            f"Stale TextMatch: operator index {op_idx} out of range "
            f"(content stream has {len(ops)} ops). The PDF appears to "
            f"have been modified since find() was called — re-run find() "
            f"against the current PDF state."
        )
    inst = ops[op_idx]
    operator = str(inst.operator if hasattr(inst, "operator") else inst[1])
    operands = inst.operands if hasattr(inst, "operands") else inst[0]
    if operator not in ("Tj", "TJ", "'", '"'):
        raise OperatorError(
            f"Stale TextMatch: operator at index {op_idx} is {operator!r}; "
            f"expected a text-showing operator (Tj/TJ). Re-run find() "
            f"against the current PDF state."
        )

    # Recover the raw bytes at the recorded fragment.
    raw: bytes | None = None
    try:
        if operator == "TJ":
            tj_items = list(operands[0])
            if first.tj_fragment_index is None:
                return  # legacy match without fragment indexing — skip
            count = 0
            for item in tj_items:
                if isinstance(item, pikepdf.String):
                    if count == first.tj_fragment_index:
                        raw = bytes(item)
                        break
                    count += 1
        else:
            # Tj / ' / " — single string operand
            raw = bytes(operands[0])
    except (IndexError, AttributeError, TypeError) as exc:
        raise OperatorError(
            f"Stale TextMatch: failed to read operand at op {op_idx} "
            f"({type(exc).__name__}). Re-run find()."
        ) from exc

    if raw is None:
        raise OperatorError(
            f"Stale TextMatch: tj_fragment_index "
            f"{first.tj_fragment_index} not found in op {op_idx}. "
            f"Re-run find()."
        )

    bp = first.byte_position
    if bp < 0 or bp + bw > len(raw):
        raise OperatorError(
            f"Stale TextMatch: byte_position {bp} out of range for "
            f"operand of length {len(raw)} at op {op_idx}. Re-run find()."
        )
    try:
        decoded = resolver.decode(raw[bp : bp + bw])
    except KeyError as exc:
        raise OperatorError(
            f"Stale TextMatch: bytes at op {op_idx} byte {bp} cannot be "
            f"decoded by the current font ({exc}). Re-run find()."
        ) from exc
    if decoded != first.unicode_char:
        raise OperatorError(
            f"Stale TextMatch: op {op_idx} now decodes to "
            f"{decoded!r}, expected {first.unicode_char!r}. The PDF was "
            f"modified since find() was called — re-run find() against "
            f"the current PDF state."
        )


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


def _kerning_decision(factor: float) -> tuple[float | None, Degradation | None]:
    """Map a kerning ``factor`` to a Tz-emit decision and an optional Degradation.

    Pure function (no PDF state). Two independent axes:

    - **Tz emission**: emit a Tz operator pair only when the factor differs
      from 100 by more than ±0.05 (visually imperceptible deadzone keeps
      the operator stack clean for unmodified-width replacements).
    - **Degradation emission**: symmetric 95-105 deadzone per design doc §4a.
      Below 95 → ``kerning_compressed`` (warning, glyphs visibly squished).
      Above 105 → ``kerning_widened`` (info, glyphs spread; less visually
      objectionable). Within [95, 105] → no degradation.

    Returns ``(tz_factor, degradation)`` — ``tz_factor=None`` means do not
    emit Tz operators; ``degradation=None`` means no surfacing required.
    """
    needs_scaling = abs(factor - 100.0) > 0.05

    deg: Degradation | None = None
    if factor < 95:
        deg = Degradation(
            kind="kerning_compressed",
            detail=f"Tz {factor:.0f}%",
            severity="warning",
        )
    elif factor > 105:
        deg = Degradation(
            kind="kerning_widened",
            detail=f"Tz {factor:.0f}%",
            severity="info",
        )

    return (factor if needs_scaling else None, deg)


@dataclass(frozen=True)
class KerningEncoding:
    """Result of ``_encode_with_kerning``.

    ``tj_items`` is the list of items to put into a TJ array (typically a
    single ``pikepdf.String`` when ``tz_factor`` is set; per-glyph kerning
    ints are no longer emitted as of v0.1.3 Algo A).

    ``tz_factor`` is the horizontal-scaling percentage to apply via the
    PDF ``Tz`` operator, computed as
    ``100 * original_width / replacement_width``. ``None`` means no Tz
    wrapping is needed (factor is within ±0.05 of 100, or the inputs were
    degenerate).

    ``degradation`` is a typed Degradation event for callers to surface
    via ``FidelityReport.degradations``: ``kerning_compressed`` (warning)
    when factor < 95 or ``kerning_widened`` (info) when factor > 105.
    None within the symmetric 95-105 deadzone.
    """

    tj_items: list[object]
    tz_factor: float | None
    degradation: Degradation | None


def _encode_with_kerning(
    text: str,
    original_width_page: float,
    font_size: float,
    resolver: FontResolver,
    width_cache: GlyphWidthCache,
    page: pikepdf.Page,
    font_name: str,
) -> KerningEncoding:
    """Encode text into TJ items with horizontal Tz scaling (Algo A, v0.1.3).

    Replaces the v0.1.2 proportional TJ-gap kerning with a single ``Tz``
    horizontal-scaling factor applied via the PDF graphics-state operator.
    ``Tz`` preserves glyph identity regardless of factor — there is no
    refusal threshold (per design doc §1). Symmetric 95-105 deadzone for
    Degradation emission: factor < 95 emits ``kerning_compressed``
    (warning); factor > 105 emits ``kerning_widened`` (info).

    Args:
        text: Replacement text to encode.
        original_width_page: Original match width in page-space units.
        font_size: Font size in points.
        resolver: FontResolver for encoding characters.
        width_cache: Glyph width cache.
        page: PDF page for width lookup.
        font_name: Font resource name.

    Returns:
        KerningEncoding with tj_items (flat single-string list when
        non-empty), tz_factor (None or the percentage to scale by), and
        an optional Degradation event.
    """
    if not text:
        return KerningEncoding(tj_items=[], tz_factor=None, degradation=None)

    bw = resolver.byte_width
    glyph_widths_fu: list[float] = []
    full_encoded = resolver.encode(text)
    for i in range(0, len(full_encoded), bw):
        glyph_bytes = full_encoded[i : i + bw]
        char_code = (glyph_bytes[0] << 8) | glyph_bytes[1] if bw == 2 else glyph_bytes[0]
        glyph_widths_fu.append(width_cache.get_width(page, font_name, char_code))

    # Tz scales horizontally — glyph identity preserved — so the TJ array
    # collapses to a single flat string. No per-gap kerning ints needed.
    tj_items: list[object] = [pikepdf.String(full_encoded)] if full_encoded else []

    if not glyph_widths_fu or original_width_page <= 0 or font_size <= 0:
        return KerningEncoding(tj_items=tj_items, tz_factor=None, degradation=None)

    # Compute factor in font units (avoids page-space round-trip).
    original_fu = original_width_page * 1000.0 / font_size
    replacement_fu = sum(glyph_widths_fu)
    if replacement_fu <= 0 or original_fu <= 0:
        return KerningEncoding(tj_items=tj_items, tz_factor=None, degradation=None)

    factor = 100.0 * original_fu / replacement_fu
    tz_factor, degradation = _kerning_decision(factor)
    return KerningEncoding(
        tj_items=tj_items,
        tz_factor=tz_factor,
        degradation=degradation,
    )


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

    Raises:
        KeyError: When *new_text* contains characters the resolver
            cannot encode. The caller is responsible for handling
            this (either by extending the font or by skipping the
            width-based reflow decision — see ARY-282 in CHANGELOG
            for the decision rationale).
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
    resolver_cache: FontResolverCache,
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
        width_cache: Glyph width cache (mutated on eviction).
        resolver_cache: Font resolver cache (mutated on eviction).
        dry_run: If True, skip actual modifications.

    Returns:
        Tuple of (EditResult, FontResolver). The resolver may be refreshed
        after font extension — callers should use the returned resolver
        for subsequent operations.
    """
    # Always derive the resolver from the match's own font. Callers such
    # as replace_all() iterate over matches on a page and may pass in a
    # resolver from the previous iteration that belongs to a different
    # font. Trusting that stale resolver would cause cross-font CID
    # pollution: can_encode() would validate against font A, no extension
    # would run for font B, and _modify_tj_operator() would write font
    # A's CIDs into font B's content-stream operator. Fetching from the
    # cache here is cheap when the match reuses the previous font.
    match_font_name = match.characters[0].font_name
    resolver = _get_font_resolver(page, match_font_name, resolver_cache)

    # Check encodability
    can_enc, missing = resolver.can_encode(new_text)
    font_action: Literal["kept", "extended", "substituted", "failed"] = "kept"
    # INV-C-4: collect metric-equivalent substitution events from
    # extend_subset so the resulting EditResult can surface the
    # substitution via FidelityReport.font_substituted.
    substitution_log: list[str] = []

    if not can_enc:
        # Attempt automatic font extension
        try:
            from pdf_edit_engine.fonts import extend_subset

            font_name = match.characters[0].font_name
            tier = extend_subset(
                pdf,
                page,
                font_name,
                "".join(missing),
                substitution_log=substitution_log,
            )
            # Evict stale resolver so _get_font_resolver re-parses
            resolver_cache.evict(page, font_name)
            # Evict stale width cache entry: extend_subset adds new CIDs
            # to /W, but width_cache holds the pre-extension dict and
            # would return DEFAULT_WIDTH (600) for newly-added CIDs.
            width_cache.evict(font_name)
            resolver = _get_font_resolver(page, font_name, resolver_cache)
            can_enc_after, still_missing = resolver.can_encode(new_text)
            if not can_enc_after:
                return EditResult(
                    success=False,
                    original_text=match.matched_text,
                    new_text=new_text,
                    font_action="failed",
                    fidelity_report=FidelityReport(
                        font_substituted=None,
                        overflow_detected=False,
                        reflow_applied=False,
                        glyphs_missing=still_missing,
                        degradations=[
                            Degradation(
                                kind="font_extension_failed",
                                detail="partial_fail",
                                severity="error",
                            ),
                        ],
                    ),
                ), resolver
            font_action = "extended"
            logger.info(
                "Font extension (%s) succeeded for %d missing chars",
                tier,
                len(missing),
            )
        except (FontNotFoundError, PDFEditError) as exc:
            return EditResult(
                success=False,
                original_text=match.matched_text,
                new_text=new_text,
                font_action="failed",
                fidelity_report=FidelityReport(
                    font_substituted=None,
                    overflow_detected=False,
                    reflow_applied=False,
                    glyphs_missing=missing,
                    degradations=[
                        Degradation(
                            kind="font_extension_failed",
                            detail=type(exc).__name__,
                            severity="error",
                        ),
                    ],
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

        # All-narrow fallback (ARY-276): if the match consists entirely of
        # narrow operators with no wide anchor to merge into, collapse
        # everything into the first operator.  Word and Chrome emit large-
        # font titles as per-glyph Tm+Tj operator pairs — each individual
        # Tm is sized for the original glyph advance, so leaving those
        # operators independent creates visible gaps between rendered
        # character clusters after replacement.  Routing the entire match
        # through one anchor lets PDF text flow past the original operator
        # boundaries (text is not clipped by operator boundaries), and the
        # cleared non-anchor operators render against their original Tm
        # positions with empty strings — harmless.
        if deferred and last_multi is None and len(deferred) >= 2:
            anchor = deferred[0]
            # Compute the full visual span from the anchor's first char to
            # the last matched char so _encode_with_kerning gets the
            # correct target width (sum of per-op glyph widths alone
            # misses the inter-operator Tm spacing that positions them).
            first_ch = chars_by_op[anchor][0]
            last_op = deferred[-1]
            last_ch = chars_by_op[last_op][-1]
            full_span = (last_ch.page_x + last_ch.width) - first_ch.page_x
            anchor_width = sum(ch.width for ch in chars_by_op[anchor])
            for s in deferred[1:]:
                op_replacement_map[anchor] = op_replacement_map.get(
                    anchor, ""
                ) + op_replacement_map.get(s, "")
                op_replacement_map[s] = ""
            merged_width_bonus[anchor] = max(0.0, full_span - anchor_width)

    # For merged operators, compute the target width as the Tm gap to the next
    # non-empty operator.  sum(ch.width) misses inter-operator spacing that the
    # original Tm positions encode.  Using the Tm gap ensures the replacement
    # text fills exactly the visual space between operators.
    active_ops = sorted(op_idx for op_idx in chars_by_op if op_replacement_map.get(op_idx, ""))

    # v0.1.3 (Phase 2): collect (tz_factor, degradation) per op for the
    # Tz post-pass below. The kerning loop runs in both dry_run and
    # non-dry-run paths because design doc §4c locks degradations parity:
    # dry_run=True must produce the same Degradation list as dry_run=False
    # for the same input. Ops mutation is invisible in dry_run (the save
    # is skipped further up the call chain).
    op_tz_factors: dict[int, float] = {}
    kerning_degradations: list[Degradation] = []

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

        op_tz: float | None = None
        op_deg: Degradation | None = None
        if op_str in ("TJ",):
            op_tz, op_deg = _modify_tj_operator(
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
            op_tz, op_deg = _modify_tj_single_operator(
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

        if op_tz is not None:
            op_tz_factors[op_idx] = op_tz
        if op_deg is not None:
            kerning_degradations.append(op_deg)

    # Tz post-pass: wrap each affected op_idx with `Tz <factor>` ... `Tz 100`.
    # Iterate in REVERSE op_idx order so the .insert() calls don't shift
    # indices we still have to process. Only mutate when not dry_run; the
    # degradations themselves were already collected above.
    if not dry_run and op_tz_factors:
        tz_op = pikepdf.Operator("Tz")
        for op_idx in sorted(op_tz_factors.keys(), reverse=True):
            tz_factor = op_tz_factors[op_idx]
            ops.insert(op_idx + 1, ([100], tz_op))
            ops.insert(op_idx, ([round(tz_factor, 3)], tz_op))

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
            # INV-C-4: surface the metric-equivalent name (if any).
            font_substituted=substitution_log[0] if substitution_log else None,
            overflow_detected=overflow,
            reflow_applied=False,
            glyphs_missing=[],
            degradations=list(kerning_degradations),
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
) -> tuple[float | None, Degradation | None]:
    """Modify a TJ operator's array to apply replacement text.

    Returns ``(tz_factor, degradation)`` for the caller to wrap the
    operator with PDF ``Tz`` operators (in a separate post-pass to keep
    operator-index stability across multiple edits) and to plumb the
    Degradation up through the FidelityReport. Both fields are ``None``
    when no kerning/scaling was applied (e.g., same-length path).
    """
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
        return None, None
    else:
        # Different-length or empty: rebuild the TJ array, applying Tz scaling
        # if the replacement width differs materially from the original.
        tz_factor: float | None = None
        degradation: Degradation | None = None
        if (
            replacement_text
            and width_cache is not None
            and page is not None
            and font_name
            and font_size
        ):
            op_original_width = sum(ch.width for ch in op_chars) + width_bonus
            enc = _encode_with_kerning(
                replacement_text,
                op_original_width,
                font_size,
                resolver,
                width_cache,
                page,
                font_name,
            )
            replacement_items = enc.tj_items
            tz_factor = enc.tz_factor
            degradation = enc.degradation
        elif replacement_text:
            replacement_items = [pikepdf.String(resolver.encode(replacement_text))]
        else:
            replacement_items = []
        new_array = _rebuild_tj_array(tj_items, op_chars, replacement_items)
        ops[op_idx] = ([new_array], operator)
        return tz_factor, degradation


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
) -> tuple[float | None, Degradation | None]:
    """Modify a Tj (or ') operator's string to apply replacement text.

    Returns ``(tz_factor, degradation)`` — see ``_modify_tj_operator``.
    """
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
        return None, None
    elif replacement_text:
        # Different-length: use Tz scaling if available, convert to TJ
        if width_cache is not None and page is not None and font_name and font_size:
            min_pos = min(ch.byte_position for ch in op_chars)
            max_pos = max(ch.byte_position for ch in op_chars) + byte_width
            prefix_bytes = raw[:min_pos]
            suffix_bytes = raw[max_pos:]
            op_original_width = sum(ch.width for ch in op_chars) + width_bonus
            enc = _encode_with_kerning(
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
            tj_items.extend(enc.tj_items)
            if suffix_bytes:
                tj_items.append(pikepdf.String(suffix_bytes))
            ops[op_idx] = ([pikepdf.Array(tj_items)], pikepdf.Operator("TJ"))
            return enc.tz_factor, enc.degradation
        else:
            min_pos = min(ch.byte_position for ch in op_chars)
            max_pos = max(ch.byte_position for ch in op_chars) + byte_width
            encoded = resolver.encode(replacement_text)
            new_raw = raw[:min_pos] + encoded + raw[max_pos:]
            ops[op_idx] = ([pikepdf.String(new_raw)], operator)
            return None, None
    else:
        # Empty replacement: remove matched bytes
        min_pos = min(ch.byte_position for ch in op_chars)
        max_pos = max(ch.byte_position for ch in op_chars) + byte_width
        new_raw = raw[:min_pos] + raw[max_pos:]
        ops[op_idx] = ([pikepdf.String(new_raw)], operator)
        return None, None


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

    # Per-call caches (ARY-283): every public entrypoint owns its caches
    # and threads them to helpers; no module-level shared state.
    resolver_cache = FontResolverCache()
    width_cache = GlyphWidthCache()

    pdf = open_pdf(pdf_path)
    try:
        if pdf.is_encrypted:
            raise PDFEditError("Cannot edit encrypted PDF")

        if match.page_number >= len(pdf.pages):
            raise OperatorError(
                f"Page {match.page_number} out of range (PDF has {len(pdf.pages)} pages)"
            )

        page = pdf.pages[match.page_number]
        font_name = match.characters[0].font_name
        resolver = _get_font_resolver(page, font_name, resolver_cache)

        # INV-B-3: refuse stale TextMatch input. Parse the current
        # content-stream and verify that match.operator_refs still
        # address the recorded matched_text. If the PDF was mutated
        # since find() was called, operator indices may now point at
        # unrelated text — silently splicing over them would corrupt
        # the output. The parsed ops are reused below for the simple-
        # replace path, so this validation is essentially free.
        ops = list(pikepdf.parse_content_stream(page))
        _assert_match_addressable(ops, match, resolver)

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
                # Only reflow if meaningfully wider (>1pt avoids trivial diffs).
                needs_reflow = new_width > old_width + 1.0
            except (KeyError, EncodingError, FontNotFoundError):
                # Encoding failure (KeyError from resolver.encode when the
                # replacement needs glyphs outside the embedded subset) or
                # width-lookup failure — route to simple replacement, which
                # has its own extension path in _apply_single_replacement.
                # This is ARY-282 design: when we cannot cheaply compute
                # new_width, we defer the decision to simple-replace rather
                # than unconditionally triggering reflow (reflow would
                # invalidate operator_refs of subsequent matches in
                # replace_all's multi-match-per-page loop).
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
                    paragraphs = _detect_paragraphs_from_index(elements)
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
                            resolver_cache,
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

        # ops already parsed above for the addressability check; reuse it.
        result, _ = _apply_single_replacement(
            pdf,
            page,
            ops,
            match,
            new_text,
            resolver,
            width_cache,
            resolver_cache,
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
    resolver_cache: FontResolverCache,
    width_cache: GlyphWidthCache,
) -> EditResult | None:
    """Attempt reflow for a single match.  Returns EditResult on success, None on failure."""
    try:
        font_name = match.characters[0].font_name
        resolver = _get_font_resolver(page, font_name, resolver_cache)
        old_width = sum(ch.width for ch in match.characters)
        new_width = _calculate_new_width(
            new_text,
            page,
            font_name,
            match.characters[0].font_size,
            resolver,
            width_cache,
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
        paragraphs = _detect_paragraphs_from_index(elements)
        para = find_paragraph_for_match(paragraphs, match)
        if para is None:
            return None

        font_key = font_name if font_name.startswith("/") else f"/{font_name}"
        font_ref = page["/Resources"]["/Font"][font_key]
        result = reflow_paragraph(
            pdf, page, para, match, new_text, resolver, font_ref, resolver_cache
        )
        return result if result.success else None
    except (ReflowError, OperatorError, EncodingError, FontNotFoundError, KeyError, ValueError):
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
    if not dry_run:
        validate_output_path(output_path)
    from pdf_edit_engine.locator import find

    matches = find(pdf_path, search)
    if not matches:
        return []

    # Per-call caches (ARY-283)
    resolver_cache = FontResolverCache()
    width_cache = GlyphWidthCache()

    pdf = open_pdf(pdf_path)
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
                resolver_cache,
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
                        pdf,
                        page,
                        page_num,
                        m,
                        replacement,
                        resolver_cache,
                        width_cache,
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
                        pdf,
                        page,
                        ops,
                        m,
                        replacement,
                        resolver,
                        width_cache,
                        resolver_cache,
                        dry_run,
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
    if not dry_run:
        validate_output_path(output_path)
    from pdf_edit_engine.locator import find

    # Per-call caches (ARY-283)
    resolver_cache = FontResolverCache()
    width_cache = GlyphWidthCache()

    pdf = open_pdf(pdf_path)
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
                    reflow_result = _try_reflow_match(
                        pdf, page, page_num, m, repl, resolver_cache, width_cache
                    )
                    if reflow_result is not None:
                        edit_results[edit_idx].append(reflow_result)
                        used_ops_by_page[page_num].update(m.operator_refs)
                        any_success = True
                        page_reflowed = True
                        ops = list(pikepdf.parse_content_stream(page))
                        continue

                resolver = _get_font_resolver(page, m.characters[0].font_name, resolver_cache)
                try:
                    result, resolver = _apply_single_replacement(
                        pdf,
                        page,
                        ops,
                        m,
                        repl,
                        resolver,
                        width_cache,
                        resolver_cache,
                        dry_run,
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
    """Clear the locator module's content element cache after a content-stream edit.

    The locator is the only remaining shared cache across public calls —
    this module's resolver/width caches live for the duration of one
    public call and are garbage-collected when it returns.
    """
    from pdf_edit_engine import locator

    locator._cached_path = None  # noqa: SLF001
    locator._cached_elements = {}  # noqa: SLF001
