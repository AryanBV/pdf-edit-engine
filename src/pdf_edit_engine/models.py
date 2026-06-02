"""Shared data classes for pdf-edit-engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, get_args


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
    embedded_type: Literal["TrueType", "CFF", "Type1", "cff2", "opentype-glyf", "type3", "unknown"]
    font_cmap: dict[int, str] | None = field(default=None, repr=False, compare=False)
    # C.2 / INV-C-10 (v0.2.0): read-path degradation channel. Populated by
    # ``locator.get_fonts`` when an embedded font binary is present but
    # unparseable (a ``font_subset_introspection_failed`` event). Additive and
    # backward-compatible — every existing constructor omits it. Excluded from
    # equality/repr so the FontInfo shape contract is unaffected.
    degradations: list[Degradation] = field(default_factory=list, repr=False, compare=False)


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
    # F-D-CC9 (v0.1.3): emitted by Tier 1.5 (`fonts._extend_tier2` /
    # `fonts._extend_simple_tier_one_five`) when the resolved system font
    # came from a per-platform user-writable directory. Severity
    # ``"warning"``. NOT in FONT_AFFECTING_KINDS — origin surface
    # rather than a fidelity break (the font WAS found and used).
    "font_substituted_from_user_fonts",
    # B.3 (v0.2.0): ToUnicode-absent Identity-H recovery (M0 Rank-2.5).
    # ``tounicode_recovered`` — emitted on the WRITE path when a NEW-GLYPH
    # replace targets a font whose CID→Unicode map was recovered from the
    # embedded cmap (no ``/ToUnicode``). The glyph-injection path needs a
    # ``/ToUnicode`` to extend, which is out of scope for B.3, so the edit
    # REFUSES (``font_action="failed"``); severity ``"error"`` and in
    # FONT_AFFECTING_KINDS. ``untextable_cidfont`` — emitted when a Type0
    # font has no usable ``/ToUnicode`` AND embedded-cmap recovery was
    # impossible (gates failed / majority-PUA / no ``/FontFile2``); the text
    # is unaddressable. Severity ``"error"``; in FONT_AFFECTING_KINDS.
    "tounicode_recovered",
    "untextable_cidfont",
    # POS-GATE (v0.2.0): emitted by ``surgeon`` when the edited run's text
    # matrix is non-axis-aligned (rotated/sheared — NOT a~1,b~0,c~0,d~1
    # within epsilon 1e-3) and the horizontal trailing-text width-delta
    # compensation is therefore SKIPPED (subtracting width_delta from the
    # trailing ``Td``/``Tm`` operand would shift the run along the wrong
    # axis under rotation). Severity ``"warning"``. NOT in
    # FONT_AFFECTING_KINDS — glyph identity is untouched, so a rotated
    # edit keeps ``FidelityReport.font_preserved`` True.
    "positioning_adjustment_skipped",
    # B.12 (v0.2.0): emitted by ``reflow.reflow_paragraph`` when an edit on a
    # non-axis-aligned (rotated/sheared — NOT a~1,b~0,c~0,d~1 within epsilon
    # 1e-3) run would route through paragraph reflow. Reflow rewrites the
    # paragraph by re-emitting a FRESH identity text matrix
    # ``Tm = [1 0 0 1 ...]``, which would SILENTLY FLATTEN the rotation (a
    # 90deg-rotated paragraph rendered horizontal with no caller signal). The
    # engine REFUSES the reflow instead (``EditResult.success=False``) before
    # any ops mutation. Severity ``"warning"``. NOT in FONT_AFFECTING_KINDS —
    # glyph identity is untouched (no glyph was substituted or dropped; only
    # the layout operation was declined), so the refusal uses
    # ``font_action="kept"`` and ``FidelityReport.font_preserved`` stays True.
    # The rotation-safe splice (same-length) and Tz-kerning (length-change,
    # no reflow) paths preserve the run's ``Tm`` and are NOT refused.
    "rotated_text_unsupported",
    # E.4 (v0.2.0): emitted by ``reflow.reflow_paragraph`` when a re-wrap
    # leaves a *widow* — a final line holding a single short word (length
    # <= 4) while the paragraph wrapped onto >= 2 lines. E.4 is
    # detect-and-surface only: the successful output geometry is UNCHANGED
    # (no risky pull-down repair that could mis-join across the wrap
    # boundary); the engine merely tells the caller the result has a widow.
    # Severity ``"info"``. NOT in FONT_AFFECTING_KINDS — glyph identity is
    # untouched, so ``FidelityReport.font_preserved`` stays True.
    "line_break_quality_degraded",
    # Block F CORE (v0.2.0): emitted by ``reflow._build_replacement_ops`` when
    # the captured verbatim fill color-setting operator subsequence cannot be
    # replayed (capture missing/unresolvable) and the engine falls back to the
    # device length-guess fast path (len 1 -> ``g``, 3 -> ``rg``, 4 -> ``k``),
    # OR when a single re-emitted BT/ET block cannot carry a multi-color
    # paragraph's distinct per-run fills (element[0]'s fill is replayed
    # verbatim and the loss is surfaced rather than silently dropped). A
    # non-device fill (Separation/DeviceN/ICCBased/Pattern) reinterpreted as
    # device color is the canonical trigger. Severity ``"warning"``. NOT in
    # FONT_AFFECTING_KINDS — glyph identity is untouched; only the fill
    # color-space is reinterpreted, so ``FidelityReport.font_preserved`` stays
    # True.
    "color_space_approximated",
    # E.2 (v0.2.0): emitted by ``reflow.reflow_paragraph`` on the
    # DEFAULT-FLUSH-BIAS branch of its indent classifier
    # (``reflow._detect_indent_style``). A reflowed paragraph's per-line
    # indent could not be confidently classified as first-line or hanging —
    # the paragraph is single-line (``len(x_starts) < 2``), its x-delta is
    # below the ``MIN_INDENT = font_size * 0.6`` noise floor, or its
    # continuation x-starts are non-monotone (mutually inconsistent) — so the
    # classifier falls back to FLUSH and re-emits the existing byte-identical
    # relative-``Td`` continuation stream. The output geometry is therefore
    # UNCHANGED; this signal only tells the caller that a real-but-
    # unclassifiable indent signal was flattened to flush. Severity
    # ``"info"``. NOT in FONT_AFFECTING_KINDS — glyph identity is untouched,
    # so ``FidelityReport.font_preserved`` stays True. A genuinely flush
    # multi-line paragraph (all x_starts equal) does NOT emit it — that
    # would be a false positive.
    "indent_flattened",
    # E.8 (v0.2.0): emitted by ``structural._replace_block_on_page`` when the
    # opt-in ``fit="shrink"`` policy binary-searches the body font size DOWN
    # to fit a FIXED-height bbox region (against the engine's own
    # ``break_into_lines`` wrapped-line-count × natural ``size * 1.2`` leading
    # oracle), with a ``min_pt = max(4.0, original * 0.5)`` floor. Emitted iff
    # the applied size ended strictly below the original — INCLUDING the
    # floor-clamp case where even the floor cannot fit (the shrink still
    # happened; overflow is surfaced honestly + separately via
    # ``overflow_detected`` and the existing ``overflow_shift_*`` kinds).
    # Severity ``"info"`` (opt-in, best-effort, graceful — mirrors
    # ``line_height_compressed``). NOT in FONT_AFFECTING_KINDS: a font-size
    # change does not alter glyph identity, encoding, or substitution, so
    # ``FidelityReport.font_preserved`` stays True.
    "font_size_reduced",
    # A2.2 / INV-W-3 (v0.2.0): emitted by an edit verb when the input PDF was
    # linearized ("Fast Web View") but pikepdf could not re-linearize it on
    # save — ``_pathutil._save_pdf`` retried with a normal (non-linearized)
    # save so the edit still succeeds, and the verb surfaces this so the lost
    # Fast-Web-View layout is HONEST rather than silent. Emitted ONLY on that
    # fallback path: never when preservation succeeds, and never for a
    # non-linearized input (no over-surfacing). Severity ``"info"`` (best-
    # effort, graceful — mirrors ``line_height_compressed`` /
    # ``font_size_reduced``). NOT in FONT_AFFECTING_KINDS: a file-layout
    # change does not alter glyph identity, encoding, or substitution, so
    # ``FidelityReport.font_preserved`` stays True.
    "linearization_dropped",
    # C.2 / INV-C-10 (v0.2.0): emitted by ``locator.get_fonts`` (via
    # ``_build_font_info``) on the best-effort READ path when an embedded
    # font binary is PRESENT but cannot be parsed to count glyphs (neither
    # ``TTFont`` nor ``cffLib`` nor ``t1Lib`` can read it). ``glyph_count`` is
    # reported as 0 (unknown) rather than fabricated from a sparse ``/W`` dict,
    # and this Degradation tells the caller the count is unknown, not
    # zero-by-truth. READ PATH ONLY — never emitted on ``analyze_subset``
    # (which returns ``glyph_count`` 0 silently with ``embedded_type`` set, and
    # raises a structured error only when the font itself is absent). Severity
    # ``"warning"``. NOT in FONT_AFFECTING_KINDS — glyph identity is untouched;
    # only the count introspection failed, so ``FidelityReport.font_preserved``
    # stays True.
    "font_subset_introspection_failed",
    # A1.3 / INV-W-4 (v0.2.0): emitted by an EDIT verb (surgeon / reflow /
    # structural font-extension catch) ALONGSIDE ``font_extension_failed``
    # when an embedded font / CMap / ToUnicode / CIDToGIDMap stream's
    # DECOMPRESSED size exceeds the bound (Flate decompression-bomb guard,
    # ``FontStreamTooLargeError`` via ``_pathutil.read_stream_bounded``).
    # Severity ``"warning"``. NOT in FONT_AFFECTING_KINDS — the edit was
    # REFUSED before any glyph surgery; ``font_preserved`` is already driven
    # False by the companion ``font_extension_failed``.
    "font_stream_too_large",
    # B.9 / INV-B-9 (v0.2.0): emitted by an EDIT verb
    # (``surgeon._apply_single_replacement`` / ``reflow.reflow_paragraph``)
    # when ``FontResolver.encode`` actually chose a ligature CID during
    # re-encode — a MANDATORY ligature (always applied; no plain-letter
    # spacing equivalent, e.g. an Arabic presentation form) or an opted-in
    # DISCRETIONARY ligature (``allow_discretionary_ligatures=True``). It does
    # NOT fire on the DEFAULT path for typed-separate Latin ("office" → no
    # ligature chosen). Severity ``"info"``. NOT in FONT_AFFECTING_KINDS — a
    # ligature re-route selects a different glyph WITHIN THE SAME embedded font
    # (no substitution font, no font swap); glyph identity changes shape but
    # the typeface is preserved, exactly analogous to ``kerning_compressed`` /
    # ``font_size_reduced``, so ``FidelityReport.font_preserved`` stays True.
    "ligature_substituted",
    # B.11 / INV-B-10 (v0.2.0): emitted by surgeon (replace/replace_all/
    # batch_replace empty-replacement) and structural.delete_block when a
    # deletion leaves PROVABLE residual deleted text in the edited region
    # (the keep-slot emptying failed to clear a glyph, or a bbox show-text
    # op was missed). Drives EditResult.success=False (font_action="kept").
    # Severity "warning". NOT in FONT_AFFECTING_KINDS — glyph identity is
    # untouched (the failure is residue, not a font swap), so
    # FidelityReport.font_preserved stays True.
    "deletion_residual_text",
    # B.11 (v0.2.0): emitted by the deletion paths when a BI/ID/EI inline
    # image lies in/near the deletion span. A1.4: pikepdf collapses
    # BI/ID/EI to one stable operator slot, so operator_index addressing
    # survives — this is an advisory signal, NOT a failure (the deletion
    # still proceeds). Severity "info". NOT in FONT_AFFECTING_KINDS — glyph
    # identity is untouched, so FidelityReport.font_preserved stays True.
    "inline_image_present",
    # E.7 / INV-G-9 (v0.2.0): emitted by ``reflow.reflow_paragraph`` when a
    # paragraph's script has NO UAX#14 line-break opportunity and contains no
    # spaces (Thai / Lao / Khmer / Myanmar — dictionary-segmented scripts that
    # the stdlib ``unicodedata`` East-Asian-width classifier cannot break). The
    # run is left UNWRAPPED (honest: we cannot break it without a dictionary,
    # which is out of scope) and this signal tells the caller the text was not
    # reflowed. CJK (which DOES break between ideographs) and Latin (which is
    # space-segmented) never emit it. Severity ``"info"``. NOT in
    # FONT_AFFECTING_KINDS — glyph identity is untouched; only the line-break
    # segmentation was unavailable, so ``FidelityReport.font_preserved`` stays
    # True.
    "scriptless_reflow_unsupported",
    # A2.3 / INV-W-5 (v0.2.0): emitted by an edit verb when an ENCRYPTED
    # input could NOT be re-encrypted on save (pikepdf raised on the
    # encryption= save) and the edit fell back to an unencrypted output. Fires
    # ONLY on that genuine re-encryption failure — NOT on the common success
    # path, and NOT for the documented owner!=user collapse or advisory-/P
    # boundaries. Severity ``"warning"`` (a dropped encryption is a fidelity/
    # security loss, more serious than a dropped Fast-Web-View layout). NOT in
    # FONT_AFFECTING_KINDS: an encryption change does not alter glyph identity,
    # encoding, or substitution, so ``FidelityReport.font_preserved`` stays True.
    "encryption_dropped",
    # Multi-match-same-operator honest-refusal (INV-B-12, v0.2.0): emitted by
    # ``surgeon.replace_all`` / ``surgeon.batch_replace`` when TWO OR MORE
    # matches on a page splice into the SAME show-text operator AND the
    # replacement is length-changing (or ligature-forcing) — i.e. it routes
    # through the byte-shifting rebuild path. Each such match's
    # ``byte_position`` was recorded against the ORIGINAL operand, so after the
    # first splice shifts the operand bytes, every later same-operator match
    # would read the WRONG byte slice and silently corrupt the output. The
    # engine detects the collision BEFORE any mutation and REFUSES exactly
    # those colliding matches (``EditResult.success=False``,
    # ``font_action="kept"``); matches in DIFFERENT operators, and same-length
    # non-ligature edits (which splice byte-stably at fixed positions), still
    # edit correctly (partial-success preserved). A full reverse-order
    # offset-rederivation rewrite is deferred to 0.3.0. Severity ``"warning"``.
    # NOT in FONT_AFFECTING_KINDS — no edit was applied to the refused matches,
    # so glyph identity is untouched and ``FidelityReport.font_preserved`` stays
    # True.
    "multi_match_same_operator_unsupported",
]


DEGRADATION_KINDS: tuple[str, ...] = get_args(DegradationKind)
"""Machine-enumerable tuple of every canonical DegradationKind value.

