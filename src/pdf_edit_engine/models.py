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
    rendering_mode: int = 0


@dataclass
class FontInfo:
    """Metadata about a font embedded in a PDF."""

    name: str
    postscript_name: str
    encoding_type: Literal["WinAnsi", "Identity-H", "MacRoman", "Custom"]
    is_subset: bool
    glyph_count: int
    embedded_type: Literal["TrueType", "CFF", "Type1"]
    font_cmap: dict[int, str] | None = field(default=None, repr=False, compare=False)


@dataclass
class TextMatch:
    """A located text match in a PDF with operator references.

    Note: TextMatch objects contain operator indices into the content stream.
    After any replace() call on the same PDF, previously returned TextMatch
    objects are invalidated. Use batch_replace() for multi-edit workflows,
    or call find() again after each replace().
    """

    matched_text: str
    page_number: int
    bounding_box: tuple[float, float, float, float]
    characters: list[TextCharacter]
    font_info: FontInfo
    operator_refs: list[int]


DegradationKind = Literal[
    "font_extension_failed",
    "kerning_compressed",
    "kerning_widened",
    "heading_font_dropped",
    "marker_font_dropped",
    "paragraph_detection_low_confidence",
    "overflow_shift_clamped",
    "overflow_shift_suppressed",
    "line_height_compressed",
    "reflow_aborted_to_simple",
    "font_coverage_extended",
    "font_coverage_substituted",
]


FONT_AFFECTING_KINDS: frozenset[str] = frozenset(
    {
        "heading_font_dropped",
        "marker_font_dropped",
        "font_extension_failed",
    }
)


@dataclass(frozen=True)
class Degradation:
    """A single typed degradation event surfaced by an edit operation.

    Frozen so that structural equality holds (used by the dry_run parity
    contract: the degradations list produced by ``dry_run=True`` must equal
    the list produced by ``dry_run=False`` for the same input).

    ``kind`` is one of twelve canonical values (see ``DegradationKind``).
    ``detail`` carries site-specific context (e.g. ``"Tz 88%"`` or
    ``"tier=1.5,chars=ø,ü,source=Carlito-Regular"``). ``severity`` is one
    of ``"info"``, ``"warning"``, or ``"error"``.
    """

    kind: DegradationKind
    detail: str = ""
    severity: Literal["info", "warning", "error"] = "info"


@dataclass
class FidelityReport:
    """Report on the fidelity of an edit operation.

    ``font_preserved`` is a computed property derived from ``degradations``
    and ``font_substituted`` (INV-J-8); the constructor does not accept it.

    ``glyphs_missing`` reflects the **pre-extension state**: chars that
    were missing from the font at the time ``can_encode`` was called.
    After successful extension the chars ARE in the font, but
    ``glyphs_missing`` still lists them as a record of what triggered the
    extension. This is information-preserving for callers who want to see
    what extension covered.
    """

    font_substituted: str | None
    overflow_detected: bool
    reflow_applied: bool
    glyphs_missing: list[str]
    degradations: list[Degradation] = field(default_factory=list)

    @property
    def font_preserved(self) -> bool:
        """True iff the original font's identity was preserved.

        Returns False when ``font_substituted`` is non-None (a metric-equivalent
        was used) OR when any degradation kind in ``FONT_AFFECTING_KINDS``
        was emitted. Extension (``font_coverage_extended`` / ``...substituted``)
        does NOT clear this flag — those record what extension covered, not
        an identity break.
        """
        return self.font_substituted is None and not any(
            d.kind in FONT_AFFECTING_KINDS for d in self.degradations
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize including @property fields (which dataclasses.asdict drops).

        ``dataclasses.asdict`` enumerates only declared fields, so the
        computed ``font_preserved`` property is silently lost when callers
        use ``asdict`` for JSON serialization. This helper inserts
        ``font_preserved`` after ``asdict`` runs.

        ``asdict`` recurses into ``Degradation`` (a frozen dataclass with
        no ``@property`` fields — verified above), so default recursion
        is correct for the nested ``degradations`` list. If a future
        ``@property`` is added to ``Degradation`` or any nested
        dataclass type used here, this method must override the recursion
        explicitly for that nesting.
        """
        import dataclasses

        data: dict[str, object] = dict(dataclasses.asdict(self))
        data["font_preserved"] = self.font_preserved
        return data


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
            font_substituted=None,
            overflow_detected=False,
            reflow_applied=False,
            glyphs_missing=[],
        )
    )

    def __post_init__(self) -> None:
        # INV-J-3 contract enforcement: overflow_detected=True must imply
        # at least one warning whose text references "overflow", so callers
        # iterating warnings can surface the condition without inspecting
        # the FidelityReport flags. Every internal site that flips
        # overflow_detected gets this guarantee for free.
        if self.fidelity_report.overflow_detected and not any(
            "overflow" in w.lower() for w in self.warnings
        ):
            self.warnings.append("Overflow detected: replacement extends past available space.")

        # INV-J-9 contract enforcement: font_action="failed" implies the
        # FidelityReport carries at least one font-affecting Degradation
        # (kind in FONT_AFFECTING_KINDS). Without this guard, a code path
        # that constructs EditResult(font_action="failed") with the
        # default-factory FidelityReport silently inherits
        # ``font_preserved=True`` — a lying-success surfaced by F-C-05
        # at structural.py:1003 / :1026. Fails loudly at construction so
        # future paths cannot regress (mirrors INV-J-3 trip-wire shape).
        if self.font_action == "failed" and not any(
            d.kind in FONT_AFFECTING_KINDS for d in self.fidelity_report.degradations
        ):
            raise ValueError(
                "INV-J-9: font_action='failed' requires a Degradation with "
                "kind in FONT_AFFECTING_KINDS; got "
                f"degradations={self.fidelity_report.degradations!r}"
            )


@dataclass
class Edit:
    """A find-and-replace pair for batch operations."""

    find: str
    replace: str


@dataclass
class GraphicsStateSnapshot:
    """Snapshot of the PDF graphics state at a point in the content stream.

    Stroke color and text rise are intentionally absent: every consumer
    in the engine reads ``fill_color`` only, never stroke. Tracking
    stroke state was dead code from v0.1.0 to v0.1.1; removed in v0.1.2.
    """

    ctm: tuple[float, float, float, float, float, float]
    fill_color: tuple[float, ...] | None
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


@dataclass(frozen=True)
class TextBlock:
    """A text element with its rendered position, font, and size."""

    text: str
    x: float
    y: float
    width: float
    height: float
    font_name: str
    font_size: float
    page: int


@dataclass
class Paragraph:
    """A detected paragraph of related text elements on a PDF page."""

    elements: list[ContentElement]
    full_text: str
    left_margin: float
    right_margin: float
    paragraph_width: float
    line_height: float
    font_name: str
    font_size: float
    first_line_y: float
    line_count: int
    operator_indices: list[int]
