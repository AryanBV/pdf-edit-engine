"""INV-G-4: break_into_lines with infinite width returns a single-line list."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from pdf_edit_engine.encoding import FontResolverCache
from pdf_edit_engine.reflow import break_into_lines

if TYPE_CHECKING:
    from pathlib import Path


def test_g_4_break_into_lines_infinite_width(reportlab_simple: Path) -> None:
    """`break_into_lines(text, paragraph_width=float('inf'), ...)` returns a list of length 1."""
    pdf = pikepdf.Pdf.open(str(reportlab_simple))
    try:
        page = pdf.pages[0]
        # Pick first font resource on the page.
        fonts = page["/Resources"]["/Font"]
        font_name = next(iter(fonts.keys())).lstrip("/")
        cache = FontResolverCache()
        resolver = cache.get_resolver(page, font_name)
        font_ref = page["/Resources"]["/Font"][f"/{font_name}"]

        lines = break_into_lines(
            "The quick brown fox jumps over the lazy dog and then some more text.",
            float("inf"),
            resolver,
            font_ref,
            12.0,
        )
        assert isinstance(lines, list)
        assert len(lines) == 1, (
            f"expected single line on infinite width, got {len(lines)}: {lines!r}"
        )
    finally:
        pdf.close()