Derived from the ``DegradationKind`` Literal via ``typing.get_args`` so it can
never drift from the type. ``len == 30`` as of v0.2.0 (A2.3 ``encryption_dropped``
+ the multi-match ``multi_match_same_operator_unsupported``)."""


_SEVERITY_ORDER: dict[Literal["info", "warning", "error"], int] = {
    "info": 0,
    "warning": 1,
    "error": 2,
}
"""Single source of truth for degradation severity ordering (info < warning <
error), used by ``FidelityReport.max_severity``."""


FONT_AFFECTING_KINDS: frozenset[str] = frozenset(
    {
        "heading_font_dropped",
        "marker_font_dropped",
        "font_extension_failed",
        # B.3 (v0.2.0): both are hard refusals on the WRITE path / text
        # layer — the edit could not preserve (or even address) the font's
        # text. Inclusion means font_action="failed" with one of these
        # satisfies the INV-J-9 construction guard, and font_preserved
        # computes False.
        "tounicode_recovered",
        "untextable_cidfont",
    }
)


@dataclass(frozen=True)
class Degradation:
    """A single typed degradation event surfaced by an edit operation.

    Frozen so that structural equality holds (used by the dry_run parity
    contract: the degradations list produced by ``dry_run=True`` must equal
    the list produced by ``dry_run=False`` for the same input).

    ``kind`` is one of the canonical set of ``DegradationKind`` values
    enumerated in the ``Literal`` above.
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

    @property
    def is_clean(self) -> bool:
        """True iff there is nothing to report at all.

        ``is_clean`` is True when there are no degradations AND no font
        substitution AND no overflow. This is STRICTER than
        ``font_preserved``: an info-severity, non-font-affecting degradation
        (e.g. ``line_break_quality_degraded``) keeps ``font_preserved`` True
        but makes ``is_clean`` False. The two answer different questions —
        ``is_clean`` = "nothing happened worth reporting", ``font_preserved``
        = "the typeface identity is intact". They never contradict INV-J-8:
        ``is_clean`` True implies no degradations, which implies
        ``font_preserved`` True.
        """
        return (
            not self.degradations and self.font_substituted is None and not self.overflow_detected
        )

    @property
    def max_severity(self) -> Literal["info", "warning", "error"] | None:
        """Highest severity among ``degradations``, or ``None`` when empty.

        Ordering is ``info < warning < error`` (via ``_SEVERITY_ORDER``). This
        considers ONLY ``degradations`` — it does NOT factor in
        ``overflow_detected`` or ``font_substituted`` (those are surfaced via
        ``is_clean`` / ``summary``, not on the severity axis).
        """
        if not self.degradations:
            return None
        ranked: Literal["info", "warning", "error"] = "info"
        for d in self.degradations:
            if _SEVERITY_ORDER[d.severity] > _SEVERITY_ORDER[ranked]:
                ranked = d.severity
        return ranked

    def warnings(self) -> list[Degradation]:
        """Degradations of severity ``"warning"`` or ``"error"`` (first-seen order).

        Info-severity degradations are excluded. Returns the ``Degradation``
        objects themselves (distinct from ``EditResult.warnings``, which is a
        ``list[str]`` of human-readable strings).
        """
        return [d for d in self.degradations if d.severity in ("warning", "error")]

    def summary(self) -> str:
        """One-line human-readable rendering of the report.

        - ``"clean"`` when ``is_clean``.
        - ``"FAILED: <reasons>"`` when any error-severity degradation is present.
        - ``"saved with N warning(s): <reasons>"`` otherwise (warnings/info
          degradations present, or a substitution/overflow with no error).

        Reasons are the degradation kinds rendered with spaces (not
        underscores), de-duplicated in first-seen order, plus synthetic phrases
        for ``font_substituted`` and ``overflow_detected`` when those are not
        already implied by a degradation kind. ``Degradation.detail`` is NOT
        folded into the one-line summary (it stays accessible via
        ``to_dict`` / iterating ``degradations``).
        """
        if self.is_clean:
            return "clean"

        reasons: list[str] = []
        seen_kinds: set[str] = set()
        for d in self.degradations:
            if d.kind in seen_kinds:
                continue
            seen_kinds.add(d.kind)
            reasons.append(d.kind.replace("_", " "))

        if self.font_substituted is not None and "font_coverage_substituted" not in seen_kinds:
            reasons.append(f"font substituted ({self.font_substituted})")

        if self.overflow_detected and not any(k.startswith("overflow_shift_") for k in seen_kinds):
            reasons.append("overflow shifted")

        if self.max_severity == "error":
            return "FAILED: " + "; ".join(reasons)
        n = len(reasons)
        return f"saved with {n} warning{'s' if n != 1 else ''}: " + "; ".join(reasons)


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

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-ready dict (nested report keeps font_preserved).

        ``dataclasses.asdict`` would recurse into ``fidelity_report`` and DROP
        its computed ``font_preserved`` @property — the exact trap that
        ``FidelityReport.to_dict`` exists to close, reopened one level up. This
        method serializes the top-level scalar/list fields itself and delegates
        the nested report to ``fidelity_report.to_dict()`` so ``font_preserved``
        is present in the output dict.
        """
        return {
            "success": self.success,
            "original_text": self.original_text,
            "new_text": self.new_text,
            "font_action": self.font_action,
            "warnings": list(self.warnings),
            "fidelity_report": self.fidelity_report.to_dict(),
        }


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

    ``fill_color_ops`` (v0.2.0, Block F CORE color slice) is the verbatim
    fill color-setting operator subsequence that produced ``fill_color`` —
    a list of ``(operands, operator_name)`` entries whose operands are kept
    as the original pikepdf objects (``pikepdf.Name`` for ``cs`` color-space
    names, numerics for ``sc``/``scn``/``g``/``rg``/``k``), NOT coerced to
    floats. ``fill_color`` is the lossy float tuple used for *matching*;
    ``fill_color_ops`` is the lossless record used by reflow to *replay* the
    fill verbatim and preserve non-device (Separation/DeviceN/ICCBased/
    Pattern) color-space identity. Defaults to ``None`` so all existing
    construction sites are unchanged.

    ``leading`` / ``leading_authoritative`` (v0.2.0, E.3 declared-leading
    capture) carry the document's declared text leading at snapshot time.
    ``leading`` is the ``TL``/``TD``-declared value (a plain float, ``0.0``
    when never declared); ``leading_authoritative`` is ``True`` iff an
    explicit ``TL`` or ``TD`` declared a leading in the current ``q``-scope.
    Reflow consults ``leading`` only when ``leading_authoritative`` is
    ``True``, so the ``0.0`` default never affects non-declaring paragraphs.
    Both defaulted so all existing construction sites are unchanged.
    """

    ctm: tuple[float, float, float, float, float, float]
    fill_color: tuple[float, ...] | None
    font_name: str | None
    font_size: float | None
    text_matrix: tuple[float, float, float, float, float, float] | None
    fill_color_ops: list[tuple[list[object], str]] | None = None
    leading: float | None = None
    leading_authoritative: bool = False


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
    # E.2 (v0.2.0): per-line indent style classified from the source
    # paragraph's ``x_starts`` by ``reflow._detect_indent_style``. All
    # defaulted to the flush no-op so the single construction site
    # (``reflow._build_paragraph``) and any external constructor are
    # unaffected; a confident first-line/hanging classification populates
    # them and ``reflow._build_replacement_ops`` re-emits the indent.
    indent_style: Literal["first_line", "hanging", "flush"] = "flush"
    first_line_indent: float = 0.0
    hanging_indent: float = 0.0
