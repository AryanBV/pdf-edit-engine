"""Invariant probes for the non-CID Tier 1.5 simple-font extension path.

Phase 13.4 (ARY-348). 12 probes split into:

- 5 plan-rev-5 probes (success canary, helper contracts, cache-collision
  regression).
- 7 M.6 probes from PLAN_AMENDMENTS lines 264-313 (dispatcher rejection
  failure modes + helper-level guards + corrupt-/FontFile2 surfacing).

The probes pin behaviour of helpers added in Phase 13.1 and the dispatcher
branch added in Phase 13.2. Probe 5 (`test_double_extension_no_byte_collision`)
is load-bearing on Phase 13.4's cache-deletion architecture; it tests
``extend_subset`` end-to-end without the deprecated ``_FONTFILE2_CACHE``
indirection (the module-global cache was removed in ARY-348; queries now
re-parse ``/FontFile2`` directly).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
import pytest
from fontTools.ttLib import TTFont

from pdf_edit_engine import replace
from pdf_edit_engine._pathutil import open_pdf
from pdf_edit_engine.errors import FontNotFoundError
from pdf_edit_engine.fonts import (
    _allocate_free_bytes,
    _collect_component_names,
    _extend_simple_encoding,
    _extend_simple_tier_15,
    _extend_simple_widths,
    _glyph_name_for_codepoint,
    extend_subset,
)
from pdf_edit_engine.locator import find
from tests._identity_h_fixture import _build_identity_h_pdf, _no_ttf
from tests._simple_font_fixture import (
    _build_simple_winansi_pdf,
    _find_ttf_for_simple_font,
    _no_ttf_simple,
)

CORPUS = Path(__file__).parent / "corpus"
SIMPLE_WINANSI_PDF = CORPUS / "simple_winansi_subset.pdf"
CIDFONT_SYNTH_PDF = CORPUS / "cidfont_synthetic.pdf"


# ──────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ──────────────────────────────────────────────────────────────────────────


def _ensure_simple_winansi_pdf() -> Path:
    """Build the simple-font fixture corpus PDF if missing."""
    if not SIMPLE_WINANSI_PDF.exists():
        SIMPLE_WINANSI_PDF.parent.mkdir(parents=True, exist_ok=True)
        built = _build_simple_winansi_pdf(SIMPLE_WINANSI_PDF)
        if not built:
            pytest.skip("no TrueType font available to build simple_winansi_subset.pdf")
    return SIMPLE_WINANSI_PDF


def _ensure_cidfont_synthetic_pdf() -> Path:
    """Build the Identity-H synthetic fixture corpus PDF if missing."""
    if not CIDFONT_SYNTH_PDF.exists():
        CIDFONT_SYNTH_PDF.parent.mkdir(parents=True, exist_ok=True)
        built = _build_identity_h_pdf(CIDFONT_SYNTH_PDF)
        if not built:
            pytest.skip("no TrueType font available to build cidfont_synthetic.pdf")
    return CIDFONT_SYNTH_PDF


def _make_simple_font_dict(
    pdf: pikepdf.Pdf,
    *,
    subtype: str = "/TrueType",
    base_font: str = "/ArialMT",
    encoding: object | None = None,
    first_char: int = 32,
    last_char: int = 125,
    widths_count: int | None = None,
    with_fontfile2: bool = True,
    with_fontfile3: bool = False,
    fontfile_bytes: bytes | None = None,
) -> pikepdf.Object:
    """Build an in-memory simple-font dict for synthesis-driven tests.

    Returns an indirect font dict registered in `pdf` (objgen != (0, 0))
    so callers can pass it to helpers that key on `font_dict.objgen`.
    """
    if widths_count is None:
        widths_count = last_char - first_char + 1
    widths = pikepdf.Array([500.0] * widths_count)

    fd_dict: dict[str, object] = {
        "/Type": pikepdf.Name("/FontDescriptor"),
        "/FontName": pikepdf.Name(base_font),
        "/Flags": 32,
        "/FontBBox": pikepdf.Array([-100, -100, 1100, 1100]),
        "/ItalicAngle": 0,
        "/Ascent": 750,
        "/Descent": -250,
        "/CapHeight": 700,
        "/StemV": 80,
    }
    if with_fontfile2:
        bytes_to_use = fontfile_bytes if fontfile_bytes is not None else b"dummy"
        ff_stream = pikepdf.Stream(pdf, bytes_to_use)
        ff_stream["/Length1"] = len(bytes_to_use)
        fd_dict["/FontFile2"] = ff_stream
    if with_fontfile3:
        ff3_stream = pikepdf.Stream(pdf, b"dummy_cff")
        ff3_stream["/Subtype"] = pikepdf.Name("/OpenType")
        fd_dict["/FontFile3"] = ff3_stream
    font_descriptor = pdf.make_indirect(pikepdf.Dictionary(fd_dict))

    enc_obj: object = pikepdf.Name("/WinAnsiEncoding") if encoding is None else encoding

    font_dict_data: dict[str, object] = {
        "/Type": pikepdf.Name("/Font"),
        "/Subtype": pikepdf.Name(subtype),
        "/BaseFont": pikepdf.Name(base_font),
        "/Encoding": enc_obj,
        "/FirstChar": first_char,
        "/LastChar": last_char,
        "/Widths": widths,
        "/FontDescriptor": font_descriptor,
    }
    return pdf.make_indirect(pikepdf.Dictionary(font_dict_data))


# ──────────────────────────────────────────────────────────────────────────
# Plan rev 5 probes (5)
# ──────────────────────────────────────────────────────────────────────────


@_no_ttf_simple
def test_simple_tier_15_success_via_synthetic_fixture(tmp_path: Path) -> None:
    """Probe 1: end-to-end Tier 1.5 success on the simple-font fixture.

    Replaces ASCII body text with accented Latin so the simple-font Tier
    1.5 path runs in full: dispatcher → injection → /Encoding promotion
    → /Widths bump → resolver eviction → second can_encode succeeds.
    """
    src = _ensure_simple_winansi_pdf()
    work = tmp_path / "input.pdf"
    shutil.copy(src, work)
    out = tmp_path / "output.pdf"

    matches = find(str(work), "World")
    assert matches, "expected to find 'World' in fixture"
    result = replace(str(work), matches[0], "Wörld", str(out))

    degr_summary = [(d.kind, d.severity, d.detail) for d in result.fidelity_report.degradations]
    assert result.success, (
        f"expected Tier 1.5 success-path; got success=False, "
        f"warnings={result.warnings}, degradations={degr_summary}"
    )
    assert result.font_action == "extended", (
        f"expected font_action='extended' after Tier 1.5; got {result.font_action!r}"
    )
    kinds = [d.kind for d in result.fidelity_report.degradations]
    assert any(k in {"font_coverage_substituted", "font_coverage_extended"} for k in kinds), (
        f"expected coverage Degradation; got kinds={kinds}"
    )


def test_allocate_free_bytes_deterministic_and_consecutive() -> None:
    """Probe 2: pin _allocate_free_bytes contract (consecutive + 127-skip + bounds)."""
    # Same args twice → same return (deterministic ordering)
    assert _allocate_free_bytes(set(), 2, last_char=122) == [123, 124]
    assert _allocate_free_bytes(set(), 2, last_char=122) == [123, 124]

    # 127 (DEL) is skipped; allocation continues at 128
    assert _allocate_free_bytes({127}, 2, last_char=125) == [126, 128]

    # Edge: last available byte is 255
    assert _allocate_free_bytes(set(), 1, last_char=254) == [255]

    # Exhaustion: no slots left above last_char=255
    with pytest.raises(FontNotFoundError):
        _allocate_free_bytes(set(), 1, last_char=255)


@_no_ttf_simple
def test_collect_component_names_resolves_composites() -> None:
    """Probe 3: composite glyph component walk yields injection-order list.

    Loads arial.ttf (or platform equivalent) and finds a real composite
    glyph in its glyf table — synthesizing one inline would require
    fabricating a TrueType binary which exceeds the bounded LOC budget
    for a probe.
    """
    ttf = _find_ttf_for_simple_font()
    assert ttf is not None, "_no_ttf_simple should have skipped"
    font = TTFont(str(ttf))
    try:
        glyf = font["glyf"]
        # Find first real composite glyph (accented Latin like Aacute,
        # Ccedilla, etc. are usually composites in Arial)
        composite_name: str | None = None
        for gname in font.getGlyphOrder():
            try:
                g = glyf[gname]
            except KeyError:
                continue
            if hasattr(g, "isComposite") and g.isComposite():
                composite_name = gname
                break
        assert composite_name is not None, "no composite glyph found in test font"
        components = _collect_component_names(glyf[composite_name], font)
        assert isinstance(components, list)
        assert len(components) >= 1, (
            f"expected at least 1 component for composite {composite_name!r}; got {components}"
        )

        # Simple (non-composite) glyph — '.notdef' is always simple
        simple = glyf[".notdef"]
        assert _collect_component_names(simple, font) == []
    finally:
        font.close()


def test_promote_encoding_name_to_dict() -> None:
    """Probe 4: /Encoding=Name gets promoted to /Encoding=Dict on first /Differences add."""
    pdf = pikepdf.Pdf.new()
    font_dict = _make_simple_font_dict(pdf, encoding=pikepdf.Name("/WinAnsiEncoding"))
    _extend_simple_encoding(pdf, font_dict, [(123, "oslash", 500.0)])

    enc = font_dict["/Encoding"]
    assert isinstance(enc, pikepdf.Dictionary), f"expected Dictionary, got {type(enc)}"
    assert enc["/Type"] == pikepdf.Name("/Encoding")
    assert enc["/BaseEncoding"] == pikepdf.Name("/WinAnsiEncoding")
    diffs = list(enc["/Differences"])
    assert len(diffs) == 2
    assert int(diffs[0]) == 123
    assert diffs[1] == pikepdf.Name("/oslash")


@_no_ttf_simple
def test_double_extension_no_byte_collision(tmp_path: Path) -> None:
    """Probe 5: two consecutive extensions allocate distinct bytes.

    Load-bearing on Phase 13.4's cache-deletion architecture: the
    deprecated ``_FONTFILE2_CACHE`` indirection is gone, so
    ``_used_bytes_in_encoding`` reads the first /Differences override
    directly from the live font dict on the second call. Without that
    direct read, the second extension would re-allocate the same byte.
    """
    src = _ensure_simple_winansi_pdf()
    work = tmp_path / "double.pdf"
    shutil.copy(src, work)

    pdf = open_pdf(str(work))
    page = pdf.pages[0]

    # First extension
    _ = extend_subset(pdf, page, "F1", "ø")
    fd1 = page["/Resources"]["/Font"]["/F1"]
    enc1 = fd1["/Encoding"]
    assert isinstance(enc1, pikepdf.Dictionary)
    diffs1 = list(enc1["/Differences"])
    first_byte = int(diffs1[0])

    # Second extension — different codepoint
    _ = extend_subset(pdf, page, "F1", "ü")
    fd2 = page["/Resources"]["/Font"]["/F1"]
    enc2 = fd2["/Encoding"]
    assert isinstance(enc2, pikepdf.Dictionary)
    diffs2 = list(enc2["/Differences"])

    # /Differences now lists [byte_a /name_a byte_b /name_b ...]
    bytes_used = [int(item) for item in diffs2 if not str(item).startswith("/")]
    assert len(bytes_used) >= 2, f"expected at least 2 byte slots in /Differences; got {diffs2}"
    assert len(set(bytes_used)) == len(bytes_used), f"byte collision detected: {bytes_used}"
    assert first_byte in bytes_used
    second_byte = next(b for b in bytes_used if b != first_byte)
    assert second_byte != first_byte


# ──────────────────────────────────────────────────────────────────────────
# M.6 probes (7) — PLAN_AMENDMENTS lines 264-313
# ──────────────────────────────────────────────────────────────────────────


def test_extend_subset_rejects_type1() -> None:
    """Probe 6 (M.6): Type1 dispatcher branch raises FontNotFoundError.

    Synthesises a /Type1 font dict (with /FontDescriptor + /FontFile so
    the dispatcher gets past `_get_font_objects` and reaches the subtype
    switch). The corpus's complex_contract.pdf uses base14 Helvetica
    which has no /FontDescriptor — the dispatcher fails earlier with a
    different message — so synthesis is the correct shape for this
    branch.
    """
    pdf = pikepdf.Pdf.new()
    # Build a /Type1 font dict — FontDescriptor present, /FontFile (Type1)
    # so the descriptor passes initial validation.
    page_dict = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
            "/Resources": pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": _make_simple_font_dict(
                                pdf,
                                subtype="/Type1",
                                base_font="/Helvetica",
                                with_fontfile2=True,
                            )
                        }
                    ),
                }
            ),
            "/Contents": pikepdf.Stream(pdf, b""),
        }
    )
    pdf.pages.append(pikepdf.Page(page_dict))
    page = pdf.pages[0]

    with pytest.raises(FontNotFoundError, match="Type1"):
        extend_subset(pdf, page, "F1", "ø")


def test_extend_subset_rejects_truetype_with_fontfile3() -> None:
    """Probe 7 (M.6): /TrueType + /FontFile3 dispatcher branch raises FontNotFoundError.

    Mirrors the dispatcher's defensive rejection — a /Subtype=/TrueType
    font dict that carries /FontFile3 (CFF/OpenType outlines) cannot be
    extended because Tier 1.5 requires a /FontFile2 glyf table. The
    dispatcher rejects before reaching `_extend_simple_tier_15`.
    """
    pdf = pikepdf.Pdf.new()
    page_dict = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
            "/Resources": pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": _make_simple_font_dict(
                                pdf,
                                with_fontfile2=False,
                                with_fontfile3=True,
                            )
                        }
                    ),
                }
            ),
            "/Contents": pikepdf.Stream(pdf, b""),
        }
    )
    pdf.pages.append(pikepdf.Page(page_dict))
    page = pdf.pages[0]

    with pytest.raises(FontNotFoundError, match="FontFile3"):
        extend_subset(pdf, page, "F1", "ø")


def test_simple_tier_15_raises_on_missing_fontfile2() -> None:
    """Probe 8 (M.6): _extend_simple_tier_15 raises when /FontFile2 absent."""
    pdf = pikepdf.Pdf.new()
    font_dict = _make_simple_font_dict(pdf, with_fontfile2=False)
    fd = font_dict["/FontDescriptor"]

    with pytest.raises(FontNotFoundError, match="/FontFile2"):
        _extend_simple_tier_15(pdf, font_dict, fd, additional_chars="ø")


@_no_ttf_simple
def test_simple_tier_15_raises_on_missing_system_font_no_fallback(tmp_path: Path) -> None:
    """Probe 9 (M.6): no system font + no full_font_path → FontNotFoundError.

    Strategy: load the real synthetic fixture (so /FontFile2 parses), then
    rename ``/BaseFont`` in-memory to a clearly-fake name that is not
    installed on any host and has no metric-equivalent mapping. The natural
    ``_find_font_with_origin`` lookup then returns None and the
    no-fallback branch fires. No monkeypatch — the prior version of this
    probe used three overlapping monkeypatches on
    ``sf_mod._find_font_with_origin`` which left state leaking into
    subsequent tests (CID extension paths failing because the patched
    no-op was somehow surviving teardown). Editing the font dict
    directly is the root fix: it exercises the same branch via the
    real lookup mechanism, with no global mutation.
    """
    src = _ensure_simple_winansi_pdf()
    work = tmp_path / "no_system_font.pdf"
    shutil.copy(src, work)
    pdf = open_pdf(str(work))
    page = pdf.pages[0]
    font_dict = page["/Resources"]["/Font"]["/F1"]
    fd = font_dict["/FontDescriptor"]

    # Rename to a name that is not installed and has no metric equivalent
    # in system_fonts._METRIC_EQUIVALENTS.
    font_dict["/BaseFont"] = pikepdf.Name("/NoSuchFontXyzzy12345")

    with pytest.raises(FontNotFoundError):
        _extend_simple_tier_15(pdf, font_dict, fd, additional_chars="ø", full_font_path=None)


def test_extend_simple_widths_gap_fills_skipped_byte() -> None:
    """Probe 10 (M.6): _extend_simple_widths gap-fills 127-skip slot with 0.

    Per PLAN_AMENDMENTS line 301 verbatim.
    """
    pdf = pikepdf.Pdf.new()
    font_dict = _make_simple_font_dict(
        pdf,
        first_char=32,
        last_char=125,
        widths_count=94,  # 125 - 32 + 1
    )
    _extend_simple_widths(font_dict, [(126, "a", 500.0), (128, "b", 600.0)])

    widths = list(font_dict["/Widths"])
    assert len(widths) == 97  # 128 - 32 + 1
    assert float(widths[127 - 32]) == 0.0  # gap-fill at byte 127's slot
    assert int(font_dict["/LastChar"]) == 128
    assert float(widths[126 - 32]) == 500.0
    assert float(widths[128 - 32]) == 600.0


def test_glyph_name_for_codepoint_agl_and_fallback() -> None:
    """Probe 11 (M.6): AGL hits + uniXXXX fallback for non-AGL codepoints."""
    assert _glyph_name_for_codepoint(ord("A")) == "A"
    assert _glyph_name_for_codepoint(ord("ø")) == "oslash"
    assert _glyph_name_for_codepoint(ord("é")) == "eacute"
    # Private-use (no AGL): canonical uniXXXX form (4 hex digits)
    assert _glyph_name_for_codepoint(0xE000) == "uniE000"
    # Beyond BMP — name uses uni prefix; format is "uniXXXX" with the
    # codepoint as hex (per Phase 13.1 _glyph_name_for_codepoint impl).
    name_beyond_bmp = _glyph_name_for_codepoint(0x10080)
    assert name_beyond_bmp.startswith("uni")


@_no_ttf
def test_corrupt_fontfile2_surfaces_font_extension_failed(tmp_path: Path) -> None:
    """Probe 12 (M.6): corrupt /FontFile2 → EditResult.success=False + degradation.

    PLAN_AMENDMENTS line 305: TTLibError flows through _FONT_EXTEND_FAIL_EXCS
    and surfaces as a `font_extension_failed` Degradation, NOT as a raised
    FontNotFoundError. Asserts both `success=False` and the degradation kind.
    """
    src = _ensure_cidfont_synthetic_pdf()
    work = tmp_path / "corrupt.pdf"
    shutil.copy(src, work)

    # Corrupt /FontFile2 by replacing with garbage bytes.
    pdf = pikepdf.Pdf.open(str(work), allow_overwriting_input=True)
    page = pdf.pages[0]
    fd_top = page["/Resources"]["/Font"]["/F1"]
    desc_font = fd_top["/DescendantFonts"][0]
    desc_font["/FontDescriptor"]["/FontFile2"] = pdf.make_stream(b"\x00" * 1024)
    pdf.save(str(work))
    pdf.close()

    # Trigger a missing-glyph path (CJK) so the CID Tier 1.5 code path
    # tries to load /FontFile2 and TTLibError surfaces.
    matches = find(str(work), "Acme")
    assert matches, "expected to find 'Acme' in synthetic CID PDF"
    out = tmp_path / "out.pdf"
    result = replace(str(work), matches[0], "中", str(out))

    assert result.success is False, (
        f"expected success=False on corrupt /FontFile2; got success={result.success}"
    )
    kinds = [d.kind for d in result.fidelity_report.degradations]
    assert "font_extension_failed" in kinds, (
        f"expected font_extension_failed Degradation; got kinds={kinds}"
    )
