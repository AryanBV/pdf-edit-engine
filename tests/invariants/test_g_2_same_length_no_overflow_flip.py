"""INV-G-2 (P1): same-length reflow_paragraph must not flip overflow_detected from False to True."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine import find, replace_all

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_g_2_same_length_does_not_flip_overflow(
    gdocs_document: Path,
    reportlab_simple: Path,
    tmp_path: Path,
) -> None:
    """A same-length replacement on a paragraph that was not previously
    overflowing must not flag overflow_detected=True."""
    src = reportlab_simple if reportlab_simple.exists() else gdocs_document
    if not src.exists():
        pytest.skip("no source PDF")

    matches = find(str(src), "and")
    if not matches:
        pytest.skip("'and' not in source PDF")

    out = tmp_path / "out.pdf"
    # 'and' → 'but' is length-preserving and stays within ASCII glyphs
    results = replace_all(str(src), "and", "but", str(out))
    assert results, "expected matches"

    flagged = [r for r in results if r.fidelity_report.overflow_detected]
    assert not flagged, (
        f"{len(flagged)}/{len(results)} same-length replacements falsely "
        f"flagged overflow_detected=True"
    )
