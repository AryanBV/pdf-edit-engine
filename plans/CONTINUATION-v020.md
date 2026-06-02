# Continuation — pdf-edit-engine v0.2.0 build

**This is a same-machine, same-branch handoff.** You (the next session) are already on
`feat/v020-foundation` in this local repo. Git is local-first — a fresh Claude Code
session in this directory sees all commits, the branch, and the working tree. **No push,
no branch change, no cloud needed** to continue.

> ⚠️ **HISTORICAL — the fenced block immediately below is the ORIGINAL session-2 bootstrap. It
> points to work that is now DONE (B.12, Block E, Block F core). The LIVE handoff — current HEAD
> `f8da6d6`, the committed-units list, the baseline (1016 passed), and the next-session queue
> (re-build A1.3 → B.9) — is the section at the BOTTOM of this file + `memory/project_v020_editing_depth.md`.
> Use those as the source of truth, not the historical block below.**

Original session-2 bootstrap block (kept for history):

---

```
You are continuing development of pdf-edit-engine (format-preserving in-place PDF
text editing). v0.1.3 is shipped to PyPI; we are building v0.2.0 (the "editing-depth"
universality release) on branch feat/v020-foundation (same local machine — do NOT push
or change branches unless explicitly asked; git is local-first).

OPERATE AS A PURE ORCHESTRATOR. Do the work via the dynamic Workflow tool — assign as
many agents as the task warrants. Keep YOUR main-thread context lean: agents read
source/web and return COMPACT structured results (use schemas); you hold conclusions,
not file dumps. Orchestration must INCREASE quality, never drop it — use verify +
adversarial-critique + completeness stages, not just parallel fan-out. Get the most out
of Opus 4.8 (1M context) + ultracode + Max 20x; token cost is not the constraint —
main-thread context hygiene and correctness are.

FIRST, load the live state (trust the repo over this prompt):
1. MEMORY.md + memory/project_v020_editing_depth.md  — state, decisions, discipline
2. plans/editing-iceberg-ULTIMATE.md                 — canonical roadmap (0.2.0 / 0.3.0 / 1.0)
3. CLAUDE.md + docs/                                  — architecture, strict dep boundaries
4. git log --oneline feat/v020-foundation            — exactly what's committed
5. Re-run the gate to confirm a green baseline before building:
   .venv/Scripts/python.exe -m pytest -q ; -m mypy ; -m ruff check src tests   (NOT .diagnostic-venv)

WHAT'S NEXT — the 0.2.0 core, hardest last:
- Net-additive items still open (e.g. B.12 rotated-splice WITH the positioning-gate
  generalization; Block E reflow; Block F color) — build these autonomously, TDD-first.
- The XL items — EACH needs a SPIKE first + the user's review before building:
  C.3 CFF/CID injection, B.2 Form-XObject editing (COW). (Block D tagged-PDF & B.10
  shaping are 0.3.0.)

THE 6 FEASIBILITY CORRECTIONS (the designs over-promise — apply before building):
1. B.10 shaping needs a NET-NEW GID-keyed injector (existing _inject_glyph_in_place is
   codepoint-indexed) + relative Td, not Tm.
2. C.3 CFF needs manual ROS/FDArray/FDSelect + the hmtx entry (no setupCFFCID exists).
3. C.5 Type1 is a no-op as written (system_fonts globs only ttf/otf/ttc) — keep deferred.
4. B.2 XObject COW must DEEP-CLONE the font objects (else the v0.1.0 corruption class) +
   read_bytes() not read_raw_bytes().
5. B.4 variable-width char-changing WRITE not buildable as written (PyCMap.decode discards
   byte boundaries) — 0.3.0.
6. surgeon._adjust_subsequent_positioning is hard-coded horizontal — gate/generalize
   before any vertical/rotated edit.

BUILD DISCIPLINE (non-negotiable):
- TDD-first: failing invariant probe (collision-free INV id, tests/invariants/
  test_{layer}_{id}_*.py) BEFORE the fix; confirm RED -> GREEN.
- Full green gate (pytest 0-fail + mypy --strict + ruff) per unit before commit; validate
  real behavior against tests/corpus_builders/ + tests/harness/diff_render.py.
- Commit only green units with EXPLICIT paths — experiments/, marketing/, plans/ are NOT
  gitignored, so NEVER `git add -A`/`.`.
- Respect CLAUDE.md dep boundaries (pdfminer only in locator; surgeon pikepdf-only;
  encoding stays fonttools-free via a function-local lazy import to fonts).
- Root-fix, never patch. Honest typed Degradation over silently-wrong output.
- Do NOT push to main or publish to PyPI without explicit approval. Publishing is MANUAL
  (no CI publisher; token in local ~/.pypirc); build the sdist from a CLEAN clone; the
  hatch sdist exclude must keep experiments/+marketing/ out.
- Target version is 0.2.0 (set pyproject only at release). There is no 0.1.4/0.1.5.
- A1.4 gate is SAFE (inline images don't break operator_index addressing — INV-B-6 guards it).

CADENCE: at ~70% context, checkpoint (commit) and recommend a fresh session.
```

