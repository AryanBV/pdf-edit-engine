# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.3] — 2026-05-05

### Added

- **Typed `Degradation` field on `FidelityReport`.** Every degraded
  result now appends a structured `Degradation` event (`kind`, `detail`,
  `severity`) to `fidelity_report.degradations`. Twelve canonical kinds
  cover kerning compression/widening, font extension success/failure,
  paragraph-detection low-confidence, overflow shift clamping/suppression,
  heading and marker font drops, line-height compression, reflow abort,
  and font coverage extension via cmap-only or system-font substitution.
  Permissive enum policy: clients should treat unknown kinds as opaque.
- **`docs/v0.1.3-release-notes.md`** — implementation post-mortem with
  per-phase summaries, deviation log, and v0.1.4 follow-ups.

### Changed (algorithm fixes)

- **Kerning Algo A — `Tz` horizontal scaling, no refusal threshold
  (ARY-290).** The pre-v0.1.3 `>0.5×` flat-fallback in
  `_encode_with_kerning` produced visibly squished/spread output and
  silently refused large-delta kerning. Replaced with the PDF `Tz`
  operator: glyph identity is preserved at any scale factor. Symmetric
  95-105 deadzone for Degradation emission (`kerning_compressed`
  warning <95, `kerning_widened` info >105). Pure decision lives in
  `surgeon._kerning_decision`.
- **`can_encode` strengthening (audit-bundle finding).** The non-CID
  branch of `FontResolver.can_encode` was lying when the encoding map
  had a codepoint but the embedded `/FontFile2` lacked the glyph or
  `/Widths` lacked the entry. Strengthened to verify all three:
  encoding-map ∧ `/FirstChar..LastChar` range ∧ `/Widths` entry ∧
  `/FontFile2` cmap glyph presence. Cmap check delegates to
  `fonts.font_has_codepoint` so `encoding.py` keeps no `fontTools`
  import (preserves the `CLAUDE.md` dep-boundary table).
- **Computed `font_preserved` (INV-J-8).** Converted from a stored
  dataclass field to a `@property` derived from `degradations` (none
  of kind in `FONT_AFFECTING_KINDS`) AND `font_substituted is None`.
  The pre-v0.1.3 stored field let constructors override the truth
  function — exactly the lying-success-path the audit flagged.
- **Four lying-success-path fixes.** `surgeon.py:603/625` and
  `reflow.py:1014/1037` returned `success=False` with `font_action="failed"`
  but `font_preserved=True` (architectural lie). Each site now appends a
  `font_extension_failed` Degradation (severity=error); the computed
  `font_preserved` correctly derives False because the kind is in
  `FONT_AFFECTING_KINDS`.
