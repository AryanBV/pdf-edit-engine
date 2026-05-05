# Phase 10 (M10 Demo Verification) — Gate 10 Report

> **Gate status: PARTIAL PASS WITH ARCHITECTURAL GAP — v0.1.4 BLOCKED.**
>
> The v0.1.3 honest-reporting half of the launch gate passes (engine
> correctly identifies missing glyphs + emits typed Degradation). The
> end-to-end "Søren Müller renders cleanly" half cannot pass against
> `sow.pdf` without implementing **non-CID font extension** — a
> capability documented as v0.1.4 open question §7.3 in the release
> notes. Aryan decision required.

## What was tested

- **Engine version:** `pdf-edit-engine 0.1.3` (editable-installed into
  `m10-launch/.venv/`).
- **Demo command:** `engine_edit.py sow.pdf` (variant=primary →
  Sarah Chen → Søren Müller).
- **Comparison artifacts:** `comparison.png` (PyMuPDF vs engine,
  side-by-side render); `fidelity_report.json` (engine's
  FidelityReport for the edit).

## Result vs Gate 10's expected outcome

| Gate 10 expectation | Actual v0.1.3 result | Pass? |
|---|---|---|
| `font_action: "extended"` | `font_action: "failed"` | ❌ |
| `success: true` | `success: false` | ❌ |
| `glyphs_missing` includes `ø` and `ü` | `["ø", "ü"]` | ✅ |
| `degradations` contains `font_coverage_extended` | `degradations` contains `font_extension_failed` (severity=error) | ❌ in spirit; ✅ in INV-J-5 emission |
| `font_preserved: true` (extension succeeded) | `font_preserved: false` (extension failed honestly) | ❌ |

## Root cause

`sow.pdf` has Sarah Chen in **F1 Calibri-Bold/WinAnsi** (`is_subset=True,
glyph_count=91, embedded_type=TrueType, encoding_type=WinAnsi`). The
engine's `extend_subset()` machinery only supports Type0/Identity-H CID
fonts; non-CID (WinAnsi/MacRoman) extension is unimplemented in v0.1.3.

Per the audit bundle (`experiments/v013_audit_evidence/font_extension_bug.md`):

> The fix path here would be Tier 1 (CMap-only extension) in the
> language of `docs/font-pipeline.md`, *not* Tier 1.5 — the outlines
> are physically present.

The CONCEPTUAL Tier 1 fix is correct (outlines exist in `/FontFile2`;
only the WinAnsi `/Differences` array and `/Widths` need extending).
But v0.1.3's `extend_subset` doesn't implement WinAnsi `/Differences`-
based extension — that is captured as **v0.1.4 open question §7.3**
in `docs/v0.1.3-release-notes.md`.

## What v0.1.3 DOES deliver against the M10 case

1. **`can_encode` correctly flags ø/ü as missing** (Phase 5 strengthening
   working — pre-fix, `can_encode("Søren Müller")` returned True even
   though those chars wouldn't render).
2. **`glyphs_missing` is populated** (Phase 5 audit-bundle finding #3
   resolved — pre-fix, this was always `[]`).
3. **`font_extension_failed` Degradation surfaces with severity=error**
   (Phase 4 lying-success-path fix working — pre-fix, this path returned
   `font_preserved=True` despite the failure).
4. **Computed `font_preserved=false`** (Phase 1 INV-J-8 working —
   FONT_AFFECTING_KINDS membership correctly drives the property).
5. **Honest end-user report:** anyone reading `fidelity_report.json`
   sees that the edit failed AND why (FontNotFoundError on the system
   font lookup during `extend_subset`'s non-CID-rejection path).

## Decision needed from Aryan

Two options:

**A) Ship v0.1.3 with the M10 demo failing the rendering half of
the launch gate.** The honest-reporting half passes. The marketing
narrative would shift from "Søren Müller renders cleanly" to "the
engine honestly tells you when it can't render Søren Müller — no
silent corruption, typed Degradation, severity=error". This is
defensible per the design doc's framing ("your AI agent has no
eyes; the FidelityReport is its eyes — both doing the right thing
AND reporting it correctly").

**B) Block v0.1.3, scope-creep non-CID extension into the release.**
Implement WinAnsi `/Differences`-based extension in `fonts.py`
(estimate: ~1.5 hr — add a new Tier 1-WinAnsi path that copies
`/Differences` and `/Widths` entries from a system font, similar to
the existing Tier 1 cmap path for CID). This makes the original gate
pass.

**Recommendation: A.** v0.1.3's main thrust is the typed Degradation
schema + honest reporting. The non-CID extension capability is a
discrete enhancement that fits naturally in v0.1.4 alongside the
ARY-292 detector algorithm fix.

## Phase 10 evidence

- `fidelity_report.json` — current state (engine_edit.py output)
- `comparison.png` — PyMuPDF vs engine side-by-side render

Both files are saved in this directory and committed alongside this
report so the deviation is visible in the v0.1.3 release artifact
trail.

## Demo-script change

The `engine_edit.py` had a hard pin `_v != "0.1.2": exit(1)` that
prevented running against v0.1.3. Updated to accept `0.1.2` OR
`0.1.3` to enable the verification — Aryan can revert this minimal
change in the marketing repo if he prefers (the engine pin was for
v0.1.2 demo correctness, but this verification needs v0.1.3).
