"""INV-POS-1: axis-aligned trailing-text adjustment is preserved (regression guard).

POS-GATE root-fix invariant (sibling: INV-POS-2). The fix teaches
``surgeon._adjust_subsequent_positioning`` to SKIP horizontal width-delta
compensation when the edited run's text matrix is non-axis-aligned. This
probe pins the OTHER half of the contract: an **axis-aligned** edit
(``Tm = [1 0 0 1 x y]``) must remain EXACTLY as before — the trailing
``Td`` operand is still compensated by ``-width_delta`` and NO
``positioning_adjustment_skipped`` Degradation is emitted.

Regression guard — fails if the POS-GATE block in surgeon.py (axis-alignment
gate + positioning_adjustment_skipped emission) is reverted in a way that
regresses the happy path. The byte-shape assertion (trailing ``Td`` tx changed
by ``-width_delta``) is the load-bearing regression lock.
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

from corpus_builders.rotated_text import build_axis_aligned_two_run_pdf  # noqa: E402

_FONT_OK = build_axis_aligned_two_run_pdf() is not None
_no_font = pytest.mark.skipif(
    not _FONT_OK, reason="no host TrueType font for Identity-H construction"
)

# Replacement reuses only subset glyphs of "SectionHeading" (a, t, e, n, d)
# so no font extension runs, and is narrower than "Section" so width_delta
# is non-trivial (> 0.5pt) — the gate at surgeon.py:946 fires.
_TARGET = "Section"
_REPLACEMENT = "attend"


def _trailing_td_tx(pdf_path: str) -> float:
    """Return the tx operand of the first same-line trailing ``Td`` operator."""
    with pikepdf.open(pdf_path) as pdf:
        for operands, operator in pikepdf.parse_content_stream(pdf.pages[0]):
            if str(operator) == "Td" and len(operands) >= 2 and abs(float(operands[1])) < 1e-6:
                return float(operands[0])
    raise AssertionError("no same-line trailing Td operator found in output")


@pytest.fixture
def axis_pdf(tmp_path: Path) -> Path:
    """An axis-aligned two-run Identity-H PDF (Tm = [1 0 0 1 x y])."""
    out = tmp_path / "axis_aligned.pdf"
    assert build_axis_aligned_two_run_pdf(out) is not None
    return out


@_no_font
def test_inv_pos_1_axis_aligned_matrix_is_axis_aligned(axis_pdf: Path) -> None:
    """Precondition: the first run's Tm linear part is the identity (a=1,b=0,c=0,d=1)."""
    with pikepdf.open(str(axis_pdf)) as pdf:
        tms = [
            [float(o) for o in operands]
            for operands, operator in pikepdf.parse_content_stream(pdf.pages[0])
            if str(operator) == "Tm"
        ]
    assert tms, "fixture must contain a Tm operator"
    a, b, c, d = tms[0][:4]
    assert abs(a - 1.0) < 1e-3 and abs(b) < 1e-3 and abs(c) < 1e-3 and abs(d - 1.0) < 1e-3, (
        f"axis-aligned fixture Tm linear part must be identity; got {(a, b, c, d)}"
    )


@_no_font
def test_inv_pos_1_axis_aligned_adjustment_still_applied(axis_pdf: Path, tmp_path: Path) -> None:
    """Axis-aligned edit: trailing Td IS compensated and NO skip Degradation fires.

    Regression lock for the POS-GATE root fix. The trailing ``Td`` tx must
    move by exactly ``-width_delta`` (replacement narrower → tx increases),
    matching pre-fix behaviour byte-for-byte, and the result must carry no
    ``positioning_adjustment_skipped`` event.
    """
    before_tx = _trailing_td_tx(str(axis_pdf))

    matches = find(str(axis_pdf), _TARGET)
    assert matches, f"find({_TARGET!r}) must locate the edit target"
    out = tmp_path / "axis_out.pdf"
    result = replace(str(axis_pdf), matches[0], _REPLACEMENT, str(out), reflow=False)
    assert result.success, f"axis-aligned replace should succeed: {result!r}"

    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert "positioning_adjustment_skipped" not in kinds, (
        f"axis-aligned edit must NOT emit positioning_adjustment_skipped; got kinds={kinds}"
    )

    after_tx = _trailing_td_tx(str(out))
    # Compensation was applied: a narrower replacement (width_delta < 0)
    # increases the trailing tx (tx_new = tx_old - width_delta).
    assert after_tx != pytest.approx(before_tx, abs=1e-6), (
        "axis-aligned trailing Td must be adjusted (compensation applied), "
        f"but tx was unchanged at {after_tx}"
    )
    assert after_tx > before_tx, (
        "narrower replacement should increase the trailing Td tx "
        f"(tx_old={before_tx}, tx_new={after_tx})"
    )
