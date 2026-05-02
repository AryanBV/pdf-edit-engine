"""INV-D-1: find(pdf, "") returns []."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import find

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_d_1_find_empty(reportlab_simple: Path) -> None:
    """find(pdf, "") returns []."""
    assert reportlab_simple.exists(), f"missing fixture {reportlab_simple}"
    matches = find(str(reportlab_simple), "")
    assert matches == [], f"expected [] for empty query, got {len(matches)} matches"