---

## Already committed on `feat/v020-foundation` (verify with `git log`)

- foundation: differential-render harness (`tests/harness/`) + adversarial corpus (`tests/corpus_builders/`)
- A1.4 inline-image gate proven SAFE → INV-B-6 regression guard
- B.1 width-cache objgen re-key (INV-W-1) · A1.1 content-stream exception translation (INV-B-5)
- B.6 symbol-cmap dual-lookup (INV-C-8) · B.7 NFC/NFD-aware find (INV-D-5)
- B.3 ToUnicode-absent recovery (INV-A-6/A-7) · B.5 partial-ToUnicode reconciliation (INV-A-8)
- docs: version labels reconciled to the 0.2.0 / 0.3.0 scheme

Baseline at original handoff (v1, superseded): pytest 928 passed.

**CURRENT CHECKPOINT — HEAD `f8da6d6` (2026-05-30): pytest 1016 passed / 0 failed** (10-14 skipped = font-conditional variance, NOT a regression), mypy --strict (16) clean, ruff clean, docs-drift OK.
- **Session 2 (+7 units, rotation/reflow/color):** POS-GATE `e5cc25a` (INV-POS-1/2) · B.12 `1857dbe` (INV-B-7/8) · E.4+E.6 `1aa23cd` (INV-G-6/F-7) · Block F core `5ceed07` (INV-F-8/9) · E.2 `cfdf2aa` (INV-G-7) · E.3 `7b6b086` (INV-G-8) · E.8 `1925606` (INV-F-10).
- **Session 3 (+4 units, A/C robustness):** A1.2 `eec2a1b` (INV-W-2 q/Q stack cap) · A2.2 `b855e2e` (INV-W-3 linearization preserve) · C.1 `d0e5cc9` (INV-C-9 CFF-CID KeyError fix) · C.2 `f8da6d6` (INV-C-10 truthful glyph_count).
- **NEXT SESSION RESUMES HERE:** re-build **A1.3** (INV-W-4, kind `font_stream_too_large`; Flate-bomb stream-size cap — was in-flight + ABANDONED at this checkpoint, tree reverted clean, rebuild from scratch), then **B.9** (INV-B-9, kind `ligature_substituted`). After B.9 the safe batch is exhausted. Then DECISION-GATED items (need maintainer call): C.3 CFF, B.2 XObject, Block F policy (3 `RECOMMENDATION.md` in `experiments/`), A2.1/A2.3/B.4-decode/B.11, E.1/E.5/E.7. **Full detail: `memory/project_v020_editing_depth.md` + `git log feat/v020-foundation` + `docs/decisions.md`.**

## Doc-debt to finalize during 0.2.0 release-prep (non-blocking)

Pre-existing doc-accuracy items — fix when finalizing the 0.2.0 docs (counts are version-in-flux, so don't chase them mid-build):
- `README.md` says DegradationKind has "12 canonical values" (12-row table); the Literal now has 15 (13 shipped in v0.1.3 + `tounicode_recovered` + `untextable_cidfont`). Reconcile to the then-shipped count at release.
- `README.md` probe counts disagree ("144 probes" badge vs "81 invariant probes" section); `tests/invariants/` now collects ~208 across ~83 files. Reconcile to actuals at release.
- FIXED this session: the stale v0.1.0 "re-embed" Tier 2 wording in `README.md` + the `fonts.py` `extend_subset` docstring → now correctly describes Tier 1.5 in-place injection.
