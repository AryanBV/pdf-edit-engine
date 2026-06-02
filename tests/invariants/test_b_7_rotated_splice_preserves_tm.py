"""INV-B-7: same-length splice on rotated text preserves the run's rotation.

Roadmap item B.12 (rotated/sheared text editing), rotation-SAFE half. A
same-length, same-subset replacement on a NON-axis-aligned (rotated) run must
route through the in-place splice path (``reflow=False``) and leave the run's
governing text matrix linear part UNCHANGED — the rotation must survive the
edit byte-for-byte. The splice replaces only the glyph payload of the existing
``Tj``/``TJ`` operators; it never re-emits the ``Tm``, so the rotated baseline
is preserved by construction.

This probe pins the over-refusal boundary for the B.12 rotation gate: the gate
must REFUSE only the reflow path (which re-emits a fresh identity ``Tm`` and
would silently flatten rotation), never the rotation-safe splice. A gate that
refused this correct output would be a defect (refusing correct work).

Regression guard — fails if a future change either (a) flattens the rotated
``Tm`` to the identity on the splice path, or (b) over-broadly emits the
``rotated_text_unsupported`` refusal on a rotation-safe same-length edit.
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

# A permutation of the edit target: every glyph is already in the embedded
# subset ("SectionHeading"), the multiset of advances is identical, and the
# length matches exactly. This drives the in-place splice path with no font
# extension and a width_delta of ~0 (so no positioning compensation fires) —
# the cleanest rotation-safe edit available.
_TARGET = "Section"
_REPLACEMENT = "noitceS"


def _first_tm_linear(pdf_path: str) -> tuple[float, float, float, float]:
    """Return the first ``Tm`` operator's linear part ``(a, b, c, d)``."""
    with pikepdf.open(pdf_path) as pdf:
        for operands, operator in pikepdf.parse_content_stream(pdf.pages[0]):
            if str(operator) == "Tm" and len(operands) >= 4:
                return (
                    float(operands[0]),
                    float(operands[1]),
                    float(operands[2]),
                    float(operands[3]),
                )
    raise AssertionError("no Tm operator found in output")


# The ``rotated_pdf`` fixture lives in tests/invariants/conftest.py (shared
# single source of truth with test_b_8_*); the test functions resolve it by
# name unchanged.


@_no_font
def test_inv_b_7_fixture_is_rotated(rotated_pdf: Path) -> None:
    """Precondition: the edit target's Tm linear part is rotated, not identity."""
    a, b, c, d = _first_tm_linear(str(rotated_pdf))
    axis_aligned = abs(a - 1.0) < 1e-3 and abs(b) < 1e-3 and abs(c) < 1e-3 and abs(d - 1.0) < 1e-3
    assert not axis_aligned, f"fixture must be non-axis-aligned; got Tm linear part {(a, b, c, d)}"


@_no_font
def test_inv_b_7_same_length_splice_preserves_rotation(rotated_pdf: Path, tmp_path: Path) -> None:
    """Rotation-safe splice: success, rotated Tm preserved, no refusal.

    Regression guard for the B.12 rotation gate's over-refusal boundary: the
    same-length splice path must keep working on rotated text. The output's
    governing ``Tm`` linear part must remain the rotated ``(0, -1, 1, 0)`` (NOT
    flattened to the identity), and no ``rotated_text_unsupported`` Degradation
    may be emitted.
    """
    before = _first_tm_linear(str(rotated_pdf))

    matches = find(str(rotated_pdf), _TARGET)
    assert matches, f"find({_TARGET!r}) must locate the edit target"
    out = tmp_path / "splice_out.pdf"
    result = replace(str(rotated_pdf), matches[0], _REPLACEMENT, str(out), reflow=False)

    assert result.success, f"same-length rotated splice should succeed: {result!r}"

    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert "rotated_text_unsupported" not in kinds, (
        "a rotation-SAFE same-length splice must NOT emit rotated_text_unsupported "
        f"(that refusal is reflow-only); got kinds={kinds}"
    )

    after = _first_tm_linear(str(out))
    a, b, c, d = after
    is_identity = abs(a - 1.0) < 1e-3 and abs(b) < 1e-3 and abs(c) < 1e-3 and abs(d - 1.0) < 1e-3
    assert not is_identity, (
        "rotated Tm was flattened to identity by the splice path — the rotation "
        f"must be preserved; got {after}"
    )
    assert after == pytest.approx(before, abs=1e-6), (
        "the splice path must leave the governing Tm linear part byte-identical; "
        f"got before={before}, after={after}"
    )
