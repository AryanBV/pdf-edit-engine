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

**Data flow** (replace operation): locator.find() → TextMatch → surgeon checks font →
fonts.can_render() → surgeon replaces operators → fonts.extend_subset() if needed →
serialize via pikepdf. Every edit returns a FidelityReport.

**Models**: TextCharacter, TextMatch, EditResult, FidelityReport, FontInfo, Edit,
ContentElement (wide index of ALL content stream elements), GraphicsStateSnapshot.

## Dependency Rules

| Module   | pikepdf | fonttools | pdfminer.six |
|----------|---------|-----------|--------------|
| locator  | ✓       |           | ✓            |
| surgeon  | ✓       |           |              |
| fonts    | ✓       | ✓         |              |
| reflow   |         | ✓         |              |
| wrapper  | ✓       |           |              |

Do NOT cross these boundaries. No module imports another module's libraries.

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
  just add CID→GID mappings and update /W widths. Full re-embed only when glyphs missing.

## Docs

For details: @docs/pdf-internals.md (content streams, encoding),
@docs/font-pipeline.md (subset extension workflow),
@docs/architecture.md (module details, error hierarchy),
@docs/decisions.md (decision log)
