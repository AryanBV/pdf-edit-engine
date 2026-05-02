"""INV-I-1: annotation count drops by exactly 1 after delete_annotation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import add_annotation, delete_annotation, get_annotations

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_i_1_annotation_count_after_delete(reportlab_simple: Path, tmp_path: Path) -> None:
    """On a PDF with at least one annotation, `get_annotations` →
    `delete_annotation(at index 0)` → `get_annotations`: count drops by exactly 1.
    To get an annotation, call `add_annotation` first on `reportlab_simple` if needed
    (an annotation roundtrip)."""
    src = str(reportlab_simple)
    with_annot = str(tmp_path / "i1_with_annot.pdf")
    after_delete = str(tmp_path / "i1_after_delete.pdf")

    # Seed at least one annotation.
    add_annotation(
        src,
        page=0,
        rect=(72.0, 700.0, 200.0, 720.0),
        uri="https://example.com/seed",
        output_path=with_annot,
    )

    before = get_annotations(with_annot)
    assert len(before) >= 1, f"setup failed: no annotations after add_annotation: {before}"

    delete_annotation(with_annot, before[0], after_delete)
    after = get_annotations(after_delete)

    assert len(after) == len(before) - 1, (
        f"count did not drop by exactly 1: before={len(before)}, after={len(after)}"
    )
