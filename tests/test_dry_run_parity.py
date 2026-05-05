"""dry_run=True must produce identical degradations to dry_run=False (design doc §4c).

This is the v0.1.3 dry_run ↔ degradations parity contract: the degradation
list returned by ``dry_run=True`` must equal the list returned by
``dry_run=False`` against the same input. The contract protects callers
who use dry_run as a planning step (degradations are observable signal
about what the real edit will do).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_edit_engine.locator import find
from pdf_edit_engine.surgeon import replace

CORPUS_DIR = Path(__file__).parent / "corpus"
RESUME_PDF = str(CORPUS_DIR / "Aryan_BV_Resume_2026.pdf")
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")
TABLE_PDF = str(CORPUS_DIR / "reportlab_table.pdf")


# Cases chosen to cover the Phase 2/3/4/5/6 emission paths:
#   - same-length replace (no kerning Degradation expected)
#   - different-length replace (kerning_compressed/widened may fire)
#   - extension-triggering replace (font_coverage_extended may fire)
CASES = [
    pytest.param(RESUME_PDF, "Aryan", "Bryan", id="same-length-resume"),
    pytest.param(RESUME_PDF, "Aryan", "Br", id="shorter-resume"),
    pytest.param(SIMPLE_PDF, "Test", "Best", id="same-length-simple"),
    pytest.param(SIMPLE_PDF, "Test", "Bestest", id="longer-simple"),
]


@pytest.mark.parametrize("pdf_path,old,new", CASES)
def test_dry_run_degradations_match_real(pdf_path: str, old: str, new: str, tmp_path: Path) -> None:
    if not Path(pdf_path).exists():
        pytest.skip(f"corpus {pdf_path} not present")

    matches_dry = find(pdf_path, old)
    if not matches_dry:
        pytest.skip(f"text {old!r} not found in {pdf_path}")
    matches_real = find(pdf_path, old)

    out_dry = tmp_path / "dry.pdf"
    out_real = tmp_path / "real.pdf"

    res_dry = replace(pdf_path, matches_dry[0], new, str(out_dry), dry_run=True)
    res_real = replace(pdf_path, matches_real[0], new, str(out_real), dry_run=False)

    # Frozen Degradation dataclass → structural equality.
    assert res_dry.fidelity_report.degradations == res_real.fidelity_report.degradations, (
        f"design doc §4c parity violated for {Path(pdf_path).name} "
        f"({old!r} → {new!r}):\n"
        f"  dry:  {res_dry.fidelity_report.degradations}\n"
        f"  real: {res_real.fidelity_report.degradations}"
    )
