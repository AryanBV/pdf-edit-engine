"""INV-B-10: content-deletion cleanup (B.11).

Roadmap item **B.11 — Content Deletion Cleanup**. Today three distinct
deletion code paths share the SAME silent-wrong contract: they return
``EditResult.success=True`` with ``degradations=[]`` while leaving residue or
corrupting an in-operator neighbour. This RED suite pins the *correct* contract
the GREEN agent must satisfy; the cases that exercise the live bugs FAIL today
for the right reason (residue remains / a neighbour is corrupted / trailing
text mis-shifts), all at ``success=True`` with zero degradations.

(INV-B-11 batch-delete index stability lives in its own one-per-file probe,
``test_b_11_index_stability.py``.)

This suite ALSO pins the two B.11 residue-predicate OVER-FIRE remediation
contracts (the GREEN agent must root-fix the residue checks so these pass):

* **Surgeon repeated-run over-fire** — deleting ONE of two byte-identical runs
  in the same Tj operand must report ``success=True`` AND write output. Today
  the global ``run in current`` substring search finds the untouched second
  occurrence and flips ``success=False``, which blocks the save → a CORRECT
  delete writes no output (silent data loss). Pinned by
  ``test_inv_b_10_surgeon_repeated_run_no_false_residue`` (RED today).
* **delete_block adjacent-line over-fire** — a heading deleted over its own
  bbox must report ``success=True`` even when an untouched adjacent line shares
  a token within the ~2pt region-extraction tolerance. Today the
  "target token re-appears anywhere in the region" check false-fires on the
  shared token. Pinned by
  ``test_inv_b_10_delete_block_adjacent_line_no_false_residue`` (RED today).

The four mechanisms (empirically confirmed against the resume corpus + the
synthetic fixtures built in-test):

* **PATH A — clean single-op delete (must stay GREEN).** Deleting a
  single-occurrence token rendered by ONE show-text operator already empties
  the operand correctly and leaves no residue. The B.11 fix must NOT
  over-surface a ``deletion_residual_text`` on this clean case. Pinned by
  ``test_inv_b_10_single_token_deletion_no_residue``.

* **PATH B — in-operator neighbour corruption (the real silent-wrong).**
  ``"Software"`` and ``"Developer"`` are rendered by the SAME TJ operator
  (``op_refs [103]`` on the resume). ``replace_all(SRC, "Software", "", OUT)``
  returns ``success=True, degs=[]`` but DESTROYS ``"Developer"`` →
  ``"eveloper"`` corruption marker. Mechanism: the empty-rebuild path's
  per-fragment ``_infer_byte_width`` mis-computes the shared-fragment suffix
  boundary and eats the leading glyph of the neighbour. Pinned by
  ``test_inv_b_10_batch_neighbor_integrity`` (RED today).

* **PATH C — ``delete_block`` residue (tuple-removal / missed show-text op).**
  ``delete_block`` removes operator TUPLES; when the bbox→op collection misses
  the show-text op the rendered text remains, yet the result is
  ``success=True, degs=[]``. Pinned by
  ``test_inv_b_10_provable_residue_surfaces_failure`` (RED today).

* **Trailing-text mis-shift on an axis-aligned mid-line delete.** Deleting a
  relative-``Td``-positioned mid-line run shifts the following same-line text
  because the deleted run's advance is not compensated. Pinned by
  ``test_inv_b_10_trailing_text_position_axis_aligned`` (RED today).

The rotated-run skip (POS-GATE ``positioning_adjustment_skipped``) is ALREADY
locked behaviour and is pinned here as a regression guard, NOT a RED.

All PDF opens for inspection route through ``pdf_edit_engine._pathutil.open_pdf``
(INV-L-1). Fixtures are built in-test with the deterministic corpus builders
(test tooling, outside ``src/``); the resume corpus is gitignored, so the
resume-coupled probes skip cleanly when the file is absent (conftest
precedent).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine import (
    delete_block,
    find,
    get_text,
    get_text_layout,
    replace,
    replace_all,
)
from pdf_edit_engine._pathutil import open_pdf

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
from corpus_builders.rotated_text import build_rotated_text_pdf  # noqa: E402

# ---------------------------------------------------------------------------
# Skip gates
# ---------------------------------------------------------------------------

_RESUME = _TESTS_DIR / "corpus" / "Aryan_BV_Resume_2026.pdf"
_no_resume = pytest.mark.skipif(
    not _RESUME.exists(),
    reason=f"resume corpus fixture missing: {_RESUME.name} (gitignored)",
)

_FONT_OK = find_truetype_font() is not None
_no_font = pytest.mark.skipif(
    not _FONT_OK, reason="no host TrueType font for Identity-H construction"
)

_ROT_FONT_OK = build_rotated_text_pdf() is not None
_no_rot_font = pytest.mark.skipif(
    not _ROT_FONT_OK, reason="no host TrueType font for rotated Identity-H construction"
)

_RESIDUE_KIND = "deletion_residual_text"
_INLINE_KIND = "inline_image_present"
_POS_SKIP_KIND = "positioning_adjustment_skipped"


# ---------------------------------------------------------------------------
# Synthetic fixture builders (in-test; deterministic corpus-builder pattern)
# ---------------------------------------------------------------------------


def _build_single_run_pdf(out: Path, token: str) -> None:
    """One page: a single token rendered by ONE Tj operator (clean-delete case)."""
    ttf = find_truetype_font()
    assert ttf is not None
    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf, token + " ")
        ops = [
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 700 Tm",
            f"<{font.encode(token)}> Tj",
            "ET",
        ]
        _attach_page(pdf, font, "\n".join(ops).encode("latin-1"))
        emit_or_write(save_pdf_deterministic(pdf), out)
    finally:
        pdf.close()


def _build_trailing_run_pdf(out: Path) -> tuple[str, str]:
    """Axis-aligned line ``Keep DELME End`` with relative-Td advances.

    Deleting ``DELME`` must leave ``End`` at its original x within ~1pt (the
    deleted run's advance must be compensated). Returns ``(delete_token,
    trailing_token)``.
    """
    ttf = find_truetype_font()
    assert ttf is not None
    delete_token = "DELME"
    trailing_token = "End"
    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf, "Keep DELME End ")
        fs = 12.0
        adv_keep = font.advance("Keep ", fs)
        adv_del = font.advance("DELME ", fs)
        ops = [
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 700 Tm",
            f"<{font.encode('Keep ')}> Tj",
            f"{adv_keep:g} 0 Td",
            f"<{font.encode('DELME ')}> Tj",
            f"{adv_del:g} 0 Td",
            f"<{font.encode('End')}> Tj",
            "ET",
        ]
        _attach_page(pdf, font, "\n".join(ops).encode("latin-1"))
        emit_or_write(save_pdf_deterministic(pdf), out)
    finally:
        pdf.close()
    return delete_token, trailing_token


def _build_missed_bbox_residue_pdf(out: Path, token: str) -> tuple[float, float, float, float]:
    """One page: ``token`` rendered TWICE — once collected, once co-rendered & missed.

    Modelling the PATH C ``delete_block`` residue genuinely: the same token is
    drawn on two adjacent baselines by two SEPARATE show-text ops. The lower
    instance's element bbox is ``(72, 697, ~117, 709)`` and the upper's is
    ``(72, 709, ~117, 721)``. The returned region ``(72, 697, 300, 709)``
    OVERLAPS the lower instance (collection captures op 3, keep-slot emptying
    clears it) but STRICT-AABB MISSES the upper instance
    (``elem.bbox[1] == 709`` is NOT ``< bbox[3] == 709``). The upper instance
    still renders ``token``'s glyphs into the region (the engine's own
    tolerance-aware bbox-text extractor sees them within 2pt), so after the
    deletion the collected target token SURVIVES in the region — a genuine
    co-rendered show-text op the collection missed. The engine must surface
    this as residue + success=False rather than a silent success.

    The two-instance construction (rather than a single token + a disjoint
    bbox) is deliberate: a single token with a bbox that merely touches its
    edge is geometrically indistinguishable from a legitimate "delete an empty
    region whose boundary abuts unrelated text" case (which must succeed —
    INV-F-2 idempotence), so it cannot model residue honestly. Re-rendering
    the SAME target token makes the survivor a TARGET the deletion was supposed
    to clear, which the target-specific residue predicate detects without
    over-firing on unrelated boundary text.
    """
    ttf = find_truetype_font()
    assert ttf is not None
    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf, token + " Other ")
        ops = [
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 700 Tm",
            f"<{font.encode(token)}> Tj",
            "ET",
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 712 Tm",
            f"<{font.encode(token)}> Tj",
            "ET",
        ]
        _attach_page(pdf, font, "\n".join(ops).encode("latin-1"))
        emit_or_write(save_pdf_deterministic(pdf), out)
    finally:
        pdf.close()
    # Region overlapping the LOWER instance (697-709) but strict-missing the
    # UPPER (709-721): the upper co-rendered token survives → genuine residue.
    return (72.0, 697.0, 300.0, 709.0)


def _build_repeated_run_pdf(out: Path, phrase: str) -> None:
    """One page: ``phrase`` rendered by ONE Tj operator with a REPEATED word.

    B.11 surgeon over-fire fixture. ``phrase`` (e.g. ``"review the review
    process"``) contains the same word twice inside a SINGLE Tj operand.
    Deleting the FIRST occurrence correctly empties that occurrence's byte
    slot, but the matched CID run is BYTE-IDENTICAL to the second, still-present
    occurrence. The buggy ``run in current`` GLOBAL substring search over the
    whole post-emptying operand therefore finds the untouched second occurrence
    and mis-reports residue (``success=False`` + no output written). A
    count-accurate predicate (occurrences-before minus occurrences-after ==
    deleted-here) must report ``success=True`` and write the output.
    """
    ttf = find_truetype_font()
    assert ttf is not None
    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf, phrase + " ")
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


def _build_adjacent_lines_pdf(out: Path) -> tuple[float, float, float, float]:
    """Heading ``Project Summary`` at y=700 over body ``Project timeline ...`` at y=688.

    B.11 ``delete_block`` over-fire fixture. The heading and the body line are
    drawn by SEPARATE show-text ops on two adjacent baselines that share the
    leading token ``Project``. The returned bbox tightly bounds the HEADING line
    only (y 698..712); ``delete_block`` correctly empties the heading op, but the
    ~2pt-tolerant ``_bbox_region_text`` re-extraction pulls the UNTOUCHED body
    line (y=688) into the region. The buggy "does a target token re-appear
    anywhere in the (tolerance-widened) region" check then sees the shared
    ``Project`` token from the adjacent line and mis-reports residue
    (``success=False``) even though the heading deletion was correct. A
    targeted-op residue check (an OVERLAPPING show-text op that was MISSED, not
    a token re-appearing in a neighbour) must report ``success=True``.

    Returns the heading-only bbox ``(x0, y0, x1, y1)``.
    """
    ttf = find_truetype_font()
    assert ttf is not None
    pdf = pikepdf.Pdf.new()
    try:
        corpus = "Project Summary timeline details "
        font = embed_identity_h_font(pdf, ttf, corpus)
        ops = [
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 700 Tm",
            f"<{font.encode('Project Summary')}> Tj",
            "ET",
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 688 Tm",
            f"<{font.encode('Project timeline details')}> Tj",
            "ET",
        ]
        _attach_page(pdf, font, "\n".join(ops).encode("latin-1"))
        emit_or_write(save_pdf_deterministic(pdf), out)
    finally:
        pdf.close()
    # Heading-only region (y 698..712). The body line at y=688 lies ~10pt below
    # the bottom edge — strictly outside, but inside the engine's 2pt-tolerant
    # region re-extraction window's reach via the shared 'Project' token.
    return (70.0, 698.0, 300.0, 712.0)


def _build_inline_image_pdf(out: Path, delete_token: str, keep_token: str) -> None:
    """One page: a deletable run, an inline image (BI/ID/EI) adjacent, then a keep run.

    The inline image immediately follows the deletable run's ET (A1.4: pikepdf
    collapses BI/ID/EI to ONE stable operator slot), so it lies adjacent to the
    deletion span. Deleting the run must surface ``inline_image_present`` (info)
    while still succeeding and leaving the keep run intact.
    """
    ttf = find_truetype_font()
    assert ttf is not None
    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf, delete_token + " " + keep_token + " ")
        img = b"\xff\x00\x00\xff"  # 2x2 grayscale, 4 samples
        parts: list[bytes] = [
            b"BT",
            b"/F1 12 Tf",
            b"1 0 0 1 72 700 Tm",
            f"<{font.encode(delete_token + ' ')}> Tj".encode("latin-1"),
            b"ET",
            b"BI /W 2 /H 2 /CS /G /BPC 8 ID " + img + b" EI",
            b"BT",
            b"/F1 12 Tf",
            b"1 0 0 1 300 700 Tm",
            f"<{font.encode(keep_token)}> Tj".encode("latin-1"),
            b"ET",
        ]
        _attach_page(pdf, font, b"\n".join(parts))
        emit_or_write(save_pdf_deterministic(pdf), out)
    finally:
        pdf.close()


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


# ---------------------------------------------------------------------------
# Inspection helpers (read-only; route through open_pdf for INV-L-1)
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


def _trailing_token_x(pdf_path: str, token: str) -> float | None:
    """Rendered x of the block whose text contains ``token`` (get_text_layout)."""
    for block in get_text_layout(pdf_path, page=0):
        if token in block.text:
            return block.x
    return None


def _first_tm_linear(pdf_path: str) -> tuple[float, float, float, float]:
    """First ``Tm`` operator's linear part ``(a, b, c, d)`` (read via open_pdf)."""
    with open_pdf(pdf_path) as pdf:
        for operands, operator in pikepdf.parse_content_stream(pdf.pages[0]):
            if str(operator) == "Tm" and len(operands) >= 4:
                return (
                    float(operands[0]),
                    float(operands[1]),
                    float(operands[2]),
                    float(operands[3]),
                )
    raise AssertionError("no Tm operator found")


