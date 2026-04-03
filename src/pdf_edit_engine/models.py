"""Shared data classes for pdf-edit-engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class TextCharacter:
    """A single character extracted from a PDF with position and font metadata."""

    unicode_char: str
    page_x: float
    page_y: float
    width: float
    height: float
    font_name: str
    font_size: float
    color: tuple[float, ...]
    operator_index: int
    byte_position: int
    tj_fragment_index: int | None


@dataclass
class FontInfo:
    """Metadata about a font embedded in a PDF."""

    name: str
    postscript_name: str
    encoding_type: Literal["WinAnsi", "Identity-H", "Custom"]
    is_subset: bool
    glyph_count: int
    embedded_type: Literal["TrueType", "CFF", "Type1"]


@dataclass
class TextMatch:
    """A located text match in a PDF with operator references."""

    matched_text: str
    page_number: int
    bounding_box: tuple[float, float, float, float]
    characters: list[TextCharacter]
    font_info: FontInfo
    operator_refs: list[int]


@dataclass
class FidelityReport:
    """Report on the fidelity of an edit operation."""

    font_preserved: bool
    font_substituted: str | None
    overflow_detected: bool
    reflow_applied: bool
    glyphs_missing: list[str]


@dataclass
class EditResult:
    """Result of a text edit operation."""

    success: bool
    original_text: str
    new_text: str
    font_action: Literal["kept", "extended", "substituted", "failed"]
    warnings: list[str] = field(default_factory=list)
    fidelity_report: FidelityReport = field(
        default_factory=lambda: FidelityReport(
            font_preserved=True,
            font_substituted=None,
            overflow_detected=False,
            reflow_applied=False,
            glyphs_missing=[],
        )
    )


@dataclass
class Edit:
    """A find-and-replace pair for batch operations."""

    find: str
    replace: str


@dataclass
class GraphicsStateSnapshot:
    """Snapshot of the PDF graphics state at a point in the content stream."""

    ctm: tuple[float, float, float, float, float, float]
    fill_color: tuple[float, ...] | None
    stroke_color: tuple[float, ...] | None
    font_name: str | None
    font_size: float | None
    text_matrix: tuple[float, float, float, float, float, float] | None


@dataclass
class ContentElement:
    """Wide index element covering all content stream elements on a page."""

    type: Literal["text", "image", "path", "state_change", "xobject"]
    page: int
    operator_range: tuple[int, int]
    bbox: tuple[float, float, float, float]
    graphics_state: GraphicsStateSnapshot
    text_content: str | None = None
    xobject_name: str | None = None
    path_data: list[object] | None = None
    characters: list[TextCharacter] | None = None
