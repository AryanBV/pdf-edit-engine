"""Deterministic adversarial corpus builders for pdf-edit-engine tests.

Net-new test tooling (no ``src/`` changes). Each public builder produces a
*deterministic* PDF — no network, fixed font timestamps, reproducible
``/ID`` — that stresses a specific structural feature the v0.1.3 engine does
not yet fully handle:

- :func:`build_truetype_baseline_pdf` — the Identity-H control case.
- :func:`build_cff_font_pdf` — CFF / OpenType-CFF outlines (unsupported
  Tier 1.5 extension target; ARY-279).
- :func:`build_xobject_text_pdf` — text drawn inside a Form XObject (``Do``).
- :func:`build_arabic_pdf` — Arabic / right-to-left script.
- :func:`build_tagged_pdf` — Tagged PDF: ``/StructTreeRoot`` + marked
  content (BDC/EMC) + ``/ActualText``.

Each builder accepts an optional ``out_path`` and returns the PDF bytes.
Builders that depend on a host font return ``None`` when no suitable font is
installed (follow the ``cidfont_synthetic`` skipif precedent); the CFF
builder synthesises its font in-process and never returns ``None``.

See :data:`BUILD_SCHEMA` for the machine-readable description of every
builder.
"""

from __future__ import annotations

from .arabic import build_arabic_pdf
from .bare_cff_font import build_bare_cff_font_pdf
from .cff_cid_injection import (
    build_cff2_cid_pdf,
    build_cff_cid_missing_glyph_pdf,
    build_cff_cid_missing_glyph_wrapped_pdf,
    build_cff_cid_sparse_collision_pdf,
    build_cff_cid_tounicode_only_collision_pdf,
    build_cff_donor_bytes,
    build_namekeyed_otf_cff_pdf,
    build_seac_composite_donor_bytes,
    build_truetype_glyf_donor_bytes,
)
from .cff_font import build_cff_font_pdf
from .colored_runs import build_devicergb_run_pdf, build_separation_run_pdf
from .declared_leading import build_declared_leading_pdf
from .indent_styles import build_indent_styles_pdf
from .linearized import build_linearized_pdf, build_nonlinearized_pdf
from .reflow_quality import build_reflow_quality_pdf
from .rotated_text import build_axis_aligned_two_run_pdf, build_rotated_text_pdf
from .shrink_to_fit import build_overflow_text, build_shrink_to_fit_pdf
from .tagged import build_tagged_pdf
from .truetype_baseline import build_truetype_baseline_pdf
from .type1_font import build_type1_font_pdf
from .xobject_text import build_xobject_text_pdf

__all__ = [
    "BUILD_SCHEMA",
    "build_arabic_pdf",
    "build_axis_aligned_two_run_pdf",
    "build_bare_cff_font_pdf",
    "build_cff2_cid_pdf",
    "build_cff_cid_missing_glyph_pdf",
    "build_cff_cid_missing_glyph_wrapped_pdf",
    "build_cff_cid_sparse_collision_pdf",
    "build_cff_cid_tounicode_only_collision_pdf",
    "build_cff_donor_bytes",
    "build_cff_font_pdf",
    "build_namekeyed_otf_cff_pdf",
    "build_seac_composite_donor_bytes",
    "build_truetype_glyf_donor_bytes",
    "build_declared_leading_pdf",
    "build_devicergb_run_pdf",
    "build_indent_styles_pdf",
    "build_linearized_pdf",
    "build_nonlinearized_pdf",
    "build_overflow_text",
    "build_reflow_quality_pdf",
    "build_rotated_text_pdf",
    "build_separation_run_pdf",
    "build_shrink_to_fit_pdf",
    "build_tagged_pdf",
    "build_truetype_baseline_pdf",
    "build_type1_font_pdf",
    "build_xobject_text_pdf",
]

