"""INV-F-3: batch_replace_block with no replacements is a no-op."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pdf_edit_engine import batch_replace_block

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_f_3_batch_replace_block_empty(reportlab_simple: Path, tmp_path: Path) -> None:
    """`batch_replace_block(p, [], out)` is a no-op: returns [] AND does not write
    output file. (If signature requires, weaken to "returns []".)"""
    src = str(reportlab_simple)
    out = str(tmp_path / "f3_empty.pdf")
    # Signature: batch_replace_block(pdf_path, page_number, replacements, output_path)
    result = batch_replace_block(src, 0, [], out)
    assert result == [], f"expected [], got {result!r}"
    # Strong form: the output file should NOT be created for an empty batch.
    assert not os.path.exists(out), f"empty batch wrote output anyway: {out}"
