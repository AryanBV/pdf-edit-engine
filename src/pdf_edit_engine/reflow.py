"""ReflowEngine module — reflow paragraphs when text length changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pdf_edit_engine.models import EditResult, TextMatch


def reflow_paragraph(
    pdf_path: str,
    match: TextMatch,
    new_text: str,
    output_path: str,
    *,
    dry_run: bool = False,
) -> EditResult:
    """Reflow a paragraph after text replacement to fit within bounds.

    Uses fonttools for glyph metrics to calculate line breaks and
    repositioning of text operators.

    Args:
        pdf_path: Path to the input PDF file.
        match: TextMatch identifying the paragraph to reflow.
        new_text: New text content for the paragraph.
        output_path: Path for the output PDF.
        dry_run: If True, simulate the reflow without writing output.

    Returns:
        EditResult with fidelity report including reflow details.
    """
    raise NotImplementedError