# ===========================================================================
# (a) PATH A — clean single-op deletion leaves NO residue, NO over-surfacing
# ===========================================================================


@_no_font
def test_inv_b_10_single_token_deletion_no_residue(tmp_path: Path) -> None:
    """Deleting a single-op token cleanly removes it; no residue, no over-surface.

    Clean deletion must NOT flip success=False nor emit deletion_residual_text.
    This is the over-surfacing control: the B.11 provable-residue predicate must
    be EXACT (full-token delete ⇒ slice gone ⇒ success=True). Passes today; the
    B.11 fix must keep it GREEN.
    """
    token = "LinkedIn"
    src = tmp_path / "single.pdf"
    _build_single_run_pdf(src, token)

    matches = find(str(src), token)
    assert len(matches) == 1, f"expected one {token!r} match, got {len(matches)}"

    out = tmp_path / "single_out.pdf"
    res = replace(str(src), matches[0], "", str(out))

    assert res.success is True, f"clean single-op deletion must succeed: {res!r}"
    assert token not in get_text(str(out), page=0), "deleted token must be absent"
    assert _RESIDUE_KIND not in _kinds(res), (
        "clean deletion must NOT over-surface deletion_residual_text"
    )


# ===========================================================================
# (b) PATH B — in-operator NEIGHBOUR integrity (THE silent-wrong; RED today)
# ===========================================================================


