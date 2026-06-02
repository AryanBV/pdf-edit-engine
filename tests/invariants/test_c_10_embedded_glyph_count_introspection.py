"""INV-C-10 — truthful embedded-font glyph_count introspection per outline type.

C.2 closes two glyph-count honesty gaps that C.1's outline-table dispatch
(``fonts.classify_embedded_outline`` / ``fonts._classify_outline_table``)
makes fixable:

1. ``fonts.analyze_subset`` (via ``_analyze_from_page``) does a bare
   ``TTFont(io.BytesIO(font_bytes))`` to read ``len(getGlyphOrder())``. For a
   **bare CFF** (raw Type1C charstrings embedded as ``/FontFile3 /Type1C`` with
   NO sfnt directory) ``TTFont`` raises ``TTLibError`` ("bad sfntVersion"),
   which ``_with_fonttools_translation`` rebrands to ``FontNotFoundError``. So
   glyph-count introspection is structurally UNAVAILABLE for CFF / Type1 today
   — ``analyze_subset`` RAISES instead of returning a truthful count.

2. ``locator.get_fonts`` (via ``_build_font_info``) only loads ``TTFont`` for a
   ``/FontFile2`` (glyf) font. For a CFF / Type0 font it FABRICATES
   ``glyph_count`` from ``len(parse_cid_widths(/W))`` — but a sparse ``/W`` dict
   is NOT a glyph count (a CFF with 4 glyphs and a 1-entry ``/W`` reports
   ``glyph_count == 1``). The CLAUDE.md "check glyph count before assuming
   extension is needed" rule is therefore unreliable for CFF / Type1.

C.2 routes both through a new ``fonts.embedded_glyph_count(fd)`` public helper
(internally ``_introspect_embedded_font``) that dispatches on
``classify_embedded_outline`` and parses the REAL count per outline type —
``glyf`` → ``len(TTFont.getGlyphOrder())``, ``cff`` / ``cff2`` →
``len(cffLib.CFFFontSet[...].CharStrings)``, ``Type1`` →
``len(t1Lib.T1Font.font['CharStrings'])`` — each guarded best-effort so the
read never raises. ``analyze_subset`` stops raising on CFF and returns the
truthful count (or 0); ``get_fonts`` calls the ``fonts.py`` helper (keeping the
fontTools / cffLib / t1Lib dependency OUT of ``locator`` per the CLAUDE.md
dependency-boundary table). On a genuinely unparseable embedded binary the
best-effort READ path (``get_fonts``) yields ``glyph_count == 0`` and surfaces
a typed ``font_subset_introspection_failed`` (severity ``warning``, NOT in
``FONT_AFFECTING_KINDS``) Degradation; ``analyze_subset`` returns 0 silently
(it raises a structured error only when the font itself is absent).

INV-C-10 minted as the next collision-free C-layer (font) slot; INV-C-{1..9}
taken (``test_c_9_fontfile3_outline_dispatch.py`` is the prior max — a
repo-wide ``git grep INV-C-10`` returns 0 hits at design time).

RED today:
* ``test_inv_c_10_analyze_subset_bare_cff_returns_truthful_count`` — RED:
  ``analyze_subset`` RAISES ``FontNotFoundError`` (translated ``TTLibError``)
  on a bare CFF instead of returning the real charset count (4).
* ``test_inv_c_10_get_fonts_cff_glyph_count_is_truthful_not_w_length`` — RED:
  ``get_fonts`` reports ``glyph_count == 1`` (the ``/W`` dict length) for a
  4-glyph CFF, NOT the truthful 4.
* ``test_inv_c_10_embedded_glyph_count_helper_exists`` — RED: the public
  ``fonts.embedded_glyph_count`` helper does not exist (``AttributeError``).
* ``test_inv_c_10_corrupt_font_surfaces_introspection_failed`` — RED today
  for two independent reasons: the ``font_subset_introspection_failed`` kind is
  not yet in the ``DegradationKind`` Literal, and ``get_fonts`` has no
  degradation channel to surface it on.
"""

from __future__ import annotations

import io

import pikepdf
import pytest

