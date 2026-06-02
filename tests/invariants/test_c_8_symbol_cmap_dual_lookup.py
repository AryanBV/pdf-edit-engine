"""INV-C-8 — symbol-font cmap (3,0)/(1,0) dual-lookup coverage.

Symbol / symbolic TrueType fonts frequently expose their glyphs ONLY through
a ``(3, 0)`` "Symbol" cmap subtable (codepoints in the U+F000-F0FF PUA range
per the well-known ``0xF000 | byte`` convention) or a ``(1, 0)`` Macintosh
subtable — never the ``(3, 1)`` Windows-Unicode subtable that
``fontTools.TTFont.getBestCmap()`` consults.

``getBestCmap()`` is documented to scan only Unicode platform/encoding pairs
``(3,10) (0,6) (0,4) (3,1) (0,3) (0,2) (0,1) (0,0)`` and returns ``None`` when
none are present. ``fonts.font_has_codepoint`` builds its ``covered`` set from
that result, so before this fix it reported a symbol-mapped glyph as ABSENT
even though the outline is present in the embedded ``/FontFile2``. Downstream,
``encoding.FontResolver.can_encode`` (non-CID branch, check 3) then flagged the
character as missing.

INV-C-8 minted as the next collision-free C-layer (font extension) slot;
INV-C-{1..7} taken. Probe shape: synthesize a TrueType font whose glyphs are
reachable only via a symbol / Mac subtable, embed it as a simple-font
``/FontFile2``, and assert coverage is detected — while a control font with a
normal Unicode ``(3,1)`` cmap continues to resolve unchanged (no regression).
"""

from __future__ import annotations

import io

import pikepdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

from pdf_edit_engine.encoding import FontResolver
from pdf_edit_engine.fonts import font_has_codepoint

# Glyph codes (low byte) used by every fixture below: 0x41 -> "alpha",
# 0x42 -> "beta". The symbol cmap stores them at 0xF000 | code; the Mac and
# Unicode cmaps store them at the raw code.
_CODE_ALPHA = 0x41
_CODE_BETA = 0x42
_GLYPH_ORDER = (".notdef", "alpha", "beta")


def _build_font_bytes(platform_id: int, plat_enc_id: int, code_base: int) -> bytes:
    """Synthesize a tiny TrueType font with ONLY the given cmap subtable.

    Args:
        platform_id: cmap subtable platformID (3 = Windows, 1 = Macintosh).
        plat_enc_id: cmap subtable platEncID (0 = Symbol/Mac-Roman, 1 = BMP).
        code_base: offset added to the glyph code in the cmap (0xF000 for the
            symbol-cmap PUA convention, 0 for Mac / Unicode tables).

    Returns:
        Serialized TrueType font bytes carrying exactly one cmap subtable.
    """
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(list(_GLYPH_ORDER))
    glyphs = {}
    for gname in _GLYPH_ORDER:
        pen = TTGlyphPen(None)
        if gname != ".notdef":
            pen.moveTo((100, 0))
            pen.lineTo((100, 700))
            pen.lineTo((500, 700))
            pen.lineTo((500, 0))
            pen.closePath()
        glyphs[gname] = pen.glyph()
    fb.setupGlyf(glyphs)
    metrics = {gname: (600, 100) for gname in _GLYPH_ORDER}
    metrics[".notdef"] = (600, 0)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable(
        {"familyName": "SymTest", "styleName": "Regular", "psName": "SymTest-Regular"}
    )

    cmap_table = newTable("cmap")
    cmap_table.tableVersion = 0
    sub = CmapSubtable.newSubtable(4)
    sub.platformID = platform_id
    sub.platEncID = plat_enc_id
    sub.format = 4
    sub.language = 0
    sub.cmap = {code_base + _CODE_ALPHA: "alpha", code_base + _CODE_BETA: "beta"}
    cmap_table.tables = [sub]
    fb.font["cmap"] = cmap_table

    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=700)
    fb.setupPost()
    fb.setupMaxp()

    buf = io.BytesIO()
    fb.font.save(buf)
    return buf.getvalue()


