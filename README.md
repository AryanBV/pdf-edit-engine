# pdf-edit-engine

[![PyPI](https://img.shields.io/pypi/v/pdf-edit-engine)](https://pypi.org/project/pdf-edit-engine/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CI](https://github.com/AryanBV/pdf-edit-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/AryanBV/pdf-edit-engine/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-85%25-yellowgreen)]()

Format-preserving PDF text editing. Modify text in existing PDFs at the content stream level — fonts, layout, and spacing stay intact.

## The problem

Editing text in existing PDFs is a common need — names, dates, labels, typos. But PDF was designed as a display format, not an editing format. Text is stored as positioned glyph indices, not editable strings.

Most tools handle this in one of two ways: redact the area and re-insert text with a substitute font, or extract content to another format and re-render. Both approaches lose the original typographic fidelity.

pdf-edit-engine takes a different approach:

| | Redact-and-replace | pdf-edit-engine |
|---|---|---|
| **Method** | White out text, stamp new text | Modify content stream operators in-place |
| **Font** | Substituted (often Helvetica) | Original font preserved |
| **Layout** | Re-calculated | Exact original positioning |
| **Quality feedback** | None — silent degradation | FidelityReport on every edit |

## Quick start

```bash
pip install pdf-edit-engine
```

Requires Python 3.12+. No external binaries, no API keys, no network calls.

```python
from pdf_edit_engine import find, replace

# Find text in a PDF
matches = find("document.pdf", "Software Engineer")

# Replace with format preservation
result = replace("document.pdf", matches[0], "Senior Engineer", "output.pdf")

# Every edit reports exactly what happened
report = result.fidelity_report
report.font_preserved      # True — original font kept
report.overflow_detected   # False — text fits in original space
report.glyphs_missing      # [] — all characters rendered
```

## FidelityReport

Every edit function returns a `FidelityReport` documenting exactly what changed:

```python
@dataclass
class FidelityReport:
    font_preserved: bool        # Original font kept?
    font_substituted: str | None  # Fallback font name (if any)
    overflow_detected: bool     # Text wider than available space?
    reflow_applied: bool        # Paragraph reflow triggered?
    glyphs_missing: list[str]   # Characters that couldn't be rendered
```

Automated pipelines and AI agents inspect these fields to verify edit quality programmatically — no manual PDF review needed. All edit functions also support `dry_run=True` to preview the report without writing to disk.

## Comparison

| | pdf-edit-engine | PyMuPDF | reportlab |
|---|---|---|---|
| **Approach** | Modify operators in-place | Redact + re-insert | Create new PDF |
| **Edits existing PDFs** | Yes | Yes (destructive) | No |
| **Font preservation** | Original kept | Substituted | N/A |
| **Layout preservation** | Operator-level precision | Approximate | N/A |
| **Edit verification** | FidelityReport | None | None |
| **dry_run preview** | Yes | No | No |
| **Font subset extension** | 2-tier (CMap + re-embed) | No | No |
| **License** | MIT | AGPL-3.0 | BSD |

## Key capabilities

| Category | Functions | Description |
|----------|-----------|-------------|
| Search | `find`, `get_text`, `get_text_layout`, `get_fonts`, `extract_bbox_text` | Locate text with operator-level precision, extract positioned blocks |
| Replace | `replace`, `replace_all`, `batch_replace` | Format-preserving replacement with kerning distribution |
| Structural | `replace_block`, `batch_replace_block`, `delete_block`, `insert_text_block` | Bbox-based content block operations |
| Fonts | `analyze_subset`, `can_render`, `extend_subset` | Two-tier font extension (CMap-only fast path + full re-embed) |
| Reflow | `detect_paragraphs`, `reflow_paragraph` | Paragraph detection and greedy line-breaking |
| PDF ops | `merge_pdfs`, `split_pdf`, `rotate_pages`, `encrypt_pdf`, +11 more | 15 pikepdf wrappers for document manipulation |
| Annotations | `get_annotations`, `add_annotation`, `update_annotation_uri`, `delete_annotation`, `move_annotation` | Read, create, modify, remove annotations |

All edit functions support `dry_run=True` to preview changes without writing.

## Usage examples

### Batch replace

```python
from pdf_edit_engine import batch_replace, Edit

edits = [
    Edit(find="John Doe", replace="Jane Smith"),
    Edit(find="2024", replace="2025"),
    Edit(find="Draft", replace="Final"),
]
results = batch_replace("contract.pdf", edits, "updated.pdf")

for r in results:
    assert r.success and r.fidelity_report.font_preserved
```

### Font analysis before editing

```python
from pdf_edit_engine import analyze_subset, can_render

info = analyze_subset("document.pdf", "F1")
ok, missing = can_render(info, "Resume — Pro Edition")
# ok=True if all glyphs available; missing lists gaps
```

For structural editing, annotations, reflow, and all 15 PDF operations, see the [API exports](src/pdf_edit_engine/__init__.py) and [architecture docs](docs/architecture.md).

## How it works

1. **Index** — `find()` interprets content stream operators (BT/ET blocks), tracking graphics state through each page
2. **Match** — Characters assembled into a string; position-aware matching locates the target across split operators
3. **Encode** — Replacement text encoded using the font's CID mapping (Identity-H) or byte encoding (WinAnsi), with micro-kerning distributed across glyphs to match original text width
4. **Extend** — If new text needs glyphs not in the font's CMap, the subset is extended: CMap-only when glyphs exist in the font binary, full re-embed (with `--retain-gids`) when they don't
5. **Reflow** — If replacement is wider than the original, the containing paragraph is reflowed with greedy line breaking
6. **Serialize** — Modified operators re-serialized via `pikepdf.unparse_content_stream()` and saved

<details>
<summary>Architecture</summary>

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

**locator** — Text search using pdfminer.six for extraction and pikepdf for content stream correlation.

**surgeon** — Content stream modification with Identity-H CID encoding and kerning-aware replacement.

**fonts** — Font analysis and subset extension. Two-tier: CMap-only fast path when glyphs exist in embedded font, full re-embed when they don't.

**reflow** — Paragraph reflow using fonttools for glyph metrics and greedy line breaking.

**wrapper** — 15 pikepdf wrapper operations (merge, split, rotate, encrypt, etc.).

</details>

## AI agent integration

pdf-edit-engine powers [@aryanbv/pdf-edit-mcp](https://github.com/AryanBV/pdf-edit-mcp) — a TypeScript MCP server that exposes 38 tools for AI agents to edit PDFs through the [Model Context Protocol](https://modelcontextprotocol.io).

```
AI Agent (Claude, GPT, etc.)
    ↓  MCP protocol (stdio)
pdf-edit-mcp  (TypeScript, 38 tools)
    ↓  JSON-RPC bridge
pdf-edit-engine  ← you are here
```

Several design choices in the engine exist specifically for programmatic consumers: `FidelityReport` lets agents verify edit quality without visual inspection, `dry_run=True` lets agents preview before committing, and the structured error hierarchy (`FontNotFoundError`, `EncodingError`, `OperatorError`, `ReflowError`) enables targeted recovery logic.

Install the MCP server: `npx -y @aryanbv/pdf-edit-mcp`

## Performance

Benchmarks on Windows 11, Python 3.12, WinAnsi PDFs:

| Operation | Input | Time |
|-----------|-------|------|
| `get_text()` | 100-page PDF | ~0.3s |
| `find()` | 100-page PDF, 900 matches | ~0.3s |
| `replace()` | Single page | ~0.03s |
| `batch_replace()` | 50 edits | ~0.1s |

Identity-H PDFs (Chrome, Google Docs) may be slower due to CMap parsing. Performance scales linearly with page count. Memory stays under 500MB for 100-page operations.

## Tested PDF generators

CI runs on Python 3.12 and 3.13. The test suite validates against PDFs from multiple generators:

| Generator | Encoding | Character Agreement |
|-----------|----------|-------------------|
| Chrome (Print to PDF) | Identity-H | 100% |
| Google Docs | Identity-H | 100% |
| reportlab (4 variants) | WinAnsi | 100% |
| pikepdf (synthetic) | WinAnsi | 100% |

## Error handling

```
PDFEditError (base)
├── FontNotFoundError    — font not in PDF or not on system
├── EncodingError        — CMap parse failure or unmappable characters
├── OperatorError        — content stream parse/unparse failure
└── ReflowError          — paragraph reflow failure
```

All exceptions inherit from `PDFEditError`. Catch the base class for general error handling, or specific subclasses for targeted recovery.

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
source .venv/bin/activate      # Linux/macOS
# .venv\Scripts\activate       # Windows
pip install -e ".[dev]"

make lint        # ruff check src/ tests/
make typecheck   # mypy strict
make test        # pytest with coverage
make all         # lint + typecheck + test
```

## Known limitations

- Cross-paragraph reflow not supported — text reflows within a single paragraph only
- Type 3 fonts (bitmap/procedural) not supported for extension
- PDF/A compliance not maintained after editing
- Digital signatures invalidated by any edit (inherent to PDF signatures)

Full list: [LIMITATIONS.md](LIMITATIONS.md)

## Contributing

Contributions welcome. Run `make all` before submitting a PR. See [docs/architecture.md](docs/architecture.md) for module details and [docs/decisions.md](docs/decisions.md) for design rationale.

## License

MIT — see [LICENSE](LICENSE) for details.
