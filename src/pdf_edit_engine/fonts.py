"""FontExtender module — analyze and extend font subsets in PDFs."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pikepdf
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]
from pdfminer.cmapdb import CMapParser, FileUnicodeMap

from pdf_edit_engine.errors import FontNotFoundError
from pdf_edit_engine.models import FontInfo

logger = logging.getLogger(__name__)


# ── Private helpers ──────────────────────────────────────────────────────


def _get_font_objects(
    page: pikepdf.Page,
    font_name: str,
) -> tuple[pikepdf.Object, pikepdf.Object | None, pikepdf.Object]:
    """Extract font dict, descendant CIDFont dict, and font descriptor.

    Returns the raw pikepdf.Object references (not Dictionary copies) so that
    in-place modifications propagate back to the PDF object tree.

    Args:
        page: The pikepdf Page object.
        font_name: Font resource name (e.g., 'F1', without leading '/').

    Returns:
        Tuple of (font_dict, descendant_font_dict_or_None, font_descriptor).

    Raises:
        FontNotFoundError: If the font or font descriptor is not found.
    """
    font_key = font_name if font_name.startswith("/") else f"/{font_name}"
    resources = page.get("/Resources")
    if resources is None:
        msg = f"Font {font_name} not found in page resources"
        raise FontNotFoundError(msg)
    fonts = resources.get("/Font")
    if fonts is None or font_key not in fonts:
        msg = f"Font {font_name} not found in page resources"
        raise FontNotFoundError(msg)

    font_obj = fonts[font_key]
    subtype_obj = font_obj.get("/Subtype")
    subtype = str(subtype_obj) if subtype_obj is not None else ""

    if subtype == "/Type0":
        desc_fonts = font_obj.get("/DescendantFonts")
        if desc_fonts is None or len(list(desc_fonts)) == 0:  # type: ignore[call-overload]
            msg = f"Type0 font {font_name} has no DescendantFonts"
            raise FontNotFoundError(msg)
        cid_font = desc_fonts[0]
        fd_obj = cid_font.get("/FontDescriptor")
        if fd_obj is None:
            msg = f"CIDFont for {font_name} has no FontDescriptor"
            raise FontNotFoundError(msg)
        return font_obj, cid_font, fd_obj

    # Simple font (TrueType, Type1)
    fd_obj = font_obj.get("/FontDescriptor")
    if fd_obj is None:
        msg = f"Font {font_name} has no FontDescriptor"
        raise FontNotFoundError(msg)
    return font_obj, None, fd_obj


def _extract_font_bytes(fd: pikepdf.Object) -> tuple[bytes, str]:
    """Extract embedded font binary and determine embedded type.

    Args:
        fd: Font descriptor dictionary.

    Returns:
        Tuple of (font_bytes, embedded_type) where embedded_type is
        'TrueType', 'CFF', or 'Type1'.

    Raises:
        FontNotFoundError: If no embedded font stream is found.
    """
    if "/FontFile2" in fd:
        return bytes(fd["/FontFile2"].read_bytes()), "TrueType"
    if "/FontFile3" in fd:
        return bytes(fd["/FontFile3"].read_bytes()), "CFF"
    if "/FontFile" in fd:
        return bytes(fd["/FontFile"].read_bytes()), "Type1"
    msg = "No embedded font stream (FontFile/FontFile2/FontFile3) found"
    raise FontNotFoundError(msg)


def _parse_existing_tounicode(
    font_dict: pikepdf.Object,
) -> dict[int, str]:
    """Parse existing ToUnicode CMap into CID → Unicode mapping.

    Args:
        font_dict: The top-level font dictionary (Type0).

    Returns:
        Dict mapping CID (int) to Unicode string.
    """
    if "/ToUnicode" not in font_dict:
        return {}
    tu_bytes: bytes = font_dict["/ToUnicode"].read_bytes()
    cmap = FileUnicodeMap()
    CMapParser(cmap, io.BytesIO(tu_bytes)).run()
    return dict(cmap.cid2unichr)


def _append_to_unicode_cmap(
    font_dict: pikepdf.Object,
    new_mappings: dict[int, str],
    pdf: pikepdf.Pdf,
) -> None:
    """Append new CID→Unicode entries to the ToUnicode CMap stream.

    Adds a new bfchar block before ``endcmap`` — does NOT splice into
    existing blocks (avoids fragile CMap parsing).

    Args:
        font_dict: The top-level font dictionary containing /ToUnicode.
        new_mappings: Dict of {CID: unicode_char_string} to add.
        pdf: The open PDF for creating the new stream.
    """
    if not new_mappings:
        return

    raw = font_dict["/ToUnicode"].read_bytes().decode("latin-1")
    endcmap_pos = raw.rfind("endcmap")
    if endcmap_pos < 0:
        logger.warning("ToUnicode CMap has no 'endcmap' marker; cannot append")
        return

    # Build bfchar block(s), max 100 entries per block per PDF spec
    entries = list(new_mappings.items())
    blocks: list[str] = []
    for chunk_start in range(0, len(entries), 100):
        chunk = entries[chunk_start : chunk_start + 100]
        lines = [f"{len(chunk)} beginbfchar"]
        for cid, ustr in chunk:
            cid_hex = f"<{cid:04X}>"
            # Encode each Unicode character as 4-digit hex
            uni_hex = "<" + "".join(f"{ord(ch):04X}" for ch in ustr) + ">"
            lines.append(f"{cid_hex} {uni_hex}")
        lines.append("endbfchar")
        blocks.append("\n".join(lines))

    insert = "\n".join(blocks) + "\n"
    new_cmap = raw[:endcmap_pos] + insert + raw[endcmap_pos:]
    font_dict["/ToUnicode"] = pdf.make_stream(new_cmap.encode("latin-1"))


def _rebuild_to_unicode_cmap(all_mappings: dict[int, str]) -> bytes:
    """Build a complete ToUnicode CMap from scratch.

    Args:
        all_mappings: Full dict of {CID: unicode_string} for every mapped CID.

    Returns:
        CMap stream bytes (latin-1 encoded).
    """
    header = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\n"
        "begincmap\n"
        "/CIDSystemInfo\n"
        "<< /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n"
        "/CMapType 2 def\n"
        "1 begincodespacerange\n"
        "<0000> <FFFF>\n"
        "endcodespacerange\n"
    )
    footer = "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    entries = sorted(all_mappings.items())
    blocks: list[str] = []
    for chunk_start in range(0, len(entries), 100):
        chunk = entries[chunk_start : chunk_start + 100]
        lines = [f"{len(chunk)} beginbfchar"]
        for cid, ustr in chunk:
            cid_hex = f"<{cid:04X}>"
            uni_hex = "<" + "".join(f"{ord(ch):04X}" for ch in ustr) + ">"
            lines.append(f"{cid_hex} {uni_hex}")
        lines.append("endbfchar")
        blocks.append("\n".join(lines))

    body = "\n".join(blocks) + "\n"
    return (header + body + footer).encode("latin-1")


def _append_w_entries(
    cid_font: pikepdf.Object,
    new_widths: dict[int, float],
) -> None:
    """Append new CID width entries to the /W array.

    Args:
        cid_font: The CIDFont dictionary containing /W.
        new_widths: Dict of {CID: width_in_font_units} to add.
    """
    if not new_widths:
        return

    existing: list[object] = []
    if "/W" in cid_font:
        existing = list(cid_font["/W"])  # type: ignore[call-overload]

    for cid, width in sorted(new_widths.items()):
        existing.append(cid)
        existing.append(pikepdf.Array([width]))

    cid_font["/W"] = pikepdf.Array(existing)


def _rebuild_w_array(all_widths: dict[int, float]) -> pikepdf.Array:
    """Build a complete /W array from scratch.

    Args:
        all_widths: Full dict of {CID: width_in_font_units}.

    Returns:
        pikepdf.Array in [CID [width]] format.
    """
    items: list[object] = []
    for cid, width in sorted(all_widths.items()):
        items.append(cid)
        items.append(pikepdf.Array([width]))
    return pikepdf.Array(items)


def _update_cid_to_gid_map(
    cid_font: pikepdf.Object,
    new_mappings: dict[int, int],
    pdf: pikepdf.Pdf,
) -> None:
    """Update CIDToGIDMap stream with new CID→GID entries.

    For Identity-H fonts with CIDToGIDMap = /Identity (a Name), no update is
    needed. Only updates when CIDToGIDMap is an explicit binary stream.

    Args:
        cid_font: The CIDFont dictionary containing /CIDToGIDMap.
        new_mappings: Dict of {CID: GID} to add.
        pdf: The open PDF for creating the updated stream.
    """
    if not new_mappings:
        return
    cidtogidmap = cid_font.get("/CIDToGIDMap")
    if cidtogidmap is None or isinstance(cidtogidmap, pikepdf.Name):
        return  # /Identity or absent — implicit identity mapping

    # Explicit stream — update the binary CID→GID table
    data = bytearray(cidtogidmap.read_bytes())

    for cid, gid in new_mappings.items():
        offset = cid * 2
        if offset + 2 > len(data):
            data.extend(b"\x00" * (offset + 2 - len(data)))
        data[offset] = (gid >> 8) & 0xFF
        data[offset + 1] = gid & 0xFF

    cid_font[pikepdf.Name("/CIDToGIDMap")] = pdf.make_stream(bytes(data))
    logger.info("Updated CIDToGIDMap stream with %d new entries", len(new_mappings))


def _detect_postscript_name(fd: pikepdf.Object) -> str:
    """Extract PostScript name from a font descriptor."""
    name_obj = fd.get("/FontName")
    if name_obj is None:
        return ""
    name = str(name_obj).lstrip("/")
    return name


def _strip_subset_prefix(ps_name: str) -> str:
    """Remove 6-letter subset prefix (e.g. 'ABCDEF+Calibri-Bold' → 'Calibri-Bold')."""
    if len(ps_name) > 7 and ps_name[6] == "+":
        prefix = ps_name[:6]
        if prefix.isalpha() and prefix.isupper():
            return ps_name[7:]
    return ps_name


# ── Public API ───────────────────────────────────────────────────────────


def analyze_subset(pdf_path: str | Path, font_name: str) -> FontInfo:
    """Analyze a font's embedded subset — how many glyphs, what's available.

    Args:
        pdf_path: Path to the PDF file.
        font_name: Name of the font to analyze (as it appears in the PDF, e.g. 'F1').

    Returns:
        FontInfo with subset details including glyph count, encoding type,
        and font_cmap populated with the embedded font's cmap table.

    Raises:
        FontNotFoundError: If the font or its embedded data is not found.
    """
    pdf = pikepdf.Pdf.open(str(pdf_path))
    try:
        return _analyze_from_page(pdf.pages[0], font_name, pdf_path=pdf_path)
    finally:
        pdf.close()


def _analyze_from_page(
    page: pikepdf.Page,
    font_name: str,
    *,
    pdf_path: str | Path | None = None,
) -> FontInfo:
    """Analyze a font on an already-open page.

    Args:
        page: pikepdf Page object.
        font_name: Font resource name (e.g. 'F1').
        pdf_path: Optional source path for error messages.

    Returns:
        FontInfo with all fields populated.
    """
    font_dict, cid_font, fd = _get_font_objects(page, font_name)
    subtype_obj = font_dict.get("/Subtype")
    subtype = str(subtype_obj) if subtype_obj is not None else ""

    # Determine encoding type
    if subtype == "/Type0":
        encoding_type: str = "Identity-H"
    else:
        enc_obj = font_dict.get("/Encoding")
        enc_str = str(enc_obj) if enc_obj is not None else ""
        if enc_str == "/WinAnsiEncoding":
            encoding_type = "WinAnsi"
        elif enc_str == "/MacRomanEncoding":
            encoding_type = "WinAnsi"  # Treat as WinAnsi for simplicity
        else:
            encoding_type = "Custom"

    # PostScript name and subset detection
    raw_ps_name = _detect_postscript_name(fd)
    is_subset = (
        len(raw_ps_name) > 7
        and raw_ps_name[6] == "+"
        and raw_ps_name[:6].isalpha()
        and raw_ps_name[:6].isupper()
    )
    postscript_name = _strip_subset_prefix(raw_ps_name)

    # Extract font binary and load with fonttools
    font_bytes, embedded_type = _extract_font_bytes(fd)
    font = TTFont(io.BytesIO(font_bytes))
    glyph_count = len(font.getGlyphOrder())
    font_cmap = font.getBestCmap()
    font.close()

    return FontInfo(
        name=font_name,
        postscript_name=postscript_name,
        encoding_type=encoding_type,  # type: ignore[arg-type]
        is_subset=is_subset,
        glyph_count=glyph_count,
        embedded_type=embedded_type,  # type: ignore[arg-type]
        font_cmap=font_cmap,
    )


def can_render(font_info: FontInfo, text: str) -> tuple[bool, list[str]]:
    """Check if a font can render all characters in the given text.

    Checks the embedded font's cmap table (from fonttools getBestCmap).
    Characters present in the cmap have glyph data; missing characters
    would require Tier 2 (system font) extension.

    Args:
        font_info: FontInfo from analyze_subset(), must have font_cmap populated.
        text: Text to check renderability for.

    Returns:
        Tuple of (can_render_all, list_of_missing_characters).
    """
    if not text:
        return (True, [])

    if font_info.font_cmap is None:
        return (False, list(text))

    missing: list[str] = []
    for ch in text:
        if ord(ch) not in font_info.font_cmap:
            missing.append(ch)
    return (len(missing) == 0, missing)


def extend_subset(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    font_name: str,
    additional_chars: str,
    full_font_path: str | Path | None = None,
) -> str:
    """Extend a font's character coverage in the open PDF object.

    Modifies the PDF's font objects in-place (CMap, /W, potentially font binary).
    The caller (surgeon.py) is responsible for saving the PDF afterward.

    Uses two-tier approach:
    1. CMap-only extension if glyphs exist in embedded font data.
    2. Full re-embed from system font if glyphs are missing.

    Args:
        pdf: The open pikepdf.Pdf object (modified in-place).
        page: The page containing the font.
        font_name: Font resource name (e.g. 'F1').
        additional_chars: String of characters to add to the subset.
        full_font_path: Optional explicit path to the full font file (Tier 2).

    Returns:
        Extension tier used: ``'cmap_only'`` or ``'full_extension'``.

    Raises:
        FontNotFoundError: If font not found in PDF, or system font not available
            for Tier 2 extension.
    """
    if not additional_chars:
        return "cmap_only"

    font_dict, cid_font, fd = _get_font_objects(page, font_name)
    subtype_obj = font_dict.get("/Subtype")
    subtype = str(subtype_obj) if subtype_obj is not None else ""

    if subtype != "/Type0":
        msg = (
            f"Font extension is only supported for Type0/Identity-H fonts, "
            f"got {subtype} for {font_name}"
        )
        raise FontNotFoundError(msg)

    if cid_font is None:
        msg = f"No CIDFont descendant for {font_name}"
        raise FontNotFoundError(msg)

    # Extract and load the embedded font
    font_bytes, _embedded_type = _extract_font_bytes(fd)
    embedded_font = TTFont(io.BytesIO(font_bytes))
    embedded_cmap = embedded_font.getBestCmap() or {}

    # Determine tier: check if ALL additional chars are in embedded font's cmap
    tier2_needed = False
    for ch in additional_chars:
        if ord(ch) not in embedded_cmap:
            tier2_needed = True
            break

    if not tier2_needed:
        result = _extend_tier1(
            pdf,
            font_dict,
            cid_font,
            embedded_font,
            embedded_cmap,
            additional_chars,
        )
        embedded_font.close()
        return result

    embedded_font.close()
    return _extend_tier2(
        pdf,
        font_dict,
        cid_font,
        fd,
        additional_chars,
        full_font_path,
    )


def _extend_tier1(
    pdf: pikepdf.Pdf,
    font_dict: pikepdf.Object,
    cid_font: pikepdf.Object,
    embedded_font: TTFont,
    embedded_cmap: dict[int, str],
    additional_chars: str,
) -> str:
    """CMap-only extension — glyph exists in font, just add CMap + /W entries.

    For Identity-H: CID == GID (non-negotiable).
    """
    new_cmap_entries: dict[int, str] = {}
    new_w_entries: dict[int, float] = {}

    for ch in additional_chars:
        cp = ord(ch)
        glyph_name = embedded_cmap[cp]
        gid = embedded_font.getGlyphID(glyph_name)
        # Identity-H: CID must equal GID
        cid = gid

        new_cmap_entries[cid] = ch

        # Get advance width from hmtx, normalized to PDF 1/1000-em scale
        if "hmtx" in embedded_font and glyph_name in embedded_font["hmtx"].metrics:
            raw_width = float(embedded_font["hmtx"].metrics[glyph_name][0])
            units_per_em = embedded_font["head"].unitsPerEm
            width = raw_width * 1000.0 / units_per_em
        else:
            width = 600.0  # fallback (already in 1/1000-em scale)
        new_w_entries[cid] = width

    _append_to_unicode_cmap(font_dict, new_cmap_entries, pdf)
    _append_w_entries(cid_font, new_w_entries)
    _update_cid_to_gid_map(cid_font, {cid: cid for cid in new_cmap_entries}, pdf)

    logger.info(
        "Tier 1 (CMap-only) extension: added %d characters",
        len(additional_chars),
    )
    return "cmap_only"


def _extend_tier2(
    pdf: pikepdf.Pdf,
    font_dict: pikepdf.Object,
    cid_font: pikepdf.Object,
    fd: pikepdf.Object,
    additional_chars: str,
    full_font_path: str | Path | None,
) -> str:
    """Full font extension — re-subset from system font with new characters."""
    from fontTools.subset import Options, Subsetter  # type: ignore[import-untyped]

    from pdf_edit_engine.system_fonts import find_font

    # Get PostScript name for system font lookup
    raw_ps_name = _detect_postscript_name(fd)
    ps_name = _strip_subset_prefix(raw_ps_name)

    # Find system font
    system_path = str(full_font_path) if full_font_path is not None else find_font(ps_name)

    if system_path is None or not Path(system_path).is_file():
        msg = f"System font not found for '{ps_name}'. Install the font or provide full_font_path."
        raise FontNotFoundError(msg)

    # Collect all needed Unicode codepoints: existing + new
    existing_mappings = _parse_existing_tounicode(font_dict)
    existing_unicodes: set[int] = set()
    for _cid, ustr in existing_mappings.items():
        for ch in ustr:
            existing_unicodes.add(ord(ch))
    new_unicodes = {ord(ch) for ch in additional_chars}
    all_unicodes = existing_unicodes | new_unicodes

    # Subset the system font with retain_gids
    system_font = TTFont(system_path)
    options = Options()
    options.retain_gids = True
    options.ignore_missing_unicodes = True

    subsetter = Subsetter(options=options)
    subsetter.populate(unicodes=list(all_unicodes))
    subsetter.subset(system_font)

    # Serialize and replace font stream
    bio = io.BytesIO()
    system_font.save(bio)
    new_font_bytes = bio.getvalue()
    fd["/FontFile2"] = pdf.make_stream(new_font_bytes)

    # Build complete CID→Unicode mapping using the new font's cmap
    new_cmap = system_font.getBestCmap() or {}
    all_mappings: dict[int, str] = {}
    all_widths: dict[int, float] = {}
    units_per_em = system_font["head"].unitsPerEm

    glyph_order = system_font.getGlyphOrder()

    for cp in sorted(all_unicodes):
        ch = chr(cp)
        if cp in new_cmap:
            glyph_name = new_cmap[cp]
            gid = system_font.getGlyphID(glyph_name)
            # Identity-H: CID = GID
            all_mappings[gid] = ch
            # Width from hmtx, normalized to PDF 1/1000-em scale
            if "hmtx" in system_font and glyph_name in system_font["hmtx"].metrics:
                raw_w = float(system_font["hmtx"].metrics[glyph_name][0])
                all_widths[gid] = raw_w * 1000.0 / units_per_em
            else:
                all_widths[gid] = 600.0

    # Preserve ligature entries from original ToUnicode CMap.
    # Ligatures map one CID to multiple Unicode chars (e.g., CID 302 → "fi").
    # The single-char rebuild above only creates 1:1 mappings, losing ligatures.
    for cid, ustr in existing_mappings.items():
        if (
            len(ustr) > 1
            and cid not in all_mappings
            and cid < len(glyph_order)
            and glyph_order[cid] != ".notdef"
        ):
            gn = glyph_order[cid]
            all_mappings[cid] = ustr
            # Get width from hmtx for this glyph
            if "hmtx" in system_font and gn in system_font["hmtx"].metrics:
                raw_w = float(system_font["hmtx"].metrics[gn][0])
                all_widths[cid] = raw_w * 1000.0 / units_per_em

    # Rebuild ToUnicode CMap from scratch
    font_dict["/ToUnicode"] = pdf.make_stream(_rebuild_to_unicode_cmap(all_mappings))

    # Rebuild /W array from scratch
    cid_font["/W"] = _rebuild_w_array(all_widths)

    # Update CIDToGIDMap if it's an explicit stream (not /Identity)
    _update_cid_to_gid_map(
        cid_font, {gid: gid for gid in all_mappings}, pdf,
    )

    # Update font descriptor metrics from system font's OS/2 table
    # Normalize to PDF 1/1000-em scale (same as /W widths)
    if "OS/2" in system_font:
        os2 = system_font["OS/2"]
        fd["/Ascent"] = int(os2.sTypoAscender * 1000 / units_per_em)
        fd["/Descent"] = int(os2.sTypoDescender * 1000 / units_per_em)
        if hasattr(os2, "sCapHeight"):
            fd["/CapHeight"] = int(os2.sCapHeight * 1000 / units_per_em)

    system_font.close()

    logger.info(
        "Tier 2 (full extension) from %s: %d total characters",
        system_path,
        len(all_mappings),
    )
    return "full_extension"