@_no_resume
def test_inv_b_10_batch_neighbor_integrity(tmp_path: Path) -> None:
    """Deleting ``Software`` (shared TJ op [103]) must NOT corrupt ``Developer``.

    RED today: replace_all returns success=True, degs=[] but emits the
    ``"eveloper"`` corruption marker (the leading glyph of the neighbour is
    eaten by the shared-fragment suffix-boundary mis-inference). The GREEN fix
    threads the exact Identity-H byte_width so the suffix starts on a CID
    boundary and ``Developer`` survives intact.
    """
    src = str(_RESUME)

    # Precondition: the two words share one TJ operator (the corruption vector).
    sw = find(src, "Software")
    dv = find(src, "Developer")
    assert sw and dv, "resume must contain both Software and Developer"
    assert sw[0].operator_refs == dv[0].operator_refs, (
        "precondition: Software and Developer must share one TJ operator; "
        f"got {sw[0].operator_refs} vs {dv[0].operator_refs}"
    )

    out = tmp_path / "neighbor.pdf"
    res = replace_all(src, "Software", "", str(out))
    text = get_text(str(out), page=0)

    # Today the engine reports a clean success with zero degradations while
    # silently corrupting the neighbour — the silent-wrong this case pins.
    silent_clean = _all_success(res) and not _kinds(res)

    assert "Software" not in text, "Software must be deleted"
    # THE GUARD: the in-operator neighbour must survive byte-exact.
    assert "Developer" in text, (
        "in-operator neighbour 'Developer' was corrupted by the shared-TJ-fragment "
        f"deletion (silent_clean={silent_clean}; extracted text fragment: {text!r})"
    )
    assert "eveloper" not in text or "Developer" in text, (
        "corruption marker 'eveloper' present — the leading glyph of the neighbour "
        "was eaten by the empty-rebuild suffix-boundary calc"
    )


