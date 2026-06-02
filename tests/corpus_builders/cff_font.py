"""Builder: CFF / OpenType-CFF (Type1C) Identity-H PDF.

The engine's Tier 1.5 in-place glyph injection only understands ``glyf``
(TrueType) outlines; a font whose ``/FontFile3`` carries CFF (Type1C)
charstrings is the documented unsupported case (``docs/font-pipeline.md``
"CFF / OpenType outlines are not supported", ARY-279). This builder
manufactures exactly that adversarial input.

Rather than depend on a host OpenType-CFF font (only one ships on a stock
Windows install and none is guaranteed on CI / Linux / macOS), it
synthesises a tiny, fully self-contained CFF font in-process with
``fontTools.fontBuilder.FontBuilder``. Each glyph is a simple filled box,
so the font renders visible marks while staying deterministic and
dependency-free.

The font is embedded as ``/FontFile3`` with ``/Subtype /OpenType`` inside a
Type0 / CIDFontType2 / Identity-H chain — the structure a CFF-bearing
CIDFont uses in the wild.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pikepdf

from ._common import FIXED_FONT_EPOCH, build_tounicode_cmap, emit_or_write, save_pdf_deterministic

if TYPE_CHECKING:
    from pathlib import Path

_TEXT = "CFF Outline Sample 2026"
_UPM = 1000
_PS_NAME = "CorpusCFF-Regular"


def _build_cff_font_bytes(chars: list[str]) -> tuple[bytes, dict[int, int], dict[int, int]]:
    """Synthesize a minimal CFF/OpenType font covering ``chars``.

    Args:
        chars: The distinct characters that must be encoded.

    Returns:
        Tuple of (font bytes, codepoint→GID map, GID→advance-in-1000 map).
    """
    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]
    from fontTools.pens.t2CharStringPen import T2CharStringPen  # type: ignore[import-untyped]

    # Assign a glyph name + GID to each distinct codepoint, GID 0 == .notdef.
    glyph_order = [".notdef"]
    cmap: dict[int, str] = {}
    cp_to_gid: dict[int, int] = {}
    for cp in sorted({ord(c) for c in chars}):
        gname = f"cid{len(glyph_order):04d}"
        cp_to_gid[cp] = len(glyph_order)
        cmap[cp] = gname
        glyph_order.append(gname)

    def _box_charstring(width: int, *, filled: bool) -> object:
        pen = T2CharStringPen(width, None)
        if filled:
            pen.moveTo((60, 0))
            pen.lineTo((60, 680))
            pen.lineTo((width - 60, 680))
            pen.lineTo((width - 60, 0))
            pen.closePath()
        return pen.getCharString()

    charstrings: dict[str, object] = {".notdef": _box_charstring(500, filled=False)}
    advances_1000: dict[int, int] = {0: 500}
    metrics: dict[str, tuple[int, int]] = {".notdef": (500, 0)}
    for cp, gid in cp_to_gid.items():
        gname = glyph_order[gid]
        # Space is blank; everything else is a 600-unit box.
        is_space = cp == 0x20
        width = 300 if is_space else 600
        charstrings[gname] = _box_charstring(width, filled=not is_space)
        advances_1000[gid] = width
        metrics[gname] = (width, 0 if is_space else 60)

    fb = FontBuilder(_UPM, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupCFF(
        _PS_NAME,
        {"FullName": _PS_NAME, "FamilyName": "CorpusCFF", "Weight": "Regular"},
        charstrings,
        {},
    )
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable(
        {
            "familyName": "CorpusCFF",
            "styleName": "Regular",
            "psName": _PS_NAME,
            "fullName": _PS_NAME,
        }
    )
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=680)
    fb.setupPost()
    # Pin head timestamps for reproducibility.
    fb.font["head"].created = FIXED_FONT_EPOCH
    fb.font["head"].modified = FIXED_FONT_EPOCH

    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue(), cp_to_gid, advances_1000


def build_cff_font_pdf(out_path: Path | None = None) -> bytes:
    """Build an Identity-H PDF whose embedded font is CFF (Type1C) outlines.

    Args:
        out_path: Optional file to write the PDF to. The bytes are returned
            regardless.

    Returns:
        The PDF bytes. This builder has no host-font dependency, so it never
        returns ``None``.
    """
    font_bytes, cp_to_gid, advances_1000 = _build_cff_font_bytes(list(_TEXT))

    pdf = pikepdf.Pdf.new()
    try:
        font_stream = pikepdf.Stream(pdf, font_bytes)
        # /FontFile3 carries CFF/OpenType; /Subtype distinguishes it from glyf.
        font_stream["/Subtype"] = pikepdf.Name("/OpenType")

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
                    "/CapHeight": 680,
                    "/StemV": 80,
                    "/FontFile3": font_stream,
                }
            )
        )

        w_flat: list[object] = []
        for gid in sorted(set(cp_to_gid.values())):
            w_flat.append(gid)
            w_flat.append(pikepdf.Array([advances_1000.get(gid, 500)]))

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
                    "/W": pikepdf.Array(w_flat),
                    "/CIDToGIDMap": pikepdf.Name("/Identity"),
                }
            )
        )

        tounicode = build_tounicode_cmap({gid: cp for cp, gid in cp_to_gid.items()})
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

        def _encode(text: str) -> str:
            data = bytes(
                b
                for cp in (ord(c) for c in text)
                for b in ((cp_to_gid.get(cp, 0) >> 8) & 0xFF, cp_to_gid.get(cp, 0) & 0xFF)
            )
            return data.hex().upper()

        content = ("BT\n/F1 24 Tf\n1 0 0 1 72 720 Tm\n" + f"<{_encode(_TEXT)}> Tj\nET").encode(
            "latin-1"
        )

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
