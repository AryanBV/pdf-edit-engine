# Phase 10 (M10 Demo Verification) — Gate 10 Report

> **Gate status: PASS.**
>
> Both halves of the launch gate pass:
> - **Honest-reporting half**: engine emits typed Degradation
>   (`font_coverage_substituted`, severity=warning) for the system-font
>   sourcing event.
> - **Rendering half**: "Søren Müller" renders cleanly with both ø and
>   ü as recognisable glyphs (no `.notdef` boxes, no missing-glyph hash
>   patterns).
>
> Phase 13 (Block 3) folded non-CID Tier 1.5 extension into v0.1.3 per
> Aryan's Decision B, closing the gap that the original Gate 10 report
> (preserved below for historical context) flagged.

## What was tested

- **Engine version**: pdf-edit-engine 0.1.3 (editable-installed into
  `m10-launch/.venv/`; pulls the current `prereq/v013-honesty-fixes`
  branch automatically).
- **Engine commit**: `c903335` (Phase 13.5 e2e landed).
  Branch HEAD at the time of this re-run.
- **Demo command**: `engine_edit.py sow.pdf` (Sarah Chen → Søren Müller).
- **Comparison artefacts**: `comparison.png` (PyMuPDF vs engine,
  side-by-side render); `fidelity_report.json` (engine's
  `FidelityReport` for the edit).

## Result vs Gate 10's expected outcome

| Gate 10 expectation | Actual v0.1.3 result | Pass? |
|---|---|---|
| `success: true` | `success: true` | ✅ |
| `font_action: "extended"` | `font_action: "extended"` | ✅ |
| `glyphs_missing` includes `ø` and `ü` | `["ø", "ü"]` | ✅ |
| `degradations` contains `font_coverage_substituted` (severity=warning) | present (`tier=1.5,chars=ø,ü`) | ✅ |
| `font_extension_failed` NOT in degradations | absent | ✅ |
| `font_preserved: true` | `true` (computed; `font_coverage_substituted` and `kerning_compressed` are not in `FONT_AFFECTING_KINDS`) | ✅ |
| `comparison.png` right-half: clean Søren Müller | ✅ (verified visually; ø and ü render as recognisable glyphs) | ✅ |

Side-effect Degradation observed: `kerning_compressed` (severity=warning,
`detail="Tz 87%"`). Expected v0.1.3 behaviour — "Søren Müller" is wider
than "Sarah Chen" and the engine compresses via the Tz operator to fit
the original bbox. Per ARY-290 / design doc §4a. Not part of the gate.

## How v0.1.3 closes the gap

The original report (preserved below) flagged that v0.1.3's
`extend_subset()` only supported Type0/Identity-H CID fonts; the M10
SOW uses simple `/TrueType` + `/WinAnsiEncoding` + `/FontFile2`
(`BCDEEE+Calibri-Bold`) which routed to `font_extension_failed` via
the `is_cid_font` short-circuit at `structural._extend_font:864-865`.

Phase 13 commits (in order):

1. `5a0d8b4 fix(fonts): repair _font_dict_key for pikepdf >=10.5.1 (ARY-349)`
2. `4e1f989 feat(fonts): add _glyph_name_for_codepoint, _extend_simple_widths, _allocate_free_bytes, _extend_simple_tier_15 helpers (ARY-348)`
3. `0e908c5 feat(structural): drop is_cid_font gate; defer subtype handling to dispatcher (ARY-348)`
4. `39f7f75 test(fixtures): add deterministic synthetic non-CID PDF builder (ARY-348)`
5. `63eae36 fix(fonts): delete _FONTFILE2_CACHE; centralise _FONT_EXTEND_FAIL_EXCS (ARY-348)`
6. `40224da test(invariants): 12 probes for non-CID Tier 1.5 path (ARY-348)`
7. `c903335 test(integration): M10 SOW Søren Müller e2e for non-CID Tier 1.5 (ARY-348)`

The new `_extend_simple_tier_15` orchestrator mirrors `_extend_tier2`'s
font-binary surgery (system-font sourcing via `_find_font_with_origin`,
composite resolution via `_collect_component_names`, hinting strip via
`_strip_glyph_hinting`, glyf-table append via `_append_glyph_to_font`)
but updates `/Encoding /Differences` + `/Widths` instead of `/ToUnicode`
+ `/W`. The dispatcher in `extend_subset()` now switches on subtype
(`/Type0` → CID path, `/TrueType` → simple path with `/FontFile3` and
missing-`/FontFile2` rejections, `/Type1` → reject, else → reject).

## Phase 10 evidence (current)

- `fidelity_report.json` — refreshed from this re-run
  (m10-launch/results/fidelity_report.json copied here).
- `comparison.png` — refreshed from this re-run.
- `non_cid_extension_scope.py` — preserved scope-investigation script
  from the original gap-discovery effort.

## Audit-bundle correction

The original report cited the audit bundle's finding that "outlines are
physically present" → "Tier 1 (CMap-only)" fix. Phase 13.9 corrects this
framing in `experiments/v013_audit_evidence/font_extension_bug.md`: the
6,954 `glyf` slots include only 116 real named glyphs; the remaining
6,838 are anonymous `glyph0XXXX` placeholders the embedder retained for
slot-index consistency. **Neither `oslash` nor `udieresis` exists in
F1's glyph table.** The correct fix is therefore Tier 1.5 (system-font
sourcing), not Tier 1 (cmap-only) — which is what `_extend_simple_tier_15`
implements.

---

## Original report (historical context — DO NOT MODIFY)

> The text below is preserved verbatim from the v0.1.3-shipping
> Phase 10 verification, when the gap was open and Aryan picked
> Decision A (ship with gap). Decision B was taken later in Block 3,
> folding non-CID Tier 1.5 into v0.1.3.

> **Gate status (HISTORICAL): PARTIAL PASS WITH ARCHITECTURAL GAP — v0.1.4 BLOCKED.**
>
> The v0.1.3 honest-reporting half of the launch gate passes (engine
> correctly identifies missing glyphs + emits typed Degradation). The
> end-to-end "Søren Müller renders cleanly" half cannot pass against
> `sow.pdf` without implementing **non-CID font extension** — a
> capability documented as v0.1.4 open question §7.3 in the release
> notes. Aryan decision required.

(Full original sections — root cause, what v0.1.3 DOES deliver, decision
options A/B, and the recommendation favouring A — are preserved in the
git history of this file at the pre-Phase-13 commit. They are not
re-listed here because they describe a state that no longer exists.)

> Aryan eventually picked **Decision B** on 2026-05-05: fold non-CID
> Tier 1.5 into v0.1.3 rather than defer. Block 3 (Phase 13) executed
> Decision B. This re-run confirms the gate now passes end-to-end.