# ===========================================================================
# (c) Trailing-text position preserved on an axis-aligned mid-line delete
#     (RED today: End mis-shifts because the deleted run's advance is not
#      compensated)
# ===========================================================================


@_no_font
def test_inv_b_10_trailing_text_position_axis_aligned(tmp_path: Path) -> None:
    """Deleting a mid-line run keeps the trailing same-line token's x (~1pt).

    RED today: the trailing ``End`` shifts by the deleted run's advance because
    the empty-deletion path does not compensate the following relative ``Td``.
    The GREEN compensating-advance (POS-GATE, axis-aligned) must hold ``End`` at
    its original x.
    """
    src = tmp_path / "trail.pdf"
    delete_token, trailing_token = _build_trailing_run_pdf(src)

    before_x = _trailing_token_x(str(src), trailing_token)
    assert before_x is not None, f"could not locate trailing token {trailing_token!r}"

    matches = find(str(src), delete_token)
    assert matches, f"could not find {delete_token!r}"

    out = tmp_path / "trail_out.pdf"
    res = replace(str(src), matches[0], "", str(out))
    assert res.success is True, f"axis-aligned mid-line delete must succeed: {res!r}"

    text = get_text(str(out), page=0)
    assert delete_token not in text, f"{delete_token!r} must be deleted"

    after_x = _trailing_token_x(str(out), trailing_token)
    assert after_x is not None, f"trailing token {trailing_token!r} lost after delete"
    assert after_x == pytest.approx(before_x, abs=1.0), (
        "trailing same-line text mis-shifted: the deleted run's advance was not "
        f"compensated (x {before_x:.3f} -> {after_x:.3f})"
    )


