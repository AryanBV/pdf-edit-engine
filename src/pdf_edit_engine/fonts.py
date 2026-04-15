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


def _strip_glyph_hinting(glyph: object) -> None:
    """Replace a glyph's TrueType hinting program with an empty program.

    Injected glyphs from a system font carry hinting bytecode that
    references the source font's fpgm/prep/cvt tables. Those tables
    are not in the destination (embedded) font, so the hinting would
    fail at render time. Stripping the hinting produces an unhinted
    glyph that renders correctly at typical text sizes (9pt+).

    Args:
        glyph: A fontTools Glyph object (simple or composite).
    """
    from fontTools.ttLib.tables import ttProgram  # type: ignore[import-untyped]

    empty = ttProgram.Program()
    empty.fromBytecode(b"")
    if hasattr(glyph, "program"):
        glyph.program = empty


def _collect_component_names(
    glyph: object,
    font: TTFont,
    _seen: set[str] | None = None,
) -> list[str]:
    """Recursively enumerate component glyph names for a composite glyph.

    Composite TrueType glyphs (common for accented Latin) reference child
    glyphs by name. To inject a composite into a new font, the child
    glyphs must also be present. Walks the composite graph and returns
    every referenced component name in injection order (leaves first,
    roots last). Returns an empty list for simple (non-composite) glyphs.

    Args:
        glyph: A fontTools Glyph object.
        font: The TTFont the glyph belongs to (for recursive lookups).
        _seen: Internal visited set to prevent cycles.

    Returns:
        Deduplicated list of component glyph names in injection order.
    """
    if _seen is None:
        _seen = set()
    if not hasattr(glyph, "isComposite") or not glyph.isComposite():
        return []
    order: list[str] = []
    for component in glyph.components:
        name = component.glyphName
        if name in _seen:
            continue
        _seen.add(name)
        if name in font["glyf"].glyphs:
            sub = font["glyf"][name]
            order.extend(_collect_component_names(sub, font, _seen))
        order.append(name)
    return order


def _pad_glyph_order(embedded: TTFont, target_length: int) -> None:
    """Pad ``embedded.glyphOrder`` up to ``target_length`` with empty glyphs.

    Used by Tier 1.5 when the font's ToUnicode CMap references CIDs
    beyond the current glyph order length. Padding each slot with an
    empty simple glyph (zero contours, zero advance) preserves the
    slot numbering so subsequent injections land at safe, unused GIDs
    without colliding with existing CMap CIDs.

    Each padding slot gets a unique glyph name so fontTools does not
    alias multiple GIDs to the same glyph.

    Args:
        embedded: Destination TTFont.
        target_length: Desired glyph order length after padding.
            If the current length is already >= target_length, this
            is a no-op.
    """
    from fontTools.ttLib.tables._g_l_y_f import Glyph  # type: ignore[import-untyped]

    current = len(embedded.getGlyphOrder())
    for gid in range(current, target_length):
        placeholder = f"_ary278_pad_{gid:05X}"
        empty_glyph = Glyph()
        empty_glyph.numberOfContours = 0
        embedded["glyf"][placeholder] = empty_glyph
        embedded["hmtx"][placeholder] = (0, 0)
    embedded["maxp"].numGlyphs = len(embedded.getGlyphOrder())


def _append_glyph_to_font(
    embedded: TTFont,
    system: TTFont,
    glyph_name: str,
) -> int:
    """Append a single glyph (by name) from system to embedded at a fresh GID.

    Low-level helper for ``_inject_glyph_in_place``. Assumes both fonts
    are TrueType with compatible upem (caller validates). Updates glyf,
    hmtx, glyph order, and maxp.numGlyphs. Strips hinting from the
    injected copy so it does not reference the source font's
    fpgm/prep/cvt tables (which are not present in the destination).

    Args:
        embedded: Destination TTFont.
        system: Source TTFont.
        glyph_name: Glyph name to copy (must exist in system["glyf"]).

    Returns:
        The new GID assigned in embedded.
    """
    import copy

    system_glyph = system["glyf"][glyph_name]
    new_glyph = copy.deepcopy(system_glyph)
    _strip_glyph_hinting(new_glyph)

    # Assigning to glyf[name] auto-appends the name to the glyph order
    # when the name is new. hmtx assignment does not.
    embedded["glyf"][glyph_name] = new_glyph

    advance, lsb = system["hmtx"][glyph_name]
    embedded["hmtx"][glyph_name] = (advance, lsb)

    order = list(embedded.getGlyphOrder())
    embedded["maxp"].numGlyphs = len(order)
    return order.index(glyph_name)


