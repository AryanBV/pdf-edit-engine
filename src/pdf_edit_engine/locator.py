"""TextLocator module — find text in PDFs with operator-level precision."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pdf_edit_engine.models import FontInfo, TextMatch


def find(
    pdf_path: str,
    search_text: str,
    *,
    page: int | None = None,
    case_sensitive: bool = True,
) -> list[TextMatch]:
    """Locate text in a PDF, returning matches with operator references.

    Args:
        pdf_path: Path to the PDF file.
        search_text: Text to search for.
        page: Restrict search to a specific page (0-indexed). None searches all pages.
        case_sensitive: Whether the search is case-sensitive.

    Returns:
        List of TextMatch objects with character positions and operator references.
    """
    raise NotImplementedError


def get_text(pdf_path: str, *, page: int | None = None) -> str:
    """Extract all text from a PDF or a specific page.

    Args:
        pdf_path: Path to the PDF file.
        page: Specific page to extract (0-indexed). None extracts all pages.

    Returns:
        Extracted text content.
    """
    raise NotImplementedError


def get_fonts(pdf_path: str, *, page: int | None = None) -> list[FontInfo]:
    """List all fonts used in a PDF or a specific page.

    Args:
        pdf_path: Path to the PDF file.
        page: Specific page to analyze (0-indexed). None analyzes all pages.

    Returns:
        List of FontInfo objects describing each font.
    """
    raise NotImplementedError
