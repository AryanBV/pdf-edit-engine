# Architecture

## Module dependency diagram (v0.1.2)

```
┌────────────────────────────────────────────────────────────────────┐
│                       Public API (__init__.py)                     │
│  find, replace, replace_all, batch_replace, replace_block,         │
│  merge_pdfs, get_text, get_annotations, …                          │
└────────────────────────────────────────────────────────────────────┘
       │           │           │            │             │
       ▼           ▼           ▼            ▼             ▼
  ┌─────────┐ ┌─────────┐ ┌─────────────┐ ┌────────┐ ┌────────────┐
  │ locator │ │ surgeon │ │ structural  │ │ wrapper │ │annotations│
  │         │ │         │ │             │ │         │ │           │
  │pdfminer │ │ pikepdf │ │   pikepdf   │ │ pikepdf │ │  pikepdf  │
  │+pikepdf │ │  only   │ │  + reflow   │ │  only   │ │   only    │
  └────┬────┘ └────┬────┘ └──────┬──────┘ └────┬────┘ └─────┬─────┘
       │           │             │              │            │
       └─────┬─────┴─────┬───────┴──────┬───────┴─────┬──────┘
             │           │              │             │
             ▼           ▼              ▼             ▼
        ┌────────┐ ┌─────────┐ ┌─────────────┐ ┌────────────┐
        │ reflow │ │  state  │ │   fonts     │ │  encoding  │
        │        │ │         │ │             │ │            │
        │fonttools│ │ pikepdf │ │  fontTools  │ │  pikepdf   │
        │+pikepdf│ │  only   │ │  + pikepdf  │ │ +pdfminer  │
        └────┬───┘ └─────────┘ └──────┬──────┘ └────────────┘
             │                        │
             ▼                        ▼
       ┌──────────┐         ┌────────────────┐
       │ widths   │         │ system_fonts   │
       │(parsing) │         │ (filesystem    │
       │          │         │  font discovery)│
       └──────────┘         └────────────────┘

  Used by every public-API entry point as a leaf:
   ┌────────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────┐
   │ _pathutil  │  │   models     │  │  errors  │  │fragments │
   │ (open_pdf, │  │  (dataclasses│  │ (PDF-    │  │ (TJ frag-│
   │ output     │  │  only — no   │  │ EditError│  │ ment re- │
   │ validation)│  │  logic save  │  │ subclass │  │ construc-│
   │            │  │  __post_init__│ │  tree)   │  │ tion)    │
   └────────────┘  └──────────────┘  └──────────┘  └──────────┘
```

## Module descriptions

**`__init__.py`** — Public API surface. Exports the curated subset
of names listed in `__all__`; everything not in `__all__` is
internal to the package.

**`_pathutil.py`** — Internal utilities: `validate_output_path`,
`validate_output_dir`, and **`open_pdf`** — the single canonical
entry point for opening a PDF. Every public API entry point routes
through `open_pdf`; raw `pikepdf.Pdf.open` calls outside this module
are an architectural violation. Translates pikepdf and filesystem
exceptions into `PDFEditError` subclasses (INV-L-1 root fix). Also
houses the symlink/junction-traversal check used by both
`validate_output_*` helpers.

**`errors.py`** — `PDFEditError` and four subclasses
(`FontNotFoundError`, `EncodingError`, `OperatorError`,
`ReflowError`). Tiny module, no logic.

**`models.py`** — Shared dataclasses: `TextCharacter`, `TextMatch`,
`EditResult`, `FidelityReport`, `FontInfo`, `Edit`,
`ContentElement`, `GraphicsStateSnapshot`, `Paragraph`,
`TextBlock`. The only "logic" present is
`EditResult.__post_init__`, a contract guard that enforces
INV-J-3 (`overflow_detected=True` implies a corresponding warning
in the `warnings` list). All other fields are inert data. No
imports beyond stdlib.

**`fragments.py`** — TJ-array reconstruction helper. Used by
`locator` to map TJ items back to logical character positions.

**`encoding.py`** — Font encoding and decoding.
`FontResolver` understands four encoding paths (Identity-H,
WinAnsi, MacRoman, custom `/Differences`). `FontResolverCache`
keys resolvers on the font dictionary's object generation pair so
shared fonts across pages share one cached instance.

**`widths.py`** — Glyph width-table parsing for both CIDFonts (`/W`)
and simple fonts (`/Widths` + `/FirstChar`). `GlyphWidthCache`
caches the parsed dicts per page+font.

**`state.py`** — `GraphicsStateTracker` walks a parsed content
stream and tracks CTM, text matrix, fill color, font, and text-
state knobs (Tc, Tw, Tz, TL, Tr). Stroke state and text-rise are
intentionally not tracked (every consumer reads only fill_color).

