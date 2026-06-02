"""Builder: BARE CFF (Type1C) Identity-H PDF — no sfnt wrapper.

The existing :func:`build_cff_font_pdf` embeds a *full OpenType* binary
(sfnt-wrapped CFF, ``/FontFile3 /Subtype /OpenType``). ``fontTools.ttLib.TTFont``
parses that sfnt directory fine, so glyph-count introspection via ``TTFont``
already works on it — it is NOT the adversarial case for C.2.

This builder embeds a **bare CFF** table — the raw Type1C charstring program
with NO sfnt table directory — as ``/FontFile3 /Subtype /Type1C``. This is the
shape produced by Distiller, Ghostscript, and many print-pipeline tools for a
CFF CIDFont. ``TTFont(BytesIO(bare_cff))`` RAISES ``TTLibError`` ("bad
sfntVersion") because there is no sfnt header — which is exactly why
``fonts.analyze_subset`` (which does a bare ``TTFont(BytesIO(...))``) cannot
introspect the glyph count today and instead raises, and why
``locator.get_fonts`` falls back to fabricating the count from the ``/W`` dict
length. The REAL glyph count lives in the CFF ``CharStrings`` index and is
recoverable via ``fontTools.cffLib.CFFFontSet``.

The ``/W`` array deliberately lists only ONE CID width, while the embedded CFF
carries four glyphs (``.notdef`` + ``A`` + ``B`` + ``C``). So the truthful
glyph count (4) and the fabricated ``/W``-length count (1) DIVERGE, which is
the C.2 fabrication the RED probe pins.

Deterministic and host-font-free: the CFF is synthesised in-process with
``fontTools.fontBuilder``, so this builder never returns ``None``.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pikepdf

from ._common import FIXED_FONT_EPOCH, build_tounicode_cmap, emit_or_write, save_pdf_deterministic

if TYPE_CHECKING:
    from pathlib import Path

# Glyphs embedded in the CFF (the TRUE count is len(_GLYPHS) == 4, incl
# .notdef). The content stream shows CID 0x0001 ('A').
_GLYPH_CHARS = ["A", "B", "C"]
_TRUE_GLYPH_COUNT = len(_GLYPH_CHARS) + 1  # + .notdef
_UPM = 1000
_PS_NAME = "BareCFF-Regular"


def _bare_cff_table_bytes() -> bytes:
    """Synthesize a CFF font and return ONLY its raw ``CFF `` table bytes.

    Builds a full OpenType/CFF binary in-process, then extracts the raw
    ``CFF `` sfnt table — the bare Type1C program with no sfnt wrapper. This
    is the byte stream a ``/FontFile3 /Subtype /Type1C`` carries in the wild.

    Returns:
        The raw CFF table bytes (no sfnt directory). ``TTFont`` cannot parse
        these; ``cffLib.CFFFontSet`` can.
    """
    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]
    from fontTools.pens.t2CharStringPen import T2CharStringPen  # type: ignore[import-untyped]
    from fontTools.ttLib.sfnt import SFNTReader  # type: ignore[import-untyped]

    glyph_order = [".notdef", *_GLYPH_CHARS]
    cmap = {ord(c): c for c in _GLYPH_CHARS}

    def _box(width: int) -> object:
        pen = T2CharStringPen(width, None)
        pen.moveTo((50, 0))
        pen.lineTo((50, 600))
        pen.lineTo((width - 50, 600))
        pen.lineTo((width - 50, 0))
        pen.closePath()
        return pen.getCharString()

    charstrings: dict[str, object] = {".notdef": _box(500)}
    metrics: dict[str, tuple[int, int]] = {".notdef": (500, 0)}
    for c in _GLYPH_CHARS:
        charstrings[c] = _box(600)
        metrics[c] = (600, 40)

    fb = FontBuilder(_UPM, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupCFF(
        _PS_NAME,
        {"FullName": _PS_NAME, "FamilyName": "BareCFF", "Weight": "Regular"},
        charstrings,
        {},
    )
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable(
        {
            "familyName": "BareCFF",
            "styleName": "Regular",
            "psName": _PS_NAME,
            "fullName": _PS_NAME,
        }
    )
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=600)
    fb.setupPost()
    fb.font["head"].created = FIXED_FONT_EPOCH
    fb.font["head"].modified = FIXED_FONT_EPOCH

    buf = io.BytesIO()
    fb.save(buf)
    # Pull the raw 'CFF ' sfnt table out — that is the bare Type1C program.
    return SFNTReader(io.BytesIO(buf.getvalue()))["CFF "]


def build_bare_cff_font_pdf(out_path: Path | None = None) -> bytes:
    """Build an Identity-H PDF whose embedded font is a BARE CFF (Type1C).

    The ``/FontFile3`` carries a raw CFF table (``/Subtype /Type1C``) with no
    sfnt directory, and the ``/W`` array lists a single CID width while the
    CFF carries four glyphs — so the truthful glyph count (4) and the
    fabricated ``/W``-length count (1) diverge.

    Args:
        out_path: Optional file to write the PDF to. The bytes are returned
            regardless.

    Returns:
        The PDF bytes. Synthesised in-process; never returns ``None``.
    """
    cff_bytes = _bare_cff_table_bytes()

    pdf = pikepdf.Pdf.new()
    try:
        font_stream = pikepdf.Stream(pdf, cff_bytes)
        # /Type1C marks a BARE CFF program (vs /OpenType = sfnt-wrapped).
        font_stream["/Subtype"] = pikepdf.Name("/Type1C")

        font_descriptor = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/FontDescriptor"),
                    "/FontName": pikepdf.Name("/" + _PS_NAME),
                    "/Flags": 4,
                    "/FontBBox": pikepdf.Array([0, -200, 600, 800]),
                    "/ItalicAngle": 0,
                    "/Ascent": 800,
                    "/Descent": -200,
                    "/CapHeight": 600,
                    "/StemV": 80,
                    "/FontFile3": font_stream,
                }
            )
        )

        # Deliberately ONE width entry — diverges from the 4-glyph CFF so the
        # /W-length fabrication (1) != the truthful charset count (4).
        cid_font = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/CIDFontType0"),
                    "/BaseFont": pikepdf.Name("/" + _PS_NAME),
                    "/CIDSystemInfo": pikepdf.Dictionary(
                        {
                            "/Registry": pikepdf.String("Adobe"),
                            "/Ordering": pikepdf.String("Identity"),
                            "/Supplement": 0,
                        }
                    ),
                    "/FontDescriptor": font_descriptor,
                    "/DW": 1000,
                    "/W": pikepdf.Array([1, pikepdf.Array([600])]),
                    "/CIDToGIDMap": pikepdf.Name("/Identity"),
                }
            )
        )

        tounicode = build_tounicode_cmap({1: ord("A"), 2: ord("B"), 3: ord("C")})
        type0 = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/" + _PS_NAME),
                "/Encoding": pikepdf.Name("/Identity-H"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
                "/ToUnicode": pikepdf.Stream(pdf, tounicode.encode("latin-1")),
            }
        )

        # Show CIDs 0x0001 0x0002 0x0003 == 'A' 'B' 'C'.
        content = ("BT\n/F1 24 Tf\n1 0 0 1 72 720 Tm\n<000100020003> Tj\nET").encode("latin-1")

        page = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Page"),
                "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
                "/Resources": pikepdf.Dictionary(
                    {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(type0)})}
                ),
                "/Contents": pikepdf.Stream(pdf, content),
            }
        )
        pdf.pages.append(pikepdf.Page(page))
        return emit_or_write(save_pdf_deterministic(pdf), out_path)
    finally:
        pdf.close()
