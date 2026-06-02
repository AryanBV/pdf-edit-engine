"""INV-POS-2: non-axis-aligned (rotated) edits SKIP horizontal compensation.

POS-GATE root-fix invariant (sibling: INV-POS-1). When the edited run's
text matrix is non-axis-aligned (rotated or sheared — NOT
``a~1 AND b~0 AND c~0 AND d~1`` within epsilon ~1e-3),
``surgeon._adjust_subsequent_positioning`` must NOT subtract the horizontal
``width_delta`` from the trailing run's ``Td``/``Tm`` operand (that shift is
along the wrong axis under rotation and visibly mis-places the trailing
text). Instead the engine must:

1. leave the trailing operand UNCHANGED (skip the horizontal compensation),
   and
2. surface a typed ``Degradation(kind="positioning_adjustment_skipped",
   severity="warning")`` so the caller sees that the trailing-text
   adjustment was intentionally declined — honest typed degradation over
   silently-wrong output.

Regression guard — fails if the POS-GATE block in surgeon.py (axis-alignment
gate + positioning_adjustment_skipped emission) is reverted: without the gate
the engine mutates the trailing ``Td`` tx (mis-shift) and emits NO
``positioning_adjustment_skipped`` event.
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

# Same-subset, narrower replacement (a, t, e, n, d are all in "SectionHeading")
# so no font extension runs and |width_delta| > 0.5pt — the adjustment gate
# at surgeon.py:946 fires, reaching _adjust_subsequent_positioning.
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
def rotated_pdf(tmp_path: Path) -> Path:
    """A 90deg-rotated two-run Identity-H PDF (Tm = [0 -1 1 0 x y])."""
    out = tmp_path / "rotated.pdf"
    assert build_rotated_text_pdf(out) is not None
    return out


@_no_font
def test_inv_pos_2_fixture_is_non_axis_aligned(rotated_pdf: Path) -> None:
    """Precondition: the first run's Tm is rotated (NOT a~1,b~0,c~0,d~1)."""
    with pikepdf.open(str(rotated_pdf)) as pdf:
        tms = [
            [float(o) for o in operands]
            for operands, operator in pikepdf.parse_content_stream(pdf.pages[0])
            if str(operator) == "Tm"
        ]
    assert tms, "fixture must contain a Tm operator"
    a, b, c, d = tms[0][:4]
    axis_aligned = abs(a - 1.0) < 1e-3 and abs(b) < 1e-3 and abs(c) < 1e-3 and abs(d - 1.0) < 1e-3
    assert not axis_aligned, f"fixture must be non-axis-aligned; got Tm linear part {(a, b, c, d)}"


@_no_font
def test_inv_pos_2_rotated_emits_positioning_adjustment_skipped(
    rotated_pdf: Path, tmp_path: Path
) -> None:
    """Rotated edit surfaces a typed positioning_adjustment_skipped Degradation.

    Regression guard — fails if the POS-GATE block in surgeon.py is reverted:
    without the gate the engine emits no such event.
    """
    matches = find(str(rotated_pdf), _TARGET)
    assert matches, f"find({_TARGET!r}) must locate the edit target"
    out = tmp_path / "rot_out.pdf"
    result = replace(str(rotated_pdf), matches[0], _REPLACEMENT, str(out), reflow=False)
    assert result.success, f"rotated replace should still succeed: {result!r}"

    degs = result.fidelity_report.degradations
    kinds = {d.kind for d in degs}
    assert "positioning_adjustment_skipped" in kinds, (
        "rotated (non-axis-aligned) edit must surface a "
        f"positioning_adjustment_skipped Degradation; got kinds={kinds}"
    )
    match = next(d for d in degs if d.kind == "positioning_adjustment_skipped")
    assert match.severity == "warning", (
        f"positioning_adjustment_skipped must be severity='warning'; got {match.severity!r}"
    )


@_no_font
def test_inv_pos_2_rotated_trailing_td_unchanged(rotated_pdf: Path, tmp_path: Path) -> None:
    """Rotated edit leaves the trailing Td operand UNCHANGED (no wrong-axis shift).

    Regression guard — fails if the POS-GATE block in surgeon.py is reverted:
    without the gate the engine subtracts width_delta from the trailing Td tx,
    mis-shifting the trailing run along the wrong (horizontal) axis.
    """
    before_tx = _trailing_td_tx(str(rotated_pdf))

    matches = find(str(rotated_pdf), _TARGET)
    assert matches
    out = tmp_path / "rot_out2.pdf"
    result = replace(str(rotated_pdf), matches[0], _REPLACEMENT, str(out), reflow=False)
    assert result.success

    after_tx = _trailing_td_tx(str(out))
    assert after_tx == pytest.approx(before_tx, abs=1e-6), (
        "under a non-axis-aligned matrix the trailing Td tx must be left "
        f"unchanged (skip compensation); got tx_old={before_tx}, tx_new={after_tx}"
    )