**`locator.py`** — Text location and extraction. Builds a
`ContentElement` index for every page using a custom content-stream
interpreter that delegates state to `state.GraphicsStateTracker`
and decoding to `encoding.FontResolverCache`. Public surface:
`find`, `get_text`, `get_text_layout`, `get_fonts`,
`extract_bbox_text`.

**`surgeon.py`** — Content-stream surgery. Takes a `TextMatch`,
validates it via `_assert_match_addressable` (INV-B-3 contract
guard), builds replacement operators, handles encoding (Identity-H
CID + WinAnsi byte), and calls `fonts.extend_subset` if needed.
Public surface: `replace`, `replace_all`, `batch_replace`.

**`fonts.py`** — Font subset analysis and extension. Two-tier:
CMap-only fast path when the new glyph already exists in the
embedded font; **Tier 1.5 in-place glyph injection** (loads the
embedded `/FontFile2` with fontTools, appends the missing glyph
outline at a fresh GID, re-serializes — pre-existing CIDs are
preserved) when it does not. Public surface: `analyze_subset`,
`can_render`, `extend_subset`. The optional `substitution_log`
kw-argument captures metric-equivalent fallback events (INV-C-4)
so callers can populate `FidelityReport.font_substituted`.

**`system_fonts.py`** — Filesystem-only system-font discovery.
Walks the platform's standard font directories (Windows
`C:/Windows/Fonts/...`, macOS `/Library/Fonts/`, Linux
`/usr/share/fonts/`). Two-pass strategy: filename heuristic first
(80% hit rate, no parsing), then full nameID-6 scan (cached).
`_METRIC_EQUIVALENTS` maps proprietary fonts to open-source
metric-equivalents (Calibri → Carlito etc.). The new
`_find_font_with_origin` returns both the resolved path AND the
substituted name — used by `_extend_tier2` to surface the
substitution to callers.

**`reflow.py`** — Paragraph reflow for text whose replacement
length changes. Detects paragraphs (BT/ET grouping + same-y-line
clustering), greedy-line-breaks against measured glyph widths,
and re-renders. Calls `_shift_content_below_inplace` to make
vertical room when the rewritten paragraph is taller. Public
surface: `detect_paragraphs`, `reflow_paragraph`.

**`structural.py`** — Bbox-based "structural" edits — replace,
delete, insert, batch-replace bounded regions. Larger than the
TextMatch-driven `surgeon` because it has to detect heading vs.
body fonts, marker characters (bullets), and indent positions
within the bbox. `_remove_orphaned_annotations` (split out in
v0.1.2) drops hyperlink annotations whose URI keywords no longer
match the replacement text. Public surface: `replace_block`,
`batch_replace_block`, `delete_block`, `insert_text_block`,
`compute_uniform_layout`, `shift_content_below`.

**`annotations.py`** — Read, create, modify, delete PDF
annotations. `Annotation` dataclass + 5 verbs:
`get_annotations`, `add_annotation`, `update_annotation_uri`,
`delete_annotation`, `move_annotation`.

**`wrapper.py`** — 15 thin pikepdf wrappers: `merge_pdfs`,
`split_pdf`, `reorder_pages`, `rotate_pages`, `delete_pages`,
`crop_pages`, `edit_metadata`, `add_bookmark`, `encrypt_pdf`,
`decrypt_pdf`, `add_hyperlink`, `add_highlight`, `add_watermark`,
`flatten_annotations`, `fill_form`. Each is 5-20 LOC.

## Error hierarchy

```
PDFEditError (base)
├── FontNotFoundError    — font not in PDF or not on system
├── EncodingError        — CMap parse failure or unmappable chars
├── OperatorError        — content stream parse/unparse failure
└── ReflowError          — paragraph reflow failure (overflow, etc.)
```

Every public-API entry point in this package raises a `PDFEditError`
subclass on failure. Raw `pikepdf.PasswordError`, `pikepdf.PdfError`,
or `OSError` never reach a caller (INV-L-1 root fix in v0.1.2). The
translation lives in `_pathutil.open_pdf`; new modules must route
through it.

## Data flow: replace operation

```
1. locator.find(pdf, "old text")           → list[TextMatch]
2. For each match m:
   a. surgeon.replace validates m via _assert_match_addressable
      (refuses stale matches with OperatorError)
   b. resolver.can_encode(new_text) → (ok, missing_chars)
   c. If missing: fonts.extend_subset(pdf, page, font_name, missing,
                                     substitution_log=log)
      - Tier 1 (CMap-only) when glyph in embedded font
      - Tier 1.5 (in-place injection) otherwise
      - Metric-equivalent name (if any) appended to log
   d. surgeon writes replacement operators in place
   e. EditResult constructed with FidelityReport:
      - font_preserved: bool
      - font_substituted: log[0] if log else None
      - overflow_detected: bool   ← __post_init__ enforces a
        corresponding "overflow" warning is present
      - reflow_applied: bool
      - glyphs_missing: list[str]
3. pdf.save(output_path) — output_path was validated by
   _pathutil.validate_output_path before any I/O.
```
