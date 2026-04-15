"""Shared Identity-H CIDFont fixture helpers.

Used by tests/test_surgeon.py (ARY-276 regression tests) and
tests/test_fonts.py (ARY-278 Tier 1.5 in-place glyph injection tests).

Builds synthetic Identity-H PDFs in-process from a system TrueType font,
with configurable subset narrowness and content-stream emission patterns
so each test can target specific code paths.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

import pikepdf
import pytest

from pdf_edit_engine.locator import find

if TYPE_CHECKING:
    from pdf_edit_engine.models import TextMatch


def _find_ttf_for_cidfont() -> Path | None:
    """Find a TrueType font for inline Identity-H PDF construction."""
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/ARIAL.TTF"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


_no_ttf = pytest.mark.skipif(
    _find_ttf_for_cidfont() is None,
    reason="no TrueType font available for inline Identity-H PDF construction",
)


def _title_match(pdf_path: str, text: str) -> TextMatch:
    """Return the match for `text` with the largest font size (the title)."""
    matches = find(pdf_path, text)
    assert matches, f"No match for {text!r} in {pdf_path}"
    return max(matches, key=lambda m: m.characters[0].font_size)


def _build_identity_h_pdf(
    out_path: Path,
    *,
    title_text: str = "Acme Corporation",
    body_text: str = "This is body text with Acme Corporation in it.",
    extra_corpus: str = "Nova Industries Worldwide",
    title_pattern: str = "per_glyph_tm",
    title_font_size: float = 24.0,
    body_font_size: float = 12.0,
    omit_chars_from_subset: str = "",
) -> bool:
    """Construct an Identity-H PDF with a configurable title emission pattern.

    Args:
        out_path: Output path.
        title_text: Text for the 24pt title line.
        body_text: Text for the 12pt body line (always single Tj).
        extra_corpus: Additional characters to include in the font subset so
            that replacements can succeed without triggering Tier 2 extension.
        title_pattern: One of "single_tj", "per_glyph_tm", "per_char_tj_array",
            "multi_char_tj_array".
        title_font_size: Font size for the title line.
        body_font_size: Font size for the body line.
        omit_chars_from_subset: Characters to deliberately exclude from the
            font subset (used to test Tier 2 extension triggering).

    Returns:
        True if the PDF was built, False if no TTF font is available.
    """
    ttf_path = _find_ttf_for_cidfont()
    if ttf_path is None:
        return False

    from fontTools import ttLib
    from fontTools.subset import Subsetter

    full = ttLib.TTFont(str(ttf_path))
    cmap_table = full["cmap"]
    cp_map: dict[int, str] = {}
    for table in cmap_table.tables:
        if table.platformID == 3 and table.platEncID == 1:
            cp_map = table.cmap
            break
    glyph_order = full.getGlyphOrder()
    name_to_gid = {n: i for i, n in enumerate(glyph_order)}
    hmtx = full["hmtx"]
    units_per_em = full["head"].unitsPerEm

    corpus = set(title_text + body_text + extra_corpus) - set(omit_chars_from_subset)
    cp_to_gid: dict[int, int] = {}
    used_gids: set[int] = set()
    for ch in sorted(corpus):
        cp = ord(ch)
        gname = cp_map.get(cp)
        if gname and gname in name_to_gid:
            gid = name_to_gid[gname]
            cp_to_gid[cp] = gid
            used_gids.add(gid)

    sub_font = ttLib.TTFont(str(ttf_path))
    subsetter = Subsetter()
    subsetter.populate(
        glyphs=[glyph_order[gid] for gid in sorted(used_gids) if gid < len(glyph_order)]
    )
    subsetter.subset(sub_font)
    buf = io.BytesIO()
    sub_font.save(buf)
    font_bytes = buf.getvalue()

    w_flat: list[object] = []
    for gid in sorted(used_gids):
        gname = glyph_order[gid] if gid < len(glyph_order) else ".notdef"
        try:
            advance = float(hmtx[gname][0])
        except (KeyError, IndexError):
            advance = 500.0
        w_1000 = round(advance * 1000 / units_per_em)
        w_flat.append(gid)
        w_flat.append(pikepdf.Array([w_1000]))

    bfchar_lines = [f"<{cp_to_gid[cp]:04X}> <{cp:04X}>" for cp in sorted(cp_to_gid)]
    tounicode_str = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\nbegincmap\n"
        "/CIDSystemInfo\n"
        "<< /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n"
        "/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
    )
    for j in range(0, len(bfchar_lines), 100):
        chunk = bfchar_lines[j : j + 100]
        tounicode_str += f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar\n"
    tounicode_str += "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"

    def encode_text(text: str) -> bytes:
        return bytes(
            b
            for cp in (ord(c) for c in text)
            for b in [
                (cp_to_gid.get(cp, 0) >> 8) & 0xFF,
                cp_to_gid.get(cp, 0) & 0xFF,
            ]
        )

    def text_advance(text: str, size: float) -> float:
        total = 0.0
        for ch in text:
            gid = cp_to_gid.get(ord(ch), 0)
            gname = glyph_order[gid] if gid < len(glyph_order) else ".notdef"
            try:
                raw = float(hmtx[gname][0])
            except (KeyError, IndexError):
                raw = 500.0
            total += raw * size / units_per_em
        return total

    lines: list[str] = ["BT"]
    lines.append(f"/F1 {title_font_size} Tf")
    title_x = 72.0
    title_y = 730.0

    if title_pattern == "single_tj":
        lines.append(f"1 0 0 1 {title_x} {title_y} Tm")
        lines.append(f"<{encode_text(title_text).hex().upper()}> Tj")
    elif title_pattern == "per_glyph_tm":
        cursor_x = title_x
        i = 0
        while i < len(title_text):
            cluster = title_text[i : i + 2]
            lines.append(f"1 0 0 1 {cursor_x:.4f} {title_y} Tm")
            lines.append(f"<{encode_text(cluster).hex().upper()}> Tj")
            cursor_x += text_advance(cluster, title_font_size)
            i += 2
    elif title_pattern == "per_char_tj_array":
        lines.append(f"1 0 0 1 {title_x} {title_y} Tm")
        parts = ["["]
        for ch in title_text:
            parts.append(f"<{encode_text(ch).hex().upper()}>")
        parts.append("] TJ")
        lines.append("".join(parts))
    elif title_pattern == "multi_char_tj_array":
        lines.append(f"1 0 0 1 {title_x} {title_y} Tm")
        chunks = ["Acm", "e Co", "rpo", "ration"]
        parts = ["["]
        for j, chunk in enumerate(chunks):
            parts.append(f"<{encode_text(chunk).hex().upper()}>")
            if j < len(chunks) - 1:
                parts.append(" -15 ")
        parts.append("] TJ")
        lines.append("".join(parts))
    else:
        raise ValueError(f"Unknown title_pattern: {title_pattern}")

    lines.append(f"/F1 {body_font_size} Tf")
    lines.append("1 0 0 1 72 680 Tm")
    lines.append(f"<{encode_text(body_text).hex().upper()}> Tj")
    lines.append("ET")
    content_stream = "\n".join(lines).encode("latin-1")

    pdf = pikepdf.Pdf.new()
    font_stream = pikepdf.Stream(pdf, font_bytes)
    font_stream["/Length1"] = len(font_bytes)

    raw_ps = full.get("name").getDebugName(6) or "ArialMT"
    ps_name = str(raw_ps)
    bbox = [
        full["head"].xMin,
        full["head"].yMin,
        full["head"].xMax,
        full["head"].yMax,
    ]
    bbox_1000 = [round(v * 1000 / units_per_em) for v in bbox]

    font_descriptor = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/" + ps_name),
            "/Flags": 4,
            "/FontBBox": pikepdf.Array(bbox_1000),
            "/ItalicAngle": 0,
            "/Ascent": round(full["OS/2"].sTypoAscender * 1000 / units_per_em),
            "/Descent": round(full["OS/2"].sTypoDescender * 1000 / units_per_em),
            "/CapHeight": round(full["OS/2"].sCapHeight * 1000 / units_per_em),
            "/StemV": 80,
            "/FontFile2": font_stream,
        }
    )
    cid_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/BaseFont": pikepdf.Name("/" + ps_name),
            "/CIDSystemInfo": pikepdf.Dictionary(
                {
                    "/Registry": pikepdf.String("Adobe"),
                    "/Ordering": pikepdf.String("Identity"),
                    "/Supplement": 0,
                }
            ),
            "/FontDescriptor": pdf.make_indirect(font_descriptor),
            "/DW": 1000,
            "/W": pikepdf.Array(w_flat),
            "/CIDToGIDMap": pikepdf.Name("/Identity"),
        }
    )
    tounicode_stream = pikepdf.Stream(pdf, tounicode_str.encode("latin-1"))
    type0_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/BaseFont": pikepdf.Name("/" + ps_name),
            "/Encoding": pikepdf.Name("/Identity-H"),
            "/DescendantFonts": pikepdf.Array([pdf.make_indirect(cid_font)]),
            "/ToUnicode": tounicode_stream,
        }
    )
    page_dict = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
            "/Resources": pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(type0_font)}),
                }
            ),
            "/Contents": pikepdf.Stream(pdf, content_stream),
        }
    )
    pdf.pages.append(pikepdf.Page(page_dict))
    pdf.save(str(out_path))
    pdf.close()
    full.close()
    return True