def _build_simple_font_dict(
    pdf: pikepdf.Pdf,
    font_bytes: bytes,
    *,
    tounicode_target: int | None,
) -> pikepdf.Object:
    """Embed ``font_bytes`` as a symbolic simple-font dict in ``pdf``.

    Mirrors a symbolic TrueType font as emitted by real producers: /Flags has
    bit 3 (Symbolic) set, no /Encoding entry (the viewer uses the font's
    builtin cmap), and an optional /ToUnicode that maps the byte into the PUA.

    Args:
        pdf: open document to attach the dictionaries to.
        font_bytes: the embedded /FontFile2 binary.
        tounicode_target: PUA codepoint base for /ToUnicode (e.g. 0xF000), or
            None to omit /ToUnicode.

    Returns:
        The indirect font dictionary object.
    """
    ff = pikepdf.Stream(pdf, font_bytes)
    ff["/Length1"] = len(font_bytes)
    fd = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/SymTest-Regular"),
                "/Flags": 4,  # bit 3 = Symbolic
                "/FontBBox": pikepdf.Array([0, -200, 600, 800]),
                "/ItalicAngle": 0,
                "/Ascent": 800,
                "/Descent": -200,
                "/CapHeight": 700,
                "/StemV": 80,
                "/FontFile2": ff,
            }
        )
    )
    font_data: dict[str, object] = {
        "/Type": pikepdf.Name("/Font"),
        "/Subtype": pikepdf.Name("/TrueType"),
        "/BaseFont": pikepdf.Name("/SymTest-Regular"),
        "/FirstChar": _CODE_ALPHA,
        "/LastChar": _CODE_BETA,
        "/Widths": pikepdf.Array([600, 600]),
        "/FontDescriptor": fd,
    }
    if tounicode_target is not None:
        tu = (
            "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
            "1 begincodespacerange <00> <FF> endcodespacerange\n"
            f"2 beginbfchar <{_CODE_ALPHA:02X}> <{tounicode_target + _CODE_ALPHA:04X}> "
            f"<{_CODE_BETA:02X}> <{tounicode_target + _CODE_BETA:04X}> endbfchar\n"
            "endcmap CMapName currentdict /CMap defineresource pop end end\n"
        )
        font_data["/ToUnicode"] = pikepdf.Stream(pdf, tu.encode("latin-1"))
    return pdf.make_indirect(pikepdf.Dictionary(font_data))


def test_symbol_cmap_3_0_coverage_detected() -> None:
    """INV-C-8.1: a (3,0) Symbol-cmap glyph is reported covered.

    The glyph for byte 0x41 lives at cmap key 0xF041 in the (3,0) subtable.
    ``font_has_codepoint`` must find it both at the PUA codepoint U+F041 (how
    /ToUnicode reports it) AND at the raw codepoint U+0041 (via 0xF000 | byte).
    """
    pdf = pikepdf.Pdf.new()
    font_bytes = _build_font_bytes(3, 0, 0xF000)
    font_dict = _build_simple_font_dict(pdf, font_bytes, tounicode_target=0xF000)

    # Sanity: getBestCmap() returns None for this font, so the pre-fix
    # single-cmap path saw zero coverage.
    tt = TTFont(io.BytesIO(font_bytes))
    try:
        assert tt.getBestCmap() is None, "fixture must have NO Unicode cmap subtable"
    finally:
        tt.close()

    # PUA codepoint (as /ToUnicode reports it).
    assert font_has_codepoint(font_dict, 0xF041), (
        "(3,0) symbol cmap: glyph at 0xF041 must be detected as covered"
    )
    # Raw codepoint resolves via the 0xF000 | byte convention.
    assert font_has_codepoint(font_dict, 0x41), (
        "(3,0) symbol cmap: raw byte 0x41 must resolve via 0xF000 | byte"
    )
    # Absent glyph stays absent (no false positives).
    assert not font_has_codepoint(font_dict, 0xF043), (
        "uncovered code 0xF043 must NOT be reported covered"
    )
    pdf.close()


