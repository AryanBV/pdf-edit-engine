"""INV-H-5: flatten_annotations on a PDF without annotations succeeds cleanly."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pdf_edit_engine import flatten_annotations, get_text

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_h_5_flatten_no_annotations(reportlab_simple: Path, tmp_path: Path) -> None:
    """`flatten_annotations(p_no_annots, out)`: succeeds and produces a clean output
    (no errors). Use reportlab_simple."""
    src = str(reportlab_simple)
    out = str(tmp_path / "h5_flattened.pdf")

    result_path = flatten_annotations(src, out)
    assert os.path.exists(out), f"flatten_annotations did not write output: {out}"
    assert os.path.getsize(out) > 0, "flatten_annotations wrote an empty file"
    assert result_path == out, f"unexpected return: {result_path!r}"

    # Sanity: text must be preserved.
    assert get_text(src) == get_text(out), "flatten_annotations corrupted text"