def _inject_glyph_in_place(
    embedded: TTFont,
    system: TTFont,
    ch: str,
) -> int:
    """Append a system-font glyph into an embedded TTFont at a new GID.

    Copies the glyph outline and hmtx entry for a single Unicode
    character ``ch`` from ``system`` into ``embedded``. For composite
    glyphs, recursively injects component glyphs first. Strips hinting
    bytecode from injected glyphs.

    Updates the embedded font's ``glyf``, ``hmtx``, internal ``cmap``,
    ``glyph order``, and ``maxp.numGlyphs``. Does NOT modify the PDF
    font dictionary or ToUnicode/W — that is the caller's responsibility.

    Args:
        embedded: Destination TTFont (the embedded subset from /FontFile2).
        system: Source TTFont (full system font).
        ch: Single Unicode character to inject (e.g., "Z" or "é").

    Returns:
        The new GID assigned to the injected glyph in ``embedded``.

    Raises:
        FontNotFoundError: If the character is absent from ``system``,
            if the fonts have mismatched ``unitsPerEm``, if ``embedded``
            is not TrueType (``glyf`` table missing), or if a composite
            component is missing from both fonts.
    """
    if "glyf" not in embedded:
        raise FontNotFoundError(
            "embedded font is not TrueType (no glyf table); "
            "Tier 1.5 requires TrueType — CFF not supported"
        )
    if "glyf" not in system:
        raise FontNotFoundError(
            "system font is not TrueType (no glyf table); Tier 1.5 requires TrueType"
        )
    embedded_upem = embedded["head"].unitsPerEm
    system_upem = system["head"].unitsPerEm
    if embedded_upem != system_upem:
        raise FontNotFoundError(
            f"unitsPerEm mismatch: embedded={embedded_upem}, "
            f"system={system_upem}. Tier 1.5 does not rescale outlines."
        )

    system_cmap = system.getBestCmap() or {}
    cp = ord(ch)
    if cp not in system_cmap:
        raise FontNotFoundError(f"character {ch!r} (U+{cp:04X}) not in system font cmap")
    glyph_name = system_cmap[cp]
    system_glyph = system["glyf"][glyph_name]

    # Recursively inject composite components (leaves first)
    components = _collect_component_names(system_glyph, system)
    for comp_name in components:
        if comp_name in list(embedded.getGlyphOrder()):
            continue
        if comp_name not in system["glyf"].glyphs:
            raise FontNotFoundError(f"composite component {comp_name!r} missing from system font")
        _append_glyph_to_font(embedded, system, comp_name)

    # Inject the top-level glyph (caller ensures it is not already present)
    if glyph_name in list(embedded.getGlyphOrder()):
        # Glyph name already in embedded font; just update cmap below
        new_gid = list(embedded.getGlyphOrder()).index(glyph_name)
    else:
        new_gid = _append_glyph_to_font(embedded, system, glyph_name)

    # Update embedded cmap: Unicode -> glyph name (BMP Unicode table preferred)
    updated = False
    for table in embedded["cmap"].tables:
        if table.platformID == 3 and table.platEncID == 1:
            table.cmap[cp] = glyph_name
            updated = True
            break
    if not updated:
        for table in embedded["cmap"].tables:
            if table.isUnicode():
                table.cmap[cp] = glyph_name
                updated = True
                break

    return new_gid


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
            encoding_type = "MacRoman"
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
    try:
        glyph_count = len(font.getGlyphOrder())
        font_cmap = font.getBestCmap()
    finally:
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
    try:
        embedded_cmap = embedded_font.getBestCmap() or {}

        # Split additional_chars into two groups:
        #   tier1_chars  - glyph already in embedded font, only needs a
        #                  /ToUnicode + /W + /CIDToGIDMap entry
        #   tier15_chars - glyph missing from embedded font, needs full
        #                  in-place injection (Tier 1.5)
        tier1_chars: list[str] = []
        tier15_chars: list[str] = []
        seen: set[str] = set()
        for ch in additional_chars:
            if ch in seen:
                continue
            seen.add(ch)
            if ord(ch) in embedded_cmap:
                tier1_chars.append(ch)
            else:
                tier15_chars.append(ch)

        # Apply Tier 1 for chars whose glyph is already in the embedded
        # font (no font-file change needed).
        if tier1_chars:
            _extend_tier1(
                pdf,
                font_dict,
                cid_font,
                embedded_font,
                embedded_cmap,
                "".join(tier1_chars),
            )
    finally:
        embedded_font.close()

    # Tier 1.5 handles the remaining chars whose glyphs are absent from
    # the embedded font. If there are none, we are done with Tier 1.
    if not tier15_chars:
        return "cmap_only"

    return _extend_tier2(
        pdf,
        font_dict,
        cid_font,
        fd,
        "".join(tier15_chars),
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
    """Tier 1.5 in-place glyph injection (root fix for ARY-276 Mode 2).

    Rather than replacing the embedded font file with a subset of the
    system font (which renumbers pre-existing CIDs and corrupts
    unrelated content-stream text), this function loads the existing
    embedded TTFont and APPENDS missing glyphs into its glyf table at
    fresh GIDs. Pre-existing CIDs remain valid because only the tail
    of the glyph order changes.

    For each appended glyph:
    - The glyph outline is deep-copied from the system font
    - Hinting bytecode is stripped (the source font's fpgm/prep/cvt
      tables are not available in the embedded subset)
    - Composite glyph components are injected recursively (leaves first)
    - The embedded font's glyf, hmtx, internal cmap, glyph order, and
      maxp.numGlyphs are updated
    - The embedded font is re-serialized back into /FontFile2

    Then, using the existing Tier 1 helpers, the PDF-level ToUnicode,
    /W, and /CIDToGIDMap entries are added for the new CIDs
    (CID == new GID under Identity-H).

    Returns ``"full_extension"`` for backward compatibility with existing
    tests and callers; the string is unchanged from the legacy Tier 2
    contract even though the underlying strategy is now additive.

    Args:
        pdf: Open pikepdf.Pdf (mutated in place).
        font_dict: The top-level Type0 font dictionary.
        cid_font: The CIDFontType2 descendant font dictionary.
        fd: The FontDescriptor dictionary.
        additional_chars: Unicode characters to add.
        full_font_path: Optional explicit system font path override.

    Raises:
        FontNotFoundError: If the system font cannot be found, the
            embedded font is not TrueType, upem does not match, a
            character is absent from the system font, or a composite
            component is missing from both fonts.
    """
    from pdf_edit_engine.system_fonts import find_font

    raw_ps_name = _detect_postscript_name(fd)
    ps_name = _strip_subset_prefix(raw_ps_name)

    system_path = str(full_font_path) if full_font_path is not None else find_font(ps_name)
    if system_path is None or not Path(system_path).is_file():
        msg = f"System font not found for '{ps_name}'. Install the font or provide full_font_path."
        raise FontNotFoundError(msg)

    # Load the embedded font (so we can extend it in place)
    embedded_bytes = bytes(fd["/FontFile2"].read_bytes())
    embedded = TTFont(io.BytesIO(embedded_bytes))
    system = TTFont(system_path)
    try:
        units_per_em = embedded["head"].unitsPerEm
        new_cmap_entries: dict[int, str] = {}
        new_w_entries: dict[int, float] = {}

        # Compute a collision-free starting GID for new glyphs.
        # Under Identity-H, CID == GID, so the new GID must be above
        # BOTH the current glyph order length AND any CID already used
        # by the ToUnicode CMap (which, for some synthetic/retain_gids
        # fonts, references CIDs beyond the embedded font's glyph
        # count). Pad the glyph order with unique .notdef placeholders
        # up to that point so fontTools preserves the slot numbering.
        existing_cmap_cids = _parse_existing_tounicode(font_dict).keys()
        max_existing_cid = max(existing_cmap_cids, default=-1)
        safe_start = max(len(embedded.getGlyphOrder()), max_existing_cid + 1)
        _pad_glyph_order(embedded, safe_start)

        for ch in additional_chars:
            cp = ord(ch)
            # Skip if already in the embedded cmap (defensive — caller
            # should have routed through Tier 1 first).
            if cp in (embedded.getBestCmap() or {}):
                continue
            new_gid = _inject_glyph_in_place(embedded, system, ch)

            new_cmap_entries[new_gid] = ch
            # Width comes from the newly-injected glyph's hmtx entry
            # (which we just copied from the system font).
            glyph_name = (system.getBestCmap() or {})[cp]
            raw_w = float(system["hmtx"][glyph_name][0])
            new_w_entries[new_gid] = raw_w * 1000.0 / units_per_em

        if not new_cmap_entries:
            # Every requested char was already in the embedded cmap —
            # nothing to do at the font level. Caller's PDF-level
            # metadata is already up to date via Tier 1 or a previous
            # extension.
            return "full_extension"

        # Re-serialize the extended embedded font and replace /FontFile2.
        buf = io.BytesIO()
        embedded.save(buf)
        fd["/FontFile2"] = pdf.make_stream(buf.getvalue())

        # Apply PDF-level metadata updates using Tier 1 helpers.
        _append_to_unicode_cmap(font_dict, new_cmap_entries, pdf)
        _append_w_entries(cid_font, new_w_entries)
        _update_cid_to_gid_map(
            cid_font,
            {cid: cid for cid in new_cmap_entries},
            pdf,
        )

        logger.info(
            "Tier 1.5 (in-place glyph injection) from %s: %d new glyph(s) appended",
            system_path,
            len(new_cmap_entries),
        )
        return "full_extension"
    finally:
        embedded.close()
        system.close()
