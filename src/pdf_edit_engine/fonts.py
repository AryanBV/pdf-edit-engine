"""FontExtender module — analyze and extend font subsets in PDFs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pdf_edit_engine.models import FontInfo


def analyze_subset(pdf_path: str, font_name: str) -> FontInfo:
    """Analyze a font subset embedded in a PDF.

    Args:
        pdf_path: Path to the PDF file.
        font_name: Name of the font to analyze (as it appears in the PDF).

    Returns:
        FontInfo with subset details including glyph count and encoding type.
    """
    raise NotImplementedError


def can_render(font_info: FontInfo, text: str) -> tuple[bool, list[str]]:
    """Check if a font can render all characters in the given text.

    Args:
        font_info: FontInfo from analyze_subset().
        text: Text to check renderability for.

    Returns:
        Tuple of (can_render_all, list_of_missing_glyphs).
    """
    raise NotImplementedError


def extend_subset(
    pdf_path: str,
    font_name: str,
    additional_chars: str,
    *,
    full_font_path: str | None = None,
) -> bytes:
    """Extend a font subset to include additional characters.

    Uses two-tier approach:
    1. CMap-only extension if glyphs exist in embedded font data.
    2. Full re-embed from system font if glyphs are missing.

    Args:
        pdf_path: Path to the PDF file.
        font_name: Name of the font to extend.
        additional_chars: String of characters to add to the subset.
        full_font_path: Optional path to the full font file for Tier 2 extension.

    Returns:
        Modified PDF bytes with extended font subset.
    """
    raise NotImplementedError
