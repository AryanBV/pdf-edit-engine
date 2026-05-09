"""INV-W0-10 — Worker/wire-format invariants for the simple-font Tier 1.5 path.

Five probes covering the end-to-end success canary, helper contracts, and
the cache-collision regression that pins Phase 13.2.3's `_font_dict_key`
repair. Probes pin behaviour of helpers added in Phase 13.1 and the
dispatcher branch added in Phase 13.2.

Relocated verbatim from `tests/test_simple_extension.py` per audit-charter
`test_{layer}_{id}_*.py` convention. INV-W0-10 minted as the next
collision-free W0-layer slot (INV-W0-{1..7,9} taken; INV-W0-8 reserved).
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
    extend_subset,
)
from pdf_edit_engine.locator import find
from tests._simple_font_fixture import (
    _build_simple_winansi_pdf,
    _find_ttf_for_simple_font,
    _no_ttf_simple,
)

CORPUS = Path(__file__).parent.parent / "corpus"
SIMPLE_WINANSI_PDF = CORPUS / "simple_winansi_subset.pdf"


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
# INV-W0-10 probes (5)
# ──────────────────────────────────────────────────────────────────────────


@_no_ttf_simple
def test_simple_tier_15_success_via_synthetic_fixture(tmp_path: Path) -> None:
    """INV-W0-10.1: end-to-end Tier 1.5 success on the simple-font fixture.

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
    """INV-W0-10.2: _allocate_free_bytes contract (consecutive + 127-skip + bounds)."""
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
    """INV-W0-10.3: composite glyph component walk yields injection-order list.

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
    """INV-W0-10.4: /Encoding=Name promotes to /Encoding=Dict on first /Differences add."""
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
    """INV-W0-10.5: two consecutive extensions allocate distinct bytes.

    Load-bearing on Phase 13.2.3's _font_dict_key repair and Phase 13.1's
    step 5b cache eviction. Without the chain, _used_bytes_in_encoding
    fails to see the first /Differences override and the second
    extension would re-allocate the same byte.
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
