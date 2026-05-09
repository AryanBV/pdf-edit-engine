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


def _build_word_style_fixture(out_path: Path) -> Path:
    """Build a Word-style simple-font fixture (zero widths for non-body bytes).

    Used by INV-W0-10.6/.7/.8 to exercise the heal-in-place path. Mirrors
    Microsoft Word's PDF export behavior: codepoints whose glyph isn't in
    the body subset get /Widths=0, even when the encoding map has a name
    for them. This is the precondition for the Tier 1.5 K-class bug.
    """
    built = _build_simple_winansi_pdf(out_path, zero_unused_widths=True)
    if not built:
        pytest.skip("no TrueType font available for Word-style fixture")
    return out_path

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
def test_extend_subset_heals_zero_width_standard_byte(tmp_path: Path) -> None:
    """INV-W0-10.6: chars whose standard encoding byte is in [/FirstChar, /LastChar]
    with zero pre-extension width must encode post-extension via the existing byte
    (heal in place) rather than via a high-end /Differences entry.

    Architectural root fix from 2026-05-09 Tier 1.5 K-class bug.

    Symptom (pre-fix): when Word's PDF export reserves /Widths slots for unused
    standard bytes with zero width, ``_extend_simple_tier_one_five`` allocates
    a NEW high byte for the missing char and adds a /Differences override.
    But ``encoding._build_reverse_map`` prefers the lowest byte, so the resolver
    still picks the standard byte (whose width is still 0). Check 2 in
    ``can_encode`` rejects it. Surgeon emits ``font_extension_failed``.

    Universal contract: for ANY char whose standard encoding byte is in range
    with zero pre-extension width, post-extension ``resolver.can_encode(ch)``
    MUST return ``(True, [])``.
    """
    from pdf_edit_engine.encoding import FontResolver

    work = _build_word_style_fixture(tmp_path / "k.pdf")

    pdf = open_pdf(str(work))
    try:
        page = pdf.pages[0]
        font_dict = page["/Resources"]["/Font"]["/F1"]
        fc = int(font_dict["/FirstChar"])
        lc = int(font_dict["/LastChar"])

        # Fixture precondition: byte 75 ('K' standard WinAnsi) in /Widths range with zero width.
        assert fc <= 75 <= lc, f"fixture must include byte 75 in /Widths range; got [{fc}, {lc}]"
        pre_widths = list(font_dict["/Widths"])
        assert float(pre_widths[75 - fc]) == 0.0, (
            f"Word-style fixture must have zero width for byte 75; got {pre_widths[75 - fc]}"
        )

        # Pre-extension: K must be reported missing.
        resolver = FontResolver(font_dict, font_name="F1")
        can_enc_pre, missing_pre = resolver.can_encode("K")
        assert not can_enc_pre, f"pre-extension: K should be missing; got missing={missing_pre}"

        # Run the extension.
        tier = extend_subset(pdf, page, "F1", "K")
        assert tier == "full_extension"

        # POST-EXTENSION CONTRACT: K must encode via SOME byte the resolver picks.
        resolver_after = FontResolver(font_dict, font_name="F1")
        can_enc_post, still_missing = resolver_after.can_encode("K")
        assert can_enc_post, (
            f"post-extension: can_encode('K') failed; still_missing={still_missing}. "
            "Root: extension should heal /Widths[byte 75] in place rather than "
            "allocating a new high byte that _build_reverse_map deprioritizes."
        )

        # Cleanliness: /Widths[byte 75] must be non-zero (heal in place).
        post_widths = list(font_dict["/Widths"])
        assert float(post_widths[75 - fc]) > 0.0, (
            f"/Widths[byte 75] should be non-zero after heal; got {post_widths[75 - fc]}"
        )

        # No redundant /Differences entry for /K (the heal makes it unnecessary).
        enc = font_dict.get("/Encoding")
        if isinstance(enc, pikepdf.Dictionary) and enc.get("/Differences"):
            diffs = list(enc["/Differences"])  # type: ignore[arg-type]
            for item in diffs:
                assert str(item) != "/K", (
                    f"/Differences should NOT contain /K (use existing byte 75); got {diffs}"
                )
    finally:
        pdf.close()


