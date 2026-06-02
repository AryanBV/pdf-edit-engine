# Font Subset Extension Pipeline

How pdf-edit-engine handles font extension when replacement text needs
glyphs that are not yet mapped in the embedded font's CMap, or whose
outlines are not yet present in the embedded font binary.

## Two-Tier Approach (as of v0.1.1)

Every call to `fonts.extend_subset` first classifies each missing
character into one of two tiers:

- **Tier 1 — CMap-only extension.** The glyph outline already exists
  in the embedded font's internal `cmap` table. We only need to add a
  new CID → Unicode mapping to `/ToUnicode` and a `/W` width entry.
  No font-binary changes. Runs in milliseconds.
- **Tier 1.5 — in-place glyph injection.** The glyph outline is
  absent from the embedded font. We open the embedded font with
  `fontTools.ttLib.TTFont`, copy the required glyph outline and
  hmtx entry from the matching system font, append it at a fresh
  GID, re-serialize the font back into `/FontFile2`, and update the
  PDF-level `/ToUnicode`, `/W`, and `/CIDToGIDMap` to point at the
  new CID. Pre-existing CIDs are left alone, so unrelated
  content-stream text continues to render correctly.

The `extend_subset` entry point at `fonts.py` splits the input
`additional_chars` into `tier1_chars` (already in the embedded cmap)
and `tier15_chars` (not in the embedded cmap), applies Tier 1 to the
first group, then Tier 1.5 to the second group if any remain.

### Why the name "Tier 1.5"?

v0.1.0 shipped a different Tier 2 strategy that re-embedded a fresh
subset of the system font. That strategy had a subtle failure mode
on narrow subsets (notably Chrome PDFs emitting ~179 glyphs per
font): when the re-subsetted GIDs did not line up with the original
embedded subset's CID numbering, the output renumbered pre-existing
CIDs and corrupted unrelated content-stream text (the `"1ova
,ndustries"` Mode 2 symptom). ARY-278 replaced that Tier 2 entirely
with the additive injection above; we kept the name "Tier 1.5" to
signal that it operates on the existing embedded font binary rather
than re-subsetting a fresh system font. Callers still receive the
string `"full_extension"` from `extend_subset` for backward
compatibility.

## Identity-H CID == GID invariant

Tier 1 chooses the new CID to equal the GID reported by fontTools'
`font.getGlyphID(glyph_name)`. This must hold for Identity-H
CIDFonts: the CIDToGIDMap defaults to `/Identity`, which means the
content-stream emits the CID and the PDF viewer uses it directly as
a GID. Any departure from CID = GID under Identity-H produces
silent rendering failures.

Tier 1.5 preserves this invariant by assigning new glyphs to GIDs
above both the current `glyphOrder` length AND any CID already
referenced by the existing ToUnicode CMap. Some generator outputs
list ToUnicode CIDs past the end of the embedded font's glyph order
— `_pad_glyph_order` at `fonts.py` inserts unique empty
placeholder glyphs up to that CID so fontTools preserves the slot
numbering without aliasing.

## System-font matching

For Tier 1.5, the system font is looked up via
`system_fonts.find_font(postscript_name)` after stripping any
six-letter subset prefix (`"ABCDEF+"`) from the embedded font's
PostScript name. The lookup walks the Windows, macOS, and Linux
system font directories in this order:

1. **Exact PostScript-name match** on any installed font. This is
   what runs for users who have the original font (commercial or
   free) installed.
2. **Metric-equivalent fallback** — if the exact font is absent,
   `system_fonts._METRIC_EQUIVALENTS` maps several common
   commercial fonts to their open-source metric-equivalents
   (Calibri → Carlito / Liberation Sans / Arimo; Arial →
   Liberation Sans; Times-Roman → Liberation Serif; etc.).
   Whichever equivalent is installed first is returned. An
   `INFO`-level log records the substitution (`Using metric
   equivalent X for Y`).
3. **FontNotFoundError** — if neither the exact font nor any
   metric equivalent is installed, Tier 1.5 raises
   `FontNotFoundError` with the PostScript name and asks the
   caller to install the font or pass `full_font_path`.

**Substitution surfacing (INV-C-4, closed in v0.1.2):** when Tier 1.5
is reached via a metric equivalent, the injected glyph outlines come
from the substitute font, not the original. `extend_subset` accepts
an optional `substitution_log: list[str] | None` keyword argument;
when a metric-equivalent is used, its PostScript name is appended to
the list. `surgeon._apply_single_replacement` and
`reflow.reflow_paragraph` populate the log and surface its first
entry through `FidelityReport.font_substituted`, so callers that
gate on substitution see the resolved name (e.g. `"Carlito-Regular"`).

## Hinting bytecode

Glyphs copied from a system font carry hinting bytecode that
references the source font's fpgm/prep/cvt tables. Those tables are
not present in the embedded subset. `_strip_glyph_hinting` at
`fonts.py` replaces each injected glyph's program with an empty
one. The resulting glyph is unhinted but renders correctly at
typical text sizes (9 pt and above).

## Composite glyphs

Many Latin accented glyphs in TrueType fonts are composites that
reference child glyphs by name. `_collect_component_names` at
`fonts.py` walks the composite graph recursively and returns
every referenced component in dependency order (leaves first).
Each component is injected with `_append_glyph_to_font` before the
top-level glyph, so the final composite's references resolve cleanly
in the destination font.

## Width propagation

Widths are copied from the system font's `hmtx` table and normalised
to the PDF /W scale (`raw * 1000 / unitsPerEm`). Tier 1.5 requires
matching `unitsPerEm` between the embedded and system fonts —
`_inject_glyph_in_place` raises `FontNotFoundError` if they differ,
since in-place rescaling of outlines is out of scope.

## Integration with surgeon / structural

Every edit path that needs extension calls `extend_subset` on the
current PDF object and re-fetches the resolver from the FontResolver
cache afterwards (`surgeon._apply_single_replacement`,
`structural._extend_font`, `reflow.reflow_paragraph`). Eviction is
keyed on the font-dictionary's object generation pair so all pages
that share the font via indirect reference see the refreshed
resolver together.

## Known limitations

- **CFF / OpenType outlines are not supported.** Extension refuses
  honestly via outline-table classification (INV-C-9, C.1). The pure
  `_classify_outline_table` at `fonts.py` sniffs the table the embedded
  binary ACTUALLY carries (`glyf` / `CFF ` / `CFF2`), not the
  `/FontFile2` vs `/FontFile3` slot; `classify_embedded_outline` maps it
  to the truthful `embedded_type` and is the single source both
  `_extract_font_bytes` and `locator._detect_embedded_type` route
  through. `_extend_tier2` gates on this BEFORE any glyph surgery: a
  Type0/CID font whose embedded outline is not `glyf`-in-`/FontFile2`
  raises `FontNotFoundError` (in `_FONT_EXTEND_FAIL_EXCS`), so every
  write path surfaces an honest `font_extension_failed` Degradation
  (`success=False`) instead of leaking a raw `KeyError('/FontFile2')`
  from the old unconditional `fd["/FontFile2"]` read. The simple-font
  path (`_extend_simple_tier_one_five`) already rejected `/FontFile3`.
  CFF *injection* remains deferred to C.3 (tracked in ARY-279).
- **Type 3 fonts (bitmap/procedural) are not supported** for extension.
- **unitsPerEm mismatch** between the embedded and system font is
  treated as a hard failure (no rescaling).
- Emoji and other multi-codepoint characters cannot be rendered if
  the system font lacks those glyphs; the FidelityReport records them
  as `glyphs_missing`.
