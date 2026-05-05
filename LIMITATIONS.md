# Known Limitations

## Text editing

- Cross-paragraph reflow is not supported — text reflows within a single paragraph only
- Mixed-font paragraphs (e.g., bold words within regular text) lose inline formatting during reflow
- Text inside Form XObjects (reusable content streams) is not found or edited
- Justified text may lose justification after reflow (replaced with left-aligned)
- Empty string replacement removes the text visually but leaves the operator structure intact

## Transformed text

- Rotated text (non-zero CTM rotation) is extracted correctly and `find()` returns matches for small angles (tested up to 5 degrees). Replacement positioning under rotation has not been extensively tested.
- Horizontally scaled text (CTM Tz) is found and replaceable — the engine correctly handles width changes.
- Very small text (6pt) is found and replaceable.
- Character spacing (`Tc` operator) is handled correctly — text is stored as a single string in the content stream, not as individual characters with spaces.

## Cross-tool compatibility

- PDFs previously edited by PyMuPDF (redact + re-insert) can be read, searched, and edited by pdf-edit-engine. The mixed font origins (original + PyMuPDF-added) do not cause issues.

## Font handling

- Tier 1.5 font extension (in-place glyph injection) requires the matching system font (or a metric-equivalent fallback like Carlito for Calibri) to be installed; the resolved substitute name is surfaced through `FidelityReport.font_substituted` AND a `font_coverage_substituted` Degradation (v0.1.3)
- CFF / OpenType (Type1C) embedded fonts are not yet supported for Tier 1.5 (`_inject_glyph_in_place` raises `FontNotFoundError`); tracked as ARY-279 for a future release
- CJK fonts with 30,000+ glyphs have not been tested
- Type 3 fonts (bitmap/procedural) are not supported for extension
- Emoji and other multi-codepoint characters cannot be rendered if the font lacks those glyphs (reported via `FidelityReport.degradations` as `font_extension_failed`)
- Non-CID (simple WinAnsi/MacRoman) fonts cannot be extended via `extend_subset` — extension only works on Identity-H CID fonts. Replacements requiring missing glyphs in a simple font return `success=False` with `font_extension_failed` Degradation (v0.1.3 surfacing).

## Encoding

- Identity-H CIDFont is the primary encoding path; WinAnsi is fully supported
- Complex color spaces (ICC profiles, Separation, DeviceN) are tracked but not fully resolved
- Custom encodings with unusual /Differences arrays may fail
- MacRoman and other legacy encodings are less tested

## Performance

Benchmarks on a typical developer machine (Windows 11, Python 3.12, WinAnsi PDFs):

| Operation | Input | Time |
|-----------|-------|------|
| `get_text()` | 100-page PDF | ~0.3s |
| `find()` | 100-page PDF (900 matches) | ~0.3s |
| `replace()` | Single page | ~0.03s |
| `batch_replace()` | 50 edits | ~0.1s |

Identity-H CIDFont PDFs (Chrome, Google Docs, Word) may be slower due to CMap parsing and width lookups. Performance scales linearly with page count.

## PDF compatibility

- Encrypted PDFs cannot be edited without providing the password
- PDF/A compliance is not maintained after editing
- Digital signatures are invalidated by any edit (inherent to how PDF signatures work)
- Linearization (fast web view) is removed after editing
- Right-to-left text is not corrupted but reflow does not handle RTL properly
- XFA forms are not supported
- Content streams with non-UTF-8 byte sequences in operators are rejected with a clear error

## Concurrency and thread safety

- The library is **not thread-safe**. Caches (`FontResolverCache`,
  `GlyphWidthCache`) and the public `pikepdf.Pdf` handle returned by
  `_pathutil.open_pdf` are designed for single-threaded use. As of v0.1.2
  (ARY-283) every public entrypoint constructs fresh per-call caches —
  there is no shared module-level state that two threads could race on
  inside the engine — but the underlying `pikepdf.Pdf` object, the
  page-level mutations performed by `surgeon` and `structural`, and the
  `fontTools.ttLib.TTFont` instances loaded inside `fonts.extend_subset`
  are not safe to share across threads.
- **Recommended pattern**: one engine call per thread, each operating
  on its own `pikepdf.Pdf` handle (i.e. its own input path or its own
  `open_pdf(...)` context). Do not pass the same `Pdf`, page, or font
  resolver between threads.
- For server-side concurrent processing, scale by **process** (one
  worker per request) rather than by thread. The library's per-call
  cost (single-document edits in tens of milliseconds; see Performance
  table above) makes process-level concurrency cheap.
