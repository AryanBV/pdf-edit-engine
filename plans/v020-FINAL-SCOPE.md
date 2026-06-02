# v0.2.0 — Final Scope + Build Contract (locked 2026-06-01)

Produced by a 16-agent planning workflow (14 per-candidate feasibility assessors →
adversarial scope critic → synthesizer) over the full 63-item iceberg + the 3 spike
RECOMMENDATION.md verdicts, re-verifying every feasibility claim against HEAD `6574b69`.
Maintainer approved: **all 5 units, C.3 as headline XL.**

## Theme

**"Universality + honesty."** Every unit either REMOVES a refusal or ROOT-FIXES a
silent-wrong. **Zero new runtime dependencies.** ~5 net-new units on top of the ~19
already landed in v0.2.0.

The assessors produced 9 "include" verdicts; the adversarial critic overturned the
aggregate (a hot-file collision pileup that won't ship at the 1055-green bar) and cut
to these 5 coherent units. Two assessor includes were explicitly overturned: **B.2
deferred** (release-threatening refactor on an unlanded prereq) and **E.1 deferred**
(silent-re-justification blast radius).

## Locked scope (build order = this order)

| # | Unit | INV | What it does | Effort | Risk | Hot files |
|---|------|-----|--------------|--------|------|-----------|
| 1 | **C.3** CFF/Type1C glyph injection | INV-C-11/12/13 | Font extension for CFF-outline fonts (today refused outright) | XL | High | `fonts.py` (isolated) |
| 2 | **B.11** deletion cleanup | INV-B-10 (+11) | `replace(x,'')` stops leaving silent residue | M | Med | `surgeon.py`, `models.py` |
| 3 | **E.7** CJK / UAX#14 line-break | INV-G-9 (+10) | CJK re-wraps instead of silently overflowing | L | Med | `reflow.py`, new `linebreak.py`, `models.py` |
| 4 | **F.3+F.2+F.4** text-state honesty | INV-F-11..14 | Tr emit + clip-refuse; Pattern/SMask relocation-refuse; Ts detect-surface | M | Low | `state.py`, `reflow.py`, `models.py` |
| 5 | **A2.3** encryption round-trip | INV-W-5 | Encrypted PDFs stay encrypted after edit (today: silent decrypt) | L | Med | `_pathutil.py`, ~13-verb cascade, `models.py` |

## FROZEN DegradationKind ledger (the hard mandate)

25 kinds today → **33** after this release. Append to the `models.py` `DegradationKind`
Literal **in this exact order**, re-reading `models.py` before each unit's append. **None
are font-affecting** (no glyph-identity change) → each gets an `INV-J-8` truth-table row
asserting `font_preserved=True`. Each unit's design phase may emit FEWER than reserved
(consolidation OK) but must NOT reorder earlier kinds.

| Order | Kind | Unit | Severity | Fires when |
|-------|------|------|----------|-----------|
| 26 | `deletion_residual_text` | B.11 | warning | a deletion leaves provable extraction residue → edit becomes `success=False` |
| 27 | `inline_image_present` | B.11 | info | an inline image lies in/near the deletion span (operator-index caution) |
| 28 | `scriptless_reflow_unsupported` | E.7 | info | a script segment (Thai/Lao/Khmer/…) has no UAX#14 break; left unwrapped (honest SA-fallback) |
| 29 | `text_clip_mode_unsupported` | F.3 | warning | Tr clip modes 4–7 present → refuse (do NOT silently emit mode-0 over a clip region) |
| 30 | `text_rise_approximated` | F.4 | info | Ts (text rise) present in a reflowed run; surfaced, not per-run segmented (reflow flattens runs) |
| 31 | `pattern_fill_flattened` | F.2 | warning | Pattern fill on relocated text cannot be safely replayed |
| 32 | `soft_mask_realignment_unsupported` | F.2 | warning | SMask on relocated text cannot be safely realigned |
| 33 | `encryption_dropped` | A2.3 | warning | re-encryption could not be preserved on save (honest fallback) |

**C.3 adds ZERO new kinds:** the refuse path reuses the existing `font_extension_failed`
funnel (`_FONT_EXTEND_FAIL_EXCS`); a successful CFF injection reuses existing
coverage-extension semantics (no degradation, `font_preserved=True`). CFF2 / out-of-scope
shapes refuse via `font_extension_failed` (NO `cff2_unsupported` kind — one less Literal edit).

## Sequencing rules (the real ship risk is integration, not feasibility)

- **`models.py`** — every unit edits the Literal → STRICTLY SERIAL; re-read before each append.
- **`reflow.py`** — E.7 and F-batch both edit `break_into_lines`/`_build_replacement_ops` → NEVER concurrent; E.7 then F.
- **`fonts.py`** — C.3 is the ONLY editor (B.2 deferred) → C.3 owns it uncontended. (This is *why* B.2 was deferred.)
- **`surgeon.py`** — B.11 then A2.3, serialized.
- Build back-to-back where files are disjoint (C.3→B.11). Full 5-way verify (gate +
  dep-boundary + invariant-probe + adversarial critic + corpus/diff-render) per unit
  BEFORE commit, explicit-path commit. A2.3 built LAST so `models.py` is frozen for its
  ~13-verb `password` cascade.

## Locked per-unit decisions (recommended answers, adopted)

**C.3** — slice-1 ceiling: CID-keyed Identity-H + single non-composite Latin glyph +
**CFF/OTF-CFF donor only**. Everything else (CFF composite/seac, TrueType-donor→T2,
unitsPerEm mismatch, multi-FD CID/CJK, name-keyed simple-OTF `/FontFile3`, CFF2) HARD-FAILS
via `font_extension_failed`. **Donor-gap accepted + documented**: works only when the exact
original CFF font is installed (TrueType metric-equivalents hard-fail); the glyf→T2 pen
bridge is deferred to 0.3.0 (turns XL into XXL). **MUST use the `f["hmtx"][name]=(adv,lsb)`
idiom** (bumps `numberOfHMetrics`), NOT `hmtx.metrics[name]=` (the committed `probe_cid.py`
bug that fails the round-trip). Extend `font_has_codepoint` to read the `/FontFile3` CFF
charset so `can_encode` stops returning best-effort-True on CFF. Reuse ARY-279.

**B.11** — keep-slot / empty-operand / compensating-advance (index-stable; do NOT remove
operator tuples — shifts downstream `operator_index` within the pass). Leave trailing text
at its absolute `Tm` (no gap-collapse in slice-1). Axis-aligned-gate the compensating advance
(POS-GATE pattern). Residual that is provable → `deletion_residual_text` warning + `success=False`.

**E.7** — reflow-side ONLY. Reduced ~8-class UAX#14 set (ID/CL/CP/NS/OP/CM/SP/mandatory).
Segmentation-aware join at `reflow.py:1338-1339` is load-bearing (empty for ID, space for AL).
**Read-path space-insertion fix (`locator.py:674/883`) CARVED OUT** to a separate 0.3.0
read-path issue (keeps `locator.py` uncontended). **Drop the pythainlp Thai extra** — stdlib
`unicodedata` core + honest `scriptless_reflow_unsupported` SA-fallback. New `linebreak.py`
is a stdlib leaf in reflow's dependency zone.

**F-batch** — F.3: snapshot Tr + emit `[mode] Tr` after BT; clip modes 4–7 → refuse
(`text_clip_mode_unsupported`). F.2: Pattern/SMask on relocated text → refusal (value already
replays via the shipped Block F CORE). F.4: Ts → detect-and-surface (`text_rise_approximated`)
only — per-run segmentation is structurally blocked by reflow run-flattening. Color-space
CONVERSION (Separation/DeviceN→RGB) is DROPPED from 0.2.0 (needs ICC/tint dep).

**A2.3** — preserve encryption across edit via the landed A2.2 `_save_pdf` contract; honest
`encryption_dropped` fallback. Accept the full ~13-verb keyword-only `password` cascade
(read verbs need it for encrypted-input symmetry). Exempt-list (strip encryption by design):
`decrypt_pdf`/`encrypt_pdf`/`merge_pdfs`/`split_pdf`. Permission flags advisory-only (pikepdf
bypasses `/P`; no DRM enforcement — stated as the honesty boundary). INV-L-1 preserved.

## Explicitly DEFERRED to 0.3.0 (reason verified to still hold)

- **B.2 Form-XObject COW** — unlanded A1.4 prereq + largest refactor in the set (5 hot files)
  + collides with C.3's `fonts.py`; release-threatening. Deferring it is what makes the rest sequencable.
- **E.1 alignment (full re-justify)** — silent re-justification blast radius worse than its S5
  precedent; a "nice differentiator," not a silent-wrong fix. (Detect-and-surface-only slice
  acceptable later.)
- **A2.1 signature detect** — lower-value half; doubles the A2.3 keyword cascade; bundle with
  annotation/Block-G work in 0.3.0.
- **B.8/B.10 RTL + complex-script shaping write** — empirically blocked: `_inject_glyph_in_place`
  is codepoint-indexed (`cp = ord(ch)`); shaped Arabic emits GIDs. Needs net-new GID-keyed
  injector + bidi + shaping + uharfbuzz. (B.8a read-only bidi is shippable later, lowest priority.)
- **B.4 named-CMap char-changing write** — blocked-as-designed (`PyCMap.decode` discards byte boundaries).
- **C.5 Type1** — triple-blocked (no .pfb/.pfa discovery; t1Lib path-only; gated behind C.3).
- **Block D tagged-PDF** — needs the unlanded D.1 marked-content interpreter.
- **Block G full** — depends on deferred B.2 / Block C `/DA`. (G.2-lite simple-font `/AP` regen is
  an optional later pull-forward.)
- **E.5 Pyphen hyphenation** — dep clean (MPL-1.1) but bundled fr/de dictionaries are LGPL-only →
  license-audit burden disproportionate to QoL value.

## DROPPED (not even a 0.3.0 commitment)

- **Block H scale** — NO profiled bottleneck (`test_performance.py` passes 100pp <30s); ceiling
  half depends on deferred B.2. Drop until a real large-doc profile shows the global-dict hurts.
- **Color-space conversion** (Separation/DeviceN→RGB) — needs ICC/tint dep; 0.3.0+.
- **pythainlp [thai] extra** — packaging/test surface for a minority script; honest SA-fallback suffices.

## Biggest risk + mitigation

**Integration sequencing, NOT feasibility.** Five units grow the `DegradationKind` frozenset
(25→33) and INV-J-8 table; two edit `reflow.break_into_lines`. Failure mode = flaky cross-unit
interactions → amber gate → release stall. Mitigations: (1) this frozen ledger up front; (2) hard
serial build with re-reads; (3) full 5-way verify per unit (the adversarial critic caught real
bugs behind green gates in A1.3 ×3, A2.2, C.2 — non-negotiable). Deferring B.2 is the
highest-leverage de-risking decision (removes the one item churning 5 hot files).

## At release-prep (0.2.0 cut)

bump `pyproject` 0.1.3→0.2.0; reconcile README counts (33 kinds; probe badge); add `plans/`
to the hatch sdist exclude; fix the pre-existing pdfminer-in-`fonts.py` dep debt; real-PDF
validation pass.

## ADDENDUM — session-5 status + re-sequencing (2026-06-01, HEAD `d0f1e6e`, gate 1151/0)

**Committed:** A1.3 `ea1b57e`, B.9 `6574b69`, C.3 `e550c32`, B.11 `298d995`, E.7 `d0f1e6e`
(+ this plan `4a45bae`). DegradationKinds now **28** (A1.3 +1, B.11 +2, E.7 +1; C.3 +0).
INV ledger: POS=2 · B=11 · F=10 · G=10 · C=13 · W=4 · H=5.

**Re-sequencing decision (maintainer-approved):** the original §5 build order put F-batch
before A2.3 (a mild `models.py`-freeze reason), but the §3 tiers rank **A2.3 = Tier-1
(must-have)** and **F-batch = Tier-3 (optional polish)**. After E.7 bled five critic rounds
in `reflow.py`, the path is re-sequenced:
1. **A2.3 NEXT** (Tier-1 encryption round-trip; clean subsystem, away from `reflow.py`).
2. **Multi-match-same-operator honesty** — NEW unit, slotted NOW (was a deferred follow-up).
   Honest-refusal at minimum (detect N>1 matches sharing one operator → typed refusal, not
   silent corruption). Pre-existing + general (affects `replace`-with-text too; root:
   `replace_all` sorts same-op matches only by `max(operator_refs)`). Surgeon-side.
3. **F-batch** — OPTIONAL; build only if appetite holds after the above, else defer to 0.3.0
   with the other reflow-quality items. (F.4/Ts is structurally limited by reflow run-flattening.)

**Documented residuals (0.3.0, NOT regressions — do not re-raise as blockers):** E.7 cross-line
`\n` per-boundary provenance (read-path); CFF/glyf unbounded placeholder padding (needs a
bounded cap, own unit). The multi-match-same-operator FULL fix (reverse-order-offset rewrite)
is the stretch beyond the honest-refusal slice above.
