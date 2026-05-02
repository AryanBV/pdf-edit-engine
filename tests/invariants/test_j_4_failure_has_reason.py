"""INV-J-4: success==False implies font_action=='failed' OR warnings is non-empty."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import find, replace

if TYPE_CHECKING:
    from pathlib import Path


def test_j_4_failure_has_reason(reportlab_simple: Path, tmp_path: Path) -> None:
    """success==False ⇒ (font_action=='failed' OR len(warnings) > 0)."""
    matches = find(str(reportlab_simple), "Test")
    assert matches, "fixture missing 'Test'"
    out = tmp_path / "fail.pdf"
    # Inject characters that the WinAnsi font in reportlab_simple cannot render
    # AND that no system font is likely to back-fill: combine emoji + a CJK
    # ideograph. WinAnsi has a bounded codepoint range, so this should fail
    # font extension.
    bad_text = "Test \U0001f389漢"  # 🎉 + 漢
    result = replace(str(reportlab_simple), matches[0], bad_text, str(out), reflow=False)
    if result.success:
        import pytest

        pytest.skip(
            "Could not trigger failure on this fixture (engine accepted edit); "
            "invariant not falsifiable here"
        )
    assert (result.font_action == "failed") or (len(result.warnings) > 0), (
        f"failure with no reason: success={result.success}, "
        f"font_action={result.font_action!r}, warnings={result.warnings!r}"
    )
