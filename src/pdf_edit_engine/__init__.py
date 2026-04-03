"""pdf-edit-engine: Format-preserving PDF text editing engine."""

from __future__ import annotations

from pdf_edit_engine.fonts import analyze_subset, can_render, extend_subset
from pdf_edit_engine.locator import find, get_fonts, get_text
from pdf_edit_engine.models import (
    ContentElement,
    Edit,
    EditResult,
    FidelityReport,
    FontInfo,
    GraphicsStateSnapshot,
    TextCharacter,
    TextMatch,
)
from pdf_edit_engine.reflow import reflow_paragraph
from pdf_edit_engine.surgeon import batch_replace, replace, replace_all
from pdf_edit_engine.wrapper import (
    add_bookmark,
    add_highlight,
    add_hyperlink,
    add_watermark,
    crop_pages,
    decrypt_pdf,
    delete_pages,
    edit_metadata,
    encrypt_pdf,
    fill_form,
    flatten_annotations,
    merge_pdfs,
    reorder_pages,
    rotate_pages,
    split_pdf,
)

__all__ = [
    # locator
    "find",
    "get_text",
    "get_fonts",
    # surgeon
    "replace",
    "replace_all",
    "batch_replace",
    # fonts
    "analyze_subset",
    "can_render",
    "extend_subset",
    # reflow
    "reflow_paragraph",
    # wrapper
    "merge_pdfs",
    "split_pdf",
    "reorder_pages",
    "rotate_pages",
    "delete_pages",
    "crop_pages",
    "edit_metadata",
    "add_bookmark",
    "encrypt_pdf",
    "decrypt_pdf",
    "add_hyperlink",
    "add_highlight",
    "flatten_annotations",
    "fill_form",
    "add_watermark",
    # models
    "TextMatch",
    "TextCharacter",
    "EditResult",
    "FidelityReport",
    "FontInfo",
    "Edit",
    "ContentElement",
    "GraphicsStateSnapshot",
]
