"""OperatorSurgeon module — modify PDF content stream operators."""

from __future__ import annotations

import logging
from collections import defaultdict
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
from pdf_edit_engine.encoding import FontResolver, FontResolverCache
from pdf_edit_engine.errors import (
    EncodingError,
    FontNotFoundError,
    FontStreamTooLargeError,
    OperatorError,
    ReflowError,
)
from pdf_edit_engine.fonts import _FONT_EXTEND_FAIL_EXCS
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

# INV-B-12: the typed DegradationKind surfaced when colliding same-operator
# matches are refused. Defined once so replace_all and batch_replace agree.
_MULTI_MATCH_KIND = "multi_match_same_operator_unsupported"


def _splice_ops(match: TextMatch) -> frozenset[int]:
    """Operator indices the match's CHARACTERS actually splice bytes into.

    INV-B-12 detection key. NOT ``operator_refs`` (which can include
    positioning/Tf operators in the span that never receive show-text bytes);
    only the show-text operators whose operand is mutated. Mirrors
    ``_apply_single_replacement``'s ``chars_by_op`` grouping
    (``ch.operator_index for ch in match.characters``), so the collision set
    matches the actual splice surface exactly.

    Args:
        match: The text match to inspect.

    Returns:
        The frozen set of operator indices the match writes into.
    """
    return frozenset(ch.operator_index for ch in match.characters)


def _is_shifting(match: TextMatch, replacement: str) -> bool:
    """True iff this edit routes through the byte-SHIFTING rebuild path.

    INV-B-12: a same-length, non-ligature replacement splices fixed-width
    bytes at each ``byte_position`` with ZERO byte shift (``_modify_tj_*``
    same-length path), so sibling same-operator matches stay byte-stable and
    MUST NOT be refused. Only the rebuild path (length change OR ligature) is
    byte-shifting and therefore corrupts later same-operand matches. This
    mirrors ``_apply_single_replacement``'s top-level determinant
    (``same_length = (not has_ligatures) and len(new_text) == len(matched)``).

    The length test keys on the match's OWN ``matched_text`` — the exact
    determinant ``_apply_single_replacement`` uses — NOT the call's global
    search term, which can DIVERGE from ``matched_text`` when ``find`` returns a
    cluster whose length differs from the NFC needle (combining marks). Keying
    on ``matched_text`` keeps the refusal decision and the real splice path in
    lockstep.

    Args:
        match: The match whose characters + matched_text determine the path.
        replacement: The replacement text for this call/pair.

    Returns:
        True when the edit is length-changing or ligature-forcing.
    """
    cid_slots = len(
        {(ch.operator_index, ch.tj_fragment_index, ch.byte_position) for ch in match.characters}
    )
    has_ligatures = cid_slots != len(match.matched_text)
    return has_ligatures or (len(replacement) != len(match.matched_text))


def _colliding_shifting_matches(
    page_matches: list[TextMatch], replacement: str, *, require_shift: bool = True
) -> tuple[set[int], dict[int, int]]:
    """Identify matches that must be refused under INV-B-12.

    A match is refused iff it is in a COLLIDING group (>=2 matches on the page
    whose splice-op sets intersect, transitively unioned) AND, when
    ``require_shift`` is True, the edit is byte-SHIFTING for that match
    (``_is_shifting``). With ``require_shift=True`` (``replace_all``) a colliding
    group under a same-length non-ligature edit is NOT refused — replace_all
    splices all of them byte-stably in one pass. With ``require_shift=False``
    (``batch_replace`` same-edit groups, which its one-result-per-edit /
    used_ops architecture cannot apply byte-stably like replace_all) EVERY member
    of a colliding group is refused regardless of length.

    The decision is a pure function of the matches + the per-call replacement
    length, computed BEFORE any mutation, so dry_run and live take the identical
    refusal path (dry_run parity).

    Args:
        page_matches: The matches on a single page (any order).
        replacement: The replacement text for this call/pair.
        require_shift: When True (default), refuse only byte-shifting collisions;
            when False, refuse every colliding-group member regardless of length.

    Returns:
        A pair ``(refused, group_size)`` where ``refused`` is the set of
        ``id(match)`` values to refuse and ``group_size`` maps ``id(match)`` to
        the size of its colliding group (for the detail string).
    """
    n = len(page_matches)
    splice_sets = [_splice_ops(m) for m in page_matches]

    # Union-find over splice-op-set intersection to build collision groups.
    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        if not splice_sets[i]:
            continue
        for j in range(i + 1, n):
            if splice_sets[i] & splice_sets[j]:
                _union(i, j)

    group_members: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        group_members[_find(i)].append(i)

    refused: set[int] = set()
    group_size: dict[int, int] = {}
    for members in group_members.values():
        if len(members) < 2:
            continue
        for i in members:
            m = page_matches[i]
            if (not require_shift) or _is_shifting(m, replacement):
                refused.add(id(m))
                group_size[id(m)] = len(members)
    return refused, group_size


def _multi_match_refusal_result(match: TextMatch, replacement: str, group_size: int) -> EditResult:
    """Build the honest INV-B-12 refusal ``EditResult`` for a colliding match.

    ``font_action="kept"`` (NOT ``"failed"``): no edit was applied, so the
    INV-J-9 construction guard does not require a font-affecting kind, and
    ``font_preserved`` computes True (the kind is NOT in FONT_AFFECTING_KINDS).

    Args:
        match: The refused match.
        replacement: The replacement text (recorded on the result).
        group_size: Number of matches in the colliding group.

    Returns:
        An ``EditResult`` with ``success=False`` and the typed Degradation.
    """
    ops_list = sorted(_splice_ops(match))
    return EditResult(
        success=False,
        original_text=match.matched_text,
        new_text=replacement,
        font_action="kept",
        warnings=["Skipped: multiple matches share one content-stream operator"],
        fidelity_report=FidelityReport(
            font_substituted=None,
            overflow_detected=False,
            reflow_applied=False,
            glyphs_missing=[],
            degradations=[
                Degradation(
                    kind=_MULTI_MATCH_KIND,  # type: ignore[arg-type]
                    detail=(
                        f"{group_size} matches splice into shared operator(s) "
                        f"{ops_list}; refused to avoid stale-offset corruption "
                        "(per-match offset rewrite deferred to 0.3.0; use "
                        "replace_all for same-length edits)"
                    ),
                    severity="warning",
                )
            ],
        ),
    )


