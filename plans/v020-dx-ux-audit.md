# v0.2.0 DX/UX + Release-Readiness Audit

> Source: read-only 6-agent workflow `wfhf9x7wg` (2026-06-01), 5 lanes —
> public-API ergonomics, FidelityReport honesty, errors/docstrings,
> release-readiness, MCP-wrapper alignment — synthesized + prioritized.
> Advisory; edits nothing. Captured here for the 0.2.0 release-prep pass.

**Verdict: NOT ready for a 0.2.0 cut.** The engine's *behavior* (correctness +
honesty primitives) is strong. Two CI gates physically block the cut the instant
`__version__` bumps, and one error message dead-ends the most common real-world
failure. Everything else is high-value polish or 0.3.0.

**Cross-lane convergence worth naming:** the degradation taxonomy is the engine's
headline differentiator, yet it is simultaneously (B1) un-exported from the package
root, (A3) under-documented in the README, and (B13) at risk of being silently
dropped by the MCP wrapper. **Fixing B1 + A3 + B13 together closes that gap
end-to-end (model → docs → wrapper).**

---

## (A) MUST-FIX BEFORE 0.2.0 CUT

| # | Finding | File:line | Fix |
|---|---------|-----------|-----|
| A1 | CHANGELOG has no `[0.2.0]` entry → `check_docs_vs_code.py` Invariant 2 hard-fails the moment `__version__` bumps | `CHANGELOG.md`; gate `scripts/check_docs_vs_code.py:49-60` | Add `## [0.2.0]` (C.3, B.9, B.11, E.2/3/4/6/7/8, Block F, A1.2/1.3/2.2/2.3, B.3, POS-GATE, C.1/2, multi-match, +new kinds) **in the version-bump commit** |
| A2 | README probe count contradictory + stale (badge 144, prose 81; actual ~105 files / 297 `test_` fns) | `README.md:7`, `:259` | One convention; both sites agree with real suite |
| A3 | README DegradationKind table undercounts (lists 20; canonical now 28→30 after A2.3 + multi-match) | `README.md:89-110`; truth `models.py:64-245` | Add missing rows; drop "Twelve/v0.1.3" framing at `README.md:71` |
| A4 | `"Cannot edit encrypted PDF"` dead-ends the #1 real failure | `surgeon.py:1748,1973,2099` | **Largely resolved by A2.3** (password param lifts refusal); ensure residual no-password message names `decrypt_pdf` / "open with the password" |
| A5 | `plans/` ships in the sdist (experiments/, marketing/ correctly excluded) | `pyproject.toml:76-85` | Add `"plans/",` to hatch sdist exclude |
| A6 | Dep-boundary doc lie: `fonts.py` imports pdfminer (forbidden outside `locator`); pre-existing since c851042 | `fonts.py:16` (CMapParser/FileUnicodeMap, used `:859-860`), `:2638` (EncodingDB) | **Cut-minimum:** amend CLAUDE.md/architecture.md fonts row to "pikepdf + fonttools + pdfminer (CMap/encoding tables only)". Real fix = C9 (0.3.0) |

**Lockstep:** A1 + `__version__`/`pyproject.toml:7` bump land in one commit or CI is red.
**Ruthless minimum to cut:** A1, A2, A3 (CI blockers) + A4 + A5 (one line) + A6 (one doc edit) ≈ 1 hour, almost all docs, zero engine-logic change.

---

## (B) HIGH-VALUE DX WINS (small effort, non-breaking, additive)

The (B) tier is where "honesty as differentiator" pays off for the AI-agent consumer. **B1, B2, B4** especially. None are cut blockers.