- **`glyphs_missing` semantic locked.** Pre-extension state is now
  recorded in `glyphs_missing` even after extension successfully fills
  the gap (audit-bundle finding #3). Information-preserving for callers
  who want to see what extension covered.
- **Paragraph-detector S5 surfacing (ARY-292 partial).** When the
  detector emits a paragraph that triggers the locked S5 signal
  (`paragraph_width / page_width >= 0.5` ∧ `avg_row_stub_coverage <
  0.55` ∧ `x_cluster_count >= 2`), a `paragraph_detection_low_confidence`
  Degradation (info) is added to the result. The detector grouping
  itself is unchanged in v0.1.3 — algorithm replacement is v0.1.4.
- **Non-CID Tier 1.5 extension (Phase 13).** Simple TrueType WinAnsi
  fonts can now extend their glyph coverage via system-font sourcing,
  not just Type0/Identity-H. Mirrors the existing CID Tier 1.5 path
  (`_extend_tier2`) for font-binary surgery; updates `/Encoding`
  `/Differences` + `/Widths` instead of `/ToUnicode` + `/W`. Free-byte
  allocation is deterministic (consecutive low-end starting at
  `/LastChar + 1`, skipping byte 127). Surfaces via the existing
  `font_coverage_substituted` Degradation when a system font (or
  metric-equivalent like Carlito for Calibri) sources the outline.
  M.4/M.5 inline guards in `_glyph_width_from_hmtx` and
  `_extend_simple_widths` re-raise `KeyError` / `ZeroDivisionError`
  / `ValueError` as `FontNotFoundError` so the canonical
  `_FONT_EXTEND_FAIL_EXCS` tuple catches them. M10 SOW launch gate
  now passes end-to-end ("Sarah Chen → Søren Müller" with ø + ü
  rendered cleanly).

### Contracts

- **`dry_run` ↔ `degradations` parity.** The list returned by
  `dry_run=True` is structurally equal to the list returned by
  `dry_run=False` for the same input. Verified by
  `tests/test_dry_run_parity.py` (design doc §4c).
- **INV-J-3 coexistence.** The v0.1.0+ `EditResult.warnings` list and
  the v0.1.3 `FidelityReport.degradations` list both populate for
  overflow events. The two lists carry parallel information until v0.2
  collapses the duplication. Verified by
  `tests/invariants/test_j_3_overflow_coexistence.py`.
- **INV-J-5 + INV-J-8 invariant probes** registered in
  `docs/decisions.md` and `docs/ultimate-audit-charter.md` Layer J.

### Fixed

- **`_FONTFILE2_CACHE` cross-PDF collision.** The Phase 5 cache was
  keyed on `(objgen[0], objgen[1])` which alias across `pikepdf.Pdf.open`
  instances (two PDFs both having a font at object 7,0 collide). Now
  keyed on `(id(font_dict.owner), objgen[0], objgen[1])` so each PDF
  instance has its own cache namespace.

### Removed

- **`_FONTFILE2_CACHE` and `_font_dict_key` removed.** The
  module-global cache in `fonts.py` was deleted as a root fix
  during Phase 13.4. The cache had two architectural issues that
  Phase 13.4 invariant probes surfaced (populate/evict key
  mismatch from the `pikepdf.Dictionary(font_obj)` copy stripping
  objgen; cross-Pdf `id(pdf)` recycling pollution) and had been
  functionally a no-op since pikepdf 10.5.1 anyway. Removed
  alongside its API surface: the `pdf` parameter on
  `font_has_codepoint`, `FontResolver.__init__`, and
  `FontResolverCache.__init__`. `font_has_codepoint` now re-parses
  `/FontFile2` on every call. No public-API impact.
- **`_FONT_EXTEND_FAIL_EXCS` moved from `reflow.py` to `fonts.py`**
  (its semantic home). `reflow.py`, `structural.py`, and
  `surgeon.py` all import from there. Fixes `surgeon.py`'s prior
  asymmetric font-extension catch (missing `OSError` + `TTLibError`).
  No public-API impact.

## [0.1.2] — 2026-04-25

### Fixed (post-audit hardening — 2026-05-02)

A senior-level re-audit (`docs/comprehensive-audit-2026-05-02.md`) ran
five parallel sub-agents over the whole codebase, cross-validated their
claims against the source, and de-noised ~30 raw findings down to six
verified ones. All six landed before v0.1.2 publish.

- **F1 — `_pathutil.open_pdf` did not catch generic `OSError`.**
  INV-L-1 ("no raw OSError reaches a caller") covered three narrow
  subclasses (`FileNotFoundError`, `IsADirectoryError`,
  `PermissionError`) but let bare `OSError` (network FS, sharing-
  violation, EBADF, ENOSPC, EIO) slip through. Added the residual
  catch.
- **F2 — `CLAUDE.md` dependency table contradicted reality.** Table
  claimed `reflow` uses fonttools-only; `reflow.py` imports pikepdf
  directly and uses it heavily. `architecture.md` was already truthful;
  CLAUDE.md was the drifting source. Updated to match.
- **F3 — README overclaimed `dry_run` support.** Lines 65 and 92 said
  "All edit functions support `dry_run=True`". Grep confirmed only
  `surgeon.replace`, `replace_all`, `batch_replace` actually do. A
  user calling `replace_block(..., dry_run=True)` would get a
  `TypeError`. Truthful version names the three functions explicitly.
- **F4 — `structural._replace_block_on_page` did not thread the
  `substitution_log`.** `_extend_font` accepts the kwarg, surgeon and
  reflow thread it correctly, but the bbox path's three call sites
  in `_replace_block_on_page` never passed one. Result: the metric-
  equivalent fallback (e.g. Carlito for Calibri inside `extend_subset`)
  silently lost its substitute name on `replace_block`/`batch_replace_block`.
  Threaded `substitution_log` through and updated the
  `font_substituted` selection to prefer the metric-equivalent name
  over the CID-fallback alternative. Closes INV-C-4 on the structural
  path. Probe at `tests/invariants/test_c_4_structural.py`.
- **F5 — Sequential-mode `prev_last_line_y` updated even on
  failure.** `batch_replace_block` sequential mode unconditionally
  updated the running cursor with each iteration's `last_y`. Failure
  branches return `bbox[3]` (top of the failed bbox) as a placeholder
  — propagating that into the next iteration's `first_line_y_override`
  mis-positioned subsequent successful sections. Gated the update
  on `result.success`. Probe at
  `tests/invariants/test_f_6_sequential_failure_no_misposition.py`.
- **F6 — CI gaps.** Coverage had no `fail_under` gate; macOS missing
  from CI matrix despite `system_fonts.py` walking
  `/Library/Fonts/`; no Dependabot config. Added all three.
- **CI hygiene cleanup.** Resolved 69 ruff errors in
  `tests/invariants/` introduced by recent coverage-test commits
  (TC003 stdlib imports moved to `TYPE_CHECKING` block, F841 unused
  variables removed, B905 `zip()` strict-flagged, E501 docstring
  lines wrapped). CI now genuinely green; `make all` clean.
- **Documentation: Concurrency and thread safety.** Added a section
  to `LIMITATIONS.md` documenting that ARY-283 removed module-level
  shared state but the `pikepdf.Pdf` handle, page mutations, and
  `fontTools.ttLib.TTFont` instances loaded inside
  `fonts.extend_subset` are not safe to share across threads.
  Recommends scaling by process (one worker per request) rather than
  thread. Doc-only; no semantics changed.
- **README accuracy refresh.** Coverage badge 87% → 88% (matches the
  audit-measured 88.13%). Invariant-probes badge 75 → 81 (reflects the
  two new probes added with F4 and F5). Audit-suite paragraph now
  references `docs/comprehensive-audit-2026-05-02.md` alongside the
  original audit + security-review docs, and counts the full 15
  violations surfaced across both audit waves (9 invariant + 6
  hardening).

### Fixed (Ultimate Audit Charter — v0.1.2 release-gate fixes)

The Ultimate Audit Charter (see `docs/ultimate-audit-charter.md`) was
executed in a fresh Opus 4.7 xhigh session. 9 invariant violations
surfaced; all 9 are root-fixed structurally — see
`docs/audit-findings-v0.1.2.md` for the full table.

- **INV-J-3** (P0, silent overflow): `EditResult.__post_init__` now
  enforces "overflow_detected=True ⇒ at least one warning referencing
  'overflow'" universally. Future code paths inherit a caller-visible
  signal by construction.
- **INV-B-3** (P0, stale TextMatch): `_assert_match_addressable` runs
  at every `TextMatch`-consuming entry (`surgeon.replace`,
  `reflow.reflow_paragraph`). Stale matches raise `OperatorError`
  with a re-run-find() instruction.
- **INV-L-1 / M-1 / M-4 / M-5** (P0/P1/P2, pikepdf exception leakage):
  `_pathutil.open_pdf` is the **sole canonical entry** for opening a
  PDF. All 16 prior `pikepdf.Pdf.open` call sites across locator,
  surgeon, structural, reflow, fonts, wrapper, annotations migrated.
  Two duplicated `_open_pdf` helpers collapsed.
- **INV-W0-7** (P1, orphan annotations): split
  `_sync_annotations_in_bbox` into orphan-detection + rect-shift.
  Orphan removal runs unconditionally from `replace_block` /
  `batch_replace_block`. Also fixed a latent write-back bug (the
  prior code mutated only a Python list, not `/Annots`).
- **INV-C-4** (P0, metric-equivalent surfacing): `extend_subset`
  gained an optional kw-only `substitution_log: list[str] | None`.
  Surgeon and reflow surface the first event through
  `FidelityReport.font_substituted`. Closes the v0.1.1 fidelity gap.
- **INV-C-5** (P2, find_font subset prefix): `_strip_subset_prefix`
  moved to `system_fonts.py`; `find_font` normalizes on every lookup
  so callers don't have to pre-strip.
- **INV-N-1** (P1, locator extraction): two real bugs fixed.
  (1) `_group_into_lines` / `_build_flat_string` used the current
  element's line-height as the gap threshold — a tall badge absorbed
  a shorter heading. Fixed to symmetric `min(prev_h, curr_h) * 0.5`.
  (2) Same functions used `avg_char_width * 0.5` of the previous
  fragment as the space threshold — single-glyph fragments produced
  unstable thresholds. Fixed to `font_size * 0.25` (canonical
  space-glyph proxy). Both bugs visible on Chrome-printed PDFs that
  emit one glyph per text-showing operator.

### Added

- **`tests/invariants/`** — 75 invariant probes across layers A-N.
  Each probe's docstring quotes the invariant verbatim. Suite runs
  as part of `make test`.
- **`docs/audit-findings-v0.1.2.md`** — audit findings table.
- **`docs/ultimate-audit-charter.md`** — invariant-driven audit
  framework for future release gates.

### Fixed (pre-audit, also in 0.1.2)

- **ARY-283** (architecture debt): Deleted the two module-level `FontResolverCache` and the `_cached_pdf_path` guard from `surgeon.py` and `structural.py`. Every public entrypoint (`replace`, `replace_all`, `batch_replace`, `replace_block`, `batch_replace_block`, `insert_text_block`) now constructs a fresh `FontResolverCache` plus `GlyphWidthCache` at entry and threads both through internal helpers as explicit parameters. The prior shared-global state was not a reproducible defect today (v0.1.1's per-match re-fetch fix covered the hot path), but the architecture was fragile and would have let a future caller weaving surgeon + structural helpers in one transaction see stale resolver state silently. Public API is unchanged.
- **ARY-277** (reflow): Tightened the phantom-space threshold in `_build_paragraph` from `font_size * 0.25` to `font_size * 0.125`. The old value was effectively one full space width — not the "half-space threshold" the comment claimed — which let glyph-side-bearing gaps (e.g. a comma's ~0.15 × font_size offset from the preceding word) squeak above the threshold and emit a phantom space in the reconstructed paragraph text.
- **ARY-277** (reflow, partial): `reflow_paragraph` now calls `_shift_content_below_inplace` to carve out room when the replacement produces more lines than the original paragraph occupied. Previously the extra lines overlapped content below, interleaving words from the replacement mid-sentence of unrelated paragraphs in the extracted text. The shift also mirrors `structural._replace_block_on_page`'s page-bottom clamp and propagates the shift helper's warnings into `EditResult.warnings` (ultrareview merged_bug_003).
- **ARY-282** (surgeon/structural, partial): Narrowed two broad `except Exception:` catches in `structural._extend_font` and `structural.insert_text_block` to a specific tuple matching what `extend_subset` can actually raise. `insert_text_block` now surfaces the extended exception context (missing chars + message) into `EditResult.warnings` instead of only logging. The "silent reflow skip" cited by the ticket was re-examined and found to be benign (simple-replace handles extension correctly); the investigation is documented in the `_calculate_new_width` docstring.
- **ARY-281** (docs drift): Rewrote `docs/font-pipeline.md` to describe the current Tier 1 / Tier 1.5 model (in-place glyph injection) instead of the pre-ARY-278 retain-gids subset-and-replace flow, and documented the metric-equivalent system-font fallback cascade honestly (ultrareview bug_006). Added `scripts/check_docs_vs_code.py` as a CI drift guard with invariants on Tier-1.5 prose, CHANGELOG-version parity, and retain-gids absence.
- Test-hygiene: fixed `test_installed_version_matches` to parse the expected version from the wheel filename rather than hard-coding `"0.1.0"`.

### Changed (internal)

- `reflow.reflow_paragraph` gained an optional `resolver_cache: FontResolverCache | None = None` parameter. The pre-0.1.2 7-arg signature continues to work — `None` causes a per-call cache to be constructed internally (ultrareview bug_005).
- Exception tuples for font-extension failures are now unified across `reflow_paragraph`, `structural._extend_font`, and `structural.insert_text_block` via the shared `reflow._FONT_EXTEND_FAIL_EXCS` constant (ultrareview bug_002). The tuple is `(FontNotFoundError, EncodingError, OSError, TTLibError)`; previously each site had its own narrowed tuple, and a deleted / permission-denied system font silently took down `replace_block` while degrading gracefully in reflow.

### Investigated / Not reproducible

- **ARY-258** (`pdf_find_text` and accented chars like "café") — engine and MCP tool both return the expected match in the current environment. Closed with evidence; reopen if observed from a specific client/transport.
- **ARY-259** (`pdf_analyze_subset` missing_glyphs garble for CJK) — same verdict: engine's `can_render` and the MCP wrapper both return the correct Unicode chars. Closed with evidence.

### Known scope limits

- **ARY-279** (CFF / OpenType Tier 1.5) deferred to v0.1.3. `_inject_glyph_in_place` still raises `FontNotFoundError` when the embedded font has no `glyf` table.
- **ARY-280** (reproducible real-Chrome fixture generator) deferred to v0.1.3 alongside ARY-279. The existing `.claude/Acme Corporation —Chrome.pdf` fixture is gated behind `skipif(not present)`.
- **Narrow single-line paragraph with inline continuation**: when `paragraph.paragraph_width` is narrower than the replacement because the paragraph was detected from a visual span (e.g. "Sarah Johnson" in a font-change) that shares the visual line with continuing text in a different font, reflow shifts content below the paragraph but cannot move the inline continuation on the same line. The replacement widens horizontally and overlaps the continuation. The audit's INV-J-3 contract guard ensures callers see an "overflow" warning when this happens; full geometric fix tracked for v0.1.3.

### Verified

- 718 tests passing pre-second-audit (up from 643 pre-Ultimate-Audit;
  75 new invariant probes from that audit), 16 skipped, 0 xfailed.
  Post-second-audit (2026-05-02): **745 passing, 12 skipped, 2
  deselected**. Net adds since 0.1.1: 27 invariant probes + ~80
  coverage tests. mypy strict clean (16 source files), ruff clean
  (src/ and tests/), `docs-vs-code` drift check passing, 80% coverage
  floor enforced via `[tool.coverage.report] fail_under = 80`.

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
