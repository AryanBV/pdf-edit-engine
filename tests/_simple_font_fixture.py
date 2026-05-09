"""Shared simple TrueType WinAnsi fixture helpers.

Used by tests/test_fonts.py and tests/test_surgeon.py for Phase 13 non-CID
Tier 1.5 extension tests (ARY-348). Mirrors tests/_identity_h_fixture.py for
the simple-font case: pikepdf-direct construction (no reportlab) so the
output is byte-stable across runs and depends only on packages already in
the project's dependency set.

The fixture builds a deterministic /TrueType + /WinAnsiEncoding (Name form,
not a /Differences dict) PDF. The simple-font extender's name->dict
promotion path is exercised when a test calls extend_subset against this
fixture and the missing glyph forces /Encoding to be promoted to a
dictionary.
"""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
import pytest


def _find_ttf_for_simple_font() -> Path | None:
    """Find a TrueType font for inline simple-font PDF construction."""
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/ARIAL.TTF"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


_no_ttf_simple = pytest.mark.skipif(
    _find_ttf_for_simple_font() is None,
    reason="no TrueType font available for inline simple-font PDF construction",
)


def _build_simple_winansi_pdf(
    out_path: Path,
    *,
    body_text: str = "Hello World",
    font_size: float = 12.0,
) -> bool:
    """Construct a deterministic /TrueType + /WinAnsiEncoding + /FontFile2 PDF.

    The PDF has one page, one font resource /F1 with:
      - /Subtype = /TrueType (NOT /Type0)
      - /Encoding = /WinAnsiEncoding (Name form, exercises promote-to-dict
        path when extend_subset adds glyphs missing from WinAnsi)
      - /FontFile2 = subsetted arial outline
      - /FirstChar = 32, /LastChar covers ASCII through the body-text range
      - /Widths array of (LastChar - FirstChar + 1) entries
      - /FontDescriptor with the standard required fields

    Args:
        out_path: Output path for the PDF.
        body_text: ASCII body text. Must contain only WinAnsi-encodable
            characters; the initial subset deliberately excludes accents so
            Phase 13.4 tests can drive Tier 1.5 extension by replacing this
            text with accented Latin characters.
        font_size: Font size in points for the body line.

    Returns:
        True if the PDF was built, False if no TTF font is available.
    """
    ttf_path = _find_ttf_for_simple_font()
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

    # Body-text codepoints determine first/last char and widths array size.
    body_cps = sorted({ord(ch) for ch in body_text})
    if not body_cps:
        raise ValueError("body_text must contain at least one character")
    first_char = 32  # space — standard simple-font convention
    last_char = max(body_cps)
    if last_char < first_char:
        last_char = first_char

    # Subset font to the body-text glyphs only (deliberately narrow so
    # accented-Latin replacements force Tier 1.5 extension).
    used_gids: set[int] = set()
    for cp in body_cps:
        gname = cp_map.get(cp)
        if gname and gname in name_to_gid:
            used_gids.add(name_to_gid[gname])
    # Always include the space glyph so the WinAnsi encoding round-trips for
    # the conventional 0x20 first-char.
    space_gname = cp_map.get(0x20)
    if space_gname and space_gname in name_to_gid:
        used_gids.add(name_to_gid[space_gname])

    sub_font = ttLib.TTFont(str(ttf_path))
    subsetter = Subsetter()
    subsetter.populate(
        glyphs=[glyph_order[gid] for gid in sorted(used_gids) if gid < len(glyph_order)]
    )
    subsetter.subset(sub_font)
    # M-11: pin head timestamps so the fixture build is byte-stable.
    # fontTools' default save behavior writes time.time() into
    # head.created and head.modified, which makes consecutive rebuilds
    # produce different byte streams. Pin both to 0 so every rebuild
    # produces the same /FontFile2 payload — required by Phase 13.4
    # double-extension probes that rely on byte-stable inputs.
    sub_font["head"].created = 0
    sub_font["head"].modified = 0
    buf = io.BytesIO()
    sub_font.save(buf)
    font_bytes = buf.getvalue()

    # Build /Widths covering [first_char, last_char]. Glyphs missing from the
    # body subset get a 0 width (legal for simple fonts; matches the Adobe
    # spec's "missing glyph" semantics).
    widths: list[int] = []
    for cp in range(first_char, last_char + 1):
        gname = cp_map.get(cp)
        if gname:
            try:
                advance = float(hmtx[gname][0])
            except (KeyError, IndexError):
                advance = 0.0
            widths.append(round(advance * 1000 / units_per_em))
        else:
            widths.append(0)

    # Encode body text as WinAnsi bytes (ASCII subset is identical to WinAnsi
    # for the printable range we use here).
    body_bytes = body_text.encode("cp1252")

    lines: list[str] = ["BT"]
    lines.append(f"/F1 {font_size} Tf")
    lines.append("1 0 0 1 72 720 Tm")
    # Use a literal-string Tj with parens. Escape any '(' ')' '\\' just in
    # case future callers pass body_text containing them.
    escaped = body_bytes.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    lines.append("(" + escaped.decode("latin-1") + ") Tj")
    lines.append("ET")
    content_stream = "\n".join(lines).encode("latin-1")

    pdf = pikepdf.Pdf.new()

    # Deterministic docinfo so the trailer /Info is identical across runs.
    pdf.docinfo["/Title"] = pikepdf.String("simple_winansi_subset")
    pdf.docinfo["/Producer"] = pikepdf.String("pdf-edit-engine test fixture")
    pdf.docinfo["/CreationDate"] = pikepdf.String("D:20260507000000Z")
    pdf.docinfo["/ModDate"] = pikepdf.String("D:20260507000000Z")

    font_stream = pikepdf.Stream(pdf, font_bytes)
    font_stream["/Length1"] = len(font_bytes)

    raw_ps = full.get("name").getDebugName(6) or "ArialMT"
    ps_name = str(raw_ps)
    # M-12 note: full /BaseFont normalization to a synthetic name (Option B
    # in the Phase D plan) would let the fixture be platform-portable, but
    # it would break ``_extend_simple_tier_one_five`` — the extender does its
    # system-font lookup by /BaseFont PostScript name, and a synthetic
    # name has no system font to source glyphs from. Eliminating the
    # system-font dependency entirely requires a vendored TTF stub
    # (Option A), which is out of scope for this commit. The probes that
    # consume this fixture remain gated behind ``_no_ttf_simple``.
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
            "/Flags": 32,  # nonsymbolic
            "/FontBBox": pikepdf.Array(bbox_1000),
            "/ItalicAngle": 0,
            "/Ascent": round(full["OS/2"].sTypoAscender * 1000 / units_per_em),
            "/Descent": round(full["OS/2"].sTypoDescender * 1000 / units_per_em),
            "/CapHeight": round(full["OS/2"].sCapHeight * 1000 / units_per_em),
            "/StemV": 80,
            "/FontFile2": font_stream,
        }
    )
    simple_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/TrueType"),
            "/BaseFont": pikepdf.Name("/" + ps_name),
            "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
            "/FirstChar": first_char,
            "/LastChar": last_char,
            "/Widths": pikepdf.Array(widths),
            "/FontDescriptor": pdf.make_indirect(font_descriptor),
        }
    )
    page_dict = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
            "/Resources": pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(simple_font)}),
                }
            ),
            "/Contents": pikepdf.Stream(pdf, content_stream),
        }
    )
    pdf.pages.append(pikepdf.Page(page_dict))

    # static_id=True yields a constant /ID array (not derived from
    # current time / random); compress_streams=False disables zlib
    # compression whose output varies between runs even on identical
    # input. Combined with the head.created/modified=0 pin above, the
    # fixture is fully byte-stable across runs.
    pdf.save(str(out_path), static_id=True, compress_streams=False)
    pdf.close()
    full.close()
    return True
