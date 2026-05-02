"""INV-F-1: replace_block with empty replacement clears text in bbox."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import extract_bbox_text, replace_block

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_f_1_replace_block_empty_clears(reportlab_simple: Path, tmp_path: Path) -> None:
    """`replace_block(p, page_number, bbox, "", out)` produces output with the original
    text in that bbox absent."""
    # Title "Test Document" lives at x=72, y=745.5, width=140, height=18.
    # Use a slightly padded bbox to capture the whole line.
    bbox = (72.0, 740.0, 220.0, 770.0)
    src = str(reportlab_simple)
    out = str(tmp_path / "f1_empty.pdf")

    # Sanity check: original bbox contains "Test Document".
    before = extract_bbox_text(src, page=0, bbox=bbox)
    assert "Test" in before and "Document" in before, f"setup failed: {before!r}"

    result = replace_block(src, 0, bbox, "", out)
    assert result.success, f"replace_block failed: {result.warnings}"

    after = extract_bbox_text(out, page=0, bbox=bbox)
    # Must not contain the original tokens.
    assert "Test" not in after and "Document" not in after, f"original text not cleared: {after!r}"
    # And the resulting text must be empty or whitespace-only.
    assert after.strip() == "", f"bbox not empty/whitespace: {after!r}"
