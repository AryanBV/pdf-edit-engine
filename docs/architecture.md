# Architecture

## Module Dependency Diagram

```
┌──────────────────────────────────────────────────────┐
│                    Public API (__init__.py)           │
│  find, replace, batch_replace, merge_pdfs, ...       │
└──────────┬───────────┬──────────┬───────────┬────────┘
           │           │          │           │
     ┌─────▼─────┐ ┌──▼────┐ ┌──▼──────┐ ┌──▼──────┐
     │  locator   │ │surgeon│ │  fonts   │ │ wrapper │
     │            │ │       │ │          │ │         │
     │ pdfminer   │ │pikepdf│ │ pikepdf  │ │ pikepdf │
     │ + pikepdf  │ │ only  │ │+fonttools│ │  only   │
     └─────┬──────┘ └──┬────┘ └──┬──────┘ └─────────┘
           │           │          │
           ▼           ▼          ▼
     ┌──────────┐ ┌──────────┐ ┌──────────┐
     │  models  │ │  state   │ │  reflow  │
     │(dataclass│ │(graphics │ │(fonttools│
     │  es)     │ │ tracker) │ │ metrics) │
     └──────────┘ └──────────┘ └──────────┘
```

## Module Descriptions

**models.py** — Shared data classes: TextCharacter, TextMatch, EditResult, FidelityReport,
FontInfo, Edit, ContentElement, GraphicsStateSnapshot. No logic, no imports beyond stdlib.

**locator.py** — Text location using pdfminer.six for extraction and pikepdf for content
stream correlation. Maps extracted text back to specific operators and byte positions.

**surgeon.py** — Content stream modification. Takes a TextMatch, builds replacement operators,
handles encoding (Identity-H CID encoding), calls FontExtender if needed.

**fonts.py** — Font analysis and subset extension. Two-tier: CMap-only fast path when glyphs
exist in embedded font, full re-embed fallback when they don't.

**reflow.py** — Paragraph reflow for text that changes length. Uses fonttools for glyph
metrics to calculate line breaks and positioning.

**state.py** — Graphics state tracker. Processes content stream operators to maintain
current transformation matrix, font, colors, etc.

**wrapper.py** — 15 pikepdf wrapper operations (merge, split, rotate, encrypt, etc.).
Thin wrappers around pikepdf's API, 5-20 lines each.

## Error Hierarchy

```
PDFEditError (base)
├── FontNotFoundError    — font not in PDF or not on system
├── EncodingError        — CMap parse failure or unmappable characters
├── OperatorError        — content stream parse/unparse failure
└── ReflowError          — paragraph reflow failure (overflow, etc.)
```

## Data Flow: Replace Operation

```
1. locator.find(pdf, "old text")     → list[TextMatch]
2. For each match:
   a. Extract FontInfo from match
   b. fonts.can_render(font_info, "new text") → (bool, missing_glyphs)
   c. If missing glyphs: fonts.extend_subset(...)
   d. surgeon.replace(pdf, match, "new text") → EditResult
3. EditResult contains FidelityReport:
   - font_preserved: bool
   - font_substituted: str | None
   - overflow_detected: bool
   - reflow_applied: bool
   - glyphs_missing: list[str]
```
