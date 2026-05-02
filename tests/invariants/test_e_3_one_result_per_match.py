"""INV-E-3: replace_all returns one EditResult per find() match."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import find, replace_all

if TYPE_CHECKING:
    from pathlib import Path


def test_e_3_one_result_per_match(reportlab_simple: Path, tmp_path: Path) -> None:
    """`len(replace_all(...)) == len(find(...))` for the same search term."""
    needle = "and"  # appears 3x in reportlab_simple
    matches = find(str(reportlab_simple), needle)
    assert len(matches) >= 2, "fixture changed: need >=2 matches to be meaningful"
    out = tmp_path / "out.pdf"
    results = replace_all(str(reportlab_simple), needle, "but", str(out))
    assert len(results) == len(matches), (
        f"expected {len(matches)} results (one per match), got {len(results)}"
    )
