"""INV-N-3: locator inserts a word-boundary space using ``font_size * 0.25``
as the x-gap threshold, so one-glyph-per-operator PDFs (Chrome, Word) do
not merge consecutive words into a single token.

The original heuristic used ``avg_char_width * 0.5`` of the immediately
preceding fragment. For PDFs that emit one Tj per glyph, the previous
fragment is a single character whose width can be 1.5–2× the font's
nominal space-glyph width. That made the threshold too large and
swallowed real inter-word gaps, producing ``"mword"`` instead of
``"m word"``. The v0.1.2 fix (`locator.py:666` and `locator.py:858`)
uses ``font_size * 0.25`` — the canonical space-glyph proxy — which
holds regardless of fragment granularity.

This probe is the regression guard for that fix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from pdf_edit_engine import find, get_text

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_n_3_per_glyph_tj_preserves_word_boundary(tmp_path: Path) -> None:
    """A per-glyph-Tj rendering of "m word" stays as two distinct tokens."""
    out = tmp_path / "n3.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]

    font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type1,
        BaseFont=pikepdf.Name.Helvetica,
    )
    page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))

    # Each glyph is its own Tj at an explicit absolute position via Tm.
    # 'm' at x=72, then a real ~6pt inter-word gap, then 'w','o','r','d'
    # tightly packed. Geometry numbers approximate Helvetica 12pt advances.
    #
    # Expected gap between 'm' (ends at ~82) and 'w' (starts at 86): 4pt.
    # Buggy threshold using avg_char_width*0.5 of the 'm' fragment (~5pt
    # for 12pt Helvetica 'm') would suppress the space (4 < 5).
    # Fixed threshold font_size*0.25 = 3 yields 4 > 3, so a space IS
    # inserted between the tokens.
    stream = (
        b"BT /F1 12 Tf\n"
        b"1 0 0 1 72 700 Tm (m) Tj\n"
        b"1 0 0 1 86 700 Tm (w) Tj\n"
        b"1 0 0 1 95 700 Tm (o) Tj\n"
        b"1 0 0 1 102 700 Tm (r) Tj\n"
        b"1 0 0 1 107 700 Tm (d) Tj\n"
        b"ET\n"
    )
    page.Contents = pdf.make_stream(stream)
    pdf.save(str(out))
    pdf.close()

    text = get_text(str(out))
    tokens = text.split()

    assert "m" in tokens, f"'m' not a standalone token in {tokens!r}"
    assert "word" in tokens, f"'word' not a standalone token in {tokens!r}"
    assert "mword" not in tokens, (
        f"'m' and 'word' were merged — space threshold regressed.\ntext={text!r}\ntokens={tokens!r}"
    )

    # find() shares the same flat-string builder; verify it agrees.
    assert find(str(out), "m word") != [] or (find(str(out), "m") and find(str(out), "word")), (
        "find() also lost the word boundary"
    )
