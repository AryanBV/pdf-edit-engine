"""INV-D-4: get_text_layout TextBlock.page is in [0, num_pages)."""

from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_edit_engine import get_text_layout

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def test_inv_d_4_text_block_page_index() -> None:
    """For every TextBlock from get_text_layout(pdf), block.page is an integer in [0, num_pages)."""
    path = CORPUS_DIR / "reportlab_multipage.pdf"
    assert path.exists(), f"missing fixture {path}"

    with pikepdf.open(str(path)) as pdf:
        n_pages = len(pdf.pages)
    assert n_pages > 1, "fixture is not multi-page"

    blocks = get_text_layout(str(path))
    assert blocks, "no text blocks returned"

    for b in blocks:
        assert isinstance(b.page, int), f"block.page is not int: {type(b.page)}"
        assert 0 <= b.page < n_pages, (
            f"block.page={b.page} out of range [0, {n_pages}) for text={b.text!r}"
        )

    # also verify both pages observed
    pages_seen = {b.page for b in blocks}
    assert pages_seen == set(range(n_pages)), (
        f"expected pages {set(range(n_pages))}, observed {pages_seen}"
    )
