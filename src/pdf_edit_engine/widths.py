"""Shared glyph width utilities for pdf-edit-engine.

Extracted from locator.py so both locator and surgeon can access
glyph width lookups without code duplication.
"""

from __future__ import annotations

import logging

import pikepdf

logger = logging.getLogger(__name__)

DEFAULT_WIDTH: float = 600.0


def parse_cid_widths(cid_font: pikepdf.Dictionary) -> dict[int, float]:
    """Parse a CIDFont /W array into a CID -> width mapping.

    The /W array uses two formats:
    - [cid_start [w1 w2 ...]] — consecutive widths starting at cid_start
    - [cid_start cid_end width] — range of CIDs with the same width

    Args:
        cid_font: The CIDFont dictionary (DescendantFonts[0]).

    Returns:
        Dict mapping CID integers to widths in font units.
    """
    widths: dict[int, float] = {}
    if "/W" not in cid_font:
        return widths
    w_array: pikepdf.Array = cid_font["/W"]  # type: ignore[assignment]
    w_items: list[pikepdf.Object] = list(w_array)  # type: ignore[call-overload]
    i = 0
    while i < len(w_items):
        cid_start = int(w_items[i])
        i += 1
        if i >= len(w_items):
            break
        next_item = w_items[i]
        if isinstance(next_item, pikepdf.Array):
            # [cid_start [w1, w2, ...]]
            for j in range(len(next_item)):
                widths[cid_start + j] = float(next_item[j])
            i += 1
        else:
            # [cid_start cid_end width]
            if i + 1 >= len(w_items):
                break
            cid_end = int(next_item)
            width = float(w_items[i + 1])
            for cid in range(cid_start, cid_end + 1):
                widths[cid] = width
            i += 2
    return widths


def parse_simple_widths(font_dict: pikepdf.Dictionary) -> dict[int, float]:
    """Parse a simple font /Widths array into a char_code -> width mapping.

    Args:
        font_dict: The font dictionary.

    Returns:
        Dict mapping character codes to widths in font units.
    """
    widths: dict[int, float] = {}
    if "/Widths" not in font_dict:
        return widths
    first_char_obj = font_dict.get("/FirstChar")
    first_char = int(first_char_obj) if first_char_obj is not None else 0
    w_arr: pikepdf.Array = font_dict["/Widths"]  # type: ignore[assignment]
    for i in range(len(w_arr)):
        widths[first_char + i] = float(w_arr[i])
    return widths


class GlyphWidthCache:
    """Caches parsed font width tables for efficient per-glyph lookups."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[int, float]] = {}

    def get_width(
        self,
        page: pikepdf.Page,
        font_name: str,
        char_code: int,
    ) -> float:
        """Return glyph width in font units (divide by 1000 for text space).

        Args:
            page: The page containing the font.
            font_name: Font resource name (e.g., 'F1').
            char_code: The character/CID code.

        Returns:
            Width in font units. Defaults to 600 if lookup fails.
        """
        if font_name not in self._cache:
            self._cache[font_name] = self._load_widths(page, font_name)
        return self._cache[font_name].get(char_code, DEFAULT_WIDTH)

    def _load_widths(
        self,
        page: pikepdf.Page,
        font_name: str,
    ) -> dict[int, float]:
        font_key = font_name if font_name.startswith("/") else f"/{font_name}"
        try:
            font_obj = page["/Resources"]["/Font"][font_key]
        except (KeyError, TypeError):
            logger.warning("Font %s not found in page resources", font_name)
            return {}
        font_dict = pikepdf.Dictionary(font_obj)  # type: ignore[arg-type]
        subtype_obj = font_dict.get("/Subtype")
        subtype = str(subtype_obj) if subtype_obj is not None else ""
        if subtype == "/Type0":
            # CIDFont — widths in DescendantFonts[0]/W
            try:
                cid_font = font_dict["/DescendantFonts"][0]
                return parse_cid_widths(
                    pikepdf.Dictionary(cid_font),  # type: ignore[arg-type]
                )
            except (KeyError, IndexError):
                logger.warning("Cannot parse /W for CIDFont %s", font_name)
                return {}
        else:
            return parse_simple_widths(font_dict)
