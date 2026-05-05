"""pdf-edit-engine: Format-preserving PDF text editing engine."""

from __future__ import annotations

__version__ = "0.1.3"

from pdf_edit_engine.annotations import (
    Annotation,
    add_annotation,
    delete_annotation,
    get_annotations,
    move_annotation,
    update_annotation_uri,
)
from pdf_edit_engine.errors import (
    EncodingError,
    FontNotFoundError,
    OperatorError,
    PDFEditError,
    ReflowError,
)
from pdf_edit_engine.fonts import analyze_subset, can_render, extend_subset
from pdf_edit_engine.locator import (
    extract_bbox_text,
    find,
    get_fonts,
    get_text,
    get_text_layout,
)
from pdf_edit_engine.models import (
    ContentElement,
    Edit,
    EditResult,
    FidelityReport,
    FontInfo,
    GraphicsStateSnapshot,
    Paragraph,
    TextBlock,
    TextCharacter,
    TextMatch,
)
from pdf_edit_engine.reflow import detect_paragraphs, reflow_paragraph
from pdf_edit_engine.structural import (
    batch_replace_block,
    compute_uniform_layout,
    delete_block,
    insert_text_block,
    replace_block,
    shift_content_below,
)
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
    "get_text_layout",
    "get_fonts",
    "extract_bbox_text",
    # surgeon
    "replace",
    "replace_all",
    "batch_replace",
    # structural
    "replace_block",
    "batch_replace_block",
    "delete_block",
    "insert_text_block",
    "shift_content_below",
    "compute_uniform_layout",
    # fonts
    "analyze_subset",
    "can_render",
    "extend_subset",
    # reflow
    "detect_paragraphs",
    "reflow_paragraph",
    "Paragraph",
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
    # annotations
    "get_annotations",
    "add_annotation",
    "update_annotation_uri",
    "delete_annotation",
    "move_annotation",
    "Annotation",
    # models
    "TextMatch",
    "TextCharacter",
    "TextBlock",
    "EditResult",
    "FidelityReport",
    "FontInfo",
    "Edit",
    "ContentElement",
    "GraphicsStateSnapshot",
    # errors
    "PDFEditError",
    "FontNotFoundError",
    "EncodingError",
    "OperatorError",
    "ReflowError",
]
