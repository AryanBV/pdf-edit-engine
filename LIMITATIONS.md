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
- CID-keyed (Type0) CFF / Type1C embedded fonts ARE extended in place as of v0.2.0 (C.3 — `_inject_cff_glyph_in_place`, the Type2-charstring sibling of the TrueType `glyf` injector; collision-free `CID == GID`, pre-existing CIDs preserved). Out-of-scope CFF shapes (simple-font/non-CID CFF, CFF2, name-keyed CFF, multi-FD CID, composite donors, `unitsPerEm` mismatch) refuse honestly via a `font_extension_failed` Degradation (`success=False`)
- CJK fonts with 30,000+ glyphs have not been tested
- Type 3 fonts (bitmap/procedural) are not supported for extension
- Emoji and other multi-codepoint characters cannot be rendered if the font lacks those glyphs (reported via `FidelityReport.degradations` as `font_extension_failed`)
- Non-CID font extension is supported for **simple TrueType + WinAnsi + `/FontFile2`** fonts (Phase 13). Replacements requiring missing glyphs trigger `_extend_simple_tier_15`, which sources the outline from a system font (or metric-equivalent fallback like Carlito for Calibri) and surfaces via `FidelityReport.font_substituted` AND a `font_coverage_substituted` Degradation. Still NOT supported on the simple-font path: `/FontFile3` (CFF/OpenType) outlines — note that CID-keyed (Type0) CFF/Type1C IS injected as of v0.2.0 (C.3); this remaining gap is the non-CID simple-font case only; `/Type1` fonts (would require Type1 charstring surgery — out of scope); MacRoman simple fonts (architecturally similar to WinAnsi but untested); CJK fonts with 30,000+ glyphs (untested). The dispatcher in `extend_subset()` raises `FontNotFoundError` for all these cases.

  Most public-API entry points surface failure modes as typed
  `Degradation` entries on `EditResult.fidelity_report.degradations`.
  Coverage is enforced for the documented kinds via INV-J-9
  (added in v0.1.3, commit 01: `font_action="failed"` implies a
  `Degradation` of kind in `FONT_AFFECTING_KINDS`). Other failure
  modes (e.g., reflow abort, glyph-coverage gaps) are surfaced
  opportunistically and may not always carry a typed entry.
  See `docs/v0.1.3-release-notes.md` §Honesty fixes for the full
  coverage matrix.

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

- Encrypted PDFs require the password to open, but once it is provided they are edited and re-saved with their encryption preserved as of v0.2.0 (A2.3 — re-encrypted with the same algorithm and permissions; a genuine re-encryption failure falls back to an unencrypted save and surfaces an `encryption_dropped` Degradation)
- PDF/A compliance is not maintained after editing
- Digital signatures are invalidated by any edit (inherent to how PDF signatures work)
- Linearization (Fast Web View) is preserved on save as of v0.2.0 (A2.2 — a linearized input is re-linearized; on the rare case pikepdf cannot re-linearize, the edit still succeeds via a normal save and surfaces a `linearization_dropped` Degradation). A non-linearized input is unaffected
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
- **Concurrency depth is not formally probed (F-B-07).** v0.1.3 does
  not exercise `replace_block` / `batch_replace_block` under concurrent
  multi-process load. Single-process behavior is well-tested; multi-
  process callers writing the same file should serialize via OS-level
  file locks or a queue. Tracked for a future release.

## Denial-of-service and timeouts

- **No engine-side timeout on edit operations (F-D-DD).** A pathological
  PDF (corrupt CMap, deep composite glyph graph, very large embedded
  subset) can stall an edit for minutes inside `replace_block` reflow
  or `extend_subset`. Callers running the engine in a request-response
  context should impose their own timeout (e.g.
  `concurrent.futures.ProcessPoolExecutor` with a per-task deadline,
  or a watchdog process). The composite-depth cap (v0.1.3,
  `MAX_COMPOSITE_DEPTH=64`), the graphics-state `q`/`Q` depth cap
  (v0.2.0, `MAX_GRAPHICS_STATE_DEPTH=128`), and the font/CMap Flate
  decompression-bomb guard (v0.2.0, 32 MiB / 8 MiB decoded caps)
  close the deepest known classes of pathology; an engine-side
  wall-clock timeout remains tracked for a future release.