| # | Finding | File:line | Fix |
|---|---------|-----------|-----|
| B1 | Honesty taxonomy unreachable from package root — `Degradation`, `DegradationKind`, `FONT_AFFECTING_KINDS` not in `__all__` | `__init__.py:30-41,70-135` | Export the three names |
| B2 | No human-readable summary on the report (no `__str__`/`summary()`) | `models.py` FidelityReport `:284-317`, EditResult `:341-385` | Add `summary() -> str` (e.g. "Saved with 2 warnings: kerning compressed (Tz 88%), overflow shifted"). **Highest single DX item** |
| B3 | No aggregate verdict accessor (clean vs degraded-but-saved vs failed hand-computed everywhere) | `models.py:284-317` | Add `is_clean -> bool`, `max_severity`, `warnings()` |
| B4 | `EditResult` has no `to_dict()` — `dataclasses.asdict()` silently drops computed `font_preserved` (the trap `FidelityReport.to_dict` fixed, reopened one level up) | `models.py:341-385`; existing fix `:319-337` | Add `EditResult.to_dict()` calling nested `fidelity_report.to_dict()` |
| B5 | Severity hand-typed at ~57 emit sites (drift risk) | e.g. `surgeon.py:1087`, `reflow.py:1609`, `structural.py:1718,2153` | Module-level `DEGRADATION_SEVERITY: dict[DegradationKind, ...]`; derive in `Degradation` ctor |
| B6 | Kind set not machine-enumerable (tracked in out-of-band memory) | `models.py:64` | `DEGRADATION_KINDS = tuple(typing.get_args(DegradationKind))`; test asserts == severity-dict keys |
| B7 | Read verbs missing `Raises:` sections | `locator.py:1012/1145/1170/1200`; `wrapper.py:32/60/84/105` | Add `Raises:` blocks |
| B8 | `find()` raises bare `IndexError` on out-of-range page (violates "every public entry raises PDFEditError") | `locator.py:602` | Raise `OperatorError`/PDFEditError subclass |
| B9 | `FontNotFoundError` message omits documented remedy | `fonts.py:668,1050` | Append "— install the font or pass full_font_path=<path>." |
| B10 | Internal jargon `"(slice-1)"` leaks to callers | `fonts.py:1363/1365/1371/1383` | Drop from user-facing strings |
| B11 | Input-path type inconsistency (`str` vs `str \| Path`) | `reflow.py:632`, `fonts.py:1487` vs rest | Widen all public verbs to `str \| Path` |
| B12 | `linebreak.py` (new v0.2.0 leaf) undocumented in architecture/dep tables | `architecture.md`, CLAUDE.md dep table | Add `reflow → linebreak` (stdlib-only) row |
| B13 | MCP wrapper may drop the new v0.2.0 kinds if it enum-filters | `pdf-edit-mcp` (separate repo) | Pass-through `{kind, detail, severity}`; serialize via engine `to_dict()` (B4) |

---

## (C) 0.3.0 BACKLOG (API-shape / cross-repo — own issue + own RED proof)

- **C1** `page` vs `page_number` split (read vs structural verbs) — standardize on `page`.
- **C2** `page` semantics flip (`None`=all in read; required-single elsewhere) — document + reject `None` explicitly on single-page verbs.
- **C3** Mutating verbs have 3 success protocols (EditResult / None / str path) — unify.
- **C4** `dry_run` coverage gaps (absent on `shift_content_below`, all 15 wrapper, all 5 annotation verbs).
- **C5** Positional `output_path` + optional positionals enable silent arg-swap — make keyword-only.
- **C6** No single-match `find→replace` convenience (caller indexes `[0]`; intervening edits invalidate match).
- **C7** `fonts.can_render` has no MCP tool (no pre-flight render check).
- **C8** MCP block-replace tools likely don't expose `fit="none"|"shrink"` (E.8) or `FontInfo.degradations`/widened `embedded_type` (C.2).
- **C9** `fonts.py`→pdfminer real root-fix (A6's permanent resolution): route `/ToUnicode` parse + `EncodingDB` through an `encoding.py` helper (firewall pattern).
- **C10** Path-echo inconsistency (`_pathutil.py:443` echoes resolved parent vs basename elsewhere).
- **C11** Re-audit `pdf_encrypt`/`pdf_decrypt` wrapper only after A2.3 lands.
- **C12** Exported-surface polish (no `replace_all_block`; consider dropping `shift_content_below`/`compute_uniform_layout` from public surface).

---

## Recommended follow-on unit (proposed, not yet approved)

After A2.3 + multi-match ship, the highest-value remaining work for "best UX + quality"
is a **"v0.2.0 release-readiness + DX-polish" unit** = all of (A) + the additive,
non-breaking (B1–B6) — rather than F-batch (Tier-3 reflow churn, lowest value).
This makes 0.2.0 genuinely cuttable AND delivers the honesty-taxonomy DX payoff.
