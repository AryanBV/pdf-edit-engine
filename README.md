# pdf-edit-engine

[![PyPI](https://img.shields.io/pypi/v/pdf-edit-engine)](https://pypi.org/project/pdf-edit-engine/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CI](https://github.com/AryanBV/pdf-edit-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/AryanBV/pdf-edit-engine/actions/workflows/ci.yml)
[![Audit suite](https://img.shields.io/badge/invariants-315%20probes-blueviolet)]()

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
    font_substituted: str | None    # Fallback font name (if any)
    overflow_detected: bool         # Text wider than available space?
    reflow_applied: bool            # Paragraph reflow triggered?
    glyphs_missing: list[str]       # Characters that triggered extension (pre-extension state)
    degradations: list[Degradation] # typed visual-fidelity events

    @property
    def font_preserved(self) -> bool:
        """Computed: True iff font_substituted is None and no
        FONT_AFFECTING_KINDS Degradation was emitted."""
```

Automated pipelines and AI agents inspect these fields to verify edit quality programmatically — no manual PDF review needed. The text-replace functions (`replace`, `replace_all`, `batch_replace`) support `dry_run=True` to preview the report without writing to disk.

### Degradations

When the engine produces output that may differ visually from the
original — or refuses an edit it cannot do faithfully — it appends a
typed `Degradation` event to `fidelity_report.degradations`. Each event
carries `kind`, `severity`, and a free-form `detail`:

```python
@dataclass(frozen=True)
class Degradation:
    kind: DegradationKind                      # one of the canonical values
    detail: str = ""                           # site-specific context
    severity: Literal["info", "warning", "error"] = "info"
```

The 30 canonical kinds (enumerable at runtime via
`pdf_edit_engine.DEGRADATION_KINDS`; Permissive enum policy — clients
should treat unknown kinds as opaque, not crash):

| Kind | Severity | Meaning |
|---|---|---|
| `font_extension_failed` | error | Replacement needs glyphs the engine couldn't add to the font. |
| `kerning_compressed` | warning | `Tz` factor < 95 — replacement is ≥5% wider than original. |
| `kerning_widened` | info | `Tz` factor > 105 — replacement is ≥5% narrower than original. |
| `heading_font_dropped` | warning | A heading font couldn't encode the text; fell back to body font. |
| `marker_font_dropped` | warning | A list-marker font couldn't encode the bullet; fell back to body font. |
| `paragraph_detection_low_confidence` | info | Detector flagged a possible table-cell merge (S5 signal). |
| `overflow_shift_clamped` | warning | Vertical shift was bounded by page geometry. |
| `overflow_shift_suppressed` | warning | Vertical shift was skipped entirely (no room below). |
| `line_height_compressed` | info | Line height was reduced to fit content. |
| `font_size_reduced` | info | Opt-in shrink-to-fit (`fit="shrink"`): font size was binary-searched DOWN to fit a fixed-height region. Non font-affecting (glyph identity unchanged). |
| `reflow_aborted_to_simple` | warning | Complex reflow failed; flat-replace fallback used. |
| `font_coverage_extended` | info | Embedded font's cmap was extended (Tier 1, outlines present). |
| `font_coverage_substituted` | warning | Glyph outlines were sourced from a system font (Tier 1.5). |
| `positioning_adjustment_skipped` | warning | Edited run's text matrix is rotated/sheared; trailing-text horizontal compensation was skipped (wrong-axis under rotation). Non font-affecting. |
| `rotated_text_unsupported` | warning | Edit on rotated/sheared text would route through reflow (which flattens rotation); refused instead. Non font-affecting. |
| `line_break_quality_degraded` | info | A re-wrap left a widow — a final line holding a single short word. Detect-and-surface only (output geometry unchanged). Non font-affecting. |
| `color_space_approximated` | warning | A non-device fill color (Separation/DeviceN/ICCBased/Pattern) could not be replayed verbatim on reflow; fell back to a device-color approximation. Non font-affecting. |
| `indent_flattened` | info | A **multi-line** paragraph carried a genuine but un-classifiable indent (non-monotone / mutually-inconsistent continuation x-starts); fell back to flush. A plain single-line paragraph is just flush and does NOT emit this. Output geometry unchanged. Non font-affecting. |
| `linearization_dropped` | info | A linearized (Fast Web View) input could not be re-linearized on save; fell back to a normal save. Emitted only on that fallback (never when preservation succeeds, never for a non-linearized input). Non font-affecting. |
| `font_subset_introspection_failed` | warning | An embedded font binary could not be parsed to count glyphs (read path, e.g. `get_fonts`); `glyph_count` reported 0 (unknown) instead of fabricated from a sparse `/W` dict. Read-path only. Non font-affecting. |
| `font_substituted_from_user_fonts` | warning | The resolved system font for Tier 1.5 came from a per-platform user-writable font directory (origin surface). The font WAS found and used. Non font-affecting. |
| `tounicode_recovered` | error | A new-glyph replace targeted a Type0/Identity-H font whose CID→Unicode map was recovered from the embedded cmap (no `/ToUnicode`); glyph injection needs a `/ToUnicode`, so the edit refused. Font-affecting. |
| `untextable_cidfont` | error | A Type0 font had no usable `/ToUnicode` and embedded-cmap recovery was impossible; the text is unaddressable. Font-affecting. |
| `font_stream_too_large` | warning | An embedded font / CMap / ToUnicode stream's decompressed size exceeded the bound (Flate decompression-bomb guard); the edit was refused before any glyph surgery (companion to `font_extension_failed`). Non font-affecting. |
| `ligature_substituted` | info | The re-encode chose a ligature CID — a mandatory ligature (always applied) or an opted-in discretionary one. A different glyph within the same embedded font (no font swap). Non font-affecting. |
| `deletion_residual_text` | warning | A deletion left provable residual deleted text in the edited region (keep-slot emptying failed to clear a glyph, or a bbox show-text op was missed). Drives `success=False`. Non font-affecting. |
| `inline_image_present` | info | A `BI/ID/EI` inline image lies in/near a deletion span. Advisory only — the deletion still proceeds (operator-index addressing survives). Non font-affecting. |
| `scriptless_reflow_unsupported` | info | A spaceless paragraph in a dictionary-segmented script (Thai/Lao/Khmer/Myanmar) has no UAX#14 break opportunity; the run is left honestly unwrapped. CJK and Latin never emit it. Non font-affecting. |
| `encryption_dropped` | warning | An encrypted input could not be re-encrypted on save; the edit fell back to an unencrypted output. Emitted only on a genuine re-encryption failure (never on the success path). Non font-affecting. |
| `multi_match_same_operator_unsupported` | warning | Two or more matches splice into the same show-text operator with a length-changing replacement; the colliding matches were refused (`success=False`) before any mutation to avoid stale-byte-slice corruption. Matches in different operators still edit. Non font-affecting. |

**`degradations` is the visual-fidelity gate, not `font_preserved`.** For
agentic consumers building gating logic, key off `degradations` first;
`font_preserved` is for identity-only signal (it's True even when
`kerning_compressed` or `font_coverage_extended` fired, because those
preserve glyph identity).

## Comparison

| | pdf-edit-engine | PyMuPDF | reportlab |
|---|---|---|---|
| **Approach** | Modify operators in-place | Redact + re-insert | Create new PDF |
| **Edits existing PDFs** | Yes | Yes (destructive) | No |
| **Font preservation** | Original kept | Substituted | N/A |
| **Layout preservation** | Operator-level precision | Approximate | N/A |
| **Edit verification** | FidelityReport | None | None |
| **dry_run preview** | Yes | No | No |
| **Font subset extension** | 2-tier (CMap + Tier 1.5 in-place injection) | No | No |
| **License** | MIT | AGPL-3.0 | BSD |

## Key capabilities

| Category | Functions | Description |
|----------|-----------|-------------|
| Search | `find`, `get_text`, `get_text_layout`, `get_fonts`, `extract_bbox_text` | Locate text with operator-level precision, extract positioned blocks |
| Replace | `replace`, `replace_all`, `batch_replace` | Format-preserving replacement with kerning distribution |
| Structural | `replace_block`, `batch_replace_block`, `delete_block`, `insert_text_block` | Bbox-based content block operations |
| Fonts | `analyze_subset`, `can_render`, `extend_subset` | Two-tier font extension (CMap-only fast path + Tier 1.5 in-place glyph injection) |
| Reflow | `detect_paragraphs`, `reflow_paragraph` | Paragraph detection and greedy line-breaking |
| PDF ops | `merge_pdfs`, `split_pdf`, `rotate_pages`, `encrypt_pdf`, +11 more | 15 pikepdf wrappers for document manipulation |
| Annotations | `get_annotations`, `add_annotation`, `update_annotation_uri`, `delete_annotation`, `move_annotation` | Read, create, modify, remove annotations |

The text-replace functions (`replace`, `replace_all`, `batch_replace`) support `dry_run=True` to preview changes without writing.

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
4. **Extend** — If new text needs glyphs not in the font's CMap, the subset is extended: CMap-only when glyphs exist in the font binary, Tier 1.5 in-place glyph injection (the existing `/FontFile2` is loaded with fontTools, the missing glyph outline is appended, and the font is re-serialized) when they don't. Tier 1.5 preserves every pre-existing CID → glyph mapping
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

**fonts** — Font analysis and subset extension. Two-tier: CMap-only fast path when glyphs exist in embedded font; Tier 1.5 in-place glyph injection (preserves pre-existing CIDs) when they don't.

**reflow** — Paragraph reflow using fonttools for glyph metrics and greedy line breaking.

**wrapper** — 15 pikepdf wrapper operations (merge, split, rotate, encrypt, etc.).

</details>

## AI agent integration

pdf-edit-engine powers [pdf-edit-mcp](https://github.com/AryanBV/pdf-edit-mcp) — a Python MCP server (FastMCP) that exposes 38 tools for AI agents to edit PDFs through the [Model Context Protocol](https://modelcontextprotocol.io).

```
AI Agent (Claude, GPT, etc.)
    ↓  MCP protocol (stdio)
pdf-edit-mcp  (Python / FastMCP, 38 tools)
    ↓  in-process import
pdf-edit-engine  ← you are here
```

Several design choices in the engine exist specifically for programmatic consumers: `FidelityReport` lets agents verify edit quality without visual inspection, `dry_run=True` lets agents preview before committing, and the structured error hierarchy (`FontNotFoundError`, `EncodingError`, `OperatorError`, `ReflowError`) enables targeted recovery logic.

Install the MCP server: `pip install pdf-edit-mcp` (or run it with `uvx pdf-edit-mcp`).

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

## Audit suite

Beyond ~631 conventional unit tests, the engine ships **315 invariant probes**
across 108 files under `tests/invariants/`, covering layers from encoding,
content stream, font, locator, surgeon, structural, and reflow through wrapper,
annotations, the fidelity contract, public API, error hierarchy, security, and
differential checks vs pdfminer.six. Each probe quotes the invariant claim
verbatim in its docstring and runs as part of `make test`. The suite began with
the v0.1.2 release-gate audits — see `docs/audit-findings-v0.1.2.md`,
`docs/security-review-v0.1.2.md`, and `docs/comprehensive-audit-2026-05-02.md` —
and every v0.2.0 capability and honesty fix added its own permanent probes
(e.g. the `INV-W-*` robustness, `INV-C-*` font, and `INV-G-*` reflow series).
Every violation surfaced was root-fixed structurally rather than patched per
call site, and the probes are permanent regression guards.

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
