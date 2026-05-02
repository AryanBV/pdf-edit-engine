# pdf-edit-engine

Format-preserving PDF text editing. Edits text in existing PDFs while preserving fonts,
layout, and visual fidelity. Unlike PyMuPDF (redact-and-replace), this engine modifies
content stream operators in-place and extends font subsets as needed.

Stack: pikepdf (content stream parse/unparse), fonttools (font extraction/CMap/metrics),
pdfminer.six (text extraction with positions). All MIT/MPL — no AGPL.

## Architecture

Four core modules with strict dependency boundaries:

```
TextLocator ──→ OperatorSurgeon ──→ FontExtender
     │                │                   │
     ↓                ↓                   ↓
 pdfminer.six     pikepdf only      pikepdf + fonttools
 + pikepdf
                  ReflowEngine ← fonttools (metrics only)
```

**Data flow** (replace operation): locator.find() → TextMatch → surgeon validates the
match via `_assert_match_addressable` (refuses stale matches with OperatorError) →
fonts.can_render() → surgeon replaces operators → fonts.extend_subset() if needed →
serialize via pikepdf. Every edit returns a FidelityReport.

**Models**: TextCharacter, TextMatch, EditResult (with `__post_init__` enforcing
INV-J-3: overflow_detected ⇒ overflow warning), FidelityReport, FontInfo, Edit,
ContentElement (wide index of ALL content stream elements), GraphicsStateSnapshot,
Paragraph, TextBlock. `models.py` is data-only **except for dataclass `__post_init__`
contract guards** that enforce documented cross-field invariants — those belong with
the model they constrain.

**PDF I/O entry**: `_pathutil.open_pdf` is the single canonical entry point for opening
a PDF. Every public API path routes through it. Raw `pikepdf.Pdf.open` calls outside
this module are an architectural violation that breaks INV-L-1 (translated exceptions).

## Dependency Rules

| Module   | pikepdf | fonttools | pdfminer.six |
|----------|---------|-----------|--------------|
| locator  | ✓       |           | ✓            |
| surgeon  | ✓       |           |              |
| fonts    | ✓       | ✓         |              |
| reflow   | ✓       | ✓         |              |
| structural | ✓     | (via reflow) |           |
| wrapper  | ✓       |           |              |

`reflow` writes content streams (pikepdf) using widths measured by
fonttools. `structural` orchestrates locator + reflow + surgeon helpers
for bbox-based edits and inherits their dependencies. Do NOT introduce
pdfminer.six anywhere outside `locator`.

## Coding Conventions

- `from __future__ import annotations` in every file
- Type hints on all function signatures and return values (mypy strict)
- Google-style docstrings (Args/Returns/Raises sections)
- Absolute imports only: `from pdf_edit_engine.models import TextMatch`
- Line length: 100 chars (ruff enforced)

## Commands

```
make lint        # ruff check src/ tests/
make typecheck   # python -m mypy (strict)
make test        # python -m pytest -v --cov=pdf_edit_engine
make all         # lint + typecheck + test
python -m pytest tests/test_locator.py  # single file
```

## Critical PDF Rules

- **Identity-H is primary encoding**: CIDFont uses 2-byte CID glyph indices, NOT readable
  text. The hex strings in TJ operators are glyph IDs, not Unicode.
- **Use parse_content_stream + unparse_content_stream**: NOT TokenFilter. Validated in spike.
- **"Subsetted" fonts may be full**: Check glyph count before assuming extension is needed.
  Spike found Calibri "subset" with 6954 glyphs.
- **Content stream order ≠ visual order**: Bullets and lists often have out-of-order operators.
  Use position-based matching, not stream-order.
- **CMap parsing**: Must handle both bfchar (single CID→Unicode) and bfrange (sequential
  ranges AND array-of-arrays variants).
- **Glyph displacement**: `tx = ((w0 - Tj/1000) * Tfs + Tc + Tw) * Th`
- **Font extension fast path**: CMap-only extension when "subset" already has all glyphs —
  just add CID→GID mappings and update /W widths. **Tier 1.5 in-place glyph injection**
  (NOT full re-embed; ARY-278) when glyphs missing. The legacy v0.1.0 retain-gids
  subset-and-replace strategy is gone — it renumbered pre-existing CIDs and corrupted
  unrelated text.
- **Path-traversal validation**: `_pathutil.validate_output_path` refuses paths that
  traverse a symlink or junction (compares `os.path.realpath` to `os.path.abspath`,
  case-normalized). Catches POSIX symlinks AND Windows directory junctions. Pre-fix
  `Path.resolve()`-followed-by-`is_symlink()` was dead code.

## Docs

For details: @docs/pdf-internals.md (content streams, encoding),
@docs/font-pipeline.md (subset extension workflow),
@docs/architecture.md (module details, error hierarchy),
@docs/decisions.md (decision log)
