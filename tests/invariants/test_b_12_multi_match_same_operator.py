"""INV-B-12: multi-match-same-operator honest-refusal (v0.2.0).

Roadmap item **multi-match-same-operator honest-refusal**, the pre-existing,
NON-deletion-specific corruption the B.11 residue-remediation row explicitly
deferred to its own unit ("the correct fix re-derives offsets per match against
the mutated operand").

``surgeon.replace_all`` / ``surgeon.batch_replace`` route N TextMatches through
independent ``_apply_single_replacement`` calls against a SHARED, mutating
operand. When two or more matches on a page splice into the SAME show-text
operator AND the replacement is length-changing (the byte-shifting rebuild
path, ``surgeon.py`` ~:1700-1733), each match's ``byte_position`` was recorded
against the ORIGINAL operand. After the first splice shifts the operand bytes,
every later same-operator match reads the WRONG slice and SILENTLY corrupts the
output — while the engine reports ``success=True`` for every match.

Empirically (resume corpus): ``replace_all("and", "order", reflow=False)``
reports 24/24 success but yields 23 "order" + 3 surviving "and" (operators
1825×2 and 2053×3 collide). The same-length ``"and"->"but"`` writes fixed-width
bytes at each ``byte_position`` with ZERO byte shift, so it edits cleanly
(24/24 correct) and MUST NOT be refused.

The GREEN contract: detect the collision on the CHARACTER-derived splice-op set
(``{ch.operator_index for ch in m.characters}``, NOT ``operator_refs`` — only
the operators the chars actually write into) BEFORE any mutation, and REFUSE
exactly the colliding + byte-shifting matches (``EditResult.success=False``,
``font_action="kept"``, a typed ``multi_match_same_operator_unsupported``
Degradation), while matches in DIFFERENT operators AND same-length non-ligature
edits still edit correctly (partial-success preserved). The decision is a pure
function of the matches + per-call lengths computed pre-mutation, so dry_run
parity holds.

``delete_block`` / ``batch_replace_block`` (structural, bbox/region-driven
single-pass keep-slot emptying) do NOT route N stale-byte_position splices and
do NOT share this bug — they are out of scope here.

Every ``replace_all`` / ``batch_replace`` call below passes ``reflow=False`` so
the matches route through the ``_apply_single_replacement`` splice path (the
corruption site) rather than the orthogonal per-page reflow-consumption path,
exactly as the blueprint's own empirical evidence
(``replace_all(resume, "and", "order", reflow=False)``) demonstrated the bug.
``reflow=False`` confirmed in-test: the single-operator length-change
``cat``->``X`` reports ``[True, True, True]`` yet renders ``"X dog cat bird cat
fish"`` (only the first occurrence replaced, the other two silently survive),
while the byte-stable same-length ``cat``->``dog`` renders ``"dog dog dog bird
dog fish"`` (all three replaced correctly — must NOT be refused).

The new ``multi_match_same_operator_unsupported`` DegradationKind does not exist
on ``models.py`` yet; this probe references it ONLY via a string ``.kind ==``
comparison so the file COLLECTS cleanly on the current models. The CORE probe
(a) FAILS today for the right reason: ``replace_all`` reports success for every
colliding match and silently corrupts (stale byte offsets), instead of refusing.
The over-fire CONTROLS (b disjoint operators, c same-length) PASS today.

All PDF opens for inspection route through ``pdf_edit_engine._pathutil.open_pdf``
(INV-L-1). Fixtures are built in-test with the deterministic corpus builders
(test tooling, outside ``src/``) and skip cleanly when no host TrueType font is
available.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine import batch_replace, find, get_text, replace_all
from pdf_edit_engine.models import Edit

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders._common import (  # noqa: E402
    emit_or_write,
    find_truetype_font,
    save_pdf_deterministic,
)
from corpus_builders._truetype_assembler import embed_identity_h_font  # noqa: E402

# ---------------------------------------------------------------------------
# Skip gate
# ---------------------------------------------------------------------------

_FONT_OK = find_truetype_font() is not None
_no_font = pytest.mark.skipif(
    not _FONT_OK, reason="no host TrueType font for Identity-H construction"
)

_MULTI_KIND = "multi_match_same_operator_unsupported"


# ---------------------------------------------------------------------------
# Synthetic fixture builders (in-test; deterministic corpus-builder pattern)
# ---------------------------------------------------------------------------


def _attach_page(pdf: pikepdf.Pdf, font: object, content: bytes) -> None:
    """Attach a single 612x792 page carrying ``content`` and the font resource."""
    page = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
            "/Resources": pikepdf.Dictionary(
                {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(font.type0)})}  # type: ignore[attr-defined]
            ),
            "/Contents": pikepdf.Stream(pdf, content),
        }
    )
    pdf.pages.append(pikepdf.Page(page))


def _build_one_operator_multi_pdf(out: Path, phrase: str) -> None:
    """One page: ``phrase`` rendered by ONE Tj operator with a 3x-repeated token.

    The whole phrase is a single ``<...> Tj`` operand, so every ``find(token)``
    match splices into the SAME operator — the collision vector. Mirrors
    ``test_b_10._build_repeated_run_pdf`` (single Tj operand) extended to three
    occurrences. The full corpus is passed to the assembler so every glyph
    (search + replacement) is in the subset.
    """
    ttf = find_truetype_font()
    assert ttf is not None
    pdf = pikepdf.Pdf.new()
    try:
        # Cover every search + replacement letter used by the probes below.
        font = embed_identity_h_font(pdf, ttf, phrase + " Xtdog ")
        ops = [
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 700 Tm",
            f"<{font.encode(phrase)}> Tj",
            "ET",
        ]
        _attach_page(pdf, font, "\n".join(ops).encode("latin-1"))
        emit_or_write(save_pdf_deterministic(pdf), out)
    finally:
        pdf.close()


def _build_separate_operator_multi_pdf(out: Path, token: str) -> None:
    """One page: ``token`` rendered 3x in 3 SEPARATE BT/ET blocks (distinct ops).

    Each ``find(token)`` match has a distinct ``operator_index`` (pairwise
    disjoint splice-op sets), so the collision predicate must NOT group them —
    all three edit normally. Mirrors ``test_b_11`` two-block pattern, extended
    to three baselines.
    """
    ttf = find_truetype_font()
    assert ttf is not None
    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf, token + " XYZ ")
        ops = [
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 700 Tm",
            f"<{font.encode(token)}> Tj",
            "ET",
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 670 Tm",
            f"<{font.encode(token)}> Tj",
            "ET",
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 640 Tm",
            f"<{font.encode(token)}> Tj",
            "ET",
        ]
        _attach_page(pdf, font, "\n".join(ops).encode("latin-1"))
        emit_or_write(save_pdf_deterministic(pdf), out)
    finally:
        pdf.close()


def _build_mixed_share_and_separate_pdf(out: Path, token: str) -> None:
    """One page: 3x ``token`` in ONE Tj operand AND 1x ``token`` in a SEPARATE op.

    The 3 colliding occurrences (shared operator) must be refused on a
    length-changing edit; the 1 isolated occurrence (distinct operator) must
    still succeed — partial success. The phrase ``"cat cat cat tail"`` puts the
    3 in one operand; a second BT/ET block renders one more ``cat``.
    """
    ttf = find_truetype_font()
    assert ttf is not None
    pdf = pikepdf.Pdf.new()
    try:
        phrase = f"{token} {token} {token} tail"
        font = embed_identity_h_font(pdf, ttf, phrase + " catt ")
        ops = [
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 700 Tm",
            f"<{font.encode(phrase)}> Tj",
            "ET",
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 660 Tm",
            f"<{font.encode(token)}> Tj",
            "ET",
        ]
        _attach_page(pdf, font, "\n".join(ops).encode("latin-1"))
        emit_or_write(save_pdf_deterministic(pdf), out)
    finally:
        pdf.close()


# ---------------------------------------------------------------------------
# Inspection helpers (copied verbatim from test_b_10 house style)
# ---------------------------------------------------------------------------


def _kinds(result) -> set[str]:  # noqa: ANN001 - EditResult | list[EditResult]
    """Collapse a result (or list of results) to its set of degradation kinds."""
    results = result if isinstance(result, list) else [result]
    kinds: set[str] = set()
    for r in results:
        for d in r.fidelity_report.degradations:
            kinds.add(d.kind)
    return kinds


def _all_success(result) -> bool:  # noqa: ANN001
    results = result if isinstance(result, list) else [result]
    return all(r.success for r in results)


def _splice_ops(match) -> frozenset[int]:  # noqa: ANN001 - TextMatch
    """The set of operator indices the match's CHARACTERS actually splice into.

    This is the detection key (NOT ``operator_refs``): only the show-text
    operators that receive bytes. Two matches collide iff these sets intersect.
    """
    return frozenset(ch.operator_index for ch in match.characters)


# ===========================================================================
# (a) CORE — same-operator length-change edit is REFUSED, not silently corrupted
#     (RED today: replace_all returns success for all matches AND corrupts)
# ===========================================================================


@_no_font
def test_inv_b_12_same_operator_length_change_refused(tmp_path: Path) -> None:
    """3x ``cat`` in ONE Tj operator, replace_all length-change must REFUSE cleanly.

    THE CORE RED. ``"cat dog cat bird cat fish"`` renders in ONE Tj operator, so
    all three ``find("cat")`` matches share that operator (the collision vector).
    ``replace_all("cat", "X")`` is a length change (3->1) routing through the
    byte-shifting rebuild path; each match's ``byte_position`` was recorded
    against the ORIGINAL operand, so after the first splice shifts the operand,
    the later same-operator matches read the wrong slice and silently corrupt the
    output.

    GREEN contract: every colliding match reports ``success=False`` AND carries a
    ``multi_match_same_operator_unsupported`` degradation; the original text is
    NOT silently corrupted (either no output is written, or every ``cat``
    occurrence is preserved intact — never a partial/garbled mutation).

    RED today for the right reason: ``replace_all`` reports ``success=True`` for
    every match and the written output is corrupted (fewer/garbled occurrences),
    instead of refusing.
    """
    phrase = "cat dog cat bird cat fish"
    src = tmp_path / "one_op.pdf"
    _build_one_operator_multi_pdf(src, phrase)

    before = get_text(str(src), page=0)
    before_count = before.count("cat")
    assert before_count == 3, f"precondition: three 'cat' occurrences; got {before_count}"

    matches = find(str(src), "cat")
    assert len(matches) == 3, f"expected three 'cat' matches; got {len(matches)}"
    # Precondition: all three occurrences splice into ONE operator (the vector).
    splice_sets = [_splice_ops(m) for m in matches]
    assert splice_sets[0] and splice_sets[0] == splice_sets[1] == splice_sets[2], (
        "precondition: all three 'cat' runs must share one show-text operator; "
        f"got splice-op sets {[sorted(s) for s in splice_sets]}"
    )

    out = tmp_path / "one_op_out.pdf"
    res = replace_all(str(src), "cat", "X", str(out), reflow=False)

    # THE CORE GUARD: the colliding matches must be REFUSED honestly, not
    # silently corrupted. Today every result is success=True with no degradation.
    assert not _all_success(res), (
        "colliding same-operator length-change matches must NOT all report "
        f"success — the engine is silently corrupting the output; res={res!r}"
    )
    assert _MULTI_KIND in _kinds(res), (
        "a same-operator length-changing multi-match edit must surface "
        f"{_MULTI_KIND!r}; got kinds={_kinds(res)}"
    )
    # Every refused result carries the typed kind and the kept-font action.
    refused = [r for r in res if not r.success]
    assert refused, "expected at least one refused (success=False) result"
    for r in refused:
        rkinds = {d.kind for d in r.fidelity_report.degradations}
        assert _MULTI_KIND in rkinds, (
            f"every refused colliding match must carry {_MULTI_KIND!r}; got {rkinds}"
        )

    # THE CORRECTNESS GUARD: the original text must NOT be silently corrupted.
    # Acceptable honest outcomes: no output written (all refused), OR every
    # 'cat' occurrence preserved intact. A written output with fewer/garbled
    # occurrences is the silent corruption this probe forbids.
    if out.exists():
        after = get_text(str(out), page=0)
        assert after.count("cat") == before_count, (
            "refused colliding matches must leave the original 'cat' occurrences "
            f"intact (no silent corruption); before={before_count} "
            f"after={after.count('cat')} (text={after!r})"
        )
    else:
        # All-refused → no successful edit → no output written (mirrors all-fail).
        # Explicitly pin the no-output path so this guard does not go cold under
        # the GREEN implementation (invariant-probe review item 1). The source is
        # never written in place, so it stays intact.
        assert get_text(str(src), page=0).count("cat") == before_count, (
            "the source must be untouched when all colliding matches are refused"
        )


# ===========================================================================
# (b) CONTROL — matches in DIFFERENT operators all succeed (no over-firing)
#     (passes today and after the fix)
# ===========================================================================


@_no_font
def test_inv_b_12_separate_operators_all_succeed(tmp_path: Path) -> None:
    """3x ``cat`` in 3 SEPARATE operators: a length-change edit edits all cleanly.

    The over-fire control. Disjoint splice-op sets must never be grouped, so the
    collision predicate must not refuse them. ``replace_all("cat", "XYZ")``
    (length change 3->3 by char count but distinct text) replaces all three.
    """
    token = "cat"
    src = tmp_path / "sep_op.pdf"
    _build_separate_operator_multi_pdf(src, token)

    matches = find(str(src), token)
    assert len(matches) == 3, f"expected three {token!r} matches; got {len(matches)}"
    # Precondition: pairwise-disjoint splice-op sets (distinct operators).
    splice_sets = [_splice_ops(m) for m in matches]
    for i in range(len(splice_sets)):
        for j in range(i + 1, len(splice_sets)):
            assert not (splice_sets[i] & splice_sets[j]), (
                "precondition: separate-operator occurrences must have disjoint "
                f"splice-op sets; {sorted(splice_sets[i])} & {sorted(splice_sets[j])}"
            )

    out = tmp_path / "sep_op_out.pdf"
    res = replace_all(str(src), token, "XYZ", str(out), reflow=False)

    assert _all_success(res), (
        f"disjoint-operator matches must all succeed (no over-fire); res={res!r}"
    )
    assert _MULTI_KIND not in _kinds(res), (
        f"disjoint-operator matches must NOT surface {_MULTI_KIND!r}; kinds={_kinds(res)}"
    )
    after = get_text(str(out), page=0)
    assert after.count("cat") == 0, f"all 'cat' must be replaced; text={after!r}"
    assert after.count("XYZ") == 3, f"all three replacements expected; text={after!r}"


# ===========================================================================
# (c) CONTROL — same-length same-operator is NOT refused (the resume "and"->"but"
#     case): byte-stable fixed-width splice edits correctly today
# ===========================================================================


@_no_font
def test_inv_b_12_same_length_same_operator_not_refused(tmp_path: Path) -> None:
    """3x ``cat`` in ONE operator, SAME-length ``cat``->``dog``: not refused.

    The length-change-refinement control (CRITICAL — prevents over-firing on the
    common byte-stable same-length case, e.g. the resume ``"and"->"but"`` which
    is 24/24 correct today). A same-length non-ligature replacement writes
    fixed-width bytes at each ``byte_position`` with ZERO byte shift, so sibling
    same-operator matches stay byte-stable and MUST still edit correctly.
    """
    phrase = "cat dog cat bird cat fish"
    src = tmp_path / "same_len.pdf"
    _build_one_operator_multi_pdf(src, phrase)

    matches = find(str(src), "cat")
    assert len(matches) == 3, f"expected three 'cat' matches; got {len(matches)}"
    splice_sets = [_splice_ops(m) for m in matches]
    assert splice_sets[0] == splice_sets[1] == splice_sets[2], (
        "precondition: all three 'cat' runs must share one operator"
    )

    out = tmp_path / "same_len_out.pdf"
    res = replace_all(str(src), "cat", "dog", str(out), reflow=False)  # 3 -> 3, same length

    assert _all_success(res), (
        f"same-length same-operator replacement is byte-stable and must NOT be refused; res={res!r}"
    )
    assert _MULTI_KIND not in _kinds(res), (
        f"same-length edit must NOT surface {_MULTI_KIND!r}; kinds={_kinds(res)}"
    )
    after = get_text(str(out), page=0)
    assert after.count("cat") == 0, f"all 'cat' must become 'dog'; text={after!r}"
    assert after.count("dog") == 4, (  # 3 new + 1 pre-existing 'dog' in the phrase
        f"three 'cat'->'dog' plus the pre-existing 'dog'; text={after!r}"
    )


# ===========================================================================
# (d) MIXED — some matches share an operator, some don't: partial success
# ===========================================================================


@_no_font
def test_inv_b_12_mixed_some_share_some_dont(tmp_path: Path) -> None:
    """3 colliding ``cat`` + 1 isolated ``cat``, length-change: partial success.

    The colliding trio (shared operator) is refused; the isolated occurrence
    (separate operator) still edits; the output IS written (partial success) and
    the isolated occurrence is replaced.
    """
    token = "cat"
    src = tmp_path / "mixed.pdf"
    _build_mixed_share_and_separate_pdf(src, token)

    matches = find(str(src), token)
    assert len(matches) == 4, f"expected four {token!r} matches; got {len(matches)}"

    # Identify the colliding group (>=2 sharing an operator) vs the isolated one.
    splice_sets = [_splice_ops(m) for m in matches]
    counts = [
        sum(1 for other in splice_sets if other & s)  # includes self
        for s in splice_sets
    ]
    colliding_idx = [i for i, c in enumerate(counts) if c >= 2]
    isolated_idx = [i for i, c in enumerate(counts) if c == 1]
    assert len(colliding_idx) == 3 and len(isolated_idx) == 1, (
        "precondition: three colliding + one isolated 'cat'; "
        f"colliding={colliding_idx} isolated={isolated_idx} "
        f"(splice sets {[sorted(s) for s in splice_sets]})"
    )

    out = tmp_path / "mixed_out.pdf"
    res = replace_all(str(src), token, "catt", str(out), reflow=False)  # 3 -> 4, length change

    assert isinstance(res, list) and len(res) == 4, f"one result per match; got {res!r}"

    # The colliding trio is refused with the typed kind; the isolated one edits.
    refused = [r for r in res if not r.success]
    succeeded = [r for r in res if r.success]
    assert len(refused) == 3, f"the three colliding matches must be refused; res={res!r}"
    assert len(succeeded) == 1, f"the isolated match must still succeed; res={res!r}"
    for r in refused:
        assert _MULTI_KIND in {d.kind for d in r.fidelity_report.degradations}, (
            f"each refused colliding match must carry {_MULTI_KIND!r}; got {r!r}"
        )
    assert _MULTI_KIND not in {d.kind for d in succeeded[0].fidelity_report.degradations}, (
        "the isolated (non-colliding) match must NOT carry the kind"
    )

    # Partial success: file written, the isolated occurrence replaced.
    assert out.exists(), "partial success must still write output"
    after = get_text(str(out), page=0)
    assert "catt" in after, f"the isolated occurrence must be replaced; text={after!r}"


# ===========================================================================
# (e) dry_run parity — the refusal decision is pure (pre-mutation), identical
#     between dry_run=True and dry_run=False; dry_run writes no file
# ===========================================================================


@_no_font
def test_inv_b_12_dry_run_parity(tmp_path: Path) -> None:
    """Case (a) under dry_run: identical degradations + per-result success.

    The collision decision is a pure function of the matches + per-call lengths
    computed BEFORE any mutation, so dry_run=True and dry_run=False must produce
    the identical degradation set and per-result success list; the dry_run output
    must be unwritten.
    """
    phrase = "cat dog cat bird cat fish"
    src = tmp_path / "parity.pdf"
    _build_one_operator_multi_pdf(src, phrase)

    out_live = tmp_path / "parity_live.pdf"
    out_dry = tmp_path / "parity_dry.pdf"

    res_live = replace_all(str(src), "cat", "X", str(out_live), reflow=False)
    res_dry = replace_all(str(src), "cat", "X", str(out_dry), reflow=False, dry_run=True)

    assert sorted(_kinds(res_dry)) == sorted(_kinds(res_live)), (
        f"dry_run degradations diverge: dry={sorted(_kinds(res_dry))} vs "
        f"live={sorted(_kinds(res_live))}"
    )
    live_success = [r.success for r in res_live]
    dry_success = [r.success for r in res_dry]
    assert dry_success == live_success, (
        f"dry_run per-result success must match live: dry={dry_success} vs live={live_success}"
    )
    assert not out_dry.exists(), "dry_run must not write an output file"


# ===========================================================================
# (f) batch_replace — same-operator length-change edit surfaces the typed kind
#     (replaces the existing generic "overlapping" skip warning)
# ===========================================================================


@_no_font
def test_inv_b_12_batch_replace_same_operator_refused(tmp_path: Path) -> None:
    """batch_replace over a single-operator 3x token surfaces the typed kind.

    ``batch_replace`` already has a reactive ``used_ops_by_page`` overlap guard
    (the first colliding match wins, the rest are skipped with a generic
    "overlapping" warning). The GREEN contract makes the skip path emit the
    typed ``multi_match_same_operator_unsupported`` degradation instead of the
    untyped warning. ``batch_replace`` flattens to one result per edit
    (``edit_results[edit_idx][0]``), so we assert the surfaced result for the
    colliding edit reflects the typed refusal.

    RED today for the right reason: the skip path emits only a generic warning
    string (no typed degradation), so ``_MULTI_KIND`` is absent.
    """
    phrase = "cat dog cat bird cat fish"
    src = tmp_path / "batch.pdf"
    _build_one_operator_multi_pdf(src, phrase)

    matches = find(str(src), "cat")
    assert len(matches) == 3, f"expected three 'cat' matches; got {len(matches)}"
    splice_sets = [_splice_ops(m) for m in matches]
    assert splice_sets[0] == splice_sets[1] == splice_sets[2], (
        "precondition: all three 'cat' runs must share one operator"
    )

    out = tmp_path / "batch_out.pdf"
    res = batch_replace(str(src), [Edit("cat", "catt")], str(out), reflow=False)  # length change

    assert _MULTI_KIND in _kinds(res), (
        "batch_replace over a single-operator multi-match length-change edit must "
        f"surface {_MULTI_KIND!r} on the skipped colliding matches (replacing the "
        f"generic 'overlapping' warning); got kinds={_kinds(res)}"
    )
    # The colliding edit's surfaced result must report success=False, not a
    # silent partial success (invariant-probe review item 2).
    assert not _all_success(res), (
        f"the colliding same-operator batch edit must report success=False; res={res!r}"
    )


# ===========================================================================
# (g) batch_replace SAME-LENGTH same-operator multi-match REFUSES (Finding 1
#     regression lock — pre-fix it silently under-replaced with success=True)
# ===========================================================================


@_no_font
def test_inv_b_12_batch_replace_same_length_same_operator_refused(tmp_path: Path) -> None:
    """batch_replace SAME-length same-operator multi-match REFUSES (not silent partial).

    Finding 1 regression lock (adversarial critic). Unlike ``replace_all`` — which
    applies all three byte-stably for a same-length edit — ``batch_replace``'s
    one-result-per-edit aggregation + ``used_ops`` backstop can only apply the
    FIRST same-operator match and (pre-fix) SILENTLY dropped the other two while
    reporting ``success=True`` with zero degradations (2 of 3 'cat' left
    unedited). The honest contract: refuse the whole same-edit same-operator
    group with the typed kind regardless of length, so a partial edit is never
    reported as clean success; the caller uses ``replace_all`` for repeated
    same-operator matches.
    """
    phrase = "cat dog cat bird cat fish"
    src = tmp_path / "batch_samelen.pdf"
    _build_one_operator_multi_pdf(src, phrase)

    before = get_text(str(src), page=0)
    assert before.count("cat") == 3, "precondition: three 'cat' occurrences"

    matches = find(str(src), "cat")
    splice_sets = [_splice_ops(m) for m in matches]
    assert splice_sets[0] == splice_sets[1] == splice_sets[2], (
        "precondition: all three 'cat' runs must share one operator"
    )

    out = tmp_path / "batch_samelen_out.pdf"
    res = batch_replace(
        str(src), [Edit("cat", "dog")], str(out), reflow=False
    )  # 3 -> 3 same length

    assert not _all_success(res), (
        "batch_replace must NOT silently under-replace a same-length same-operator "
        f"multi-match (2 of 3 left as 'cat' with success=True); res={res!r}"
    )
    assert _MULTI_KIND in _kinds(res), (
        f"the refused same-operator batch edit must surface {_MULTI_KIND!r}; "
        f"got kinds={_kinds(res)}"
    )
    # Whole edit refused → no output written → no partial mutation.
    if out.exists():
        after = get_text(str(out), page=0)
        assert after.count("cat") == 3, (
            f"batch must not produce a partial (some-'cat'-replaced) output; text={after!r}"
        )


# ===========================================================================
# (h) CONTROL — batch_replace, one edit matching in SEPARATE operators, must
#     all succeed (the Finding 1 fix must NOT over-fire on disjoint operators)
# ===========================================================================


@_no_font
def test_inv_b_12_batch_replace_separate_operators_not_refused(tmp_path: Path) -> None:
    """batch_replace, one edit matching 3x in SEPARATE operators: all succeed.

    Over-fire guard for the Finding 1 fix. ``_same_edit_colliding`` refuses a
    same-edit group only when its matches share a splice operator; matches in
    DISTINCT operators have disjoint splice-op sets, form size-1 groups, and must
    NOT be refused — a legitimate batch edit whose term recurs across separate
    operators still applies everywhere.
    """
    token = "cat"
    src = tmp_path / "batch_sep.pdf"
    _build_separate_operator_multi_pdf(src, token)

    matches = find(str(src), token)
    assert len(matches) == 3, f"expected three {token!r} matches; got {len(matches)}"
    splice_sets = [_splice_ops(m) for m in matches]
    for i in range(len(splice_sets)):
        for j in range(i + 1, len(splice_sets)):
            assert not (splice_sets[i] & splice_sets[j]), (
                "precondition: separate-operator occurrences must have disjoint splice-op sets"
            )

    out = tmp_path / "batch_sep_out.pdf"
    res = batch_replace(str(src), [Edit(token, "XYZ")], str(out), reflow=False)

    assert _all_success(res), (
        f"disjoint-operator batch matches must all succeed (no over-fire); res={res!r}"
    )
    assert _MULTI_KIND not in _kinds(res), (
        f"disjoint-operator batch edit must NOT surface {_MULTI_KIND!r}; kinds={_kinds(res)}"
    )
    after = get_text(str(out), page=0)
    assert after.count("cat") == 0 and after.count("XYZ") == 3, (
        f"all three separate-operator occurrences must be replaced; text={after!r}"
    )


# ===========================================================================
# (i) batch_replace MIXED in ONE edit — a refused colliding group + a disjoint
#     success must surface the REFUSAL (the [0]-flatten honesty fix), never a
#     clean success that hides the dropped occurrences
# ===========================================================================


@_no_font
def test_inv_b_12_batch_replace_mixed_surfaces_refusal(tmp_path: Path) -> None:
    """batch edit with a refused colliding group + a disjoint success → success=False.

    The ``[0]``-flatten honesty lock. A single batch ``Edit`` can match BOTH a
    colliding same-operator group (refused) AND a disjoint occurrence (applied).
    ``batch_replace`` flattens to one result per edit; the plain first-result
    flatten would surface the disjoint SUCCESS (when it sorts first) and HIDE the
    refusal — reporting ``success=True`` while occurrences were dropped. The fix
    surfaces the first non-success sub-result, so the edit's verdict is honestly
    ``success=False`` + the typed kind whenever any occurrence was refused.
    """
    token = "cat"
    src = tmp_path / "batch_mixed.pdf"
    _build_mixed_share_and_separate_pdf(src, token)  # 3 colliding (one Tj) + 1 disjoint

    matches = find(str(src), token)
    assert len(matches) == 4, f"expected four {token!r} matches; got {len(matches)}"

    out = tmp_path / "batch_mixed_out.pdf"
    res = batch_replace(str(src), [Edit(token, "catt")], str(out), reflow=False)  # length change

    assert isinstance(res, list) and len(res) == 1, f"one result per edit; got {res!r}"
    assert not res[0].success, (
        "a batch edit that refused a colliding group must NOT report clean success "
        f"just because a disjoint occurrence applied; res={res!r}"
    )
    assert _MULTI_KIND in _kinds(res), (
        f"the surfaced result must carry {_MULTI_KIND!r}; got kinds={_kinds(res)}"
    )
