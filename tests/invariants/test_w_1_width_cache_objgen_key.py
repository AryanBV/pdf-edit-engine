"""INV-W-1: GlyphWidthCache keys on the font dict's objgen, not the bare name.

Two distinct font dictionaries that happen to share a resource name (e.g.
``/F1`` on two different pages, each with its own ``/Widths`` array) must NOT
collide in the width cache. Pre-fix the cache was keyed on the ``font_name``
string (``widths.py``), so the first page's widths aliased the second page's
lookups — a silent wrong-width bug for any multi-page PDF that reuses resource
names across independently-built pages.

INV-W-1 minted as the first slot of a new ``W`` layer (width-cache hygiene),
distinct from the existing ``W0`` worker/wire-format layer.
"""

from __future__ import annotations

import pikepdf

from pdf_edit_engine.widths import GlyphWidthCache


def _add_simple_font_page(pdf: pikepdf.Pdf, width: float) -> pikepdf.Page:
    """Append a page carrying a single simple font ``/F1`` with one width.

    Each call builds a *fresh* indirect font dict, so the two ``/F1`` dicts
    across two pages have distinct ``objgen`` pairs even though they share the
    resource name. ``/FirstChar`` is 65 ('A') with a one-element ``/Widths``.

    Returns:
        The newly-appended page.
    """
    font_dict = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
                "/FirstChar": 65,
                "/LastChar": 65,
                "/Widths": pikepdf.Array([width]),
            }
        )
    )
    page = pdf.add_blank_page(page_size=(200, 200))
    page["/Resources"] = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_dict})})
    return page


def test_inv_w_1_distinct_font_dicts_do_not_alias() -> None:
    """Two ``/F1`` dicts on different pages with distinct objgen must not alias.

    char_code 65 ('A') has width 500 on page 0 and width 900 on page 1. A
    cache keyed only on the name returns 500 for both (aliasing bug). A cache
    keyed on the font dict's objgen returns the page-correct width.
    """
    pdf = pikepdf.Pdf.new()
    try:
        page0 = _add_simple_font_page(pdf, width=500.0)
        page1 = _add_simple_font_page(pdf, width=900.0)

        # Sanity: the two /F1 font dicts really are distinct objects.
        f0 = page0["/Resources"]["/Font"]["/F1"]
        f1 = page1["/Resources"]["/Font"]["/F1"]
        assert f0.objgen != f1.objgen, "fixture invalid: /F1 dicts share objgen"

        cache = GlyphWidthCache()
        # Warm the cache on page 0 first; a name-keyed cache poisons /F1.
        w0 = cache.get_width(page0, "F1", 65)
        w1 = cache.get_width(page1, "F1", 65)

        assert w0 == 500.0, f"page0 /F1 'A' width: expected 500.0, got {w0}"
        assert w1 == 900.0, (
            f"page1 /F1 'A' width: expected 900.0, got {w1} — width cache "
            f"aliased the two distinct font dicts on a shared resource name"
        )
    finally:
        pdf.close()


def test_inv_w_1_in_place_w_mutation_still_needs_evict() -> None:
    """objgen re-key fixes ALIASING but not STALENESS.

    An in-place ``/Widths`` edit does not change the dict's objgen, so a stale
    cached entry survives until ``evict`` is called. This pins the contract
    that the post-extension ``evict`` at the surgeon call site remains load
    bearing even after the objgen re-key.
    """
    pdf = pikepdf.Pdf.new()
    try:
        page = _add_simple_font_page(pdf, width=500.0)
        cache = GlyphWidthCache()

        assert cache.get_width(page, "F1", 65) == 500.0  # warm

        # Mutate /Widths in place — objgen is unchanged.
        page["/Resources"]["/Font"]["/F1"]["/Widths"] = pikepdf.Array([700.0])
        assert cache.get_width(page, "F1", 65) == 500.0, (
            "expected the stale cached 500.0 (objgen unchanged ⇒ no auto-refresh)"
        )

        cache.evict(page, "F1")
        assert cache.get_width(page, "F1", 65) == 700.0, (
            "after evict, the fresh in-place /Widths must be re-parsed"
        )
    finally:
        pdf.close()
