"""Builder: ToUnicode-absent Identity-H PDF (B.3 recovery target).

Two adversarial documents that exercise ``encoding._init_identity_h``'s
embedded-cmap recovery path (the M0 Rank-2.5 spike verdict):

- :func:`build_no_tounicode_identity_h_pdf` — a well-formed Identity-H
  CIDFont whose Type0 dict has **no** ``/ToUnicode``. The content stream
  emits true CID==GID (subset glyph indices), and the embedded
  ``/FontFile2`` carries a standard (3, 1) Unicode cmap, so inverting
  ``getBestCmap() + getGlyphID()`` recovers a usable CID→Unicode map.
  Without recovery, ``find()`` returns zero matches on visible text (the
  documented ``encoding.py:136-138`` failure).

- :func:`build_pua_no_tounicode_identity_h_pdf` — the UNRECOVERABLE case:
  the embedded subset's cmap is rewritten so every codepoint lands in the
  Private-Use-Area (U+F000+gid), the convention symbol-encoded subsets use.
  Recovery must reject these (majority-PUA → treat as unrecovered) so the
  engine does not fabricate garbage text from PUA codepoints.

Both encode the content stream against the SUBSET's glyph order (true
Identity, CID==GID) — unlike :mod:`._truetype_assembler`, which emits full-
font GIDs and relies on ``/ToUnicode`` to bridge the mismatch. With no
``/ToUnicode`` that bridge is gone, so the recovery path requires real
CID==GID. This mirrors the M0 spike's ``build_consistent_fixture``.

Deterministic: no network, fixed font timestamps, reproducible /ID.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pikepdf

from ._common import (
    build_tounicode_cmap,
    emit_or_write,
    find_truetype_font,
    pin_font_timestamps,
    save_pdf_deterministic,
)

if TYPE_CHECKING:
    from pathlib import Path

_TITLE = "Acme Corporation"
_BODY = "This is body text with Acme Corporation in it."
_CORPUS = _TITLE + _BODY


@dataclass
class _ConsistentSubset:
    """A subset font where content-stream CID == embedded subset GID."""

    font_bytes: bytes
    cp_to_cid: dict[int, int]  # Unicode codepoint → CID (== subset GID)
    advances_1000: dict[int, int]  # CID → /1000 advance width
    ps_name: str
    bbox_1000: list[int]
    ascent_1000: int
    descent_1000: int
    cap_height_1000: int

    def encode(self, text: str) -> str:
        """Encode ``text`` as an upper-case hex string of 2-byte CIDs."""
        data = bytes(
            b
            for cp in (ord(c) for c in text)
            for b in ((self.cp_to_cid.get(cp, 0) >> 8) & 0xFF, self.cp_to_cid.get(cp, 0) & 0xFF)
        )
        return data.hex().upper()


def _build_consistent_subset(ttf_path: Path, *, pua: bool) -> _ConsistentSubset:
    """Subset ``ttf_path`` to ``_CORPUS`` with true CID==GID Identity.

    Args:
        ttf_path: Host TrueType font to subset.
        pua: When True, rewrite the subset cmap so every codepoint maps into
            the BMP Private-Use-Area (U+F000 + gid) — the unrecoverable case.
    """
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

        cp_to_gname: dict[int, str] = {}
        used_gids: set[int] = set()
        for ch in sorted(set(_CORPUS)):
            gname = cmap.get(ord(ch))
            if gname and gname in name_to_gid:
                cp_to_gname[ord(ch)] = gname
                used_gids.add(name_to_gid[gname])

        sub_font = ttLib.TTFont(str(ttf_path), recalcTimestamp=False)
        try:
            subsetter = Subsetter()
            subsetter.populate(
                glyphs=[glyph_order[g] for g in sorted(used_gids) if g < len(glyph_order)]
            )
            subsetter.subset(sub_font)
            sub_order = sub_font.getGlyphOrder()
            sub_name_to_gid = {n: i for i, n in enumerate(sub_order)}
            # CID == SUBSET GID — the Identity invariant recovery relies on.
            cp_to_cid = {
                cp: sub_name_to_gid[gn] for cp, gn in cp_to_gname.items() if gn in sub_name_to_gid
            }
            if pua:
                # Rewrite every cmap subtable so getBestCmap() yields only
                # U+F0xx entries — the majority-PUA unrecoverable signal.
                pua_cmap = {0xF000 + cp_to_cid[cp]: gn for cp, gn in cp_to_gname.items()}
                for table in sub_font["cmap"].tables:
                    table.cmap = dict(pua_cmap)
            pin_font_timestamps(sub_font)
            buf = io.BytesIO()
            sub_font.save(buf)
            font_bytes = buf.getvalue()
        finally:
            sub_font.close()

        advances_1000: dict[int, int] = {}
        for cp, cid in cp_to_cid.items():
            try:
                raw = float(hmtx[cp_to_gname[cp]][0])
            except (KeyError, IndexError):
                raw = 500.0
            advances_1000[cid] = round(raw * 1000 / units_per_em)

        ps_name = str(full["name"].getDebugName(6) or "CorpusFont")
        bbox = [full["head"].xMin, full["head"].yMin, full["head"].xMax, full["head"].yMax]
        bbox_1000 = [round(v * 1000 / units_per_em) for v in bbox]
        ascent_1000 = round(full["OS/2"].sTypoAscender * 1000 / units_per_em)
        descent_1000 = round(full["OS/2"].sTypoDescender * 1000 / units_per_em)
        cap_height_1000 = round(full["OS/2"].sCapHeight * 1000 / units_per_em)
    finally:
        full.close()

    return _ConsistentSubset(
        font_bytes=font_bytes,
        cp_to_cid=cp_to_cid,
        advances_1000=advances_1000,
        ps_name=ps_name,
        bbox_1000=bbox_1000,
        ascent_1000=ascent_1000,
        descent_1000=descent_1000,
        cap_height_1000=cap_height_1000,
    )


def _assemble_pdf(
    data: _ConsistentSubset,
    out_path: Path | None,
    *,
    tounicode_cid_to_cp: dict[int, int] | None = None,
) -> bytes:
    """Build a one-page Identity-H PDF from ``data``.

    Args:
        data: The consistent CID==GID subset.
        out_path: Optional destination file.
        tounicode_cid_to_cp: When ``None`` (default) the Type0 font ships NO
            ``/ToUnicode`` (the B.3 whole-map recovery target). When a dict,
            it is embedded as a ``/ToUnicode`` CMap mapping the listed CIDs to
            Unicode codepoints — pass a *subset* of the corpus CIDs to build a
            PARTIAL ``/ToUnicode`` (the B.5 asymmetric-reconciliation target),
            leaving the omitted CIDs for embedded-cmap fill.
    """
    pdf = pikepdf.Pdf.new()
    try:
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
        for cid in sorted(data.advances_1000):
            w_flat.append(cid)
            w_flat.append(pikepdf.Array([data.advances_1000[cid]]))
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
                    "/CIDToGIDMap": pikepdf.Name("/Identity"),
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
        if tounicode_cid_to_cp is None:
            # NOTE: deliberately NO /ToUnicode entry (B.3 recovery target).
            pass
        else:
            # PARTIAL (or full) /ToUnicode (B.5 reconciliation target).
            cmap_text = build_tounicode_cmap(tounicode_cid_to_cp)
            tu_stream = pikepdf.Stream(pdf, cmap_text.encode("latin-1"))
            type0_entries["/ToUnicode"] = pdf.make_indirect(tu_stream)
        type0 = pikepdf.Dictionary(type0_entries)
        content = (
            "BT\n/F1 24 Tf\n1 0 0 1 72 730 Tm\n"
            f"<{data.encode(_TITLE)}> Tj\n"
            "/F1 12 Tf\n1 0 0 1 72 680 Tm\n"
            f"<{data.encode(_BODY)}> Tj\nET"
        ).encode("latin-1")
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


def build_no_tounicode_identity_h_pdf(out_path: Path | None = None) -> bytes | None:
    """Build a recoverable Identity-H PDF with no ``/ToUnicode``.

    Args:
        out_path: Optional file to write the PDF to. The bytes are returned
            regardless.

    Returns:
        The PDF bytes, or ``None`` if no host TrueType font is available.
    """
    ttf_path = find_truetype_font()
    if ttf_path is None:
        return None
    return _assemble_pdf(_build_consistent_subset(ttf_path, pua=False), out_path)


def build_pua_no_tounicode_identity_h_pdf(out_path: Path | None = None) -> bytes | None:
    """Build an UNRECOVERABLE Identity-H PDF: no ToUnicode, PUA-only cmap.

    The embedded subset's cmap maps every codepoint into the BMP Private-Use
    Area (U+F000 + gid). Recovery must classify the recovered map as
    majority-PUA and treat the font as unrecovered, so ``find()`` still
    returns zero matches rather than fabricating PUA glyph text.

    Args:
        out_path: Optional file to write the PDF to.

    Returns:
        The PDF bytes, or ``None`` if no host TrueType font is available.
    """
    ttf_path = find_truetype_font()
    if ttf_path is None:
        return None
    return _assemble_pdf(_build_consistent_subset(ttf_path, pua=True), out_path)


#: Unicode codepoint whose CID is deliberately OMITTED from the partial
#: ``/ToUnicode`` in :func:`build_partial_tounicode_identity_h_pdf`. 'A' is
#: the first letter of "Acme", so a find("Acme") on the un-reconciled engine
#: drops the whole run (the gap B.5 closes). Exposed so the probe can assert
#: against the exact hole without re-deriving it.
PARTIAL_OMITTED_CODEPOINT: int = ord("A")


def build_partial_tounicode_identity_h_pdf(
    out_path: Path | None = None,
) -> bytes | None:
    """Build an Identity-H PDF whose ``/ToUnicode`` is PARTIAL (B.5 target).

    The Type0 font ships a real ``/ToUnicode`` covering every corpus CID
    **except** the one for :data:`PARTIAL_OMITTED_CODEPOINT` (``'A'``). The
    embedded ``/FontFile2`` carries the full (3, 1) Unicode cmap, so the
    omitted CID is recoverable by inverting ``getBestCmap() + getGlyphID()``
    (CID==GID under Identity-H) — exactly the reconciliation B.5 performs.

    Before reconciliation, ``decode()`` raises ``KeyError`` on the unmapped
    CID and the locator drops the *entire* Tj run that contains it, so
    ``find("Acme")`` returns zero matches and ``get_text`` loses the title.
    After additive fill, the gap is closed and the present-CID mappings from
    ``/ToUnicode`` are preserved untouched.

    Args:
        out_path: Optional file to write the PDF to.

    Returns:
        The PDF bytes, or ``None`` if no host TrueType font is available.
    """
    ttf_path = find_truetype_font()
    if ttf_path is None:
        return None
    data = _build_consistent_subset(ttf_path, pua=False)
    # Map every corpus codepoint's CID to its real Unicode value EXCEPT the
    # omitted one, producing the partial /ToUnicode.
    partial = {cid: cp for cp, cid in data.cp_to_cid.items() if cp != PARTIAL_OMITTED_CODEPOINT}
    return _assemble_pdf(data, out_path, tounicode_cid_to_cp=partial)
