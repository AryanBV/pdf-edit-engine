# Font Subset Extension Pipeline

How pdf-edit-engine handles font extension when replacement text needs new glyphs.

## Two-Tier Approach

### Tier 1: CMap-Only Extension (Fast Path)

Many "subsetted" fonts actually contain far more glyphs than the CMap exposes.
In spike testing, a Calibri "subset" contained 6954 glyphs — essentially the full font.

When the needed glyphs exist in the embedded font but lack CMap entries:
1. Parse existing ToUnicode CMap (bfchar + bfrange entries)
2. Look up GID for each new Unicode character via the font's internal cmap table
3. Add new CID→Unicode mappings to ToUnicode CMap (as bfchar entries)
4. Add corresponding entries to the /W (widths) array
5. Re-serialize the CMap stream

This is fast (~ms) and preserves the original font embedding exactly.

### Tier 2: Full Font Extension (Fallback)

When glyphs are truly missing from the embedded font data:
1. Match the font's PostScript name to a system font file
2. Use fonttools `pyftsubset` with `--retain-gids` to create an extended subset
3. Replace the embedded font stream in the PDF
4. Update CMap and /W array as in Tier 1

The `--retain-gids` flag is critical: it preserves existing glyph IDs so that
all existing text in the PDF remains valid.

## Font Matching Cascade

When a system font is needed (Tier 2), the matching order is:
1. **Exact match** — PostScript name matches a system font exactly
2. **Metrically similar** — Liberation or Noto fonts that match metrics
   - Calibri → Liberation Sans (or Carlito)
   - Times New Roman → Liberation Serif
   - Courier New → Liberation Mono
3. **Explicit failure** — Return `font_action="failed"` in EditResult with details

Never silently substitute a font. The FidelityReport must record:
- Whether the original font was preserved
- What font was substituted (if any)
- Which glyphs were missing

## Width Calculation

Font widths in CIDFont PDFs use the /W array format:
```
/W [CID [width1 width2 ...]]  # consecutive widths starting at CID
/W [CID1 CID2 width]          # range of CIDs with same width
```

Widths are in CID units (typically 1000 units = 1 text space unit).
Get widths from fonttools: `font['hmtx'][glyph_name][0]` gives advance width.

## Integration with Surgeon

The OperatorSurgeon calls into FontExtender:
1. Before replacing text, call `can_render(font_info, new_text)`
2. If all glyphs available → proceed with replacement
3. If glyphs missing but font extensible → call `extend_subset()` first
4. If extension fails → report failure, do not corrupt the PDF
