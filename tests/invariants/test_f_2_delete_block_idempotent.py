"""INV-F-2: delete_block applied twice is idempotent for bbox text."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import delete_block, extract_bbox_text

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_f_2_delete_block_idempotent(reportlab_simple: Path, tmp_path: Path) -> None:
    """`delete_block(p, page, bbox, out)` then `delete_block(out, page, bbox, out2)` —
    both calls succeed; second is idempotent (extract_bbox_text(out2, page, bbox) matches
    extract_bbox_text(out, page, bbox))."""
    bbox = (72.0, 740.0, 220.0, 770.0)  # Test Document title bbox
    src = str(reportlab_simple)
    out1 = str(tmp_path / "f2_step1.pdf")
    out2 = str(tmp_path / "f2_step2.pdf")

    r1 = delete_block(src, 0, bbox, out1)
    assert r1.success, f"first delete_block failed: {r1.warnings}"

    r2 = delete_block(out1, 0, bbox, out2)
    assert r2.success, f"second delete_block failed: {r2.warnings}"

    after1 = extract_bbox_text(out1, page=0, bbox=bbox)
    after2 = extract_bbox_text(out2, page=0, bbox=bbox)
    assert after1 == after2, f"second delete_block not idempotent: {after1!r} vs {after2!r}"
