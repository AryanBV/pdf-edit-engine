"""INV-J-1: every EditResult carries a ``fidelity_report: FidelityReport``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import (
    Edit,
    EditResult,
    FidelityReport,
    batch_replace,
    replace_all,
)
from pdf_edit_engine.structural import replace_block

if TYPE_CHECKING:
    from pathlib import Path


def test_j_1_edit_result_has_fidelity(reportlab_simple: Path, tmp_path: Path) -> None:
    """Every EditResult from every public edit function has ``fidelity_report: FidelityReport``."""
    out1 = tmp_path / "ra.pdf"
    out2 = tmp_path / "br.pdf"
    out3 = tmp_path / "rb.pdf"

    ra_results = replace_all(str(reportlab_simple), "and", "but", str(out1))
    br_results = batch_replace(
        str(reportlab_simple),
        [Edit(find="and", replace="but"), Edit(find="Test", replace="Demo")],
        str(out2),
    )
    rb_result = replace_block(
        str(reportlab_simple),
        0,
        (50.0, 700.0, 300.0, 730.0),
        "Block text",
        str(out3),
    )

    all_results: list[EditResult] = [*ra_results, *br_results, rb_result]
    assert all_results, "no EditResults produced — invariant cannot be evaluated"
    for r in all_results:
        assert isinstance(r, EditResult)
        assert isinstance(r.fidelity_report, FidelityReport), (
            f"EditResult.fidelity_report is {type(r.fidelity_report)}, not FidelityReport"
        )