import pdf_edit_engine
import pdf_edit_engine.fonts as fonts_mod
from pdf_edit_engine.errors import FontNotFoundError
from tests.corpus_builders import (
    build_bare_cff_font_pdf,
    build_cff_font_pdf,
    build_type1_font_pdf,
)
from tests.corpus_builders.type1_font import _type1_program_bytes

# The bare-CFF fixture embeds exactly these glyphs (incl .notdef). The CFF
# CharStrings index length is the truthful count; the /W array lists ONE CID.
_TRUE_CFF_GLYPH_COUNT = 4
_W_DICT_LENGTH = 1


def _opentype_cff_truth_count() -> int:
    """Derive the build_cff_font_pdf font's REAL glyph count via fontTools.

    Loads the embedded sfnt-wrapped OpenType-CFF binary with ``TTFont`` (which
    reads OpenType-CFF fine) and returns ``len(getGlyphOrder())`` — the truth
    the introspection must match. Derived (not hardcoded) so the probe stays
    correct if the builder's covered-character set changes.
    """
    import io

    import pikepdf
    from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

    pdf = pikepdf.open(io.BytesIO(build_cff_font_pdf()))
    try:
        fd = pdf.pages[0]["/Resources"]["/Font"]["/F1"]["/DescendantFonts"][0]["/FontDescriptor"]
        font_bytes = bytes(fd["/FontFile3"].read_bytes())
    finally:
        pdf.close()
    tt = TTFont(io.BytesIO(font_bytes))
    try:
        return len(tt.getGlyphOrder())
    finally:
        tt.close()


def _type1_truth_count() -> int:
    """Derive the build_type1_font_pdf font's REAL CharStrings count.

    Writes the synthesised Type1 PFA program to a temp file, parses it with
    ``t1Lib.T1Font`` (the canonical reader), and returns ``len(CharStrings)``.
    Derived (not hardcoded) so the probe tracks the builder's glyph set.
    """
    import os
    import tempfile

    from fontTools import t1Lib  # type: ignore[import-untyped]

    program = _type1_program_bytes()
    fd, tmp_path = tempfile.mkstemp(suffix=".pfa")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(program)
        t1 = t1Lib.T1Font(tmp_path)
        t1.parse()
        return len(t1.font["CharStrings"])
    finally:
        os.unlink(tmp_path)


# ── (a) RED today: analyze_subset RAISES on a bare CFF ──────────────────


