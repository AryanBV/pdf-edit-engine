# pdf-edit-engine

![Under Development](https://img.shields.io/badge/status-under%20development-yellow)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Format-preserving PDF text editing engine — edit text in existing PDFs while preserving fonts, layout, and visual fidelity.

Unlike redact-and-replace approaches (PyMuPDF), pdf-edit-engine modifies content stream operators in-place and extends font subsets as needed, preserving the original document structure.

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
   │ models  │ │ state  │ │ reflow │
   └──────────┘ └────────┘ └────────┘
```

- **TextLocator** — Find text with operator-level precision using pdfminer.six + pikepdf
- **OperatorSurgeon** — Modify content stream operators in-place
- **FontExtender** — Analyze and extend font subsets (CMap-only fast path + full re-embed fallback)
- **ReflowEngine** — Reflow paragraphs when text length changes
- **Wrapper** — 15 pikepdf utility operations (merge, split, rotate, encrypt, etc.)

## Planned API

```python
from pdf_edit_engine import find, replace, batch_replace, Edit

# Find text in a PDF
matches = find("resume.pdf", "Software Engineer")

# Replace a single match
result = replace("resume.pdf", matches[0], "Senior Engineer", "output.pdf")
print(result.fidelity_report)
# FidelityReport(font_preserved=True, font_substituted=None,
#                overflow_detected=False, reflow_applied=False,
#                glyphs_missing=[])

# Batch replace multiple edits
edits = [
    Edit(find="John Doe", replace="Jane Smith"),
    Edit(find="2024", replace="2025"),
    Edit(find="Software Engineer", replace="Senior Software Engineer"),
]
results = batch_replace("resume.pdf", edits, "updated.pdf")
for r in results:
    assert r.success
    assert r.fidelity_report.font_preserved
```

Every edit returns an `EditResult` with a `FidelityReport` — no silent degradation.

## Tech Stack

| Library | Purpose | License |
|---------|---------|---------|
| [pikepdf](https://github.com/pikepdf/pikepdf) | Content stream parse/unparse, PDF manipulation | MPL-2.0 |
| [fonttools](https://github.com/fonttools/fonttools) | Font extraction, CMap parsing, glyph metrics | MIT |
| [pdfminer.six](https://github.com/pdfminer/pdfminer.six) | Text extraction with positions | MIT |

## Development

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1  # Windows
pip install -e ".[dev]"

make lint        # ruff check
make typecheck   # mypy strict
make test        # pytest with coverage
make all         # lint + typecheck + test
```

## Contributing

Contributions welcome! This project is in early development. See [docs/architecture.md](docs/architecture.md) for module details and [docs/decisions.md](docs/decisions.md) for design rationale.

## License

MIT — see [LICENSE](LICENSE) for details.
