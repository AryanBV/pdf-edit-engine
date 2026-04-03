"""OperatorSurgeon module — modify PDF content stream operators."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pdf_edit_engine.models import Edit, EditResult, TextMatch


def replace(
    pdf_path: str,
    match: TextMatch,
    new_text: str,
    output_path: str,
    *,
    dry_run: bool = False,
) -> EditResult:
    """Replace a single text match in a PDF.

    Args:
        pdf_path: Path to the input PDF file.
        match: TextMatch from locator.find() identifying the text to replace.
        new_text: Replacement text.
        output_path: Path for the output PDF.
        dry_run: If True, simulate the edit without writing output.

    Returns:
        EditResult with fidelity report.
    """
    raise NotImplementedError


def replace_all(
    pdf_path: str,
    search: str,
    replacement: str,
    output_path: str,
    *,
    dry_run: bool = False,
) -> list[EditResult]:
    """Find and replace all occurrences of text in a PDF.

    Args:
        pdf_path: Path to the input PDF file.
        search: Text to find.
        replacement: Replacement text.
        output_path: Path for the output PDF.
        dry_run: If True, simulate edits without writing output.

    Returns:
        List of EditResult objects, one per match.
    """
    raise NotImplementedError


def batch_replace(
    pdf_path: str,
    edits: list[Edit],
    output_path: str,
    *,
    dry_run: bool = False,
) -> list[EditResult]:
    """Apply multiple find-and-replace operations to a PDF in a single pass.

    Args:
        pdf_path: Path to the input PDF file.
        edits: List of Edit objects with find/replace pairs.
        output_path: Path for the output PDF.
        dry_run: If True, simulate edits without writing output.

    Returns:
        List of EditResult objects, one per edit.
    """
    raise NotImplementedError
