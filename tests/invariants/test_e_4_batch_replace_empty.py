"""INV-E-4: batch_replace(p, [], out) returns [] and does not create the output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import batch_replace

if TYPE_CHECKING:
    from pathlib import Path


def test_e_4_batch_replace_empty(reportlab_simple: Path, tmp_path: Path) -> None:
    """`batch_replace(p, [], out)` returns `[]` and does not create the output file."""
    out = tmp_path / "out.pdf"
    results = batch_replace(str(reportlab_simple), [], str(out))
    assert results == [], f"empty edits should return [], got {results!r}"
    # Proxy for "did not open and rewrite the PDF": no output produced.
    # If implementation always saves, this is the violation we want to catch.
    assert not out.exists(), (
        "batch_replace with empty edit list created an output file "
        "(implementation eagerly opens/saves regardless of edits)"
    )