def test_symbol_cmap_3_0_can_encode_round_trip() -> None:
    """INV-C-8.2: can_encode succeeds for a (3,0)-only symbol font.

    End-to-end through the FontResolver non-CID coverage branch (check 3 in
    ``can_encode`` delegates to ``font_has_codepoint``). Before this fix the
    PUA char was reported missing.
    """
    pdf = pikepdf.Pdf.new()
    font_bytes = _build_font_bytes(3, 0, 0xF000)
    font_dict = _build_simple_font_dict(pdf, font_bytes, tounicode_target=0xF000)

    resolver = FontResolver(font_dict, font_name="F1")
    # FontResolver maps the WinAnsi byte 0x41 -> 'A'; the coverage check is
    # the load-bearing gate (checks 1+2 pass for 'A' since byte 0x41 is in
    # [FirstChar, LastChar] with non-zero width). Pre-fix, check 3 failed.
    can_enc, missing = resolver.can_encode("A")
    assert can_enc, f"(3,0) symbol font: can_encode('A') must succeed; missing={missing}"
    pdf.close()


def test_symbol_cmap_1_0_coverage_detected() -> None:
    """INV-C-8.3: a (1,0) Macintosh-cmap glyph is reported covered.

    The glyph for byte 0x41 lives at the raw cmap key 0x41 in the (1,0)
    subtable. ``getBestCmap()`` ignores (1,0), so the pre-fix path missed it.
    """
    pdf = pikepdf.Pdf.new()
    font_bytes = _build_font_bytes(1, 0, 0x0000)
    font_dict = _build_simple_font_dict(pdf, font_bytes, tounicode_target=None)

    tt = TTFont(io.BytesIO(font_bytes))
    try:
        assert tt.getBestCmap() is None, "fixture must have NO Unicode cmap subtable"
    finally:
        tt.close()

    assert font_has_codepoint(font_dict, 0x41), (
        "(1,0) Mac cmap: glyph at raw byte 0x41 must be detected as covered"
    )
    assert not font_has_codepoint(font_dict, 0x43), (
        "uncovered code 0x43 must NOT be reported covered"
    )
    pdf.close()


def test_unicode_cmap_lookup_not_regressed() -> None:
    """INV-C-8.4: a normal (3,1) Unicode-cmap font still resolves unchanged.

    Regression guard: the dual-lookup must consult the Unicode cmap FIRST and
    only fall through to (3,0)/(1,0). A font with the glyph at the real
    Unicode codepoint U+0041 must report covered, and an absent codepoint must
    NOT be reported covered (no spurious symbol-offset false positive).
    """
    pdf = pikepdf.Pdf.new()
    font_bytes = _build_font_bytes(3, 1, 0x0000)  # (3,1) Windows Unicode BMP
    font_dict = _build_simple_font_dict(pdf, font_bytes, tounicode_target=None)

    tt = TTFont(io.BytesIO(font_bytes))
    try:
        assert tt.getBestCmap() is not None, "control fixture must expose a Unicode cmap"
    finally:
        tt.close()

    assert font_has_codepoint(font_dict, 0x41), (
        "(3,1) Unicode cmap: U+0041 must be detected as covered (no regression)"
    )
    # U+F041 is NOT in this Unicode font and 0xF041 & 0xFF = 0x41 must not
    # cause a false positive against a Unicode subtable.
    assert not font_has_codepoint(font_dict, 0xF041), (
        "(3,1) Unicode font: U+F041 must NOT be covered — symbol offset must "
        "only apply to (3,0) subtables"
    )
    pdf.close()
