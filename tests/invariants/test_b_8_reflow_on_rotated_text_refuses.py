"""INV-B-8: reflow on rotated text refuses instead of silently flattening.

Roadmap item B.12 (rotated/sheared text editing), refusal half (sibling:
INV-B-7). ``reflow.reflow_paragraph`` rewrites a paragraph by re-emitting a
FRESH identity text matrix ``Tm = [1 0 0 1 ...]``. Applied to a NON-axis-
aligned (rotated/sheared) run that is a SILENT FLATTEN: a 90deg-rotated
paragraph is rewritten horizontal with no signal to the caller.

The B.12 contract: when an edit on a non-axis-aligned run would route through
reflow, the engine must REFUSE — ``EditResult.success=False`` carrying a typed
``Degradation(kind="rotated_text_unsupported", severity="warning")`` — rather
than emit the rotation-flattening rewrite. The refused edit must NOT introduce
an identity ``Tm`` where the rotated baseline used to render (no partial
mutation that flattens rotation). ``rotated_text_unsupported`` is NOT in
``FONT_AFFECTING_KINDS`` (glyph identity is untouched), so the refusal uses
``font_action="kept"`` and ``font_preserved`` stays True.

The axis-aligned regression control pins the over-refusal boundary: an
axis-aligned reflow (the normal happy path) must KEEP succeeding — refusing
correct reflow would be a defect.

Regression guard — fails if reflow flattens rotated text without refusing
(the current pre-B.12 behaviour: success=True, identity Tm emitted, no
rotated_text_unsupported event).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.locator import find
from pdf_edit_engine.surgeon import replace

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders.rotated_text import build_rotated_text_pdf  # noqa: E402

_FONT_OK = build_rotated_text_pdf() is not None
_no_font = pytest.mark.skipif(
    not _FONT_OK, reason="no host TrueType font for Identity-H construction"
)

# A meaningfully WIDER, single-word, same-subset replacement. No space (space
# is outside the "SectionHeading" subset, which would short-circuit width
# computation back to the simple path); every glyph ('S','e','c','t','i','o',
# 'n') is in the subset, so width is computable and exceeds the original by
# >1pt — this forces surgeon to route the edit through reflow_paragraph.
_TARGET = "Section"
_WIDENING_REPLACEMENT = "Sectionnnnnnnnnnnnn"


def _tm_linears(pdf_path: str) -> list[tuple[float, float, float, float]]:
    """Return the linear part ``(a, b, c, d)`` of every ``Tm`` operator."""
    out: list[tuple[float, float, float, float]] = []
    with pikepdf.open(pdf_path) as pdf:
        for operands, operator in pikepdf.parse_content_stream(pdf.pages[0]):
            if str(operator) == "Tm" and len(operands) >= 4:
                out.append(
                    (
                        float(operands[0]),
                        float(operands[1]),
                        float(operands[2]),
                        float(operands[3]),
                    )
                )
    return out


def _is_identity(linear: tuple[float, float, float, float]) -> bool:
    a, b, c, d = linear
    return abs(a - 1.0) < 1e-3 and abs(b) < 1e-3 and abs(c) < 1e-3 and abs(d - 1.0) < 1e-3


# The ``rotated_pdf`` and ``axis_pdf`` fixtures live in
# tests/invariants/conftest.py (shared single source of truth with
# test_b_7_*); the test functions resolve them by name unchanged.


@_no_font
def test_inv_b_8_rotated_fixture_has_no_identity_tm(rotated_pdf: Path) -> None:
    """Precondition: the rotated fixture contains no identity Tm to confuse the flatten check."""
    linears = _tm_linears(str(rotated_pdf))
    assert linears, "fixture must contain a Tm operator"
    assert not any(_is_identity(t) for t in linears), (
        f"rotated fixture must not already contain an identity Tm; got {linears}"
    )


@_no_font
def test_inv_b_8_reflow_on_rotated_refuses(rotated_pdf: Path, tmp_path: Path) -> None:
    """Reflow-routed edit on rotated text REFUSES with rotated_text_unsupported.

    Regression guard — fails on the pre-B.12 behaviour where reflow rewrites
    the rotated paragraph with a fresh identity Tm and returns success=True
    with no refusal event.
    """
    matches = find(str(rotated_pdf), _TARGET)
    assert matches, f"find({_TARGET!r}) must locate the edit target"
    out = tmp_path / "reflow_rotated_out.pdf"
    result = replace(str(rotated_pdf), matches[0], _WIDENING_REPLACEMENT, str(out), reflow=True)

    assert result.success is False, (
        "a reflow-routed edit on rotated/sheared text must REFUSE (success=False) "
        f"rather than silently flatten the rotation; got {result!r}"
    )

    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert "rotated_text_unsupported" in kinds, (
        "the rotated-reflow refusal must surface a typed rotated_text_unsupported "
        f"Degradation; got kinds={kinds}"
    )
    deg = next(
        d for d in result.fidelity_report.degradations if d.kind == "rotated_text_unsupported"
    )
    assert deg.severity == "warning", (
        f"rotated_text_unsupported must be severity='warning'; got {deg.severity!r}"
    )


@_no_font
def test_inv_b_8_refused_reflow_does_not_flatten_rotation(
    rotated_pdf: Path, tmp_path: Path
) -> None:
    """The refused edit must not introduce an identity Tm (no rotation-flattening mutation).

    Regression guard — fails on the pre-B.12 behaviour where reflow emits a
    fresh ``[1 0 0 1 ...]`` Tm in place of the rotated baseline.
    """
    out = tmp_path / "reflow_rotated_out2.pdf"
    matches = find(str(rotated_pdf), _TARGET)
    assert matches
    replace(str(rotated_pdf), matches[0], _WIDENING_REPLACEMENT, str(out), reflow=True)

    if not out.exists():
        # A refusal that writes no output at all is also acceptable (no
        # mutation). Nothing to flatten — contract satisfied.
        return
    linears = _tm_linears(str(out))
    assert not any(_is_identity(t) for t in linears), (
        "the refused rotated edit must not introduce an identity Tm — that is the "
        f"silent-flatten the gate exists to prevent; got Tm linears={linears}"
    )


@_no_font
def test_inv_b_8_axis_aligned_reflow_still_succeeds(axis_pdf: Path, tmp_path: Path) -> None:
    """Over-refusal control: axis-aligned reflow must KEEP succeeding.

    The rotation gate must refuse ONLY non-axis-aligned reflow. An axis-aligned
    (identity-rotation) paragraph reflow is correct output and must not be
    refused — refusing correct reflow would be a defect.
    """
    matches = find(str(axis_pdf), _TARGET)
    assert matches, f"find({_TARGET!r}) must locate the edit target"
    out = tmp_path / "reflow_axis_out.pdf"
    result = replace(str(axis_pdf), matches[0], _WIDENING_REPLACEMENT, str(out), reflow=True)

    assert result.success, f"axis-aligned reflow must still succeed: {result!r}"
    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert "rotated_text_unsupported" not in kinds, (
        f"axis-aligned reflow must NOT be refused with rotated_text_unsupported; got kinds={kinds}"
    )
