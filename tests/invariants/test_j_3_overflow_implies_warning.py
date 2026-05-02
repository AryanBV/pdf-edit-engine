"""INV-J-3: overflow_detected ⇒ ≥1 warning mentioning 'overflow' (case-insensitive)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import find, replace

if TYPE_CHECKING:
    from pathlib import Path


def test_j_3_overflow_implies_warning(reportlab_simple: Path, tmp_path: Path) -> None:
    """overflow_detected ⇒ warnings non-empty AND ≥1 mentions 'overflow' (case-insensitive)."""
    # Path A: simple-replace overflow (page-width based, surgeon._apply_single_replacement).
    # Use reflow=False so we exercise surgeon's page-width overflow detector,
    # which is one of the two places overflow_detected can be set to True.
    matches = find(str(reportlab_simple), "Test")
    assert matches, "fixture missing 'Test'"
    out = tmp_path / "overflow.pdf"
    long_text = "X" * 200
    result = replace(str(reportlab_simple), matches[0], long_text, str(out), reflow=False)
    if not result.fidelity_report.overflow_detected:
        # If we couldn't trigger overflow on this fixture, fall back to
        # the reflow-overflow path which we know works.
        matches = find(str(reportlab_simple), "simple test document")
        assert matches
        out2 = tmp_path / "overflow2.pdf"
        long2 = (
            "extraordinarily comprehensive and thoroughly detailed testing "
            "document with many words that will definitely exceed the original "
            "paragraph boundaries and require significantly more vertical space "
            "than was originally allocated in the source document layout"
        )
        result = replace(str(reportlab_simple), matches[0], long2, str(out2), reflow=True)
    assert result.fidelity_report.overflow_detected, (
        "could not trigger overflow_detected on either path"
    )
    # Now the invariant: overflow_detected ⇒ warnings non-empty AND mentions overflow.
    assert result.warnings, "overflow_detected=True but warnings is empty — INV-J-3 violated"
    assert any("overflow" in w.lower() for w in result.warnings), (
        f"overflow_detected=True but no warning mentions 'overflow': {result.warnings}"
    )
