# Decision Log

Architectural and technical decisions for pdf-edit-engine.

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-03 | Python engine + TS MCP wrapper (two repos) | fonttools irreplaceable; MCP SDK is TypeScript |
| 2026-04-03 | pikepdf + fonttools + pdfminer.six | Converged from two independent research tracks |
| 2026-04-03 | parse_content_stream + unparse_content_stream | Validated in spike; TokenFilter not suitable |
| 2026-04-03 | Identity-H as primary encoding path | Spike confirmed resume uses CIDFont/Identity-H |
| 2026-04-03 | CMap-only font extension as fast path | Spike found "subsets" contain full glyph data |
| 2026-04-03 | No AGPL dependencies | pikepdf MPL, fonttools MIT, pdfminer.six MIT |
| 2026-04-03 | FidelityReport on every edit | Key differentiator vs silent degradation (PyMuPDF) |
| 2026-04-03 | dry_run parameter on all edit methods | Enables AI agents to preview before committing |
| 2026-04-15 | ARY-278 Tier 1.5 in-place glyph injection replaces v0.1.0 Tier 2 retain-gids subset-and-replace | Tier 2 renumbered pre-existing CIDs and corrupted unrelated content-stream text on narrow Chrome subsets ("1ova,ndustries" Mode 2 symptom). Tier 1.5 appends new glyphs additively. |
| 2026-04-25 | `_pathutil.open_pdf` is the single canonical PDF-open entry | Pre-v0.1.2 had 16 direct `pikepdf.Pdf.open` call sites; a subset leaked `pikepdf.PasswordError` / `PdfError` to public API. Routing through one helper closes INV-L-1. |
| 2026-04-25 | `EditResult.__post_init__` enforces overflow contract at the dataclass boundary | INV-J-3: when `overflow_detected=True`, an "overflow" warning is auto-appended if absent. Future code paths that flip the flag inherit caller-visible signal by construction. |
| 2026-04-25 | `extend_subset(..., substitution_log=None)` non-breaking out-parameter | INV-C-4: surfaces metric-equivalent fallback (e.g. Carlito for Calibri) to callers via `FidelityReport.font_substituted` without changing the v0.1.1 `str` return type. |
| 2026-04-25 | `_assert_match_addressable` validator at every TextMatch-consuming entry | INV-B-3: stale TextMatch (operator_refs from before a mutation) silently corrupted output. Validator decodes the recorded byte_position and refuses on mismatch with re-run-find() instruction. |
| 2026-04-25 | `_group_into_lines` uses `min(prev, curr) * 0.5` line-height; `_build_flat_string` uses `font_size * 0.25` for space-insertion | INV-N-1: one-glyph-per-operator PDFs (Chrome, some Word exports) caused the prior heuristics to merge consecutive words and absorb headings into adjacent badge-text runs. Symmetric-min and font-size-based proxies are stable regardless of fragment granularity. |
| 2026-04-25 | Drop stroke-color and text-rise tracking from `state.GraphicsStateTracker` | Every consumer reads only `fill_color`. Stroke + text-rise tracking was dead from v0.1.0 to v0.1.1; removed in v0.1.2. `GraphicsStateSnapshot.stroke_color` field also removed (dataclass-shape change, documented in CHANGELOG). |
| 2026-04-25 | Output path validation refuses symlink/junction traversal via `realpath != abspath` | The first v0.1.2 attempt used `Path.resolve()` then a parent-walk for symlinks — dead because resolve() follows them all. Fixed via `os.path.realpath` vs `os.path.abspath` comparison, which catches POSIX symlinks AND Windows directory junctions on both platforms. |
| 2026-04-25 | Invariant-driven adversarial audit framework (`tests/invariants/`) | 75 invariant probes across 14 layers (encoding, content stream, font, locator, surgeon, structural, reflow, wrapper, annotations, fidelity, public API, error hierarchy, security, differential). Each probe permanently regression-guards the contract it tests. The audit charter (`docs/ultimate-audit-charter.md`) documents the methodology for future releases. |
| 2026-05-05 | `Degradation` typed list + computed `font_preserved` (v0.1.3) | INV-J-5: every code path emitting a degraded result appends a typed `Degradation` to `EditResult.fidelity_report.degradations` before returning. INV-J-8: `FidelityReport.font_preserved` is a computed `@property` derived from `degradations` (none of kind in `FONT_AFFECTING_KINDS`) AND `font_substituted is None`; never hardcoded. Probes: `tests/invariants/test_j_5_degradation_surfacing.py` and `tests/invariants/test_j_8_font_preserved_computed.py`. The 12-kind canonical Literal (10 from design doc §4a coherence table + `font_coverage_extended` / `font_coverage_substituted` from the audit-bundle Permissive enum policy) is locked. |
| 2026-05-05 | Algo A — Tz horizontal-scaling kerning, no refusal threshold (v0.1.3, ARY-290) | The pre-v0.1.3 `>0.5×` flat-fallback in `_encode_with_kerning` produced visibly squished/spread output and silently refused large-delta kerning. Replaced with PDF `Tz` operator scaling: glyph identity preserved at any factor, symmetric 95-105 deadzone for Degradation emission (`kerning_compressed` <95 warning, `kerning_widened` >105 info; both locked in design doc §4a). Pure decision lives in `surgeon._kerning_decision`. |
| 2026-05-05 | `can_encode` strengthened from encoding-map check to coverage check (v0.1.3) | Audit bundle `experiments/v013_audit_evidence/font_extension_bug.md`: the non-CID branch returned True when `_unicode_to_byte` had the codepoint, even when the embedded `/FontFile2` lacked the glyph or `/Widths` lacked the entry. Strengthened to verify all three: encoding-map ∧ `/FirstChar..LastChar` ∧ `/Widths` ∧ `/FontFile2` cmap glyph presence. Cmap check delegates to `fonts.font_has_codepoint` so encoding.py keeps no fontTools import (CLAUDE.md dep-boundary table). |

## Decision Details

### Python engine + TS MCP wrapper
The PDF editing engine lives in this repo (Python). A separate TypeScript repo will wrap it
as an MCP server. fonttools has no viable JS/TS equivalent, making Python mandatory for the
core engine. The MCP SDK is TypeScript-native, so the wrapper lives separately.

### parse_content_stream over TokenFilter
pikepdf's TokenFilter processes tokens in a streaming fashion but doesn't support random
access to specific operators. Our approach requires locating specific text operators by index
and modifying them in place. parse_content_stream returns a list of (operands, operator)
tuples that can be indexed, modified, and re-serialized via unparse_content_stream.

### Identity-H as primary encoding
Real-world testing (resumes, business documents) showed that modern PDFs overwhelmingly
use CIDFont with Identity-H encoding. WinAnsi and other encodings exist but are secondary.
The engine handles Identity-H first and falls back to simpler encodings.

### FidelityReport as differentiator
Competing approaches (PyMuPDF redact-and-replace) silently degrade formatting. Our engine
returns a FidelityReport with every edit, documenting exactly what changed: whether fonts
were preserved or substituted, whether overflow occurred, and which glyphs (if any) could
not be rendered. This is critical for AI agent consumers that need to verify edit quality.
