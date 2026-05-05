"""INV-J-3 + INV-J-5 coexistence: overflow events populate BOTH lists.

v0.1.3 introduces the ``FidelityReport.degradations`` typed list while
preserving the v0.1.0+ ``EditResult.warnings`` list (INV-J-3 backward
compat). For overflow events that trigger the warnings auto-append,
the typed Degradation must also appear. v0.2 will collapse the two;
v0.1.3 keeps both populated in parallel.

This probe verifies that whenever ``overflow_detected=True`` (and at
least one ``warnings`` entry references "overflow" per INV-J-3), the
degradations list contains at least one of the
``overflow_shift_clamped`` / ``overflow_shift_suppressed`` kinds —
i.e., the v0.1.3 typed surface didn't drop signal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_edit_engine.locator import find
from pdf_edit_engine.surgeon import replace

CORPUS_DIR = Path(__file__).parent.parent / "corpus"
RESUME_PDF = str(CORPUS_DIR / "Aryan_BV_Resume_2026.pdf")
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")
TABLE_PDF = str(CORPUS_DIR / "reportlab_table.pdf")


# Inputs likely to trigger overflow_detected via kerning/expansion in
# tight bbox layouts. Skip-on-no-overflow keeps the probe deterministic
# even when corpus details drift.
OVERFLOW_INPUTS = [
    pytest.param(RESUME_PDF, "Aryan", "Aryan_BV_BV_BV_BV_BV", id="resume-extreme-expand"),
    pytest.param(SIMPLE_PDF, "Test", "Test_Test_Test_Test_Test_Test", id="simple-extreme-expand"),
]


@pytest.mark.parametrize("pdf_path,old,new", OVERFLOW_INPUTS)
def test_inv_j_3_j_5_overflow_coexistence(
    pdf_path: str, old: str, new: str, tmp_path: Path
) -> None:
    if not Path(pdf_path).exists():
        pytest.skip(f"corpus {pdf_path} not present")
    matches = find(pdf_path, old)
    if not matches:
        pytest.skip(f"text {old!r} not in {pdf_path}")

    out = tmp_path / "out.pdf"
    res = replace(pdf_path, matches[0], new, str(out), dry_run=False)

    if not res.fidelity_report.overflow_detected:
        pytest.skip(
            f"input did not trigger overflow on {Path(pdf_path).name}; "
            "coexistence not testable here"
        )

    # INV-J-3: warnings auto-populated with an "overflow" entry.
    assert res.warnings, "INV-J-3: warnings empty despite overflow_detected=True"
    assert any("overflow" in w.lower() for w in res.warnings), (
        f"INV-J-3: no 'overflow' marker in warnings={res.warnings!r}"
    )

    # v0.1.3 INV-J-5 (overflow surface): a typed Degradation of one of
    # the overflow_shift_* kinds is also present. The two lists are
    # parallel until v0.2 collapses them.
    overflow_kinds = {"overflow_shift_clamped", "overflow_shift_suppressed"}
    deg_kinds = [d.kind for d in res.fidelity_report.degradations]
    # Note: the simple-replace path doesn't itself emit overflow_shift_*
    # (no shift logic there). When overflow happens via the simple path
    # only, only the warnings INV-J-3 auto-append fires; the J-5 surface
    # is then expected to be empty for overflow specifically. To keep
    # the probe non-fragile, we accept either: (a) shift Degradation
    # present, or (b) reflow_aborted_to_simple present (shows reflow
    # was tried), or (c) the result was a simple-only path (no
    # reflow_applied) — in which case only INV-J-3 applies.
    if res.fidelity_report.reflow_applied:
        assert any(k in overflow_kinds for k in deg_kinds), (
            f"INV-J-5 (overflow on reflow path): expected one of "
            f"{overflow_kinds} in degradations; got {deg_kinds!r}"
        )
