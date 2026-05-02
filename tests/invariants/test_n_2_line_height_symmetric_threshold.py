"""INV-N-2: locator uses min(prev, curr) line-height as the line-grouping
gap threshold so a large font block does not absorb a small heading above it.

The original ``_group_into_lines`` / ``_build_flat_string`` heuristic used
``prev_font_size * 0.5`` (asymmetric) as the y-gap threshold. When a
small heading (e.g. 36pt) sat above a much larger run (e.g. 110pt badge),
iteration order put the small element first as ``prev``, but the
threshold derived from the large element's bbox absorbed the small one
into the same line. The v0.1.2 fix (`locator.py:633` and `locator.py:847`)
takes ``min(prev_line_height, elem_line_height) * 0.5`` so the gap
threshold scales with the smaller font, preserving the boundary.

This probe is the regression guard for that fix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from pdf_edit_engine import get_text

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_n_2_small_heading_not_absorbed_by_large_run(tmp_path: Path) -> None:
    """A 36pt heading just above a 110pt run stays on its own line."""
    out = tmp_path / "n2.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]

    font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type1,
        BaseFont=pikepdf.Name.Helvetica,
    )
    page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))

    # Heading: 36pt at baseline y=713 → bbox top = 713 + 36*0.75 = 740
    # Badge:  110pt at baseline y=617 → bbox top = 617 + 110*0.75 = 699.5
    # Gap = 40.5
    # Buggy threshold (using max font): max(36,110)*0.5 = 55  → 40.5 < 55  → merged (BUG)
    # Fixed threshold (using min font): min(36,110)*0.5 = 18  → 40.5 > 18  → separate (FIX)
    stream = (
        b"BT /F1 36 Tf 72 713 Td (HEADING TEXT) Tj ET\nBT /F1 110 Tf 72 617 Td (BIG BADGE) Tj ET\n"
    )
    page.Contents = pdf.make_stream(stream)
    pdf.save(str(out))
    pdf.close()

    text = get_text(str(out))
    lines = text.splitlines()

    assert "HEADING TEXT" in text, f"heading absent from {text!r}"
    assert "BIG BADGE" in text, f"badge absent from {text!r}"

    heading_lines = [i for i, line in enumerate(lines) if "HEADING TEXT" in line]
    badge_lines = [i for i, line in enumerate(lines) if "BIG BADGE" in line]
    assert heading_lines, "heading not on any line"
    assert badge_lines, "badge not on any line"
    assert heading_lines != badge_lines, (
        f"heading and badge ended up on the same line — symmetric threshold regressed.\n"
        f"output:\n{text}"
    )