def _same_edit_colliding(
    page_pairs: list[tuple[TextMatch, str, int]],
) -> tuple[set[int], dict[int, int]]:
    """INV-B-12 group refusal for batch_replace: SAME-edit operator collisions.

    A single ``Edit`` whose search term matches N>1 times into ONE show-text
    operator is the ``replace_all`` corruption surface reached via batch.
    Unlike ``replace_all`` — which splices every same-operator match in one
    byte-stable pass for a same-length edit — ``batch_replace``'s
    one-result-per-edit aggregation + ``used_ops`` backstop can only apply the
    FIRST such match and silently skips the rest. So batch refuses the WHOLE
    same-edit same-operator group REGARDLESS of length (``require_shift=False``):
    an honest ``success=False`` + typed kind beats a silent partial edit. A
    CROSS-edit overlap (two DIFFERENT edits touching one operator) is NOT refused
    here — it keeps the reactive used_ops "first edit wins" backstop.

    For each ``edit_idx`` independently, the matches of that edit on the page are
    grouped by splice-op intersection via ``_colliding_shifting_matches`` with
    ``require_shift=False``. A match is refused iff its own edit produced a
    colliding group of size >=2.

    The decision is a pure function of the matches + per-edit lengths computed
    pre-mutation, so dry_run parity holds.

    Args:
        page_pairs: The ``(match, replacement, edit_idx)`` tuples on one page.

    Returns:
        ``(refused_ids, group_size)`` keyed by ``id(match)``.
    """
    by_edit: dict[int, list[tuple[TextMatch, str]]] = defaultdict(list)
    for m, repl, edit_idx in page_pairs:
        by_edit[edit_idx].append((m, repl))

    refused: set[int] = set()
    group_size: dict[int, int] = {}
    for items in by_edit.values():
        edit_matches = [m for m, _ in items]
        if len(edit_matches) < 2:
            continue
        for m, repl in items:
            r, sizes = _colliding_shifting_matches(edit_matches, repl, require_shift=False)
            if id(m) in r:
                refused.add(id(m))
                group_size[id(m)] = sizes[id(m)]
    return refused, group_size


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
        # F-C-03 / INV-W0-9: drop {exc} (the missing CID bytes) from the
        # user-visible message; type name is stable across encoding
        # variants and the full forensic detail lives in the log below.
        logger.error(
            "stale TextMatch decode failure at op %d byte %d",
            op_idx,
            bp,
            exc_info=True,
        )
        raise OperatorError(
            f"Stale TextMatch: bytes at op {op_idx} byte {bp} cannot be "
            f"decoded by the current font ({type(exc).__name__}). Re-run find()."
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


def _matrix_is_axis_aligned(a: float, b: float, c: float, d: float, eps: float = 1e-3) -> bool:
    """Return True iff the text-matrix linear part is the identity within ``eps``.

    Pure function (no PDF state). The governing text matrix's linear part
    ``(a, b, c, d)`` is "axis-aligned" when it is the identity
    (``a~1, b~0, c~0, d~1``). Any rotation, shear, or non-unit scale bakes
    a non-identity linear part, which means a horizontal width-delta in
    text space no longer maps 1:1 to a horizontal page-space shift — the
    trailing-text compensation would be on the wrong axis.

    Args:
        a: Text-matrix entry [0] (x-scale / cos for rotation).
        b: Text-matrix entry [1] (shear / sin for rotation).
        c: Text-matrix entry [2] (shear / -sin for rotation).
        d: Text-matrix entry [3] (y-scale / cos for rotation).
        eps: Epsilon for the identity comparison.

    Returns:
        True when the linear part is the identity within ``eps``.
    """
    return abs(a - 1.0) < eps and abs(b) < eps and abs(c) < eps and abs(d - 1.0) < eps


def _positioning_decision(
    is_axis_aligned: bool,
    a: float,
    b: float,
    c: float,
    d: float,
    width_delta: float,
) -> Degradation | None:
    """Map an axis-aligned verdict to an optional ``positioning_adjustment_skipped``.

    Pure function (no PDF state), mirroring ``_kerning_decision`` so the
    decision is unit-testable and dry_run-parity-safe: it is computed
    identically in both the dry_run and non-dry-run paths, while only the
    ops mutation in ``_adjust_subsequent_positioning`` stays gated on
    ``not dry_run``.

    Returns a typed ``Degradation(kind="positioning_adjustment_skipped",
    severity="warning")`` when the governing text matrix is non-axis-aligned
    (the horizontal trailing-text compensation will be declined), else
    ``None``.
    """
    if is_axis_aligned:
        return None
    return Degradation(
        kind="positioning_adjustment_skipped",
        detail=f"tm=[{a:.3f} {b:.3f} {c:.3f} {d:.3f}],width_delta={width_delta:.2f}pt",
        severity="warning",
    )


_INLINE_IMAGE_OPS: frozenset[str] = frozenset({"INLINE IMAGE", "BI", "ID", "EI"})
_SHOW_TEXT_OPS: frozenset[str] = frozenset({"Tj", "TJ", "'", '"'})


def _op_str(inst: Any) -> str:
    """Return the operator string for a parsed instruction or (operands, op) tuple."""
    if hasattr(inst, "operator"):
        return str(inst.operator)
    return str(inst[1])


def _inline_image_in_span(ops: _Ops, operator_refs: list[int]) -> bool:
    """Detect a ``BI``/``ID``/``EI`` inline image in or adjacent to a deletion span.

    B.11 / INV-B-10. The span runs from the enclosing ``BT`` preceding the
    first matched operator to the next ``BT`` after the matched block's
    ``ET`` (exclusive) — so an inline image that immediately follows the
    deleted run's ``ET`` (between two text blocks) is treated as adjacent.
    pikepdf collapses ``BI``/``ID``/``EI`` to ONE stable ``INLINE IMAGE``
    operator slot (A1.4), so this is a pure scan over the parsed ops; the
    deletion still proceeds and the caller surfaces an advisory
    ``inline_image_present`` info Degradation.

    Args:
        ops: Parsed content-stream instruction list (read-only here).
        operator_refs: The match's operator indices.

    Returns:
        True iff an inline-image operator lies in/adjacent to the span.
    """
    if not operator_refs:
        return False
    lo = min(operator_refs)
    hi = max(operator_refs)
    # Walk back to the enclosing BT (or stream start).
    start = lo
    for i in range(lo, -1, -1):
        if _op_str(ops[i]) == "BT":
            start = i
            break
        start = i
    # Walk forward to the next BT after hi (exclusive), capturing the
    # inter-block region where an adjacent inline image lives.
    end = hi
    seen_et = False
    for i in range(hi, len(ops)):
        op = _op_str(ops[i])
        if op == "ET":
            seen_et = True
        elif op == "BT" and seen_et:
            break
        end = i
    for i in range(start, min(end, len(ops) - 1) + 1):
        if _op_str(ops[i]) in _INLINE_IMAGE_OPS:
            return True
    return False


def _operand_bytes_after_deletion(
    ops: _Ops, op_idx: int, op_chars: list[TextCharacter], byte_width: int
) -> bytes:
    """Return the concatenated string bytes the op's operand would carry post-delete.

    B.11 / INV-B-10 helper for the residue predicate. Reads the CURRENT
    operand of ``ops[op_idx]`` (already emptied when the caller mutated it,
    or the original in a dry_run) and returns the concatenation of all its
    string fragments. Used only to compare against the matched CID byte run.

    Args:
        ops: Parsed content-stream instruction list.
        op_idx: Operator index to read.
        op_chars: Match characters in this operator (unused for the read but
            kept for signature symmetry with the residue scan).
        byte_width: Resolver byte width (unused here; kept for symmetry).

    Returns:
        The concatenated string-operand bytes currently held by the op.
    """
    inst = ops[op_idx]
    operands = inst.operands if hasattr(inst, "operands") else inst[0]
    if not operands:
        return b""
    first: Any = operands[0]
    out = bytearray()
    if _op_str(inst) == "TJ":
        for item in list(first):
            if isinstance(item, pikepdf.String):
                out += bytes(item)
    elif isinstance(first, pikepdf.String):
        out += bytes(first)
    return bytes(out)


def _matched_cid_run(
    ops: _Ops, op_idx: int, op_chars: list[TextCharacter], byte_width: int
) -> bytes:
    """Extract the contiguous matched CID byte run from an operator's ORIGINAL operand.

    B.11 / INV-B-10. Captured before the deletion empties the operand so the
    residue predicate has the exact byte sub-sequence that must disappear.
    For a ``Tj``/``'`` the operand is one string; for a ``TJ`` the matched
    chars span string fragments — the run is the concatenation of the matched
    bytes within each touched fragment. The width per glyph is the resolver's
    ``byte_width`` (2 for Identity-H, 1 for simple fonts).

    Args:
        ops: Parsed content-stream instruction list (pre-emptying).
        op_idx: Operator index of the show-text op.
        op_chars: Match characters in this operator.
        byte_width: Resolver byte width.

    Returns:
        The contiguous matched CID byte run, or ``b""`` when not extractable.
    """
    inst = ops[op_idx]
    operands = inst.operands if hasattr(inst, "operands") else inst[0]
    if not operands:
        return b""
    first: Any = operands[0]
    bw = byte_width if byte_width > 0 else 1
    run = bytearray()
    if _op_str(inst) == "TJ":
        tj_items: list[Any] = list(first)
        chars_by_frag: dict[int, list[TextCharacter]] = defaultdict(list)
        for ch in op_chars:
            if ch.tj_fragment_index is not None:
                chars_by_frag[ch.tj_fragment_index].append(ch)
        for frag_idx in sorted(chars_by_frag):
            try:
                arr_idx = _nth_string_index(tj_items, frag_idx)
            except OperatorError:
                continue
            raw = bytes(tj_items[arr_idx])
            frag_chars = chars_by_frag[frag_idx]
            lo = min(ch.byte_position for ch in frag_chars)
            hi = max(ch.byte_position for ch in frag_chars) + bw
            run += raw[lo : min(hi, len(raw))]
    elif isinstance(first, pikepdf.String):
        raw = bytes(first)
        lo = min(ch.byte_position for ch in op_chars)
        hi = max(ch.byte_position for ch in op_chars) + bw
        run += raw[lo : min(hi, len(raw))]
    return bytes(run)


def _deletion_residue_proven(
    ops: _Ops,
    chars_by_op: dict[int, list[TextCharacter]],
    op_replacement_map: dict[int, str],
    matched_cid_bytes: dict[int, bytes],
    original_operand_bytes: dict[int, bytes],
) -> bool:
    """Prove whether a keep-slot deletion left the deleted occurrence behind.

    B.11 / INV-B-10, over-fire root-fix. Position/count-accurate predicate:
    for every operator whose matched chars were fully deleted
    (``op_replacement_map`` empty for that op), count how many times the exact
    contiguous matched CID byte run occurs in the operand BEFORE emptying vs
    AFTER. A correct deletion of THIS occurrence must drop that count by at
    least one (the byte slot the deletion emptied no longer carries the run).
    Residue is proven only when the count did NOT decrease — i.e. the matched
    run is still present at the SAME multiplicity, so the specific deleted
    occurrence was not actually cleared.

    This replaces the prior GLOBAL ``run in current`` substring search, which
    mis-fired when a byte-IDENTICAL run sat at an UNRELATED, untouched position
    in the same operand: the deletion correctly emptied its own slot, but the
    surviving identical repeat made ``run in current`` True, falsely flipping
    ``success=False`` and (because the surgeon gates the save on success)
    writing NO output for a CORRECT delete — silent data loss. Counting
    occurrences before/after makes a correct single-occurrence delete among
    identical repeats report no residue (count drops by exactly one → output
    written), while a genuinely un-cleared run (count unchanged) is still
    caught. As a bonus this surfaces an honest ``success=False`` for a
    multi-match-same-operator deletion that left survivors instead of
    silently corrupting (see the deferred follow-up note below).

    The check is a pure function of the captured-before and current operand
    bytes, so it yields the same verdict in dry_run (where the modify loop
    mutated the in-memory ops identically and only the save is gated) and in
    the live path → dry_run parity.

    NOTE (the multi-match-SAME-operator REPLACE corruption, NOT a deletion bug):
    this predicate does not repair the pre-existing stale-byte-position
    corruption on the reverse-order multi-match REPLACE path (it predates B.11
    and reproduces identically for a non-empty replacement, so it is general,
    not deletion-specific). The count-delta here surfaces the DELETION variant
    honestly (success=False). As of INV-B-12 (v0.2.0) the engine now REFUSES the
    colliding same-operator + byte-shifting matches up front in ``replace_all`` /
    ``batch_replace`` (typed ``multi_match_same_operator_unsupported``
    Degradation, ``success=False``) rather than silently corrupting — see
    ``_colliding_shifting_matches``. The full reverse-order
    offset-rederivation rewrite (re-deriving each match's offsets against the
    mutated operand) remains the 0.3.0 stretch.

    Args:
        ops: Parsed content-stream instruction list (post-emptying state).
        chars_by_op: Match characters grouped by operator index.
        op_replacement_map: Per-operator replacement text (empty => deleted).
        matched_cid_bytes: Per-operator contiguous CID byte run that was
            matched, captured from the ORIGINAL operand before emptying.
        original_operand_bytes: Per-operator full concatenated operand bytes
            captured from the ORIGINAL operand before emptying.

    Returns:
        True iff any deleted operator's matched-run occurrence count did not
        decrease (the specific deleted occurrence still renders).
    """
    for op_idx, op_chars in chars_by_op.items():
        if op_replacement_map.get(op_idx, ""):
            continue  # not a full deletion of this op's span
        run = matched_cid_bytes.get(op_idx, b"")
        if not run:
            continue
        before = original_operand_bytes.get(op_idx, b"")
        current = _operand_bytes_after_deletion(ops, op_idx, op_chars, len(run))
        before_count = before.count(run)
        after_count = current.count(run)
        # A correct deletion of this occurrence drops the count by >= 1. Residue
        # only when the count is unchanged (the deleted occurrence survived).
        if after_count >= before_count:
            return True
    return False


def _governing_tm_linear(
    ops: _Ops, first_op_index: int
) -> tuple[float, float, float, float] | None:
    """Recover the governing text matrix's linear part for an edited run.

    Scans BACKWARD from ``first_op_index`` to the nearest preceding ``Tm``
    operator, stopping at the block-start ``BT`` operator. Returns the
    ``Tm`` operands ``[0..3]`` as ``(a, b, c, d)``. When ``BT`` is reached
    with no intervening ``Tm`` (the matrix is the BT-reset identity), or no
    ``Tm`` is found before the start of the stream, returns ``None`` — the
    caller treats ``None`` as axis-aligned (identity).

    Args:
        ops: Parsed content stream instruction list.
        first_op_index: Index of the first operator in the match
            (``min(match.operator_refs)``); the scan starts just before it.

    Returns:
        ``(a, b, c, d)`` linear part of the governing ``Tm``, or ``None``
        when no governing ``Tm`` precedes the run within its text block.
    """
    for i in range(first_op_index, -1, -1):
        inst = ops[i]
        op_str = str(inst.operator) if hasattr(inst, "operator") else str(inst[1])
        if op_str == "Tm":
            operands = inst.operands if hasattr(inst, "operands") else inst[0]
            if len(operands) >= 4:
                return (
                    float(operands[0]),
                    float(operands[1]),
                    float(operands[2]),
                    float(operands[3]),
                )
            return None
        if op_str == "BT":
            # Block start with no preceding Tm — text matrix is the
            # BT-reset identity (axis-aligned).
            return None
    return None


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
    observed: list[str] | None = None,
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
        observed: B.9 out-param threaded to ``encode`` — collects any source
            substring collapsed into a ligature CID so the caller can surface a
            ``ligature_substituted`` Degradation. None on width-only probes.

    Returns:
        KerningEncoding with tj_items (flat single-string list when
        non-empty), tz_factor (None or the percentage to scale by), and
        an optional Degradation event.
    """
    if not text:
        return KerningEncoding(tj_items=[], tz_factor=None, degradation=None)

    bw = resolver.byte_width
    glyph_widths_fu: list[float] = []
    full_encoded = resolver.encode(text, _observed=observed)
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
    byte_width: int = 0,
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
        byte_width: B.11 / INV-B-10 — the resolver's KNOWN per-glyph byte
            width (2 for Identity-H, 1 for simple fonts). When > 0, the
            last-fragment suffix boundary is computed as
            ``max(byte_position) + byte_width`` so it lands EXACTLY on a CID
            boundary. The pre-B.11 default (0) falls back to the per-fragment
            ``_infer_byte_width`` heuristic, which mis-inferred the boundary
            on a SHARED TJ fragment carrying a matched glyph + an unmatched
            in-operator neighbour (the "Software" delete eating "Developer"'s
            leading "D" — the fragment was ``<011e 0003 0018>`` and a
            single matched char at byte 0 made ``_infer_byte_width`` return
            ``len(raw)//1 == 6``, swallowing the trailing space + neighbour).
            Threading the exact width is the root fix; the simple-font path
            keeps the heuristic byte-for-byte by leaving ``byte_width`` 0.

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
    # Determine byte_width from character data. B.11 / INV-B-10: prefer the
    # resolver's KNOWN byte_width (threaded by the caller) so the suffix
    # boundary lands exactly on a CID boundary; only fall back to the
    # per-fragment heuristic when the caller did not supply it (the
    # simple-font path keeps its pre-B.11 behaviour byte-for-byte).
    if len(last_frag_raw) > 0 and last_chars:
        effective_bw = (
            byte_width if byte_width > 0 else _infer_byte_width(last_frag_raw, last_chars)
        )
        max_byte_end = max(ch.byte_position for ch in last_chars) + effective_bw
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
    is_axis_aligned: bool = True,
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
        is_axis_aligned: When False (the governing text matrix is rotated
            or sheared), skip the horizontal compensation entirely — the
            ``-width_delta`` shift is along the wrong axis and would
            visibly mis-place the trailing text. Defaults to True so every
            existing caller's axis-aligned behaviour stays byte-identical.
    """
    # POS-GATE: a non-axis-aligned governing matrix means the horizontal
    # width-delta no longer maps 1:1 to a horizontal page shift. Decline the
    # compensation (the caller surfaces a positioning_adjustment_skipped
    # Degradation) rather than mutate the trailing operand on the wrong axis.
    if not is_axis_aligned:
        return
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
    # v0.1.3 (Phase 5, audit-bundle scope): record extension events as
    # typed Degradations on success — font_coverage_extended for Tier 1
    # (cmap-only) or font_coverage_substituted for Tier 1.5 (in-place
    # injection from a system font, possibly metric-equivalent).
    coverage_degradations: list[Degradation] = []
    # Pre-extension state of missing chars — captured BEFORE extend_subset
    # mutates the font. Per locked semantic (design doc §5 + Phase 5
    # docstring): glyphs_missing reflects what TRIGGERED the extension,
    # even after extension succeeds. Information-preserving for callers.
    pre_extension_missing: list[str] = []

    if not can_enc:
        # B.3 WRITE-path refusal (M0 Rank-2.5 verdict: WRITE = refuse-on-gap).
        # When the match's font had its CID→Unicode map RECOVERED from the
        # embedded cmap (no /ToUnicode), a new-glyph extension would have to
        # write a /ToUnicode CMap — out of scope for B.3 (the fonts.py
        # _append_to_unicode_cmap KeyError blocker). The same-subset case
        # never reaches here (can_enc is True), so this branch is strictly
        # the new-glyph path: refuse cleanly with a typed Degradation rather
        # than crashing on the missing /ToUnicode.
        if resolver.is_tounicode_recovered:
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
                            kind="tounicode_recovered",
                            detail="new_glyph_extension_unsupported_on_recovered_font",
                            severity="error",
                        ),
                    ],
                ),
            ), resolver

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
            # would return DEFAULT_WIDTH (600) for newly-added CIDs. The
            # in-place /W mutation does not change objgen, so this manual
            # eviction is still required after the objgen re-key (INV-W-1).
            width_cache.evict(page, font_name)
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
            # v0.1.3 Phase 5: record what was extended.
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
        except _FONT_EXTEND_FAIL_EXCS as exc:
            # Aligns surgeon's font-extension catch with reflow.py:1093 +
            # structural.py:883/1691. The prior tuple
            # (FontNotFoundError, PDFEditError) caught FontNotFoundError /
            # EncodingError / OperatorError / ReflowError but missed
            # OSError (filesystem) and TTLibError (corrupt /FontFile2),
            # which would escape the EditResult and propagate out of
            # replace() unhandled. PLAN_AMENDMENTS M.6 test 12 documents
            # the contract that TTLibError surfaces as a Degradation, not
            # a raised exception.
            # A1.3 / INV-W-4: a Flate decompression bomb in the embedded
            # font / CMap surfaces a SECOND, specific-cause
            # font_stream_too_large Degradation (warning) alongside the
            # font_extension_failed (error) that already drives success=False
            # and font_preserved=False.
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
                    reflow_applied=False,
                    glyphs_missing=missing,
                    degradations=degs,
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

    # B.11 / INV-B-10: a full deletion is new_text == "". Capture the exact
    # contiguous matched CID byte run per operator FROM THE ORIGINAL operand
    # (before the modify loop empties it) so the post-emptying residue
    # predicate can prove whether the run survived. Pure read; runs in both
    # dry_run and live paths (the modify loop mutates the in-memory ops
    # identically in both — only the save is gated), so the residue verdict
    # has dry_run parity. Also detect an inline image in/adjacent to the span
    # (advisory; A1.4 stable slot).
    is_deletion = new_text == ""
    matched_cid_bytes: dict[int, bytes] = {}
    # B.11 over-fire root-fix: capture the per-op ORIGINAL operand bytes (the
    # full concatenated string content BEFORE emptying) alongside the matched
    # run, so the residue predicate can compare the occurrence COUNT of the
    # matched run before vs after emptying — a position/count-accurate check —
    # instead of a GLOBAL substring search that mis-fires on an IDENTICAL but
    # untouched repeat elsewhere in the same operand. Empty in the non-deletion
    # path (no residue surfacing then).
    original_operand_bytes: dict[int, bytes] = {}
    if is_deletion:
        for op_idx, op_chars in chars_by_op.items():
            run = _matched_cid_run(ops, op_idx, op_chars, byte_width)
            if run:
                matched_cid_bytes[op_idx] = run
                original_operand_bytes[op_idx] = _operand_bytes_after_deletion(
                    ops, op_idx, op_chars, len(run)
                )
    inline_image_present = is_deletion and _inline_image_in_span(ops, match.operator_refs)

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
    # B.9 (INV-B-9): collect every source substring that encode() collapsed
    # into a ligature CID (mandatory always, opted-in discretionary). The
    # collapse decision is a pure function of the input text + font and is
    # computed in BOTH the dry_run and non-dry-run passes (this loop runs in
    # both), so the resulting ligature_substituted Degradation has dry_run
    # parity. Empty on the default typed-separate path.
    ligature_observed: list[str] = []

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
                observed=ligature_observed,
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
                observed=ligature_observed,
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

    # Adjust subsequent positioning if needed.
    #
    # POS-GATE: the horizontal width-delta compensation only maps 1:1 to a
    # page-space shift when the edited run's governing text matrix is
    # axis-aligned (identity linear part). Under a rotated/sheared matrix the
    # shift is on the wrong axis and silently mis-places the trailing text;
    # we skip it and surface a typed positioning_adjustment_skipped
    # Degradation instead. Design-doc §4c locks dry_run/non-dry-run
    # degradation PARITY, so the gate DECISION + Degradation are computed in
    # BOTH paths (mirroring the _kerning_decision pattern); only the ops
    # mutation in _adjust_subsequent_positioning stays gated on `not dry_run`.
    positioning_degradations: list[Degradation] = []
    if abs(width_delta) > 0.5:
        linear = _governing_tm_linear(ops, min(match.operator_refs))
        if linear is None:
            # No governing Tm before BT — text matrix is the BT-reset
            # identity, i.e. axis-aligned.
            is_axis_aligned = True
            tm_a, tm_b, tm_c, tm_d = 1.0, 0.0, 0.0, 1.0
        else:
            tm_a, tm_b, tm_c, tm_d = linear
            is_axis_aligned = _matrix_is_axis_aligned(tm_a, tm_b, tm_c, tm_d)

        pos_deg = _positioning_decision(is_axis_aligned, tm_a, tm_b, tm_c, tm_d, width_delta)
        if pos_deg is not None:
            positioning_degradations.append(pos_deg)

        # B.11 / INV-B-10: a FULL DELETION leaves the trailing text at its
        # ABSOLUTE position (no gap-collapse / no reclaim). The empty show-text
        # operand advances the text cursor by ZERO, and a following relative
        # `Td` is line-matrix-relative (NOT cursor-relative), so trailing
        # same-line text already lands at its original x WITHOUT compensation.
        # Applying the replacement-era `-width_delta` here would shove it RIGHT
        # by the deleted run's advance (the empirically-confirmed mis-shift).
        # So decline the horizontal compensation for the deletion case on the
        # axis-aligned path; the non-axis-aligned path keeps emitting
        # positioning_adjustment_skipped via _positioning_decision above.
        if not dry_run and not is_deletion:
            last_op_idx = max(match.operator_refs)
            match_y = match.characters[0].page_y
            _adjust_subsequent_positioning(
                ops,
                last_op_idx,
                width_delta,
                match_y,
                match.characters[0].font_size,
                is_axis_aligned=is_axis_aligned,
            )

    # Overflow detection
    page_width = float(page.MediaBox[2]) if page.MediaBox else 612.0
    overflow = (match.bounding_box[0] + new_width) > page_width

    # IMP-2: emit a typed Degradation when horizontal overflow is
    # detected. Tz post-pass (above) and _adjust_subsequent_positioning
    # already attempted to compress the layout; reaching this branch
    # means the page edge clamps the visible result. `overflow_shift_clamped`
    # is the closest match in the locked 12-kind enumeration (no
    # horizontal-overflow-specific kind exists; structural.replace_block
    # uses the same kind for analogous "shift was applied but bounded"
    # situations — see structural.py:1168-1186).
    overflow_degradations: list[Degradation] = []
    if overflow:
        overflow_degradations.append(
            Degradation(
                kind="overflow_shift_clamped",
                detail=(
                    f"horizontal,page_width={page_width:.1f}pt,"
                    f"replacement_right={(match.bounding_box[0] + new_width):.1f}pt"
                ),
                severity="warning",
            )
        )

    # B.9 (INV-B-9): surface a ligature_substituted info Degradation when
    # encode() actually chose a ligature CID (mandatory or opted-in
    # discretionary). Empty on the default typed-separate path → no Degradation.
    ligature_degradations: list[Degradation] = []
    if ligature_observed:
        ligature_degradations.append(
            Degradation(
                kind="ligature_substituted",
                severity="info",
                detail=f"applied ligature(s) {sorted(set(ligature_observed))} during re-encode",
            )
        )

    # B.11 / INV-B-10: deletion-cleanup surfacing. The keep-slot emptying loop
    # already mutated the in-memory ops in BOTH dry_run and live paths (only
    # the save is gated), so the residue predicate reads the post-emptying
    # operands identically in both → dry_run parity. Provable residue (the
    # count-accurate predicate: the matched run's occurrence count did NOT
    # decrease) flips success=False with a deletion_residual_text (warning); an
    # inline image in/adjacent to the span is an advisory inline_image_present
    # (info) that does NOT fail the edit (A1.4 stable slot). Both kinds are NOT
    # in FONT_AFFECTING_KINDS, so font_preserved stays True.
    #
    # INV-B-12 (v0.2.0): the multi-match-SAME-operator path (e.g.
    # replace_all("cat dog cat bird cat fish", "cat", "")) corrupts via STALE
    # byte-positions — replace_all routes each match through an independent
    # _apply_single_replacement on the SHARED, already-mutated operand; a match's
    # byte_position was recorded against the ORIGINAL operand, so after the first
    # byte-SHIFTING splice every later same-operator match reads the WRONG slice.
    # The non-deletion replacement (.., "X") corrupts IDENTICALLY, confirming a
    # general reverse-order multi-match REPLACE bug (NOT deletion-specific, NOT
    # introduced by B.11). The count-delta below cannot surface it: the stale
    # offset corrupts the matched-run CAPTURE itself, so each per-call check
    # honestly sees its (wrong) run drop by one. As of INV-B-12 the engine
    # REFUSES these colliding same-operator + byte-shifting matches up front in
    # replace_all / batch_replace (see _colliding_shifting_matches; typed
    # multi_match_same_operator_unsupported Degradation, success=False) so the
    # corrupt path is never reached. The full reverse-order offset-rederivation
    # rewrite (re-deriving each match's offsets against the mutated operand)
    # remains the 0.3.0 stretch.
    deletion_degradations: list[Degradation] = []
    success = True
    if is_deletion:
        if _deletion_residue_proven(
            ops, chars_by_op, op_replacement_map, matched_cid_bytes, original_operand_bytes
        ):
            success = False
            deletion_degradations.append(
                Degradation(
                    kind="deletion_residual_text",
                    detail=f"residual deleted text remains in op(s) {sorted(matched_cid_bytes)}",
                    severity="warning",
                )
            )
        if inline_image_present:
            deletion_degradations.append(
                Degradation(
                    kind="inline_image_present",
                    detail=f"inline image in/adjacent to deletion span {match.operator_refs}",
                    severity="info",
                )
            )

    return EditResult(
        success=success,
        original_text=match.matched_text,
        new_text=new_text,
        font_action=font_action,
        fidelity_report=FidelityReport(
            # INV-C-4: surface the metric-equivalent name (if any).
            font_substituted=substitution_log[0] if substitution_log else None,
            overflow_detected=overflow,
            reflow_applied=False,
            # Audit-bundle finding #3: glyphs_missing reflects pre-extension
            # state (what TRIGGERED the extension), even after extension
            # successfully fills the gap. Empty when no extension ran.
            glyphs_missing=pre_extension_missing,
            degradations=[
                *coverage_degradations,
                *kerning_degradations,
                *overflow_degradations,
                *positioning_degradations,
                *ligature_degradations,
                *deletion_degradations,
            ],
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
    observed: list[str] | None = None,
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
                observed=observed,
            )
            replacement_items = enc.tj_items
            tz_factor = enc.tz_factor
            degradation = enc.degradation
        elif replacement_text:
            replacement_items = [
                pikepdf.String(resolver.encode(replacement_text, _observed=observed))
            ]
        else:
            replacement_items = []
        # B.11 / INV-B-10: thread the EXACT Identity-H byte width (2) so the
        # shared-fragment suffix boundary is CID-exact; the simple-font path
        # (byte_width == 1) passes 0 to keep the pre-B.11 _infer_byte_width
        # heuristic byte-for-byte.
        rebuild_bw = byte_width if byte_width == 2 else 0
        new_array = _rebuild_tj_array(tj_items, op_chars, replacement_items, rebuild_bw)
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
    observed: list[str] | None = None,
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
                observed=observed,
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
            encoded = resolver.encode(replacement_text, _observed=observed)
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
    password: str | bytes | None = None,
) -> EditResult:
    """Replace a single text match in a PDF.

    Args:
        pdf_path: Path to the input PDF file.
        match: TextMatch from locator.find() identifying the text to replace.
        new_text: Replacement text.
        output_path: Path for the output PDF.
        dry_run: If True, simulate the edit without writing output.
        reflow: If True and replacement is wider, reflow the paragraph.
        password: Optional password to open an encrypted input. When supplied,
            the in-memory document is decrypted, the edit applied, and the
            output RE-ENCRYPTED with the same password (A2.3 / INV-W-5) so the
            protection round-trips.

    Returns:
        EditResult with fidelity report.

    Raises:
        PDFEditError: If the PDF is password-protected and no/wrong password
            is supplied.
        OperatorError: If operator references are stale or invalid.
    """
    if not dry_run:
        validate_output_path(output_path)

    pdf = open_pdf(pdf_path, password=password)
    try:
        # Per-call caches (ARY-283): every public entrypoint owns its caches
        # and threads them to helpers; no module-level shared state. The
        # module-level ``_FONTFILE2_CACHE`` referenced by earlier comments
        # was deleted in Phase 13.4 (ARY-348); ``font_has_codepoint`` now
        # re-parses ``/FontFile2`` on each call, so no PDF-keyed cache
        # threading is required.
        resolver_cache = FontResolverCache()
        width_cache = GlyphWidthCache()

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
        ops = _parse_content_stream(page, context="surgeon.replace")
        _assert_match_addressable(ops, match, resolver)

        # v0.1.3 Phase 6: track if reflow was attempted but threw, so we can
        # surface reflow_aborted_to_simple on the simple-replacement result.
        # F-C-03 / INV-W0-9 (commit 07): the companion ``reflow_abort_msg``
        # local was deleted with the str(exc)[:80] truncation it carried.
        reflow_abort_reason: str | None = None
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
                # INV-G-11: a byte-stable (same-length, non-ligature) edit has no
                # length change and MUST take the in-place splice path even when the
                # same-count glyphs are wider — _is_shifting gates the width check so
                # the byte-stable case routes to the clean splice (POS-GATE handles
                # any trailing same-line shift), never a whole-paragraph re-wrap.
                needs_reflow = _is_shifting(match, new_text) and new_width > old_width + 1.0
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
                except (ReflowError, OperatorError, EncodingError, KeyError, ValueError) as exc:
                    logger.warning(
                        "Reflow failed, falling back to simple replacement",
                        exc_info=True,
                    )
                    # v0.1.3 Phase 6: capture the abort reason; surface it on the
                    # simple-replacement EditResult below as reflow_aborted_to_simple.
                    # F-C-03 / INV-W0-9: drop the str(exc)[:80] truncation — it
                    # was a length cap mitigating the same exception-bytes leak
                    # we now close at source. The bare type name is the stable
                    # user-visible signal; full traceback is in the logger.warning
                    # above.
                    reflow_abort_reason = type(exc).__name__

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

        # v0.1.3 Phase 6: surface reflow abort if reflow was attempted
        # and threw, before the simple-replacement fallback ran. The
        # Degradation is added to the simple-replacement result's
        # FidelityReport so callers see why complex reflow was skipped.
        # F-C-03 / INV-W0-9: detail is just the exception type name; the
        # str(exc)[:80] suffix is gone (forensic detail in logger.warning).
        if reflow_abort_reason is not None:
            result.fidelity_report.degradations.append(
                Degradation(
                    kind="reflow_aborted_to_simple",
                    detail=reflow_abort_reason,
                    severity="warning",
                )
            )

        if result.success and not dry_run:
            new_stream = _unparse_content_stream(ops, context="surgeon.replace")
            page.Contents = pdf.make_stream(new_stream)
            simple_lin_log: list[str] = []
            simple_enc_log: list[str] = []
            _save_pdf(
                pdf,
                output_path,
                linearization_log=simple_lin_log,
                reencrypt_password=password,
                encryption_log=simple_enc_log,
            )
            _surface_linearization_dropped(result, simple_lin_log)
            _surface_encryption_dropped(result, simple_enc_log)

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
        # INV-G-11: a byte-stable (same-length, non-ligature) edit must take the
        # in-place splice path even when the same-count glyphs are wider — never a
        # whole-paragraph re-wrap; the clean splice handles it (POS-GATE shifts any
        # trailing same-line text).
        if not _is_shifting(match, new_text) or new_width <= old_width + 1.0:
            return None  # byte-stable, or not meaningfully wider

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
    password: str | bytes | None = None,
) -> list[EditResult]:
    """Find and replace all occurrences of text in a PDF.

    Args:
        pdf_path: Path to the input PDF file.
        search: Text to find.
        replacement: Replacement text.
        output_path: Path for the output PDF.
        dry_run: If True, simulate edits without writing output.
        reflow: If True and replacement is wider, attempt paragraph reflow.
        password: Optional password to open an encrypted input. When supplied,
            the in-memory document is decrypted, the edits applied, and the
            output RE-ENCRYPTED with the same password (A2.3 / INV-W-5).

    Returns:
        List of EditResult objects, one per match. When two or more matches
        splice into the SAME show-text operator AND the replacement changes
        length (or forces a ligature), those matches are refused
        (``success=False`` + ``multi_match_same_operator_unsupported``) rather
        than risk stale-offset corruption; same-length matches sharing one
        operator are applied normally (INV-B-12).
    """
    if not dry_run:
        validate_output_path(output_path)
    from pdf_edit_engine.locator import find

    matches = find(pdf_path, search, password=password)
    if not matches:
        return []

    pdf = open_pdf(pdf_path, password=password)
    try:
        # Per-call caches (ARY-283); pdf threaded for ARY-349 cache key.
        resolver_cache = FontResolverCache()
        width_cache = GlyphWidthCache()

        results: list[EditResult] = []

        # Group matches by page
        matches_by_page: dict[int, list[TextMatch]] = defaultdict(list)
        for m in matches:
            matches_by_page[m.page_number].append(m)

        any_success = False

        for page_num in sorted(matches_by_page.keys()):
            page = pdf.pages[page_num]
            ops = _parse_content_stream(page, context="surgeon.replace_all")
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

            # INV-B-12: refuse colliding same-operator + byte-shifting matches
            # BEFORE any mutation (pure decision → dry_run parity). A refused
            # match is neither reflowed nor spliced; non-colliding matches and
            # byte-stable same-length edits still process normally.
            refused_ids, group_size = _colliding_shifting_matches(page_matches, replacement)

            page_results: list[EditResult] = []
            page_reflowed = False
            simple_success = False
            for m in page_matches:
                if id(m) in refused_ids:
                    page_results.append(
                        _multi_match_refusal_result(m, replacement, group_size[id(m)])
                    )
                    continue

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
                        ops = _parse_content_stream(page, context="surgeon.replace_all")
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
                new_stream = _unparse_content_stream(ops, context="surgeon.replace_all")
                page.Contents = pdf.make_stream(new_stream)

            # Reverse back to original order (we processed in reverse)
            page_results.reverse()
            results.extend(page_results)

        if any_success and not dry_run:
            lin_log: list[str] = []
            enc_log: list[str] = []
            _save_pdf(
                pdf,
                output_path,
                linearization_log=lin_log,
                reencrypt_password=password,
                encryption_log=enc_log,
            )
            _surface_linearization_dropped(results, lin_log)
            _surface_encryption_dropped(results, enc_log)

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
    password: str | bytes | None = None,
) -> list[EditResult]:
    """Apply multiple find-and-replace operations to a PDF in a single pass.

    Args:
        pdf_path: Path to the input PDF file.
        edits: List of Edit objects with find/replace pairs.
        output_path: Path for the output PDF.
        dry_run: If True, simulate edits without writing output.
        reflow: If True and replacement is wider, attempt paragraph reflow.
        password: Optional password to open an encrypted input. When supplied,
            the in-memory document is decrypted, the edits applied, and the
            output RE-ENCRYPTED with the same password (A2.3 / INV-W-5).

    Returns:
        List of EditResult objects, one per edit. When a single edit's search
        term matches two or more times within the SAME show-text operator, that
        edit is refused (``success=False`` + ``multi_match_same_operator_unsupported``)
        regardless of length — ``batch_replace``'s one-result-per-edit model
        cannot apply same-operator siblings byte-stably the way ``replace_all``
        can, so it refuses honestly rather than silently under-replace; use
        ``replace_all`` for repeated same-operator matches (INV-B-12).
    """
    if not dry_run:
        validate_output_path(output_path)
    from pdf_edit_engine.locator import find

    pdf = open_pdf(pdf_path, password=password)
    try:
        # Per-call caches (ARY-283); pdf threaded for ARY-349 cache key.
        resolver_cache = FontResolverCache()
        width_cache = GlyphWidthCache()

        results: list[EditResult] = []
        used_ops_by_page: dict[int, set[int]] = defaultdict(set)
        any_success = False

        # Collect all (match, replacement) pairs with dedup
        all_pairs: list[tuple[TextMatch, str, int]] = []
        for edit_idx, edit in enumerate(edits):
            matches = find(pdf_path, edit.find, password=password)
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
            ops = _parse_content_stream(page, context="surgeon.batch_replace")

            # Sort in reverse operator order
            page_pairs = sorted(
                pairs_by_page[page_num],
                key=lambda p: max(p[0].operator_refs),
                reverse=True,
            )

            # INV-B-12: refuse the colliding same-operator + byte-shifting
            # matches that belong to the SAME edit (one Edit whose search term
            # matched N>1 times into one show-text operator — the replace_all
            # corruption surface, here reached via a single batch edit). These
            # are refused UP FRONT as a group (pure, pre-mutation → dry_run
            # parity). A CROSS-edit overlap (two DIFFERENT edits touching one
            # operator, e.g. "Test"->"X" and "Test Document"->"Y") keeps the
            # existing reactive "first edit wins, rest skipped" partial-success
            # behavior via the used_ops guard below; when such a skip is itself
            # a same show-text operator + byte-shifting collision it surfaces the
            # typed kind instead of the generic "overlapping" warning.
            same_edit_refused, group_size = _same_edit_colliding(page_pairs)
            spliced_ops_by_page: dict[int, set[int]] = defaultdict(set)

            page_changed = False
            page_reflowed = False
            for m, repl, edit_idx in page_pairs:
                # INV-B-12: same-edit multi-match-into-one-operator group refusal.
                if id(m) in same_edit_refused:
                    edit_results[edit_idx].append(
                        _multi_match_refusal_result(m, repl, group_size[id(m)])
                    )
                    continue

                # Skip if operators overlap with already-processed match on same page
                op_set = set(m.operator_refs)
                if op_set & used_ops_by_page[page_num]:
                    # INV-B-12: a same show-text operator + byte-shifting skip is
                    # the multi-match collision — surface it as the typed kind.
                    splice = _splice_ops(m)
                    if splice & spliced_ops_by_page[page_num] and _is_shifting(m, repl):
                        edit_results[edit_idx].append(_multi_match_refusal_result(m, repl, 2))
                    else:
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
                        ops = _parse_content_stream(page, context="surgeon.batch_replace")
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
                    # INV-B-12: record the show-text operators this match
                    # actually spliced bytes into, so a later same-operator
                    # byte-shifting skip is recognised as the typed collision.
                    spliced_ops_by_page[page_num].update(_splice_ops(m))
                    any_success = True
                    page_changed = True

            if page_changed and not dry_run:
                new_stream = _unparse_content_stream(ops, context="surgeon.batch_replace")
                page.Contents = pdf.make_stream(new_stream)

        lin_log: list[str] = []
        enc_log: list[str] = []
        if any_success and not dry_run:
            _save_pdf(
                pdf,
                output_path,
                linearization_log=lin_log,
                reencrypt_password=password,
                encryption_log=enc_log,
            )

        _invalidate_locator_cache()

        # Flatten results: one per edit (aggregate per edit_idx).
        for edit_idx in range(len(edits)):
            if edit_idx in edit_results:
                # INV-B-12: surface a REFUSED/failed sub-result over a successful
                # one so a partial edit is never reported as clean success. A
                # single edit can produce a mix — e.g. a refused same-operator
                # colliding group PLUS a disjoint match that succeeds — and the
                # plain first-result flatten would HIDE the refusal whenever the
                # disjoint match sorts first (higher operator index). Preferring
                # the first non-success result keeps batch_replace's verdict
                # honest; an all-success edit is unchanged (returns the first).
                edit_res = edit_results[edit_idx]
                results.append(next((r for r in edit_res if not r.success), edit_res[0]))
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

        _surface_linearization_dropped(results, lin_log)
        _surface_encryption_dropped(results, enc_log)
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
