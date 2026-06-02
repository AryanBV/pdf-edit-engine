# v0.2.0 Release Checklist (USER-triggered cut + publish)

> Status at session-6 end (2026-06-02): all v0.2.0 build units are COMMITTED on
> `feat/v020-foundation` (HEAD `4c20f6a`), gate **1202 passed / 0 failed**, mypy
> strict + ruff + docs-vs-code clean. **Nothing pushed; version still 0.1.3.**
> The cut (version bump + CHANGELOG + README) and the PyPI publish are deliberately
> left to you — publishing is outward-facing and one-way. This is the exact
> procedure. Sourced from `plans/v020-dx-ux-audit.md` (section A).

## Why these must land together
`scripts/check_docs_vs_code.py` Invariant 2 hard-fails if `__version__` bumps
without a matching `## [<version>]` CHANGELOG section. So the **version bump +
CHANGELOG [0.2.0] must be in the SAME commit**. README + sdist exclude can ride
along in that commit too.

## The cut commit (one commit, on `feat/v020-foundation`)

1. **Version bump** — `pyproject.toml` `version` and `src/pdf_edit_engine/__init__.py`
   `__version__`: `0.1.3` → `0.2.0`.

2. **CHANGELOG `## [0.2.0]`** — add a section above `## [0.1.3]`. The full unit
   list is the authoritative source in `docs/decisions.md` (rows dated 2026-05-29
   → 2026-06-02). Headline groups:
   - **Universality/honesty (encoding/locator):** B.1, A1.1, B.3 (ToUnicode recovery),
     B.5/B.6 (symbol-cmap dual-lookup), B.7 (NFC/NFD cluster-safe), POS-GATE.
   - **Fonts:** C.1 (/FontFile3 outline-table dispatch), C.2 (truthful glyph-count),
     C.3 (CFF/Type1C in-place glyph injection).
   - **Reflow/layout:** E.2 (indent), E.3 (declared leading), E.4 (widow/orphan),
     E.6 (line-height), E.7 (CJK/UAX-14 line-break — new `linebreak.py`),
     E.8 (shrink-to-fit), Block F CORE (verbatim color).
   - **Surgeon:** B.9 (ligature round-trip), B.11 (deletion cleanup),
     multi-match-same-operator honest-refusal.
   - **Robustness/save:** A1.2 (q/Q depth cap), A1.3 (Flate-bomb stream cap),
     A2.2 (linearization preservation), A2.3 (encryption round-trip).
   - **DX:** honesty-taxonomy surface (root-level exports, `FidelityReport.summary()`
     / `is_clean` / `max_severity` / `warnings()`, `EditResult.to_dict()`,
     `DEGRADATION_KINDS`), `find()` PDFEditError contract, error-message remedies.
   - **DegradationKinds:** 20 (v0.1.3) → **30** (12 new across the batch; full set
     enumerable at runtime via `pdf_edit_engine.DEGRADATION_KINDS`).

3. **README** (`README.md`):
   - **Probe-count (A2):** the badge (`README.md:7`) and prose (`README.md:259`)
     currently disagree (144 vs 81) and are stale. Get the real number with
     `.venv/Scripts/python.exe -m pytest --collect-only -q | tail -1` (collected
     count) and `(test file count via Glob tests/**/test_*.py)`, then make both
     sites agree on one convention.
   - **DegradationKind table (A3):** `README.md:89-110` lists ~20; the canonical
     set is now 30 (`models.py` Literal / `DEGRADATION_KINDS`). Add the missing
     rows and drop the "Twelve/v0.1.3" framing at `README.md:71`.

4. **sdist exclude (A5)** — `pyproject.toml` `[tool.hatch.build.targets.sdist]`
   exclude list (~lines 76-85): add `"plans/",` (experiments/ + marketing/ are
   already excluded; `plans/` currently SHIPS — same bloat class as the v0.1.3
   sdist near-miss).

5. Run the full gate to confirm green AFTER the bump (the CHANGELOG invariant now
   has [0.2.0] to match):
   `make all` (or venv pytest + mypy + ruff + `scripts/check_docs_vs_code.py`).

## Publish (manual — your call; see memory `reference_release_publish_mechanics`)
- Build the sdist + wheel from a **clean clone** (token in `~/.pypirc`).
- Confirm experiments/ + marketing/ + plans/ are NOT in the sdist
  (`tar tzf dist/*.tar.gz | grep -E 'experiments|marketing|plans'` → empty).
- Clear `./dist` before `twine upload` (stale-artifact near-miss 2026-05-29).
- Tag + smoke-verify install from PyPI.

## Documented carry-over items (decide at cut or 0.3.0 — NOT done this session)
- **A6 / C9 — `fonts.py` imports pdfminer** (`fonts.py:16` CMapParser/FileUnicodeMap,
  `:2638` EncodingDB), which the CLAUDE.md/architecture.md dep-boundary table
  forbids outside `locator`/`encoding`. PRE-EXISTING (since c851042), not a v0.2.0
  regression. The dep-boundary-reviewer flags it every run. **Two honest resolutions:**
  (C9, recommended) route the `/ToUnicode` CMap parse + `EncodingDB` lookup through
  an `encoding.py` helper (firewall pattern; ~1 helper + 2 redirects) — keeps the
  contract TRUE; or (A6) amend the dep table to "pikepdf + fonttools + pdfminer
  (CMap/encoding tables only)" — weaker. Left for you because changing a central
  font-pipeline subsystem (C9) or the project's stated dep contract (A6) is a
  decision worth your review. **NOT auto-applied.**
- **B12 — `linebreak.py` (new E.7 leaf) undocumented** in architecture.md / CLAUDE.md
  dep tables. Add a `reflow → linebreak` (stdlib-only leaf) row.
- **F-batch (Tr/Pattern/SMask/Ts)** — Tier-3 text-state honesty. Deferred to 0.3.0
  (lowest value, reflow churn, re-enters the E.7-churned reflow path). This is the
  "cut 0.2.0 now" recommendation from the original build queue's F-batch decision.
- **A2.3 Finding 2** — linearized+encrypted re-encryption-failure mislabels the
  coupled save failure as `linearization_dropped`; surfaced-not-silent, narrow.
  Decoupling the shared W-3/W-5 fallback → 0.3.0.
- **Multi-match full reverse-order rewrite** — so `replace_all`/`batch_replace`
  APPLY same-operator length-changing matches instead of refusing → 0.3.0.
- **batch_replace mixed-page silent-false-success** (found session-6 by the INV-G-11
  critic; PROVEN pre-existing, NOT a 0.2.0 regression): on a page where one batch
  edit reflows, a co-page non-colliding edit's splice can be discarded (re-parsed
  against stale ops) while the result reports `success=True`. The documented "one
  reflow per page" batch limitation. Fix = multiple reflows per page, or an
  INV-B-12-style honest refusal of a splice edit sharing a page with a reflow.
- **CHECK-3 reflow overflow honesty** (found session-6 by the visual-diff gate): a
  genuine length-INCREASE overflow with "no room below" emits
  `overflow_shift_suppressed` + `success=True` while the wrapped line visually
  collides with the line below; the message names the mechanism, not the collision.
  Fix = `success=False` (or a distinct degradation) when suppressed-overflow leaves
  content overlapping (`reflow.py` ~:1767). INV-G-11 (shipped) removed its most
  common trigger (same-length edits).
- DX audit (C) backlog: `page`/`page_number` unify, `dry_run` coverage,
  keyword-only `output_path`, the `summary()` info-vs-warning wording micro-polish.
