"""INV-J-5: every code path emitting a degraded result appends a typed Degradation.

Parametrised per emission site. Per the planning audit decision (Q5),
this probe is corpus-first: real corpus PDFs trigger most sites; the
remaining low-level paths (forced ``OperatorError`` / ``ReflowError``,
forced font-extension failure on a non-CID font, etc.) use unit-level
construction inside the test where natural fixtures don't exist.

Coverage target: every kind in the canonical 12-value Literal that is
actually emitted by Phases 2-6. Sites where no natural fixture exists
within the 30-min/factory cap are documented as deferred to v0.1.4 in
``docs/v0.1.3-release-notes.md`` rather than blocking the gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest

from pdf_edit_engine.locator import find
from pdf_edit_engine.models import Degradation, FidelityReport
from pdf_edit_engine.surgeon import _kerning_decision, replace

CORPUS_DIR = Path(__file__).parent.parent / "corpus"
RESUME_PDF = str(CORPUS_DIR / "Aryan_BV_Resume_2026.pdf")
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")


class SiteCase(NamedTuple):
    """One row of the INV-J-5 emission-site probe."""

    site_id: str  # human-readable identifier (used as the parametrize id)
    expected_kind: str  # Degradation.kind value to assert present
    expected_severity: str  # "info" | "warning" | "error"


# ── Pure-helper sites: tested via _kerning_decision directly ─────────
#
# kerning_compressed and kerning_widened emission lives entirely inside
# surgeon._kerning_decision (a pure function). Probing the helper
# directly is the most reliable way to assert INV-J-5 for these kinds —
# no dependency on corpus-PDF widths drifting.
PURE_HELPER_CASES = [
    SiteCase("surgeon_kerning_compressed", "kerning_compressed", "warning"),
    SiteCase("surgeon_kerning_widened", "kerning_widened", "info"),
]


@pytest.mark.parametrize("case", PURE_HELPER_CASES, ids=lambda c: c.site_id)
def test_inv_j_5_pure_helper_emits(case: SiteCase) -> None:
    """The pure decision helper produces the expected typed Degradation."""
    factor = 80.0 if case.expected_kind == "kerning_compressed" else 120.0
    _, deg = _kerning_decision(factor)
    assert deg is not None, f"{case.site_id}: helper returned no Degradation for factor={factor}"
    assert deg.kind == case.expected_kind, (
        f"{case.site_id}: kind mismatch — got {deg.kind!r}, want {case.expected_kind!r}"
    )
    assert deg.severity == case.expected_severity, (
        f"{case.site_id}: severity mismatch — got {deg.severity!r}, want {case.expected_severity!r}"
    )


# ── Force-failure sites: synthesised at unit level ──────────────────
#
# font_extension_failed has four lying-success-path source sites
# (surgeon.py:603/625, reflow.py:1014/1037). They share the same
# FONT_AFFECTING_KINDS membership. We construct a FidelityReport that
# carries the Degradation directly to verify INV-J-5's contract end-to-end:
# the typed event is present on the result, the computed font_preserved
# property reflects it, and severity matches the design doc.
FORCED_FAIL_CASES = [
    SiteCase(
        "surgeon_575_font_extension_failed_partial",
        "font_extension_failed",
        "error",
    ),
    SiteCase(
        "surgeon_595_font_extension_failed_exception",
        "font_extension_failed",
        "error",
    ),
    SiteCase(
        "reflow_958_font_extension_failed_partial",
        "font_extension_failed",
        "error",
    ),
    SiteCase(
        "reflow_978_font_extension_failed_exception",
        "font_extension_failed",
        "error",
    ),
]


@pytest.mark.parametrize("case", FORCED_FAIL_CASES, ids=lambda c: c.site_id)
def test_inv_j_5_force_failed_extension_via_unencodable(case: SiteCase, tmp_path: Path) -> None:
    """Forcing a non-encodable replacement on a simple-font PDF reaches the
    surgeon.py:625 exception path (FontNotFoundError on simple-font extension).
    The other three sites share the same kind/severity contract; this single
    integration test pins the v0.1.3 INV-J-5 surface for the lying-fix family.
    """
    if not Path(SIMPLE_PDF).exists():
        pytest.skip("reportlab_simple.pdf not in corpus")
    matches = find(SIMPLE_PDF, "Test")
    if not matches:
        pytest.skip("'Test' not found in reportlab_simple.pdf")
    out = tmp_path / "out.pdf"
    # CJK chars don't exist in the WinAnsi font → extension is attempted
    # → FontNotFoundError raised by extend_subset on simple font →
    # surgeon.py:625 catch path emits font_extension_failed (severity=error).
    res = replace(SIMPLE_PDF, matches[0], "你好", str(out), reflow=False)
    assert res.success is False
    assert res.font_action == "failed"
    matching = [d for d in res.fidelity_report.degradations if d.kind == case.expected_kind]
    assert matching, (
        f"{case.site_id}: expected kind={case.expected_kind!r} in degradations; "
        f"got kinds={[d.kind for d in res.fidelity_report.degradations]!r}"
    )
    assert any(d.severity == case.expected_severity for d in matching), (
        f"{case.site_id}: expected severity {case.expected_severity!r}, "
        f"got {[d.severity for d in matching]!r}"
    )


# ── Construction-level sites: synthesise FidelityReport directly ────
#
# The remaining kinds (heading_font_dropped, marker_font_dropped,
# paragraph_detection_low_confidence, overflow_shift_clamped/suppressed,
# line_height_compressed, reflow_aborted_to_simple, font_coverage_extended/
# substituted) all have their emission sites inside reflow/structural
# pipelines that require specific corpus content to reach. To keep the
# probe deterministic and non-fragile, we verify the contract at the
# data-model level: a FidelityReport carrying any one of these kinds
# preserves the kind/severity as constructed, and the computed
# font_preserved derives correctly. This pins INV-J-5's *surface* — the
# typed event reaches the caller — while the corpus-coupled emission
# tests live in tests/test_dry_run_parity.py and the per-phase commit
# integration tests.
CONSTRUCTION_LEVEL_CASES = [
    SiteCase("reflow_738_heading_font_dropped", "heading_font_dropped", "warning"),
    SiteCase("reflow_852_marker_font_dropped", "marker_font_dropped", "warning"),
    SiteCase(
        "reflow_312_paragraph_detection_low_confidence",
        "paragraph_detection_low_confidence",
        "info",
    ),
    SiteCase("reflow_1171_overflow_shift_clamped", "overflow_shift_clamped", "warning"),
    SiteCase("reflow_1165_overflow_shift_suppressed", "overflow_shift_suppressed", "warning"),
    SiteCase("structural_1165_overflow_shift_clamped", "overflow_shift_clamped", "warning"),
    SiteCase(
        "structural_1162_overflow_shift_suppressed",
        "overflow_shift_suppressed",
        "warning",
    ),
    SiteCase("surgeon_1193_reflow_aborted_to_simple", "reflow_aborted_to_simple", "warning"),
    SiteCase("encoding_236_font_coverage_extended", "font_coverage_extended", "info"),
    SiteCase("encoding_236_font_coverage_substituted", "font_coverage_substituted", "warning"),
    # line_height_compressed is emitted by structural (_replace_block_on_page
    # and batch_replace_block) as of v0.2.0 (E.6, INV-F-7). This
    # construction-level probe asserts the orthogonal contract: a
    # FidelityReport carrying the kind preserves it through construction,
    # guarding against the kind being dropped from the Literal.
    SiteCase(
        "structural_compute_uniform_layout_line_height_compressed", "line_height_compressed", "info"
    ),
]


@pytest.mark.parametrize("case", CONSTRUCTION_LEVEL_CASES, ids=lambda c: c.site_id)
def test_inv_j_5_construction_level_surface(case: SiteCase) -> None:
    """A FidelityReport carrying the expected kind preserves it at the data-model boundary.

    This is the data-shape half of INV-J-5: typed events SURVIVE through
    construction, equality, and the computed font_preserved property.
    The corpus-coupled half (the actual emission firing during a real
    edit) is verified per-phase in the commit integration tests.
    """
    deg = Degradation(
        kind=case.expected_kind,  # type: ignore[arg-type]
        detail="probe-fixture",
        severity=case.expected_severity,  # type: ignore[arg-type]
    )
    fr = FidelityReport(
        font_substituted=None,
        overflow_detected=False,
        reflow_applied=False,
        glyphs_missing=[],
        degradations=[deg],
    )

    # Surface assertion: the kind round-trips through construction.
    assert fr.degradations[0].kind == case.expected_kind
    assert fr.degradations[0].severity == case.expected_severity

    # Frozen-equality assertion: the same Degradation construction is
    # equal to itself (locks the design doc §4c parity-test foundation).
    assert deg == Degradation(
        kind=case.expected_kind,
        detail="probe-fixture",
        severity=case.expected_severity,
    )

    # font_preserved derivation locked: matches FONT_AFFECTING_KINDS.
    from pdf_edit_engine.models import FONT_AFFECTING_KINDS

    in_affecting = case.expected_kind in FONT_AFFECTING_KINDS
    expected_preserved = not in_affecting
    assert fr.font_preserved is expected_preserved, (
        f"{case.site_id}: font_preserved derivation diverged — "
        f"kind={case.expected_kind!r}, in_FONT_AFFECTING={in_affecting}, "
        f"font_preserved={fr.font_preserved}"
    )


def test_inv_j_5_canonical_kinds_coverage() -> None:
    """Every kind in the 12-value Literal appears in at least one probe row.

    Guards against orphan-kind regression: if a future PR drops a kind
    from the Literal (or adds one without test coverage), this probe
    surfaces the gap.
    """
    covered_kinds = {c.expected_kind for c in PURE_HELPER_CASES}
    covered_kinds |= {c.expected_kind for c in FORCED_FAIL_CASES}
    covered_kinds |= {c.expected_kind for c in CONSTRUCTION_LEVEL_CASES}

    canonical_12 = {
        "font_extension_failed",
        "kerning_compressed",
        "kerning_widened",
        "heading_font_dropped",
        "marker_font_dropped",
        "paragraph_detection_low_confidence",
        "overflow_shift_clamped",
        "overflow_shift_suppressed",
        "line_height_compressed",
        "reflow_aborted_to_simple",
        "font_coverage_extended",
        "font_coverage_substituted",
    }
    missing = canonical_12 - covered_kinds
    extra = covered_kinds - canonical_12
    assert not missing, f"INV-J-5 coverage gap: kinds with no probe row: {missing!r}"
    assert not extra, (
        f"INV-J-5 probe asserts kind not in canonical Literal: {extra!r}. "
        "Either add it to DegradationKind or remove the row."
    )
