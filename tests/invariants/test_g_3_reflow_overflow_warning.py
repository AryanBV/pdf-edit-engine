"""INV-G-3: reflow_paragraph shift_content_below_inplace surfaces a warning.

When reflow triggers a vertical shift to make room for a longer paragraph,
``EditResult.warnings`` must contain an entry mentioning the shift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import find, replace

if TYPE_CHECKING:
    from pathlib import Path


def test_g_3_reflow_overflow_warning(reportlab_simple: Path, tmp_path: Path) -> None:
    """Reflow shift surfaces a warning entry mentioning the shift."""
    matches = find(str(reportlab_simple), "simple test document")
    assert matches, "fixture missing 'simple test document'"
    out = tmp_path / "overflow.pdf"
    long_text = (
        "extraordinarily comprehensive and thoroughly detailed testing "
        "document with many words that will definitely exceed the original "
        "paragraph boundaries and require significantly more vertical space "
        "than was originally allocated in the source document layout"
    )
    result = replace(str(reportlab_simple), matches[0], long_text, str(out), reflow=True)
    assert result.success
    assert result.fidelity_report.reflow_applied
    assert result.fidelity_report.overflow_detected, "did not trigger overflow"
    # The shift either succeeded (no warning), got clamped (clamp warning),
    # or got suppressed (suppress warning). When the shift actually fired
    # via _shift_content_below_inplace, that helper itself returns warnings
    # propagated by reflow_paragraph. So at minimum, when overflow was
    # detected, warnings should be present somewhere in the path. We check
    # the broader spec: overflow_detected ⇒ at least one warning mentioning
    # shift / overflow / room / clamp.
    keywords = ("shift", "overflow", "room", "clamp", "page")
    if not result.warnings:
        # If the in-place shift hit no clamp/suppress AND _shift_content_below_inplace
        # itself returned no warnings, this is technically allowed per current
        # implementation. But the invariant in the audit charter requires at
        # least one shift-mentioning warning whenever the shift path fires.
        # Surface it as a probe failure with evidence.
        raise AssertionError(
            "overflow_detected=True but warnings is empty — "
            "shift path fired silently, violating INV-G-3"
        )
    assert any(any(k in w.lower() for k in keywords) for w in result.warnings), (
        f"warnings present but none mention shift/overflow/clamp: {result.warnings}"
    )
