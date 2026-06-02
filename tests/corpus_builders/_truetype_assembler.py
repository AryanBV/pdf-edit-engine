"""Deterministic Identity-H TrueType subset assembler.

Shared by every builder that embeds a real TrueType font: the baseline,
XObject, Arabic, and tagged builders. Given a host TrueType font and the
text that must render, it subsets the font to the used glyphs, builds the
Type0 / CIDFontType2 / Identity-H dictionary chain, the ToUnicode CMap, and
the ``/W`` width array — exactly the structure the engine treats as its
primary encoding path.

Mirrors the construction in ``tests/_identity_h_fixture.py`` but factored so
multiple builders can share it and inject their own content stream / page
extras (Form XObjects, struct-tree marked content, etc.).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pikepdf

from ._common import build_tounicode_cmap, pin_font_timestamps

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class EmbeddedFont:
    """A subsetted, embedded Identity-H font ready for content emission.

    Attributes:
        type0: The top-level Type0 font dictionary (already indirect-able).
        cp_to_gid: Unicode codepoint → CID(==GID) used by the subset.
        glyph_order: The subset font's glyph order (GID → glyph name).
        advances_1000: GID → advance width in /1000 text-space units.
    """

    type0: pikepdf.Dictionary
    cp_to_gid: dict[int, int]
    glyph_order: list[str]
    advances_1000: dict[int, int]

    def encode(self, text: str) -> str:
        """Encode ``text`` as an upper-case hex string of 2-byte CIDs."""
        data = bytes(
            b
            for cp in (ord(c) for c in text)
            for b in ((self.cp_to_gid.get(cp, 0) >> 8) & 0xFF, self.cp_to_gid.get(cp, 0) & 0xFF)
        )
        return data.hex().upper()

    def advance(self, text: str, font_size: float) -> float:
        """Total horizontal advance of ``text`` at ``font_size`` (pt)."""
        total = 0.0
        for ch in text:
            gid = self.cp_to_gid.get(ord(ch), 0)
            total += self.advances_1000.get(gid, 500) * font_size / 1000.0
        return total


@dataclass
class _SubsetData:
    font_bytes: bytes
    cp_to_gid: dict[int, int]
    glyph_order: list[str]
    advances_1000: dict[int, int]
    used_gids: list[int]
    ps_name: str
    bbox_1000: list[int]
    ascent_1000: int
    descent_1000: int
    cap_height_1000: int


def _subset_truetype(ttf_path: Path, corpus: str) -> _SubsetData:
    """Subset ``ttf_path`` to the glyphs needed for ``corpus``."""
    from fontTools import ttLib  # type: ignore[import-untyped]
    from fontTools.subset import Subsetter  # type: ignore[import-untyped]

    full = ttLib.TTFont(str(ttf_path))
    try:
        cmap: dict[int, str] = {}
        for table in full["cmap"].tables:
            if table.platformID == 3 and table.platEncID == 1:
                cmap = table.cmap
                break
        glyph_order = full.getGlyphOrder()
        name_to_gid = {n: i for i, n in enumerate(glyph_order)}
        hmtx = full["hmtx"]
        units_per_em = full["head"].unitsPerEm

        cp_to_gid: dict[int, int] = {}
        used_gids: set[int] = set()
        for ch in sorted(set(corpus)):
            cp = ord(ch)
            gname = cmap.get(cp)
            if gname and gname in name_to_gid:
                gid = name_to_gid[gname]
                cp_to_gid[cp] = gid
                used_gids.add(gid)

        # recalcTimestamp=False is load-bearing for determinism: fontTools'
        # default save() overwrites head.modified with the wall-clock time,
        # which cascades into the head checksum + checkSumAdjustment and
        # makes the subset bytes vary run-to-run. With it off, our explicit
        # pin_font_timestamps() value survives the save.
        sub_font = ttLib.TTFont(str(ttf_path), recalcTimestamp=False)
        try:
            subsetter = Subsetter()
            subsetter.populate(
                glyphs=[glyph_order[g] for g in sorted(used_gids) if g < len(glyph_order)]
            )
            subsetter.subset(sub_font)
            pin_font_timestamps(sub_font)
            buf = io.BytesIO()
            sub_font.save(buf)
            font_bytes = buf.getvalue()
        finally:
            sub_font.close()

        advances_1000: dict[int, int] = {}
        for gid in sorted(used_gids):
            gname = glyph_order[gid] if gid < len(glyph_order) else ".notdef"
            try:
                raw = float(hmtx[gname][0])
            except (KeyError, IndexError):
                raw = 500.0
            advances_1000[gid] = round(raw * 1000 / units_per_em)

        raw_ps = full["name"].getDebugName(6) or "CorpusFont"
        ps_name = str(raw_ps)
        bbox = [full["head"].xMin, full["head"].yMin, full["head"].xMax, full["head"].yMax]
        bbox_1000 = [round(v * 1000 / units_per_em) for v in bbox]
        ascent_1000 = round(full["OS/2"].sTypoAscender * 1000 / units_per_em)
        descent_1000 = round(full["OS/2"].sTypoDescender * 1000 / units_per_em)
        cap_height_1000 = round(full["OS/2"].sCapHeight * 1000 / units_per_em)
    finally:
        full.close()

    return _SubsetData(
        font_bytes=font_bytes,
        cp_to_gid=cp_to_gid,
        glyph_order=glyph_order,
        advances_1000=advances_1000,
        used_gids=sorted(used_gids),
        ps_name=ps_name,
        bbox_1000=bbox_1000,
        ascent_1000=ascent_1000,
        descent_1000=descent_1000,
        cap_height_1000=cap_height_1000,
    )


def embed_identity_h_font(
    pdf: pikepdf.Pdf,
    ttf_path: Path,
    corpus: str,
    *,
    include_tounicode: bool = True,
    cid_to_gid_map: str = "/Identity",
) -> EmbeddedFont:
    """Subset ``ttf_path`` and add a Type0 / Identity-H font to ``pdf``.

    Args:
        pdf: The open pikepdf document to attach the font dictionaries to.
        ttf_path: Host TrueType font to subset and embed.
        corpus: All text that must render; its glyphs are kept in the subset.
        include_tounicode: When ``False`` the ``/ToUnicode`` entry is omitted
            from the Type0 dict — the adversarial case B.3 targets, where
            ``find()`` returns zero matches on visible text unless embedded-
            cmap recovery runs. The subset still encodes CID==GID so the
            embedded font's own cmap remains a valid recovery source.
        cid_to_gid_map: Value of the descendant CIDFont's ``/CIDToGIDMap``.
            Defaults to ``"/Identity"``; pass any other name (e.g.
            ``"/Custom"``) to exercise the recovery gate that requires an
            Identity (or absent) ``/CIDToGIDMap``.

    Returns:
        An :class:`EmbeddedFont` describing the embedded font.
    """
    data = _subset_truetype(ttf_path, corpus)

    font_stream = pikepdf.Stream(pdf, data.font_bytes)
    font_stream["/Length1"] = len(data.font_bytes)

    font_descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/" + data.ps_name),
                "/Flags": 4,
                "/FontBBox": pikepdf.Array(data.bbox_1000),
                "/ItalicAngle": 0,
                "/Ascent": data.ascent_1000,
                "/Descent": data.descent_1000,
                "/CapHeight": data.cap_height_1000,
                "/StemV": 80,
                "/FontFile2": font_stream,
            }
        )
    )

    w_flat: list[object] = []
    for gid in data.used_gids:
        w_flat.append(gid)
        w_flat.append(pikepdf.Array([data.advances_1000[gid]]))

    cid_font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/BaseFont": pikepdf.Name("/" + data.ps_name),
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
                "/CIDToGIDMap": pikepdf.Name(cid_to_gid_map),
            }
        )
    )

    type0_entries: dict[str, object] = {
        "/Type": pikepdf.Name("/Font"),
        "/Subtype": pikepdf.Name("/Type0"),
        "/BaseFont": pikepdf.Name("/" + data.ps_name),
        "/Encoding": pikepdf.Name("/Identity-H"),
        "/DescendantFonts": pikepdf.Array([cid_font]),
    }
    if include_tounicode:
        tounicode = build_tounicode_cmap({gid: cp for cp, gid in data.cp_to_gid.items()})
        type0_entries["/ToUnicode"] = pikepdf.Stream(pdf, tounicode.encode("latin-1"))
    type0 = pikepdf.Dictionary(type0_entries)

    return EmbeddedFont(
        type0=type0,
        cp_to_gid=data.cp_to_gid,
        glyph_order=data.glyph_order,
        advances_1000=data.advances_1000,
    )


@dataclass
class TextLine:
    """One absolutely-positioned text line to emit inside a BT/ET block."""

    text: str
    x: float
    y: float
    font_size: float = 12.0
    font_resource: str = "/F1"


@dataclass
class PageContent:
    """A simple page: a media box plus a list of text lines."""

    lines: list[TextLine] = field(default_factory=list)
    width: float = 612.0
    height: float = 792.0
