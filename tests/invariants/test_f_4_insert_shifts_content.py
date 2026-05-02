"""INV-F-4 (P1): insert_text_block shifts existing below-content downward."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as rl_canvas

from pdf_edit_engine import get_text_layout, insert_text_block

if TYPE_CHECKING:
    from pathlib import Path


def _make_two_line_pdf(path: Path) -> None:
    page_w, page_h = letter
    c = rl_canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, page_h - 100, "ALPHA above the insertion")
    c.drawString(72, page_h - 200, "BETA below the insertion")
    c.save()


def test_inv_f_4_insert_text_block_shifts_below(tmp_path: Path) -> None:
    """insert_text_block at y=Y above existing content with y' < Y must
    shift those existing elements downward (smaller y in PDF coords)."""
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    _make_two_line_pdf(src)

    pre = get_text_layout(str(src))
    pre_beta = next((b for b in pre if "BETA" in b.text), None)
    if pre_beta is None:
        pytest.skip("BETA not located")

    # Insert between ALPHA and BETA at y=letter_h-150
    page_h = 792.0  # letter height
    insert_text_block(
        str(src),
        page_number=0,
        x=72,
        y=page_h - 150,
        text="INSERTED LINE",
        output_path=str(out),
    )

    post = get_text_layout(str(out))
    post_beta = next((b for b in post if "BETA" in b.text), None)
    assert post_beta is not None, "BETA disappeared after insert"

    # In PDF coords, "downward" is smaller y. Tolerance for layout shifts.
    assert post_beta.y < pre_beta.y - 1e-3, (
        f"BETA did not shift downward: pre.y={pre_beta.y}, post.y={post_beta.y}"
    )