# ===========================================================================
# (d) Rotated run: horizontal compensation SKIPPED + positioning_adjustment_skipped
#     (already locked POS-GATE behaviour; regression guard, NOT a RED)
# ===========================================================================


@_no_rot_font
def test_inv_b_10_rotated_run_skips_advance(tmp_path: Path) -> None:
    """Deleting a run on a rotated baseline emits positioning_adjustment_skipped.

    The non-axis-aligned gate must skip horizontal width-delta compensation and
    surface ``positioning_adjustment_skipped`` (warning); the trailing run must
    not be mis-shifted along the wrong axis and the rotated Tm must survive.
    """
    src = tmp_path / "rot.pdf"
    assert build_rotated_text_pdf(src) is not None

    before_tm = _first_tm_linear(str(src))
    a, b, c, d = before_tm
    assert not (abs(a - 1) < 1e-3 and abs(b) < 1e-3 and abs(c) < 1e-3 and abs(d - 1) < 1e-3), (
        f"fixture must be rotated; got Tm linear {before_tm}"
    )

    matches = find(str(src), "Section")
    assert matches, "could not find rotated edit target 'Section'"

    out = tmp_path / "rot_out.pdf"
    res = replace(str(src), matches[0], "", str(out))

    assert _POS_SKIP_KIND in _kinds(res), (
        "deleting a run on a rotated baseline must emit positioning_adjustment_skipped; "
        f"got kinds={_kinds(res)}"
    )
    # The trailing run survives and the rotation is preserved.
    assert "Heading" in get_text(str(out), page=0), "trailing rotated run must survive"
    after_tm = _first_tm_linear(str(out))
    assert after_tm == pytest.approx(before_tm, abs=1e-3), (
        f"rotated Tm must be preserved; got {before_tm} -> {after_tm}"
    )


# ===========================================================================
# (e) PATH C — provable residue surfaces success=False + deletion_residual_text
#     (RED today: residue remains at success=True, degs=[])
# ===========================================================================


@_no_font
def test_inv_b_10_provable_residue_surfaces_failure(tmp_path: Path) -> None:
    """A delete_block whose bbox misses the show-text op must FAIL honestly.

    RED today: ``delete_block`` returns success=True, degs=[] yet the rendered
    token survives (the bbox→op collection missed the show-text op, the classic
    "GitHub" PATH C residue). The GREEN provable-residue predicate must detect
    the leftover glyphs and surface success=False + deletion_residual_text
    (warning), with font_preserved staying True (residue, not a font swap).
    """
    token = "GitHubX"
    src = tmp_path / "residue.pdf"
    bbox = _build_missed_bbox_residue_pdf(src, token)

    out = tmp_path / "residue_out.pdf"
    res = delete_block(str(src), 0, bbox, str(out), close_gap=False)

    # The bug today: residue remains AND the engine claims success with no signal.
    text = get_text(str(out), page=0)
    residue_present = token in text

    assert res.success is False, (
        "a deletion that leaves provable residue must report success=False "
        f"(residue_present={residue_present}, degs={_kinds(res)})"
    )
    assert _RESIDUE_KIND in _kinds(res), (
        "provable residue must surface a deletion_residual_text degradation; "
        f"got kinds={_kinds(res)}"
    )
    assert res.fidelity_report.font_preserved is True, (
        "deletion residue is NON font-affecting — font_preserved must stay True"
    )


