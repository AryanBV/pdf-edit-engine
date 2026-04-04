# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-04-04

### Added

- **Text search**: `find()` with case-sensitive/insensitive matching, cross-element support, and operator-level precision
- **Text replacement**: `replace()`, `replace_all()`, `batch_replace()` with format preservation — edits content stream operators in-place
- **Font subset extension**: Tier 1 CMap-only fast path + Tier 2 full re-subset with system font fallback using `--retain-gids`
- **Single-paragraph reflow**: Greedy line breaking when replacement text is wider than the original
- **FidelityReport**: Every edit returns a detailed report (font_preserved, overflow_detected, reflow_applied, glyphs_missing)
- **dry_run mode**: Preview any edit without writing to disk
- **15 PDF wrapper operations**: merge, split, reorder, rotate, delete, crop, metadata, bookmarks, encrypt, decrypt, hyperlinks, highlights, flatten annotations, fill forms, watermarks
- **Text extraction**: `get_text()` and `get_fonts()` for inspecting PDF content
- **Paragraph detection**: `detect_paragraphs()` for analyzing page layout
- **Output path validation**: All file-writing functions validate paths before I/O
- **Identity-H and WinAnsi encoding**: Full support for CIDFont (modern PDFs) and WinAnsi (legacy PDFs)

### Technical

- Python 3.10+, pikepdf + fonttools + pdfminer.six (all MIT/MPL-2.0)
- 384 tests, 85% coverage, mypy strict
- Tested against 7 PDF generators: Chrome, Google Docs, reportlab (4 variants), pikepdf synthetic
- 100% character agreement across all tested generators
- Zero external binaries, zero API keys, zero network calls