# Machine-readable manifest of the builders. ``returns_none_when`` documents
# the host-dependency skip condition each smoke test honours.
BUILD_SCHEMA: dict[str, dict[str, object]] = {
    "build_truetype_baseline_pdf": {
        "feature": "truetype_identity_h_baseline",
        "encoding": "Identity-H",
        "font_kind": "TrueType (glyf) subset, /FontFile2",
        "deterministic": True,
        "returns_none_when": "no host TrueType font installed",
        "adversarial_for": "control / happy-path reference",
    },
    "build_cff_font_pdf": {
        "feature": "cff_opentype_outlines",
        "encoding": "Identity-H",
        "font_kind": "CFF/OpenType (Type1C) charstrings, /FontFile3 /OpenType",
        "deterministic": True,
        "returns_none_when": "never (font synthesised in-process)",
        "adversarial_for": "Tier 1.5 injection unsupported on CFF (ARY-279)",
    },
    "build_bare_cff_font_pdf": {
        "feature": "bare_cff_type1c_no_sfnt",
        "encoding": "Identity-H",
        "font_kind": "BARE CFF (Type1C) charstrings, /FontFile3 /Type1C (no sfnt wrapper)",
        "deterministic": True,
        "returns_none_when": "never (font synthesised in-process)",
        "adversarial_for": (
            "C.2 glyph-count introspection: TTFont(BytesIO(bare_cff)) raises "
            "TTLibError, so analyze_subset cannot introspect and raises, and "
            "get_fonts fabricates glyph_count from the /W dict length (1) "
            "instead of the truthful CFF charset count (4) (INV-C-10)"
        ),
    },
    "build_cff_cid_missing_glyph_pdf": {
        "feature": "cid_keyed_cff_missing_glyph",
        "encoding": "Identity-H",
        "font_kind": "CID-keyed (ROS) BARE CFF, /FontFile3 /Type1C (no sfnt)",
        "deterministic": True,
        "returns_none_when": "never (font synthesised in-process)",
        "adversarial_for": (
            "C.3 CFF injection: a genuinely CID-keyed (ROS) bare CFF carrying "
            ".notdef+A+B (CIDs 1,2) renders 'AB' but LACKS the glyph for 'C'. "
            "Editing 'AB'->'ABC' must inject C from the donor at CID==GID==3 "
            "(INV-C-11/12); pre-C.3 the C.1 gate refuses every CFF extension "
            "(font_extension_failed)"
        ),
    },
    "build_cff_cid_missing_glyph_wrapped_pdf": {
        "feature": "cid_keyed_cff_missing_glyph_sfnt_wrapped",
        "encoding": "Identity-H",
        "font_kind": "CID-keyed (ROS) sfnt-wrapped OpenType-CFF, /FontFile3 /OpenType",
        "deterministic": True,
        "returns_none_when": "never (font synthesised in-process)",
        "adversarial_for": (
            "C.3 CFF injection wrapped variant: same CID-keyed CFF as "
            "build_cff_cid_missing_glyph_pdf but embedded sfnt-wrapped (OTTO). "
            "Exercises the wrapped-vs-bare _load_cff_as_ttfont path (INV-C-11)"
        ),
    },
    "build_cff_cid_sparse_collision_pdf": {
        "feature": "cid_keyed_cff_sparse_noncontiguous_collision",
        "encoding": "Identity-H",
        "font_kind": "CID-keyed (ROS) SPARSE bare CFF, /FontFile3 /Type1C (no sfnt)",
        "deterministic": True,
        "returns_none_when": "never (font synthesised in-process)",
        "adversarial_for": (
            "C.3 INV-C-12 COLLISION: a non-contiguous CID-keyed bare CFF carries "
            "A at CID 1/GID 1 and B at CID 3/GID 2 (CID 2 skipped) so glyph "
            "count is 3 yet CID 3 is occupied. The naive injector picks "
            "new_cid=new_gid=len(glyphOrder)=3, colliding with the pre-existing "
            "cid00003 (B): donor outline lands at B's CID, charset gains a "
            "duplicate cid00003, /ToUnicode <0003>->B is overwritten by "
            "<0003>->C, get_text 'AB'->'AC' — success=True, ZERO degradations "
            "(ARY-278 1ova,ndustries no-renumber failure ported to CFF). The "
            "fix mirrors glyf: new_cid=max(len(glyphOrder), max_existing_cid+1)"
        ),
    },
    "build_cff_cid_tounicode_only_collision_pdf": {
        "feature": "cid_keyed_cff_tounicode_only_cid_collision",
        "encoding": "Identity-H",
        "font_kind": "CID-keyed (ROS) BARE CFF, /FontFile3 /Type1C (no sfnt)",
        "deterministic": True,
        "returns_none_when": "never (font synthesised in-process)",
        "adversarial_for": (
            "C.3 INV-C-12 remediation #2 TOUNICODE-ONLY-CID COLLISION: embedded "
            "charset is .notdef+cid00001(A) (max CID 1, glyph count 2) but "
            "/ToUnicode maps CID 1->A AND CID 2->Q and content renders 'AQ'. "
            "The injector's placement folds in only the charset CIDs "
            "(max_existing_cid+1=2) and len(glyphOrder)=2, NOT the "
            "ToUnicode-only CID 2 unless the caller threads "
            "min_cid=max(/ToUnicode CIDs)+1. Without it, injecting C picks "
            "new_cid=max(2,2,0)=2, colliding with CID 2: /ToUnicode <0002>->Q "
            "is remapped to <0002>->C, get_text 'AQ'->'AC' — success=True, "
            "ZERO degradations. The charset-CID sparse fixture cannot catch "
            "this (its colliding CID 3 is in the charset). Fix mirrors glyf: "
            "thread the /ToUnicode CID floor so new_cid clears both."
        ),
    },
    "build_cff2_cid_pdf": {
        "feature": "cff2_cid_out_of_scope",
        "encoding": "Identity-H",
        "font_kind": "CFF2-outlined CID font, /FontFile3 /OpenType",
        "deterministic": True,
        "returns_none_when": "never (font synthesised in-process)",
        "adversarial_for": (
            "C.3 INV-C-13 hard-fail: CFF2 charstrings cannot be injected by the "
            "slice-1 CFF(Type2) injector; extension must refuse via "
            "font_extension_failed (success=False), never crash or silent-wrong"
        ),
    },
    "build_namekeyed_otf_cff_pdf": {
        "feature": "namekeyed_cff_out_of_scope",
        "encoding": "Identity-H",
        "font_kind": "NAME-keyed (non-ROS) simple OTF/CFF, /FontFile3 /OpenType",
        "deterministic": True,
        "returns_none_when": "never (font synthesised in-process)",
        "adversarial_for": (
            "C.3 INV-C-13 hard-fail: a name-keyed (no ROS) CFF must be refused "
            "by the CID-keyed injector's hasattr(e_td,'ROS') gate "
            "(font_extension_failed)"
        ),
    },
    "build_type1_font_pdf": {
        "feature": "embedded_type1_fontfile",
        "encoding": "WinAnsi",
        "font_kind": "Type1 (PFA) program embedded as /FontFile (simple /Type1 font)",
        "deterministic": True,
        "returns_none_when": "never (Type1 program synthesised in-process)",
        "adversarial_for": (
            "C.2 glyph-count introspection: the Type1 (/FontFile) branch of "
            "fonts._introspect_embedded_font called t1Lib.T1Font() with no "
            "positional path arg, raising TypeError before t1.data was set, so "
            "it ALWAYS returned 0 — a valid Type1 font reported glyph_count 0 "
            "plus a FALSE font_subset_introspection_failed (INV-C-10)"
        ),
    },
    "build_xobject_text_pdf": {
        "feature": "form_xobject_text",
        "encoding": "Identity-H",
        "font_kind": "TrueType (glyf) subset, /FontFile2",
        "deterministic": True,
        "returns_none_when": "no host TrueType font installed",
        "adversarial_for": "text in Form XObject invoked via Do (page-only parse misses it)",
    },
    "build_arabic_pdf": {
        "feature": "arabic_rtl_script",
        "encoding": "Identity-H",
        "font_kind": "TrueType (glyf) subset of an Arabic-covering font",
        "deterministic": True,
        "returns_none_when": "no installed font covers the Arabic block",
        "adversarial_for": "non-Latin RTL codepoints (logical order, no shaping)",
    },
    "build_tagged_pdf": {
        "feature": "tagged_pdf_actualtext",
        "encoding": "Identity-H",
        "font_kind": "TrueType (glyf) subset, /FontFile2",
        "deterministic": True,
        "returns_none_when": "no host TrueType font installed",
        "adversarial_for": "StructTreeRoot + BDC/EMC marked content + /ActualText override",
    },
    "build_rotated_text_pdf": {
        "feature": "rotated_non_axis_aligned_text",
        "encoding": "Identity-H",
        "font_kind": "TrueType (glyf) subset, /FontFile2",
        "deterministic": True,
        "returns_none_when": "no host TrueType font installed",
        "adversarial_for": (
            "90deg Tm [0 -1 1 0 x y]; horizontal width-delta compensation of "
            "trailing text mis-shifts under a non-axis-aligned matrix (POS-GATE)"
        ),
    },
    "build_reflow_quality_pdf": {
        "feature": "reflow_typographic_quality",
        "encoding": "WinAnsi",
        "font_kind": "standard-14 Helvetica (no embedding)",
        "deterministic": True,
        "returns_none_when": "never (standard-14 font, no host discovery)",
        "adversarial_for": (
            "E.4 widow/orphan after reflow re-wrap; E.6 line-height compression "
            "in a short fixed-height bbox (line_break_quality_degraded / "
            "line_height_compressed honesty surfacing)"
        ),
    },
    "build_declared_leading_pdf": {
        "feature": "declared_leading_reflow",
        "encoding": "WinAnsi",
        "font_kind": "standard-14 Helvetica (no embedding)",
        "deterministic": True,
        "returns_none_when": "never (standard-14 font, no host discovery)",
        "adversarial_for": (
            "E.3 declared-leading preservation: a paragraph laid out with an "
            "explicit TL leading + T* line advances is reflowed and the engine "
            "re-synthesizes a font_size*1.2 proxy line advance, discarding the "
            "document's declared leading; plus a non-TL control and a q/Q leak "
            "guard (INV-G-8 re-emit / leak isolation)"
        ),
    },
    "build_indent_styles_pdf": {
        "feature": "paragraph_indent_styles_reflow",
        "encoding": "WinAnsi",
        "font_kind": "standard-14 Helvetica (no embedding)",
        "deterministic": True,
        "returns_none_when": "never (standard-14 font, no host discovery)",
        "adversarial_for": (
            "E.2 indent preservation: first-line / hanging / flush paragraphs "
            "whose per-line x_starts the reflow collapses to one mode left_margin, "
            "discarding first-line and hanging indents (INV-G-7 round-trip)"
        ),
    },
    "build_separation_run_pdf": {
        "feature": "separation_colorspace_reflow",
        "encoding": "WinAnsi",
        "font_kind": "standard-14 Helvetica (no embedding)",
        "deterministic": True,
        "returns_none_when": "never (standard-14 font, self-contained Separation CS)",
        "adversarial_for": (
            "Block F CORE: a /CS0 cs 0.8 scn Separation fill collapses to device "
            "gray (0.8 g) on reflow re-wrap, silently losing spot-color identity "
            "(INV-F-8 preservation / INV-F-9 honest degradation)"
        ),
    },
    "build_shrink_to_fit_pdf": {
        "feature": "shrink_to_fit_fixed_height_region",
        "encoding": "WinAnsi",
        "font_kind": "standard-14 Helvetica (no embedding)",
        "deterministic": True,
        "returns_none_when": "never (standard-14 font, no host discovery)",
        "adversarial_for": (
            "E.8 opt-in shrink-to-fit: a short fixed-height bbox holds ~one "
            "11pt line; a multi-line replacement overflows it at the original "
            "size and (fit='shrink') must binary-search the font size DOWN "
            "until it fits, emitting font_size_reduced (INV-F-10). The DEFAULT "
            "fit reproduces today's no-shrink output byte-for-byte."
        ),
    },
    "build_devicergb_run_pdf": {
        "feature": "devicergb_reflow_control",
        "encoding": "WinAnsi",
        "font_kind": "standard-14 Helvetica (no embedding)",
        "deterministic": True,
        "returns_none_when": "never (standard-14 font, no host discovery)",
        "adversarial_for": (
            "Block F CORE regression control: a 1 0 0 rg device-RGB fill already "
            "round-trips via the length-guess; Block F must not regress it (INV-F-8)"
        ),
    },
    "build_axis_aligned_two_run_pdf": {
        "feature": "axis_aligned_two_run_control",
        "encoding": "Identity-H",
        "font_kind": "TrueType (glyf) subset, /FontFile2",
        "deterministic": True,
        "returns_none_when": "no host TrueType font installed",
        "adversarial_for": (
            "control for POS-GATE: same two runs under axis-aligned "
            "Tm [1 0 0 1 x y]; compensation must remain byte-identical (INV-POS-1)"
        ),
    },
    "build_linearized_pdf": {
        "feature": "linearized_fast_web_view",
        "encoding": "WinAnsi",
        "font_kind": "standard-14 Helvetica (no embedding)",
        "deterministic": True,
        "returns_none_when": "never (standard-14 font, no host discovery)",
        "adversarial_for": (
            "A2.2 linearization preservation: a linearized (Fast Web View) input "
            "saved through the engine today is silently written non-linearized "
            "(pdf.save default), losing is_linearized with no degradation signal "
            "(INV-W-3 detect + preserve / honest linearization_dropped fallback)"
        ),
    },
    "build_nonlinearized_pdf": {
        "feature": "nonlinearized_control",
        "encoding": "WinAnsi",
        "font_kind": "standard-14 Helvetica (no embedding)",
        "deterministic": True,
        "returns_none_when": "never (standard-14 font, no host discovery)",
        "adversarial_for": (
            "A2.2 control: byte-content-identical to build_linearized_pdf but saved "
            "non-linearized; editing it must leave the output non-linearized and "
            "emit NO linearization_dropped (INV-W-3 control / no over-surfacing)"
        ),
    },
}