@_no_ttf_simple
def test_extend_subset_universal_mixed_groups(tmp_path: Path) -> None:
    """INV-W0-10.7: extend_subset handles BOTH heal-in-place AND new-byte chars
    in a single call. Universal contract — the partition logic must work
    correctly when the same call has chars in both categories.

    Group A (heal in place): chars whose standard byte is in [/FirstChar, /LastChar]
    with zero width. Examples on the Hello-World fixture (range [32, 114]):
    'K' (0x4B=75), 'M' (0x4D=77).

    Group B (allocate new byte): chars whose standard byte is OUT of range.
    Examples: 'ö' (0xF6=246), 'ø' (0xF8=248).

    All chars across both groups must be encodable post-extension.
    """
    from pdf_edit_engine.encoding import FontResolver

    work = _build_word_style_fixture(tmp_path / "mix.pdf")

    pdf = open_pdf(str(work))
    try:
        page = pdf.pages[0]
        font_dict = page["/Resources"]["/Font"]["/F1"]

        # Mix: K (group A), M (group A), ö (group B)
        chars = "KMö"
        tier = extend_subset(pdf, page, "F1", chars)
        assert tier == "full_extension"

        resolver = FontResolver(font_dict, font_name="F1")
        for ch in chars:
            can_enc, missing = resolver.can_encode(ch)
            assert can_enc, (
                f"post-extension: can_encode({ch!r}) failed; missing={missing}. "
                "Universal contract violated — partition logic must serve both groups."
            )

        # Group A bytes (75, 77) must have non-zero width post-extension.
        fc = int(font_dict["/FirstChar"])
        post_widths = list(font_dict["/Widths"])
        for std_byte in (75, 77):
            assert float(post_widths[std_byte - fc]) > 0.0, (
                f"/Widths[byte {std_byte}] should be healed (non-zero); "
                f"got {post_widths[std_byte - fc]}"
            )

        # Group B byte for ö must exist either via /Differences override OR
        # via the standard WinAnsi byte 0xF6=246 (now extended into range).
        # Either way, can_encode passed for ö above — that's the operative check.

        # Group A glyph names (K, M) must NOT appear in /Differences (cleanliness).
        enc = font_dict.get("/Encoding")
        if isinstance(enc, pikepdf.Dictionary) and enc.get("/Differences"):
            diffs = [str(d) for d in list(enc["/Differences"])]  # type: ignore[arg-type]
            for forbidden in ("/K", "/M"):
                assert forbidden not in diffs, (
                    f"/Differences must not contain {forbidden} (Group A chars heal in place); "
                    f"got {diffs}"
                )
    finally:
        pdf.close()


@_no_ttf_simple
def test_replace_succeeds_for_in_range_zero_width_char(tmp_path: Path) -> None:
    """INV-W0-10.8: end-to-end smoke test — surgeon.replace must succeed when the
    new text contains a char whose standard byte is in /Widths range with zero
    pre-extension width. Pre-fix this surfaced as ``font_extension_failed``.

    Confirms the architectural fix composes through surgeon.replace's flow:
    can_encode (pre) → extend_subset → can_encode (post) → encode + write operator.
    """
    work = _build_word_style_fixture(tmp_path / "input.pdf")
    out = tmp_path / "output.pdf"

    matches = find(str(work), "World")
    assert matches, "fixture must contain 'World'"
    # Replace 'd' with 'K' — same length, single Group A char added.
    result = replace(str(work), matches[0], "WorlK", str(out))

    degr_summary = [(d.kind, d.severity, d.detail) for d in result.fidelity_report.degradations]
    assert result.success, (
        f"expected success post-fix; got success=False, degradations={degr_summary}, "
        f"font_action={result.font_action!r}"
    )
    failed_kinds = [
        d.kind
        for d in result.fidelity_report.degradations
        if d.kind == "font_extension_failed"
    ]
    assert not failed_kinds, (
        f"expected NO font_extension_failed Degradation; got {degr_summary}"
    )
    assert result.font_action == "extended", (
        f"expected font_action='extended'; got {result.font_action!r}"
    )


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