# ===========================================================================
# (e2) OVER-FIRE — surgeon: deleting ONE of two IDENTICAL repeated runs in the
#      SAME Tj operand must NOT mis-report residue (RED today: global-substring
#      `run in current` finds the untouched second occurrence -> false
#      success=False AND no output written = silent data loss on a CORRECT delete)
# ===========================================================================


@_no_font
def test_inv_b_10_surgeon_repeated_run_no_false_residue(tmp_path: Path) -> None:
    """Deleting the first of two identical runs in one Tj must succeed + write output.

    OVER-FIRE RED (the BLOCKER). ``"review the review process"`` renders in ONE
    Tj operator; ``find("review")`` returns two matches sharing that operator.
    Deleting the FIRST is correct — the first occurrence's byte slot is emptied
    and exactly one ``review`` must remain. But the B.11 residue predicate does
    a GLOBAL ``run in current`` byte-substring search over the whole
    post-emptying operand, so the still-present SECOND ``review`` makes the
    predicate fire: ``success=False`` AND — because the surgeon gates ``_save_pdf``
    on ``result.success`` — NO output file is written. A correct deletion that
    writes no output is silent data loss, the worst failure mode.

    GREEN contract (count-accurate residue): a delete of ONE occurrence among
    identical repeats reports ``success=True``, the output file EXISTS, and
    exactly one ``review`` was removed (the other survives intact).

    RED today for the right reason: false ``success=False`` +
    ``deletion_residual_text`` from the global-substring over-fire, and the
    output file is absent.
    """
    phrase = "review the review process"
    src = tmp_path / "repeat.pdf"
    _build_repeated_run_pdf(src, phrase)

    before = get_text(str(src), page=0)
    before_count = before.count("review")
    assert before_count == 2, f"precondition: two 'review' occurrences; got {before_count}"

    matches = find(str(src), "review")
    assert len(matches) == 2, f"expected two 'review' matches; got {len(matches)}"
    # Precondition: both occurrences share ONE Tj operator (the over-fire vector).
    assert matches[0].operator_refs == matches[1].operator_refs, (
        "precondition: both 'review' runs must share one Tj operator; "
        f"got {matches[0].operator_refs} vs {matches[1].operator_refs}"
    )

    out = tmp_path / "repeat_out.pdf"
    res = replace(str(src), matches[0], "", str(out))

    # THE BLOCKER GUARD: a correct single-occurrence delete must write output.
    assert out.exists(), (
        "deleting one of two identical repeated runs wrote NO output file — the "
        "global-substring residue over-fire flipped success=False and blocked the "
        f"save (silent data loss); res={res!r}"
    )
    assert res.success is True, (
        "deleting one occurrence among identical repeats is a CORRECT delete and "
        f"must report success=True; got {res!r} kinds={_kinds(res)}"
    )
    assert _RESIDUE_KIND not in _kinds(res), (
        "the still-present SECOND 'review' is NOT residue of the first delete — "
        f"the count-accurate predicate must not over-surface; kinds={_kinds(res)}"
    )
    after = get_text(str(out), page=0)
    assert after.count("review") == before_count - 1, (
        f"exactly one 'review' must be removed; before={before_count} "
        f"after={after.count('review')} (text={after!r})"
    )


# ===========================================================================
# (e3) OVER-FIRE — delete_block: an ADJACENT untouched line sharing a token
#      within the bbox tolerance must NOT count as residue
#      (RED today: token-reappears-in-region check false-fires)
# ===========================================================================


