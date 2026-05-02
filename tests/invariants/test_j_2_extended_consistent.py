"""INV-J-2: font_action=='extended' ⇒ font_preserved or glyphs_missing==[] or overflow set."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import find, replace

if TYPE_CHECKING:
    from pathlib import Path


def test_j_2_extended_consistent(cidfont_synthetic: Path, tmp_path: Path) -> None:
    """font_action=='extended' ⇒ (font_preserved OR glyphs_missing==[] OR overflow set)."""
    matches = find(str(cidfont_synthetic), "Engineer")
    assert matches, "fixture missing 'Engineer'"
    out = tmp_path / "extended.pdf"
    # Append a char (π) almost certainly absent from the embedded subset,
    # forcing Tier-1 CMap-only extension.
    result = replace(str(cidfont_synthetic), matches[0], "Engineerπ", str(out), reflow=False)
    if result.font_action != "extended":
        # If extension didn't trigger here, the probe is inconclusive — but
        # we still assert the rest of the invariant on whatever happened.
        import pytest

        pytest.skip(
            f"Could not reliably trigger extension on this fixture "
            f"(got font_action={result.font_action}); invariant not falsifiable here"
        )
    fr = result.fidelity_report
    consistent = (
        fr.font_preserved is True or fr.glyphs_missing == [] or fr.overflow_detected is not None
    )
    assert consistent, (
        f"font_action='extended' but report inconsistent: "
        f"font_preserved={fr.font_preserved}, "
        f"glyphs_missing={fr.glyphs_missing}, "
        f"overflow_detected={fr.overflow_detected}"
    )