def test_inv_c_10_analyze_subset_bare_cff_returns_truthful_count(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """INV-C-10.1: analyze_subset introspects a bare CFF without raising.

    Regression guard for the ``_analyze_from_page`` bare
    ``TTFont(io.BytesIO(font_bytes))`` site. A bare CFF (``/FontFile3 /Type1C``
    with no sfnt directory) cannot be parsed by ``TTFont``; today the
    ``_with_fonttools_translation`` wrapper rebrands the ``TTLibError`` to
    ``FontNotFoundError`` and the call RAISES.

    PRE-FIX (RED): ``analyze_subset`` raises ``FontNotFoundError`` — glyph-count
    introspection is structurally unavailable for CFF.
    POST-FIX (GREEN): ``analyze_subset`` dispatches on the C.1 outline-table
    classifier, parses the CFF ``CharStrings`` index via ``cffLib``, and
    returns ``glyph_count == 4`` (the truthful charset count, NOT the ``/W``
    length).
    """
    src = tmp_path / "bare_cff.pdf"
    src.write_bytes(build_bare_cff_font_pdf())

    try:
        info = pdf_edit_engine.analyze_subset(str(src), "F1")
    except FontNotFoundError as exc:  # noqa: PT017 — the raise we are pinning
        pytest.fail(
            "INV-C-10 violation: analyze_subset RAISED FontNotFoundError "
            f"({exc!r}) on a bare CFF font — glyph-count introspection is "
            "structurally unavailable for CFF/Type1 today. C.2 must dispatch "
            "on classify_embedded_outline and return the truthful CFF charset "
            "count instead of raising."
        )

    assert info.glyph_count == _TRUE_CFF_GLYPH_COUNT, (
        "INV-C-10 violation: analyze_subset must report the truthful CFF "
        f"CharStrings count ({_TRUE_CFF_GLYPH_COUNT}); got {info.glyph_count}"
    )
    assert info.glyph_count != _W_DICT_LENGTH, (
        "INV-C-10 violation: analyze_subset glyph_count must NOT be the /W "
        f"dict length ({_W_DICT_LENGTH})"
    )


# ── (b) RED today: get_fonts fabricates glyph_count from /W length ──────


def test_inv_c_10_get_fonts_cff_glyph_count_is_truthful_not_w_length(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """INV-C-10.2: get_fonts reports the REAL CFF count, not the /W length.

    Regression guard for the ``_build_font_info`` ``/W``-length fabrication. The
    bare-CFF fixture carries 4 glyphs in its CFF ``CharStrings`` index but lists
    only ONE CID in ``/W``; today ``get_fonts`` reports ``glyph_count == 1``.

    PRE-FIX (RED): ``glyph_count == 1`` (the ``/W`` dict length).
    POST-FIX (GREEN): ``glyph_count == 4`` (routed through
    ``fonts.embedded_glyph_count``; ``locator`` does NOT import cffLib).
    """
    src = tmp_path / "bare_cff.pdf"
    src.write_bytes(build_bare_cff_font_pdf())

    fonts = pdf_edit_engine.get_fonts(str(src))
    assert fonts, "fixture must expose at least one font"
    info = next((f for f in fonts if f.name == "F1"), fonts[0])

    assert info.glyph_count != _W_DICT_LENGTH, (
        "INV-C-10 violation: get_fonts FABRICATED glyph_count from the /W dict "
        f"length ({_W_DICT_LENGTH}) for a CFF/Type0 font — a sparse /W dict is "
        "NOT a glyph count."
    )
    assert info.glyph_count == _TRUE_CFF_GLYPH_COUNT, (
        "INV-C-10 violation: get_fonts must report the truthful CFF "
        f"CharStrings count ({_TRUE_CFF_GLYPH_COUNT}); got {info.glyph_count}"
    )


# ── (c) RED today: the public introspection helper does not exist ───────


def test_inv_c_10_embedded_glyph_count_helper_exists(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """INV-C-10.3: fonts.embedded_glyph_count(fd) is the locator's dep-safe route.

    ``locator`` must NOT import fontTools / cffLib / t1Lib (CLAUDE.md
    dependency-boundary table). C.2 adds a ``fonts.py`` public helper that
    introspects the truthful glyph count for ANY embedded outline type, which
    ``locator.get_fonts`` calls. This unit probe pins that the helper exists and
    returns the truthful CFF count.

    PRE-FIX (RED): ``fonts.embedded_glyph_count`` is undefined (``AttributeError``).
    """
    helper = getattr(fonts_mod, "embedded_glyph_count", None)
    assert helper is not None, (
        "INV-C-10 violation: fonts.embedded_glyph_count is not defined — it is "
        "the dependency-boundary-safe route locator.get_fonts uses to "
        "introspect the truthful glyph count without importing cffLib/t1Lib."
    )

    pdf = pikepdf.open(io.BytesIO(build_bare_cff_font_pdf()))
    try:
        _font_dict, _cid, fd = fonts_mod._get_font_objects(pdf.pages[0], "F1")
        count = helper(fd)
    finally:
        pdf.close()

    assert count == _TRUE_CFF_GLYPH_COUNT, (
        "INV-C-10 violation: fonts.embedded_glyph_count must return the "
        f"truthful CFF charset count ({_TRUE_CFF_GLYPH_COUNT}); got {count}"
    )


# ── (d) RED today: unparseable font surfaces font_subset_introspection_failed


def test_inv_c_10_corrupt_font_surfaces_introspection_failed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """INV-C-10.4: an unparseable embedded font → glyph_count 0 + degradation.

    On the best-effort READ path (``get_fonts``), a corrupt / unparseable
    embedded font binary must NOT raise: ``glyph_count`` is 0 and a typed
    ``font_subset_introspection_failed`` (severity ``warning``, NOT in
    ``FONT_AFFECTING_KINDS``) Degradation is surfaced so the caller knows the
    count is unknown rather than zero-by-truth.

    The fixture corrupts the ``/FontFile3`` bytes so neither ``TTFont`` nor
    ``cffLib`` can parse them.

    PRE-FIX (RED): the ``font_subset_introspection_failed`` kind is not yet in
    the ``DegradationKind`` Literal, AND ``get_fonts`` returns ``list[FontInfo]``
    with NO degradation channel to surface it on. Whichever the implementation
    threads (a ``FontInfo.degradations`` additive field or a sibling read-report
    return), this probe pins that the failure is surfaced and not silent.
    """
    # Build a valid bare-CFF PDF, then corrupt the embedded /FontFile3 stream.
    pdf = pikepdf.open(io.BytesIO(build_bare_cff_font_pdf()))
    buf = io.BytesIO()
    try:
        font = pdf.pages[0]["/Resources"]["/Font"]["/F1"]
        fd = font["/DescendantFonts"][0]["/FontDescriptor"]
        # Replace the CFF program with garbage that no parser can read.
        fd["/FontFile3"].write(b"\x00\x01\x02\x03not-a-font" * 4)
        pdf.save(buf)
    finally:
        pdf.close()

    src = tmp_path / "corrupt_cff.pdf"
    src.write_bytes(buf.getvalue())

    # Must not raise on the read path.
    fonts = pdf_edit_engine.get_fonts(str(src))
    assert fonts, "get_fonts must still enumerate the font on a corrupt binary"
    info = next((f for f in fonts if f.name == "F1"), fonts[0])

    assert info.glyph_count == 0, (
        "INV-C-10 violation: an unparseable embedded font must yield "
        f"glyph_count 0 (unknown), not {info.glyph_count}"
    )

    degradations = getattr(info, "degradations", None)
    assert degradations is not None, (
        "INV-C-10 violation: get_fonts has no degradation channel to surface "
        "font_subset_introspection_failed on a corrupt font — C.2 must thread "
        "one (FontInfo.degradations additive field)."
    )
    kinds = {d.kind for d in degradations}
    assert "font_subset_introspection_failed" in kinds, (
        "INV-C-10 violation: a corrupt embedded font must surface a "
        f"font_subset_introspection_failed Degradation on the read path; "
        f"kinds={kinds}"
    )
    severities = {d.severity for d in degradations if d.kind == "font_subset_introspection_failed"}
    assert severities == {"warning"}, (
        "INV-C-10 violation: font_subset_introspection_failed must be severity "
        f"'warning'; got {severities}"
    )


# ── (e) REGRESSION: sfnt-wrapped OpenType-CFF must NOT be mis-read as 0 ──


def test_inv_c_10_opentype_cff_truthful_count_no_false_flag(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """INV-C-10.5: sfnt-wrapped OpenType-CFF introspects truthfully (no flag).

    Regression guard for the ``_introspect_embedded_font`` ``cff`` branch. An
    sfnt-WRAPPED OpenType-CFF (``/FontFile3 /Subtype /OpenType``, magic
    ``b'OTTO'``) is the DOMINANT real-world CFF embedding. The pre-fix branch
    called ``CFFFontSet().decompile(BytesIO(font_bytes), None)`` DIRECTLY on the
    raw sfnt stream — valid only for a BARE CFF table — so it raised
    ``AssertionError`` (swallowed) and returned 0. ``analyze_subset`` then
    reported ``glyph_count == 0`` (truth 18) and ``get_fonts`` emitted a FALSE
    ``font_subset_introspection_failed`` for a perfectly valid font.

    PRE-FIX (RED): ``analyze_subset`` / ``get_fonts`` report ``glyph_count == 0``
    AND ``get_fonts`` surfaces a FALSE ``font_subset_introspection_failed``.
    POST-FIX (GREEN): both report the truthful count (the sfnt glyph order
    length, derived via ``TTFont``) and NO degradation is surfaced.
    """
    truth = _opentype_cff_truth_count()
    assert truth > 0, "fixture must embed at least one glyph"

    src = tmp_path / "opentype_cff.pdf"
    src.write_bytes(build_cff_font_pdf())

    info = pdf_edit_engine.analyze_subset(str(src), "F1")
    assert info.glyph_count == truth, (
        "INV-C-10 violation: analyze_subset mis-read an sfnt-wrapped "
        f"OpenType-CFF — expected the truthful glyph count ({truth}), got "
        f"{info.glyph_count} (0 == the swallowed CFFFontSet AssertionError)."
    )

    fonts = pdf_edit_engine.get_fonts(str(src))
    assert fonts, "fixture must expose at least one font"
    f1 = next((f for f in fonts if f.name == "F1"), fonts[0])
    assert f1.glyph_count == truth, (
        "INV-C-10 violation: get_fonts mis-read an sfnt-wrapped OpenType-CFF — "
        f"expected {truth}, got {f1.glyph_count}."
    )

    kinds = {d.kind for d in (f1.degradations or [])}
    assert "font_subset_introspection_failed" not in kinds, (
        "INV-C-10 violation: get_fonts surfaced a FALSE "
        "font_subset_introspection_failed for a perfectly valid OpenType-CFF "
        f"font (the introspection regression); kinds={kinds}."
    )


# ── (f) DEAD-CODE FIX: embedded Type1 (/FontFile) must count its charstrings ──


def test_inv_c_10_type1_fontfile_truthful_count_no_false_flag(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """INV-C-10.6: an embedded Type1 (/FontFile) reports its CharStrings count.

    Regression guard for the dead-code Type1 branch of
    ``_introspect_embedded_font``: it called ``t1Lib.T1Font()`` with no
    positional ``path`` arg, raising ``TypeError`` before ``t1.data`` was set,
    so it ALWAYS returned 0 — a valid Type1 font reported ``glyph_count == 0``
    plus a FALSE ``font_subset_introspection_failed``.

    PRE-FIX (RED): ``analyze_subset`` / ``get_fonts`` report ``glyph_count == 0``
    AND ``get_fonts`` surfaces a FALSE ``font_subset_introspection_failed``.
    POST-FIX (GREEN): both report the truthful CharStrings count (derived via
    ``t1Lib``) and NO degradation is surfaced.
    """
    truth = _type1_truth_count()
    assert truth > 0, "Type1 fixture must embed at least one charstring"

    src = tmp_path / "type1.pdf"
    src.write_bytes(build_type1_font_pdf())

    info = pdf_edit_engine.analyze_subset(str(src), "F1")
    assert info.glyph_count == truth, (
        "INV-C-10 violation: analyze_subset returned the dead-code 0 for an "
        f"embedded Type1 (/FontFile) font — expected {truth}, got "
        f"{info.glyph_count}."
    )
    assert info.embedded_type == "Type1", (
        f"INV-C-10 sanity: embedded_type must be 'Type1'; got {info.embedded_type}"
    )

    fonts = pdf_edit_engine.get_fonts(str(src))
    assert fonts, "fixture must expose at least one font"
    f1 = next((f for f in fonts if f.name == "F1"), fonts[0])
    assert f1.glyph_count == truth, (
        "INV-C-10 violation: get_fonts returned the dead-code 0 for an embedded "
        f"Type1 (/FontFile) font — expected {truth}, got {f1.glyph_count}."
    )

    kinds = {d.kind for d in (f1.degradations or [])}
    assert "font_subset_introspection_failed" not in kinds, (
        "INV-C-10 violation: get_fonts surfaced a FALSE "
        "font_subset_introspection_failed for a valid Type1 font (the dead-code "
        f"branch); kinds={kinds}."
    )


def test_inv_c_10_introspect_type1_bytes_unit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """INV-C-10.6b: _introspect_embedded_font('Type1') counts in-memory bytes.

    Direct unit probe on the helper so the Type1 branch is pinned independently
    of the PDF-assembly path: feed the synthesised Type1 program bytes with the
    ``'Type1'`` outline kind and assert the truthful CharStrings count.
    """
    truth = _type1_truth_count()
    count = fonts_mod._introspect_embedded_font(_type1_program_bytes(), "Type1")
    assert count == truth, (
        "INV-C-10 violation: _introspect_embedded_font('Type1') must count the "
        f"CharStrings of a valid in-memory Type1 program ({truth}); got {count} "
        "(0 == the dead-code TypeError on the no-arg t1Lib.T1Font())."
    )