@_no_font
def test_inv_b_10_delete_block_adjacent_line_no_false_residue(tmp_path: Path) -> None:
    """Deleting a heading must not mis-report residue from an adjacent shared token.

    OVER-FIRE RED (the MAJOR). Heading ``"Project Summary"`` (y=700) sits over
    body ``"Project timeline details"`` (y=688) as two SEPARATE show-text ops
    sharing the leading token ``Project``. ``delete_block`` over the heading-only
    bbox correctly empties the heading op (the body line survives). But the B.11
    residue check re-extracts the bbox region with a ~2pt tolerance that pulls
    the adjacent body line into the region, then fires on any shared >=2-char
    target token (``Project``) re-appearing there — so it mis-reports
    ``success=False`` even though the heading deletion was correct.

    GREEN contract (targeted-op residue): residue means an OVERLAPPING
    show-text op the collection MISSED (and so still renders the deleted
    target), NOT a token that merely re-appears in a tolerance-widened
    neighbour. The adjacent untouched line must NOT trigger residue:
    ``success=True``, the heading is gone, the body line survives.

    RED today for the right reason: the shared ``Project`` token in the
    adjacent line false-fires the region-substring residue check.
    """
    src = tmp_path / "adj.pdf"
    bbox = _build_adjacent_lines_pdf(src)

    before = get_text(str(src), page=0)
    assert "Project Summary" in before and "Project timeline details" in before, (
        f"precondition: both heading and body present; got {before!r}"
    )

    out = tmp_path / "adj_out.pdf"
    res = delete_block(str(src), 0, bbox, str(out), close_gap=False)

    after = get_text(str(out), page=0)
    # The heading deletion itself is correct regardless of the verdict bug.
    assert "Summary" not in after, f"heading should be deleted; got {after!r}"
    assert "timeline details" in after, (
        f"the ADJACENT untouched body line must survive; got {after!r}"
    )

    assert res.success is True, (
        "the adjacent untouched line sharing the token 'Project' within the bbox "
        "tolerance must NOT trigger residue — the heading delete was correct; "
        f"got success=False, kinds={_kinds(res)}"
    )
    assert _RESIDUE_KIND not in _kinds(res), (
        "a shared token re-appearing in a tolerance-widened NEIGHBOUR is not "
        f"residue; kinds={_kinds(res)}"
    )


# ===========================================================================
# (f) Inline image in/adjacent to the deletion span surfaces inline_image_present
#     (RED today: the kind does not exist / is never emitted)
# ===========================================================================


@_no_font
def test_inv_b_10_inline_image_present_surfaced(tmp_path: Path) -> None:
    """Deleting a run adjacent to a BI/ID/EI inline image surfaces an info signal.

    RED today: no ``inline_image_present`` kind is emitted. The deletion still
    succeeds (A1.4: the inline image is one stable operator slot, so
    operator_index addressing survives) and the non-image keep text deletes
    cleanly; the inline image presence is surfaced as an advisory info
    degradation (does not set success=False on its own).
    """
    delete_token = "DeleteMe"
    keep_token = "Keep"
    src = tmp_path / "inline.pdf"
    _build_inline_image_pdf(src, delete_token, keep_token)

    # Precondition: the page really carries exactly one inline-image slot.
    with open_pdf(str(src)) as pdf:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
    inline_slots = [i for i, ins in enumerate(ops) if str(ins.operator) == "INLINE IMAGE"]
    assert len(inline_slots) == 1, f"fixture must carry one inline image; slots={inline_slots}"

    matches = find(str(src), delete_token)
    assert matches, f"could not find {delete_token!r}"

    out = tmp_path / "inline_out.pdf"
    res = replace(str(src), matches[0], "", str(out))

    assert res.success is True, (
        "deleting text adjacent to an inline image must still succeed (A1.4 stable slot); "
        f"got {res!r}"
    )
    assert _INLINE_KIND in _kinds(res), (
        "an inline image in/adjacent to the deletion span must surface "
        f"inline_image_present (info); got kinds={_kinds(res)}"
    )
    text = get_text(str(out), page=0)
    assert delete_token not in text, f"{delete_token!r} must be deleted"
    assert keep_token in text, f"non-image keep text {keep_token!r} must survive"


# ===========================================================================
# (g) Resume: delete GitHub via BOTH paths — no residue, neighbours intact
#     (RED today: both paths leave residue / corrupt neighbours)
# ===========================================================================


