"""INV-M-6 — Module/dispatcher invariants for `extend_subset` rejections.

Seven probes covering dispatcher rejection failure modes (PLAN_AMENDMENTS
M.6, lines 264-313): subtype refusals, /FontFile2/3 absence/presence,
helper-level guards, and corrupt-/FontFile2 surfacing through
`_FONT_EXTEND_FAIL_EXCS` as a `font_extension_failed` Degradation.

Relocated verbatim from `tests/test_simple_extension.py` per audit-charter
`test_{layer}_{id}_*.py` convention. INV-M-6 minted as the next
collision-free M-layer slot (INV-M-{1..5} taken).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine import replace
from pdf_edit_engine.errors import FontNotFoundError
from pdf_edit_engine.fonts import (
    _extend_simple_tier_one_five,
    _extend_simple_widths,
    _glyph_name_for_codepoint,
    extend_subset,
)
from pdf_edit_engine.locator import find
from tests._identity_h_fixture import _build_identity_h_pdf, _no_ttf
from tests._simple_font_fixture import (
    _build_simple_winansi_pdf,
    _no_ttf_simple,
)

CORPUS = Path(__file__).parent.parent / "corpus"
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
# INV-M-6 probes (7) — PLAN_AMENDMENTS lines 264-313
# ──────────────────────────────────────────────────────────────────────────


def test_extend_subset_rejects_type1() -> None:
    """INV-M-6.1: Type1 dispatcher branch raises FontNotFoundError.

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
    """INV-M-6.2: /TrueType + /FontFile3 dispatcher branch raises FontNotFoundError.

    Mirrors the dispatcher's defensive rejection — a /Subtype=/TrueType
    font dict that carries /FontFile3 (CFF/OpenType outlines) cannot be
    extended because Tier 1.5 requires a /FontFile2 glyf table. The
    dispatcher rejects before reaching `_extend_simple_tier_one_five`.
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
    """INV-M-6.3: _extend_simple_tier_one_five raises when /FontFile2 absent."""
    pdf = pikepdf.Pdf.new()
    font_dict = _make_simple_font_dict(pdf, with_fontfile2=False)
    fd = font_dict["/FontDescriptor"]

    with pytest.raises(FontNotFoundError, match="/FontFile2"):
        _extend_simple_tier_one_five(pdf, font_dict, fd, additional_chars="ø")


@_no_ttf_simple
def test_simple_tier_15_raises_on_missing_system_font_no_fallback(tmp_path: Path) -> None:
    """INV-M-6.4: no system font + no full_font_path → FontNotFoundError.

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
    from pdf_edit_engine._pathutil import open_pdf

    pdf = open_pdf(str(work))
    page = pdf.pages[0]
    font_dict = page["/Resources"]["/Font"]["/F1"]
    fd = font_dict["/FontDescriptor"]

    # Rename to a name that is not installed and has no metric equivalent
    # in system_fonts._METRIC_EQUIVALENTS.
    font_dict["/BaseFont"] = pikepdf.Name("/NoSuchFontXyzzy12345")

    with pytest.raises(FontNotFoundError):
        _extend_simple_tier_one_five(pdf, font_dict, fd, additional_chars="ø", full_font_path=None)


def test_extend_simple_widths_gap_fills_skipped_byte() -> None:
    """INV-M-6.5: _extend_simple_widths gap-fills 127-skip slot with 0.

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
    """INV-M-6.6: AGL hits + uniXXXX fallback for non-AGL codepoints."""
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
    """INV-M-6.7: corrupt /FontFile2 → EditResult.success=False + degradation.

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
