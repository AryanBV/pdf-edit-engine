"""INV-J-8: ``FidelityReport.font_preserved`` is a computed @property.

The property derives its value from ``degradations`` (none of kind in
``FONT_AFFECTING_KINDS``) AND ``font_substituted is None``. Never
hardcoded. This probe pins the truth function across the FONT_AFFECTING
membership combinations and asserts the field-shape invariant.
"""

from __future__ import annotations

import dataclasses

import pytest

from pdf_edit_engine.models import (
    FONT_AFFECTING_KINDS,
    Degradation,
    FidelityReport,
)

# Truth table per design doc §4b Shape 2:
#   font_preserved == (font_substituted is None) AND
#                     (no degradation kind in FONT_AFFECTING_KINDS)
CASES: list[tuple[str | None, list[tuple[str, str]], bool]] = [
    # (font_substituted, [(kind, severity), ...], expected_font_preserved)
    # Baseline: nothing substituted, no degradations → preserved.
    (None, [], True),
    # font_substituted populated → False regardless of degradations.
    ("Carlito-Regular", [], False),
    ("Carlito-Regular", [("kerning_compressed", "warning")], False),
    # Non-FONT-AFFECTING degradations alone → still preserved.
    (None, [("kerning_compressed", "warning")], True),
    (None, [("kerning_widened", "info")], True),
    (None, [("paragraph_detection_low_confidence", "info")], True),
    (None, [("overflow_shift_clamped", "warning")], True),
    (None, [("overflow_shift_suppressed", "warning")], True),
    (None, [("line_height_compressed", "info")], True),
    (None, [("reflow_aborted_to_simple", "warning")], True),
    (None, [("font_coverage_extended", "info")], True),
    (None, [("font_coverage_substituted", "warning")], True),
    # FONT-AFFECTING degradations → not preserved.
    (None, [("heading_font_dropped", "warning")], False),
    (None, [("marker_font_dropped", "warning")], False),
    (None, [("font_extension_failed", "error")], False),
    # Mixed: any FONT-AFFECTING wins regardless of non-affecting siblings.
    (
        None,
        [("kerning_compressed", "warning"), ("heading_font_dropped", "warning")],
        False,
    ),
]


@pytest.mark.parametrize("font_substituted,degs,expected", CASES)
def test_inv_j_8_font_preserved_truth_table(
    font_substituted: str | None,
    degs: list[tuple[str, str]],
    expected: bool,
) -> None:
    fr = FidelityReport(
        font_substituted=font_substituted,
        overflow_detected=False,
        reflow_applied=False,
        glyphs_missing=[],
        degradations=[Degradation(kind=k, severity=s) for k, s in degs],  # type: ignore[arg-type]
    )
    assert fr.font_preserved is expected, (
        f"INV-J-8 violated: font_substituted={font_substituted!r}, "
        f"degradations={degs!r}, expected font_preserved={expected}, "
        f"got {fr.font_preserved}"
    )


def test_inv_j_8_font_preserved_is_property_not_field() -> None:
    """Field-shape invariant: font_preserved must be a @property, not a stored field.

    A regression that re-introduced font_preserved as a stored dataclass
    field would let constructors override the truth function — exactly
    the v0.1.2 lying-success-path that v0.1.3 fixes.
    """
    field_names = {f.name for f in dataclasses.fields(FidelityReport)}
    assert "font_preserved" not in field_names, (
        "INV-J-8: font_preserved must be a computed @property, not a stored field"
    )
    # Sanity: the property exists on the class (not on instances).
    assert isinstance(FidelityReport.font_preserved, property), (
        "INV-J-8: FidelityReport.font_preserved must be a property descriptor"
    )


def test_inv_j_8_font_affecting_kinds_locked() -> None:
    """FONT_AFFECTING_KINDS is the locked frozen 3-element set per design doc §4a."""
    assert (
        frozenset(
            {
                "heading_font_dropped",
                "marker_font_dropped",
                "font_extension_failed",
            }
        )
        == FONT_AFFECTING_KINDS
    )
