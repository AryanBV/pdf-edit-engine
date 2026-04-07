# pdf-edit-engine

[![PyPI](https://img.shields.io/pypi/v/pdf-edit-engine)](https://pypi.org/project/pdf-edit-engine/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-508%20passed-brightgreen)]()

Format-preserving PDF text editing — edit text in existing PDFs while preserving fonts, layout, and visual fidelity.

## Why this exists

Most open-source tools either substitute fonts during editing (losing the original appearance) or use a redact-and-replace approach that silently degrades formatting. pdf-edit-engine operates directly on PDF content stream operators to preserve the original font, size, color, and position. Every edit returns a `FidelityReport` documenting exactly what changed — no silent degradation.

## Installation

```bash
pip install pdf-edit-engine
```

Requires Python 3.10+. No external binaries or API keys needed.

## Quick start

```python
from pdf_edit_engine import find, replace, batch_replace, Edit

# Find text in a PDF
matches = find("document.pdf", "Software Engineer")
print(f"Found {len(matches)} matches")

# Replace a single match
result = replace("document.pdf", matches[0], "Senior Engineer", "output.pdf")
print(result.fidelity_report)
# FidelityReport(font_preserved=True, font_substituted=None,
#                overflow_detected=False, reflow_applied=False,
#                glyphs_missing=[])

# Batch replace multiple edits at once
edits = [
    Edit(find="John Doe", replace="Jane Smith"),
    Edit(find="2024", replace="2025"),
]
results = batch_replace("document.pdf", edits, "updated.pdf")
for r in results:
    print(r.success, r.font_action)  # True, 'kept'
```

## Core operations

### Text search

```python
from pdf_edit_engine import find, get_text, get_fonts

# Find text with operator-level precision
matches = find("doc.pdf", "search text")
matches = find("doc.pdf", "search text", page=0)              # specific page
matches = find("doc.pdf", "search text", case_sensitive=False) # case-insensitive

# Extract all text
text = get_text("doc.pdf")
text = get_text("doc.pdf", page=0)  # specific page

# List fonts used
fonts = get_fonts("doc.pdf")
for f in fonts:
    print(f.name, f.encoding_type, f.glyph_count)
```

### Text replacement (format-preserving)

```python
from pdf_edit_engine import find, replace, replace_all, batch_replace, Edit

# Replace a single match (returned by find())
matches = find("doc.pdf", "old text")
result = replace("doc.pdf", matches[0], "new text", "out.pdf")

# Replace all occurrences
results = replace_all("doc.pdf", "old text", "new text", "out.pdf")

# Batch replace (multiple find/replace pairs, single pass)
edits = [Edit(find="foo", replace="bar"), Edit(find="baz", replace="qux")]
results = batch_replace("doc.pdf", edits, "out.pdf")

# Dry run — simulate without writing
result = replace("doc.pdf", matches[0], "new text", "out.pdf", dry_run=True)
```

All edit functions return `EditResult` with:
- `success: bool` — whether the edit was applied
- `font_action: "kept" | "extended" | "substituted" | "failed"`
- `fidelity_report: FidelityReport` — detailed quality report

### Font management

The engine uses a two-tier approach to handle fonts:

**Tier 1 — CMap-only extension (fast path):** Many "subsetted" fonts contain far more glyphs than the CMap exposes. When needed glyphs exist in the embedded font but lack CMap entries, the engine adds mappings without touching the font binary.

**Tier 2 — Full font extension (fallback):** When glyphs are truly missing, the engine matches to a system font and re-embeds with `--retain-gids` to preserve existing text.

```python
from pdf_edit_engine import analyze_subset, can_render

# Analyze a font's embedded subset
info = analyze_subset("doc.pdf", "F1")
print(info.glyph_count, info.encoding_type, info.is_subset)

# Check if a font can render specific text
can_render_all, missing = can_render(info, "Hello World!")
```

### Text layout

```python
from pdf_edit_engine import get_text_layout

# Get positioned text blocks with font info
blocks = get_text_layout("doc.pdf")
for b in blocks:
    print(f"({b.x:.0f}, {b.y:.0f}) {b.font_name} {b.font_size}pt: {b.text[:50]}")

# Filter by page
blocks = get_text_layout("doc.pdf", page=0)
```

Each `TextBlock` contains: `text`, `x`, `y`, `width`, `height`, `font_name`, `font_size`, `page`.

### Annotations

```python
from pdf_edit_engine import get_annotations, update_annotation_uri, delete_annotation

# List all annotations
annots = get_annotations("doc.pdf")
for a in annots:
    print(f"[{a.page}] {a.subtype} at {a.rect} → {a.uri}")

# Change a link's URL
update_annotation_uri("doc.pdf", annots[0], "https://new-url.com", "out.pdf")

# Remove an annotation
delete_annotation("doc.pdf", annots[0], "out.pdf")
```

### Paragraph reflow

When replacement text is wider than the original, the engine automatically reflows the paragraph using greedy line breaking:

```python
from pdf_edit_engine import detect_paragraphs

# Detect paragraph blocks on a page
paragraphs = detect_paragraphs("doc.pdf", page=0)
for p in paragraphs:
    print(p.full_text[:50], f"({p.line_count} lines)")
```

Reflow is triggered automatically during `replace()` when `reflow=True` (default).

### PDF operations (15 wrapper functions)

Thin wrappers around pikepdf for common PDF operations:

| Operation | Function | Description |
|-----------|----------|-------------|
| Merge | `merge_pdfs(paths, output)` | Combine multiple PDFs |
| Split | `split_pdf(path, output_dir)` | Split into individual pages |
| Reorder | `reorder_pages(path, order, output)` | Rearrange page order |
| Rotate | `rotate_pages(path, pages, angle, output)` | Rotate pages (90/180/270) |
| Delete | `delete_pages(path, pages, output)` | Remove pages |
| Crop | `crop_pages(path, box, output)` | Crop to bounding box |
| Metadata | `edit_metadata(path, metadata, output)` | Edit title, author, etc. |
| Bookmark | `add_bookmark(path, title, page, output)` | Add outline entry |
| Encrypt | `encrypt_pdf(path, owner_pw, user_pw, output)` | Password-protect |
| Decrypt | `decrypt_pdf(path, password, output)` | Remove encryption |
| Hyperlink | `add_hyperlink(path, page, bbox, uri, output)` | Add clickable link |
| Highlight | `add_highlight(path, page, quad_points, output)` | Add highlight annotation |
| Flatten | `flatten_annotations(path, output)` | Remove annotations |
| Fill form | `fill_form(path, field_values, output)` | Fill AcroForm fields |
| Watermark | `add_watermark(path, watermark_pdf, output)` | Add PDF underlay |

## Supported operations

| Operation | Status | Notes |
|-----------|--------|-------|
| find / replace | Stable | Identity-H (CIDFont) + WinAnsi |
| batch_replace | Stable | Multiple edits in single pass |
| Font extension | Stable | Tier 1 CMap + Tier 2 full re-embed |
| Paragraph reflow | Stable | Single-paragraph, greedy line breaking |
| get_text_layout | Stable | Position, font, size for every text block |
| Annotations | Stable | get, update URI, delete, move |
| 15 wrapper ops | Stable | merge, split, rotate, encrypt, etc. |
| dry_run mode | Stable | Preview edits without writing |
| Cross-page reflow | Not supported | Planned for v2 |
| Image editing | Not supported | Planned for v2 |
| Table detection | Not supported | Planned for v2 |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Public API                        │
│  find() → replace() → batch_replace()               │
└────────┬──────────┬──────────┬──────────┬───────────┘
         │          │          │          │
   ┌─────▼────┐ ┌──▼─────┐ ┌─▼──────┐ ┌─▼───────┐
   │ locator  │ │surgeon │ │ fonts  │ │ wrapper │
   │          │ │        │ │        │ │         │
   │pdfminer  │ │pikepdf │ │pikepdf │ │ pikepdf │
   │+ pikepdf │ │  only  │ │+fonts  │ │  only   │
   └──────────┘ └────────┘ └────────┘ └─────────┘
         │          │          │
   ┌─────▼────┐ ┌──▼─────┐ ┌─▼──────┐
   │ models   │ │ state  │ │ reflow │
   └──────────┘ └────────┘ └────────┘
```

**locator** — Text search using pdfminer.six for extraction and pikepdf for content stream correlation. Maps extracted text to specific operators and byte positions.

**surgeon** — Content stream modification. Takes a `TextMatch`, builds replacement operators, handles Identity-H CID encoding, calls FontExtender if needed.

**fonts** — Font analysis and subset extension. Two-tier: CMap-only fast path when glyphs exist in embedded font, full re-embed fallback when they don't.

**reflow** — Paragraph reflow for text that changes length. Uses fonttools for glyph metrics to calculate line breaks and positioning.

**wrapper** — 15 pikepdf wrapper operations (merge, split, rotate, encrypt, etc.). Thin wrappers, 5-20 lines each.

## How it works

1. **Index** — `find()` builds a content element index by interpreting the page's content stream operators, tracking graphics state (font, position, color) through BT/ET blocks
2. **Match** — Extracted characters are assembled into a flat string and searched with position-aware substring matching
3. **Surgery** — `replace()` encodes new text using the font's Identity-H CID mapping, constructs replacement TJ operators, and splices them into the content stream
4. **Font extension** — If the new text needs glyphs not in the font's CMap, the engine extends the subset (CMap-only or full re-embed)
5. **Reflow** — If the replacement is wider, the engine detects the containing paragraph and reflows with greedy line breaking
6. **Serialize** — Modified operators are re-serialized via `pikepdf.unparse_content_stream()` and saved

## FidelityReport

Every edit returns a `FidelityReport` documenting exactly what happened:

```python
@dataclass
class FidelityReport:
    font_preserved: bool        # Original font kept?
    font_substituted: str | None  # Fallback font name (if any)
    overflow_detected: bool     # Text wider than available space?
    reflow_applied: bool        # Paragraph reflow triggered?
    glyphs_missing: list[str]   # Characters that couldn't be rendered
```

This is the key differentiator from tools that silently degrade formatting. AI agents and automated pipelines can inspect the report to verify edit quality before accepting changes.

## Tested generators

The test suite validates against PDFs from multiple generators:

| Generator | Encoding | Character Agreement |
|-----------|----------|-------------------|
| Chrome (Print to PDF) | Identity-H | 100% |
| Google Docs | Identity-H | 100% |
| reportlab (4 variants) | WinAnsi | 100% |
| pikepdf (synthetic) | WinAnsi | 100% |

85% code coverage.

## Comparison with PyMuPDF

| | pdf-edit-engine | PyMuPDF (redact-and-replace) |
|---|---|---|
| **Approach** | Content stream surgery | Redact area, re-insert text |
| **Font preservation** | Original font kept | Font substituted |
| **Layout preservation** | Operator-level precision | Approximate repositioning |
| **Fidelity reporting** | FidelityReport on every edit | Silent degradation |
| **License** | MIT | AGPL-3.0 |
| **Dependencies** | pikepdf + fonttools + pdfminer.six | Built-in (MuPDF C library) |

## Tech stack

| Library | Purpose | License |
|---------|---------|---------|
| [pikepdf](https://github.com/pikepdf/pikepdf) | Content stream parse/unparse, PDF manipulation | MPL-2.0 |
| [fonttools](https://github.com/fonttools/fonttools) | Font extraction, CMap parsing, glyph metrics | MIT |
| [pdfminer.six](https://github.com/pdfminer/pdfminer.six) | Text extraction with positional data | MIT |

## Development

```bash
git clone https://github.com/AryanBV/pdf-edit-engine.git
cd pdf-edit-engine
python -m venv .venv
.venv/Scripts/activate    # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -e ".[dev]"

make lint        # ruff check src/ tests/
make typecheck   # mypy strict
make test        # pytest with coverage
make all         # lint + typecheck + test
```

## Known Limitations

See [LIMITATIONS.md](LIMITATIONS.md) for a full list including text editing constraints, font handling caveats, encoding support, performance characteristics, and PDF compatibility notes.

## Contributing

Contributions welcome! Please run `make all` before submitting a PR. See [docs/architecture.md](docs/architecture.md) for module details and [docs/decisions.md](docs/decisions.md) for design rationale.

## License

MIT — see [LICENSE](LICENSE) for details.
