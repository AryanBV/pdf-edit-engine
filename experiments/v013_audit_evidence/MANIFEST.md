# v0.1.3 Audit Evidence Bundle

Generated: 2026-05-05 on branch `design/v013-implementation`.
Commission: `.claude/prompt.md` ("v0.1.3 Design Doc Audit — Evidence Surfacing").
Audited document: `docs/v0.1.3-implementation-design.md`.

This bundle collects evidence that the v0.1.3 implementation lock-in's claims
rest on. Each file maps to one of the five artifacts called for in the
commissioning prompt.

## Files

| File | Description |
|---|---|
| `MANIFEST.md` | This file. Index of bundle contents. |
| `font_extension_bug.md` | Narrative diagnosis of the M10 font-extension bug — the actual demo blocker behind design doc Section 1 verification gate (a). File:line citations to `surgeon.py:541–588` and `encoding.py:211–240`, plus a 50–100 LOC scope estimate. Two findings flagged for Aryan to weigh against the design doc's framing. |
| `font_extension_bug_trace.txt` | Raw stdout from `inspect_font_dict.py`. Shows: target font is `/F1` Calibri-Bold WinAnsi; `/Widths` covers bytes 32–122 (no high-byte coverage for ø/ü); `/FontFile2` has 6 954 glyphs but cmap maps only 118 entries (ø/ü absent); `FontResolver.can_encode("Søren Müller")` returns `(True, [])`; engine `replace()` returns `font_action='kept'`, `success=True`, `glyphs_missing=[]`. |
| `inspect_font_dict.py` | The diagnosis script. Single-purpose, ~150 LOC. Run from `experiments/v013_kerning_compare/` (re-using its venv): `.venv/Scripts/python.exe ../v013_audit_evidence/inspect_font_dict.py`. Read-only against `src/`. |
| `collision_counts.txt` | Raw stdout from `experiments/v013_kerning_compare/collision_count.py`, structured under d15/d25/d40 and `## M10 case` headers. Confirms the design doc Section 1 table (A: 75→79→80; B: 77→80→87) and the M10 overlap counts (baseline 75, Algo A 65, Algo B 75). |
| `signals_table.md` | Per-paragraph S1 / S2 / S3 signal dump from the detector calibration. Copied verbatim from `experiments/v013_detector_calibration/signals_table.md`. The summary `fpr_table.md` (already in Aryan's hands) aggregates these rows. |
| `inspection.txt` | Content-stream operator inspection across the calibration corpus. Copied verbatim from `experiments/v013_detector_calibration/inspection.txt`. Sources design doc §2 numbers ("308 `re` operators, 60 `BMC`/`BDC`" on SOW; reportlab/chrome/resume figures). |

## Mapping back to the prompt

- **Artifact 1 — Font-extension bug evidence.**
  `font_extension_bug.md` (1a) + `inspect_font_dict.py` (1b) +
  `font_extension_bug_trace.txt` (1c). All three present.
- **Artifact 2 — Kerning collision-count raw output.**
  `collision_counts.txt`. Both d15/d25/d40 and M10 cases under separate
  headers; the source script (`collision_count.py:62–73`) already
  handles M10 PDFs — no extension was needed.
- **Artifact 3 — Detector calibration full table.**
  `signals_table.md`. Verbatim copy of the existing per-paragraph dump.
- **Artifact 4 — Content-stream inspection raw output.**
  `inspection.txt`. Verbatim copy. Single combined file, so no
  `inspection/` subdirectory was needed.
- **Artifact 5 — This manifest.**

## Gaps

None. Every artifact called for by the prompt was produced from
existing files or by running an existing/new script against existing
artifacts. No fabrication, no reconstruction.

The font-extension bug evidence (Artifact 1) was newly produced — the
prior session did not save an inspection script or trace to disk.
The new `inspect_font_dict.py` reproduces the diagnosis from scratch
against the same input PDF, so the evidence is independently
reproducible and not dependent on prior session memory.

## Read-only constraints honored

- `src/` — not modified.
- `docs/v0.1.3-implementation-design.md` — not modified.
- `docs/` — not modified.
- Branch — stayed on `design/v013-implementation`. No branching, no
  merging, no pushing.
- Commits — one squash commit at the end with all bundle artifacts.

## Next step (per prompt)

Aryan pastes this bundle back to the auditing chat. The audit
completes; the design doc's final verdict (proceed to bundled
implementation, or revise the design doc first) lands with this
evidence in hand.
