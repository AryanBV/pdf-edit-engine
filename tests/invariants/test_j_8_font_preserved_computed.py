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
    # E.8 (v0.2.0): shrink-to-fit font-size reduction is NON font-affecting —
    # a size change does not alter glyph identity, so preserved stays True.
    (None, [("font_size_reduced", "info")], True),
    # A2.2 / INV-W-3 (v0.2.0): dropped Fast-Web-View linearization is NON
    # font-affecting — a file-layout change does not alter glyph identity, so
    # preserved stays True.
    (None, [("linearization_dropped", "info")], True),
    # E.4 (v0.2.0): widow/orphan line-break-quality signal is NON
    # font-affecting — glyph identity is untouched, so preserved stays True.
    (None, [("line_break_quality_degraded", "info")], True),
    (None, [("reflow_aborted_to_simple", "warning")], True),
    (None, [("font_coverage_extended", "info")], True),
    (None, [("font_coverage_substituted", "warning")], True),
    # POS-GATE (v0.2.0): rotated-edit positioning skip is NON font-affecting
    # — glyph identity is untouched, so font_preserved stays True.
    (None, [("positioning_adjustment_skipped", "warning")], True),
    # B.12 (v0.2.0): rotated-reflow refusal is NON font-affecting — glyph
    # identity is untouched (the layout op was declined), so preserved stays True.
    (None, [("rotated_text_unsupported", "warning")], True),
    # Block F CORE (v0.2.0): color-space approximation is NON font-affecting —
    # only the fill color-space is reinterpreted; glyph identity is untouched,
    # so font_preserved stays True.
    (None, [("color_space_approximated", "warning")], True),
    # E.2 (v0.2.0): indent flattening is NON font-affecting — only a layout
    # signal was flattened to flush; glyph identity is untouched, so
    # font_preserved stays True.
    (None, [("indent_flattened", "info")], True),
    # C.2 (v0.2.0): glyph-count introspection failure is NON font-affecting —
    # only the read-path glyph count is unknown; glyph identity is untouched,
    # so font_preserved stays True.
    (None, [("font_subset_introspection_failed", "warning")], True),
    # A1.3 / INV-W-4 (v0.2.0): a stream-too-large signal is NON font-affecting
    # ON ITS OWN — the edit was refused before any glyph surgery, and on the
    # edit path the companion font_extension_failed (a font-affecting kind)
    # drives preserved=False; in isolation glyph identity is untouched, so
    # preserved stays True.
    (None, [("font_stream_too_large", "warning")], True),
    # B.9 / INV-B-9 (v0.2.0): a ligature re-route selects a different glyph
    # WITHIN THE SAME embedded font (no font swap), so it is NON font-affecting
    # — glyph identity changes shape but the typeface is preserved, so
    # preserved stays True.
    (None, [("ligature_substituted", "info")], True),
    # B.11 (v0.2.0): deletion residue is NON font-affecting — residue, not a
    # font swap; glyph identity untouched, so preserved stays True.
    (None, [("deletion_residual_text", "warning")], True),
    # B.11 (v0.2.0): inline-image presence is an advisory NON font-affecting
    # signal — glyph identity untouched, so preserved stays True.
    (None, [("inline_image_present", "info")], True),
    # E.7 (v0.2.0): scriptless (no-UAX#14-opportunity) reflow signal is NON
    # font-affecting — only line-break segmentation was unavailable; glyph
    # identity untouched, so preserved stays True.
    (None, [("scriptless_reflow_unsupported", "info")], True),
    # A2.3 / INV-W-5 (v0.2.0): a dropped encryption is NON font-affecting — an
    # encryption change does not alter glyph identity, so preserved stays True.
    (None, [("encryption_dropped", "warning")], True),
    # INV-B-12 (v0.2.0): a multi-match-same-operator refusal applied NO edit to
    # the refused matches — glyph identity untouched, so preserved stays True.
    (None, [("multi_match_same_operator_unsupported", "warning")], True),
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
    """FONT_AFFECTING_KINDS is the locked frozen set.

    v0.1.3 (design doc §4a): three kinds — heading/marker font dropped and
    font_extension_failed. v0.2.0 (B.3, M0 Rank-2.5): extended with the two
    ToUnicode-absent-recovery WRITE-path refusals. Both are hard failures
    (``font_action="failed"``) that could not preserve — or even address —
    the font's text, so they satisfy the INV-J-9 construction guard and make
    ``font_preserved`` compute False. The set stays explicitly enumerated and
    guarded; any further addition must update this lock with a rationale.
    """
    assert (
        frozenset(
            {
                "heading_font_dropped",
                "marker_font_dropped",
                "font_extension_failed",
                "tounicode_recovered",
                "untextable_cidfont",
            }
        )
        == FONT_AFFECTING_KINDS
    )
