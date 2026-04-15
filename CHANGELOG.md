# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.1] — 2026-04-15

### Fixed

- **ARY-276**: Identity-H CIDFont replacement on large-font titles with per-glyph `Tm+Tj` emission (Word and Chrome generators) no longer garbles spacing. The operator merge logic now has an all-narrow anchor fallback that collapses chains of narrow `Tm+Tj` operators into a single anchor, so replacement text flows past the original operator boundaries as the PDF spec allows (`surgeon.py` F0 fallback, commit `f2b4aad`).
- **ARY-278**: Narrow Identity-H subsets (e.g., Chrome's 179-glyph ArialMT) now extend via in-place glyph injection. Missing glyphs are appended to the embedded font at fresh GIDs, preserving every pre-existing CID→GID mapping. The previous Tier 2 subset-and-replace approach renumbered CIDs and corrupted unrelated content-stream text (the `1ova ,ndustries` Mode 2 symptom) — replaced entirely (`fonts.py` `_extend_tier2`, commits `4c262d4..77d3912`).
- **Cross-font resolver pollution in `replace_all`**: `_apply_single_replacement` now always re-fetches the resolver from `match.characters[0].font_name`, discarding any stale resolver passed in by the caller. Previously, `replace_all`'s per-page loop reused one pre-fetched resolver across every match on the page. When matches used different fonts, the stale resolver validated `can_encode` against the wrong font, extension was skipped, and content-stream operators were encoded with the wrong font's CIDs. Symptom on real Chrome PDFs with multiple Identity-H fonts per page: `"ova ndustries"` extraction because the emitted CIDs only mapped to N/I in the *other* font's ToUnicode CMap. Pre-existing bug, surfaced during 0.1.1 real-PDF validation.
- **`FontResolverCache`**: now evicts by font-dict object generation number, so pages that share a font via indirect reference are invalidated together after font mutation (`encoding.py`, commit `8acbd49`).
- **`/W` and `/ToUnicode`** dedup entries on repeat `extend_subset` calls to prevent bloat (`fonts.py`, commit `60a1697`).
- **mypy strict**: resolved 15 pre-existing strict-mode errors in `structural.py` and `reflow.py`. The CI mypy step is now blocking (previously had `|| true`).

### Verified

- Tested against real-world Chrome (Skia/PDF m147) and Microsoft Word PDFs that reproduced the original ARY-276 garble. Both round-trip cleanly with no Mode-1 or Mode-2 garble tokens in extracted text and no silent font substitutions.
- 636 tests passing (up from 628), mypy strict clean on all 16 source files, ruff clean.

### Known scope limits

- CFF / Type1 embedded fonts still raise `FontNotFoundError` with a clear message when the engine needs to inject glyphs into them. Tier 1.5 handles TrueType only; CFF support is tracked in ARY-279 for 0.2.0.

## [0.1.0] — 2026-04-07

### Added

- **Text search**: `find()` with case-sensitive/insensitive matching, cross-element support, and operator-level precision
- **Text replacement**: `replace()`, `replace_all()`, `batch_replace()` with format preservation — edits content stream operators in-place
- **Font subset extension**: Tier 1 CMap-only fast path + Tier 2 full re-subset with system font fallback using `--retain-gids`
- **Single-paragraph reflow**: Greedy line breaking when replacement text is wider than the original
- **FidelityReport**: Every edit returns a detailed report (font_preserved, overflow_detected, reflow_applied, glyphs_missing)
- **dry_run mode**: Preview any edit without writing to disk
- **15 PDF wrapper operations**: merge, split, reorder, rotate, delete, crop, metadata, bookmarks, encrypt, decrypt, hyperlinks, highlights, flatten annotations, fill forms, watermarks
- **Text extraction**: `get_text()` and `get_fonts()` for inspecting PDF content
- **Text layout**: `get_text_layout()` returns positioned text blocks with font, size, and coordinates
- **Annotations module**: `get_annotations()`, `update_annotation_uri()`, `delete_annotation()`, `move_annotation()` for reading and modifying PDF link annotations
- **Rebuild path kerning**: Different-length replacements now distribute micro-kerning across glyphs to match original text width, eliminating visible spacing gaps
- **Paragraph detection**: `detect_paragraphs()` for analyzing page layout
- **Output path validation**: All file-writing functions validate paths before I/O
- **Identity-H and WinAnsi encoding**: Full support for CIDFont (modern PDFs) and WinAnsi (legacy PDFs)

### Technical

- Python 3.10+, pikepdf + fonttools + pdfminer.six (all MIT/MPL-2.0)
- 628 tests, 85% coverage, mypy strict
- Tested against 7 PDF generators: Chrome, Google Docs, reportlab (4 variants), pikepdf synthetic
- 100% character agreement across all tested generators
- Zero external binaries, zero API keys, zero network calls