@_no_resume
def test_inv_b_10_resume_delete_no_residue_neighbors_intact(tmp_path: Path) -> None:
    """On the real resume, deleting ``GitHub`` via both paths keeps neighbours intact.

    Resume contract guard (the live PATH-C residue is pinned synthetically in
    ``test_inv_b_10_provable_residue_surfaces_failure``; on this resume the
    GitHub bbox/op collection happens to hit, so this is a real-world
    regression guard rather than a fresh RED). It pins TWO properties the B.11
    fix must not regress:

    * ``delete_block`` over a ``GitHub`` bbox must remove that occurrence (or
      refuse honestly) — never leave it AND claim ``success=True`` silently.
    * ``replace_all(GitHub, "")`` must clear every rendered occurrence WITHOUT
      corrupting the real adjacent tokens. ``GitHub`` shares its TJ operator
      with ``Actions`` on the Cloud/DevOps line (the in-operator neighbour
      vector); ``Portfolio`` follows the first ``GitHub`` on the contact line.
      Both must survive byte-exact (no eaten leading glyph).
    """
    src = str(_RESUME)
    before = get_text(src, page=0)
    matches = find(src, "GitHub")
    assert matches, "resume must contain GitHub"
    before_github = before.count("GitHub")
    assert before_github >= 1, "precondition: resume renders GitHub"

    # --- Path 1: delete_block over the first GitHub bbox (close_gap=False) ---
    bbox = matches[0].bounding_box
    out_db = tmp_path / "resume_db.pdf"
    res_db = delete_block(src, 0, bbox, str(out_db), close_gap=False)
    text_db = get_text(str(out_db), page=0)
    # The contract: the targeted occurrence is gone, OR the engine refuses
    # honestly (success=False). Never "still present AND success=True silently".
    occurrence_removed = text_db.count("GitHub") < before_github
    assert occurrence_removed or res_db.success is False, (
        "delete_block left a GitHub occurrence AND claimed success with no signal "
        f"(count {before_github} -> {text_db.count('GitHub')}, degs={_kinds(res_db)})"
    )

    # --- Path 2: replace_all GitHub -> "" : clear all, neighbours byte-stable ---
    assert "GitHub Actions" in before, "precondition: resume has the 'GitHub Actions' line"
    assert "Portfolio" in before, "precondition: resume has the Portfolio neighbour"
    out_ra = tmp_path / "resume_ra.pdf"
    replace_all(src, "GitHub", "", str(out_ra))
    text_ra = get_text(str(out_ra), page=0)

    assert text_ra.count("GitHub") == 0, (
        f"replace_all must clear every rendered GitHub; {text_ra.count('GitHub')} remain"
    )
    # In-operator neighbour (shared TJ op): the Cloud/DevOps 'Actions' must keep
    # its leading glyph — a deletion that eats it leaves an orphan 'ctions'.
    assert "Actions" in text_ra, (
        "the in-operator neighbour 'Actions' (GitHub Actions line) was corrupted "
        "by the GitHub deletion (leading glyph eaten)"
    )
    assert "Portfolio" in text_ra, (
        "adjacent 'Portfolio' contact-line token was corrupted by the GitHub deletion"
    )


# ===========================================================================
# (h) dry_run parity — degradations + success identical, output unwritten
# ===========================================================================


@_no_resume
def test_inv_b_10_dry_run_parity_neighbor(tmp_path: Path) -> None:
    """Case (b) under dry_run: identical degradations + success, no file written.

    The deletion DECISION (residue / neighbour surfacing) must be computed
    pre-save in BOTH modes, so dry_run=True and dry_run=False produce the
    identical degradation list and verdict; the dry_run output must be
    unwritten.
    """
    src = str(_RESUME)
    out_live = tmp_path / "parity_live.pdf"
    out_dry = tmp_path / "parity_dry.pdf"

    res_live = replace_all(src, "Software", "", str(out_live))
    res_dry = replace_all(src, "Software", "", str(out_dry), dry_run=True)

    live_kinds = sorted(_kinds(res_live))
    dry_kinds = sorted(_kinds(res_dry))
    assert dry_kinds == live_kinds, (
        f"dry_run degradations diverge from live: dry={dry_kinds} vs live={live_kinds}"
    )
    assert _all_success(res_dry) == _all_success(res_live), (
        "dry_run success verdict must match the live verdict"
    )
    assert not out_dry.exists(), "dry_run must not write an output file"


@_no_font
def test_inv_b_10_dry_run_parity_residue_surgeon(tmp_path: Path) -> None:
    """Case (e) parity via a dry_run-capable surgeon delete.

    ``delete_block`` has no ``dry_run`` parameter, so the residue-path dry_run
    parity is exercised through the surgeon empty-replacement path (which does
    support dry_run). The degradation list + success verdict must be identical
    between modes and the dry_run output must be unwritten.
    """
    token = "LinkedIn"
    src = tmp_path / "parity_surgeon.pdf"
    _build_single_run_pdf(src, token)

    matches = find(str(src), token)
    assert matches, f"could not find {token!r}"

    out_live = tmp_path / "ps_live.pdf"
    out_dry = tmp_path / "ps_dry.pdf"

    res_live = replace(str(src), matches[0], "", str(out_live))
    res_dry = replace(str(src), matches[0], "", str(out_dry), dry_run=True)

    assert sorted(_kinds(res_dry)) == sorted(_kinds(res_live)), (
        f"dry_run degradations diverge: dry={sorted(_kinds(res_dry))} vs "
        f"live={sorted(_kinds(res_live))}"
    )
    assert res_dry.success == res_live.success, "dry_run success must match live"
    assert not out_dry.exists(), "dry_run must not write an output file"
