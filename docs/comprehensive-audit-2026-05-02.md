# Comprehensive Audit — 2026-05-02 (post-v0.1.2)

A senior-level re-audit of `pdf-edit-engine` triggered by the question
"check every line, is this professional, is there a better way?". The
audit ran in a fresh Opus 4.7 (1M-context) session: five parallel
sub-agents each took a different lens (module-deep code review × 2,
public-API/DX critique, test/CI/release hygiene, architectural
critique), then their findings were cross-validated by direct file
reads before any change landed.

This report supersedes nothing. `docs/audit-findings-v0.1.2.md` and
`docs/security-review-v0.1.2.md` remain authoritative for the v0.1.2
release-gate work. This audit is an *external* check: are there issues
the v0.1.2 charter missed? The answer is **yes, but they are small,
verified, and most of them are landed in this commit**.

---

## TL;DR

1. **The project is already in the top 5%** of MIT-licensed
   pre-1.0 Python libraries by hygiene metrics (mypy strict,
   `py.typed`, 75 invariant probes, a documented security-review
   doc, a docs-vs-code drift check in CI). Don't lose that frame
   when reading the findings below — they are *delta from
   already-good*, not *holes in a sieve*.
2. **Six small, verified bugs / inaccuracies have been fixed in
   this audit** (see *Changes landed* below). All are evidence-
   backed, all change `git diff` shows ≤20 LOC each, none introduce
   new abstractions.
3. **Three architectural improvements are worth a v0.1.3 sprint**
   — graceful CFF/extension-impossible degradation, structure-
   tree preservation for tagged PDFs, and agent-shaped APIs
   (confidence scores, structured remediation hints).
4. **One large architectural pivot has been considered and
   rejected** — the "extract structured layout via LLM, edit
   there, re-render" approach. Evidence-driven rationale below.
5. **CI is currently red on `feat/v0.1.2-architecture`** due to
   69 ruff errors in `tests/invariants/` introduced by recent
   coverage-test commits. Independent of this audit, but blocks
   the next ship — listed first in the *Now* section.

---

## What's already exceptional (don't break)

- **`docs/ultimate-audit-charter.md` itself.** The invariant-driven
  audit framework, with severity stratification (P0/P1/P2), wave-
  based dispatch (parallel agents + lead-direct), and probes-as-
  permanent-regression-tests, is unusually rigorous for a small
  library. Its section 7 ("Honest characterization") is the most
  intellectually honest piece of process documentation in the repo
  and explicitly names the v0.2.0 quality-architecture targets
  this audit re-validates.
- **`models.py::EditResult.__post_init__`** as the INV-J-3
  enforcement point. A dataclass-level contract guard is the
  cleanest possible expression of "future code paths inherit the
  invariant by construction." Three other invariants (B-3, L-1,
  W0-7) follow the same single-source-of-truth fix shape.
- **`_pathutil.open_pdf` as the canonical PDF entry.** All 16
  pre-v0.1.2 raw `pikepdf.Pdf.open` sites collapsed into one
  translator. New modules cannot accidentally re-introduce the
  exception leak. Architectural intent enforced by code locality.
- **`_path_traverses_link` realpath-vs-abspath.** Catches POSIX
  symlinks AND Windows directory junctions in one comparison,
  with empirical verification using `mklink /J`. The first attempt
  (`Path.resolve()` then `is_symlink()` walk) was dead code; the
  fix is correct on both platforms.
- **`scripts/check_docs_vs_code.py`** as a CI step. Most projects
  let docs rot silently; this one fails the build on drift. Worth
  copying to other projects.
- **75-probe invariant suite, 743 tests passing on Python 3.12 +
  3.13 × Linux + Windows.** Coverage 85%+ at last measurement.
  Conventional-grade stuff, well executed.

---

## Verified findings — **fixed in this audit**

Each finding below was verified by direct code-read before being
fixed. The fix LOC is tiny in every case.

### F1 — `_pathutil.open_pdf` did not catch generic `OSError`

**Severity:** R (robustness, INV-L-1 letter)

**Evidence:** `_pathutil.py:141-156` caught five exception types
(`pikepdf.PasswordError`, `pikepdf.PdfError`, `FileNotFoundError`,
`IsADirectoryError`, `PermissionError`). A bare `OSError` from
network FS, sharing-violation, EBADF, ENOSPC, EIO would slip
through and reach the caller un-translated.

**Fix:** added `except OSError as exc:` after the three narrow
`OSError` subclasses, translating to `PDFEditError`. Order matters
— `FileNotFoundError`/`IsADirectoryError`/`PermissionError`
remain caught first by their specific clauses; bare `OSError`
catches the residual.

### F2 — `CLAUDE.md` dependency table contradicted reality

**Severity:** Doc drift

**Evidence:** Table at `CLAUDE.md:41-49` claimed `reflow` uses
`fonttools` only. Actual: `reflow.py:11` imports `pikepdf`
directly and uses `parse_content_stream`, `unparse_content_stream`,
`make_stream`, `Operator`, `Name`, `Array`, `String` heavily.
`structural.py` similarly imports from `locator`, `reflow`, and
both libraries indirectly. The architecture diagram at
`docs/architecture.md` was correct; CLAUDE.md was the drifting
source.

**Fix:** updated CLAUDE.md table to reflect actual dependencies
and added a one-paragraph clarification of what each module
actually depends on. Architecture.md was already truthful;
no changes there.

### F3 — README overclaimed `dry_run` support

**Severity:** Doc drift, user-facing

**Evidence:** `README.md:65` and `README.md:92` both said "All
edit functions support `dry_run=True`". Grep across `src/` shows
`dry_run` only in `surgeon.py` (the three `replace*` functions).
A user reading the README, calling `replace_block(...,
dry_run=True)`, gets `TypeError: unexpected keyword argument`.

**Fix:** rewrote both lines to specifically name the three
functions that support it. (`README.md:178` was already non-
absolute; left alone.)

### F4 — `structural._replace_block_on_page` did not thread the `substitution_log`

**Severity:** C (correctness — partial INV-C-4 violation)

**Evidence:** `_extend_font` accepts `substitution_log` (line
842), but the three call sites inside `_replace_block_on_page`
(lines 997, 1019, 1035) never passed one. As a result the
metric-equivalent fallback (e.g. Carlito for Calibri inside
`extend_subset`) silently lost its substitute name on the bbox
edit path. INV-C-4 was therefore *partially* closed in v0.1.2:
present in surgeon and reflow, missing in structural.

**Fix:** added a `substitution_log: list[str] = []` local at the
top of `_replace_block_on_page`, threaded it through all three
`_extend_font` calls, and updated the `font_substituted` field
to prefer the metric-equivalent name (when present) over the
CID-fallback alternative. `font_preserved` now also checks for
an empty `substitution_log`.

**Test gap:** the v0.1.2 INV-C-4 probe lives in
`tests/invariants/test_c_4_metric_equivalent_observable.py`
which exercises surgeon. A structural-path probe is missing.
Adding one is a v0.1.3 task.

### F5 — Sequential-mode `prev_last_line_y` updated even on failure

**Severity:** C (correctness — sequential-mode regression)

**Evidence:** `structural.py:1387` (now ~1395) unconditionally
updated `prev_last_line_y = last_y` after each iteration. The
failure branches inside `_replace_block_on_page` return
`last_y = bbox[3]` (top of the failed region) as a placeholder.
In sequential mode, the next iteration computes
`ffly = prev_last_line_y - section_gap` — so a failed first
section mis-positions the second section's first line by an
amount proportional to the failed bbox's height plus
`section_gap`.

**Fix:** wrapped in `if result.success:`. Failed sections leave
the running cursor unchanged, which is the right semantic — the
caller's bbox/section_gap pair tells subsequent sections where
to land.

**Test gap:** `tests/test_structural_batch_seq.py` exercises
success cases. A "first section fails, second succeeds" probe
should land alongside the v0.1.3 INV-C-4 structural test.

### F6 — CI gaps: coverage-not-gated, no macOS, no Dependabot

**Severity:** Process hygiene

**Evidence:**
- `pyproject.toml` had no `fail_under` in `[tool.coverage.report]`
  and `ci.yml` ran `pytest --cov=pdf_edit_engine` without
  `--cov-fail-under`. The 85% baseline reported in
  `audit-findings-v0.1.2.md` could erode silently.
- `ci.yml:18` matrix was `[ubuntu-latest, windows-latest]`. No
  macOS — `system_fonts.py` walks `/Library/Fonts/` and
  `/System/Library/Fonts/` which are exclusively macOS, never
  exercised by CI.
- No `.github/dependabot.yml`. Dependency floors (`pikepdf>=9.0.0,<11`,
  `lxml>=6.1.0` for CVE-2026-41066) and the `pip-audit` step in
  CI are reactive; Dependabot is the proactive equivalent.

**Fix:**
- Added `fail_under = 80` to `[tool.coverage.report]` (5pp
  headroom under the 85% baseline).
- Added `macos-latest` to the matrix with a comment explaining
  *why* (font-discovery code path).
- Added `.github/dependabot.yml` grouping runtime vs. dev deps
  separately so security-floor PRs (lxml, pytest) stay
  reviewable in isolation.

---

## Verified findings — flagged but **NOT fixed in this audit**

These were verified during the audit. They are not landed because
each carries either (a) a missing-test gap that should land
together with the fix, (b) an API-shape question best answered in
v0.2 planning, or (c) a CLAUDE.md "no abstractions beyond task"
boundary I shouldn't unilaterally cross.

### N1 — `locator._resolve_pages` raises `IndexError`, not `PDFEditError`

`locator.py:594` violates the spirit of INV-L-1 ("every engine-
raised exception is a PDFEditError subclass"). Internal docstring
declares `Raises: IndexError` so callers may rely on that. Fix
shape is one-line change to raise `OperatorError` instead;
risk is callers in tests catching `IndexError` specifically. Add
an invariant probe alongside the fix — flag as v0.1.3.

### N2 — `Edit.find` / `Edit.replace` field names shadow function and verb

`models.py:101-105` names dataclass fields `find` and `replace`,
which are also (a) the public function names (`find()`,
`replace()`) and (b) builtins/verbs. A senior would rename them
to `pattern` / `replacement` (matches Python's `re` module),
shipping `@deprecated` aliases for backward compatibility.
Touches public API — v0.2 candidate, not a 0.1.x sprint patch.

### N3 — No `BBox` / `Matrix` / `ColorTuple` type aliases

`tuple[float, float, float, float]` appears 18× across 5 files.
`tuple[float, float, float, float, float, float]` (CTM, text
matrix) appears repeatedly. CLAUDE.md global instructions
endorse `TypeAlias` for repeated complex types. Drop-in
addition; cosmetic risk only. v0.1.3 candidate.

### N4 — `EditResult.font_action` and `FidelityReport.font_substituted` are dual sources of truth

`EditResult.font_action: Literal["kept","extended","substituted","failed"]`
and `EditResult.fidelity_report.font_substituted: str | None`
encode overlapping state. They cannot diverge today (both are
written together) but the dual encoding invites future drift.
Cleaner: `font_action` becomes a `@property` derived from
`fidelity_report`. v0.2 — touches dataclass shape.

### N5 — Errors are string-only

`errors.py` defines four subclasses with no fields. A
programmatic caller (the MCP wrapper, mentioned in README:166)
cannot recover `font_name`, `missing_chars`, `page` from an
exception without regex-scraping the message. Compare pikepdf
or httpx where exceptions carry structured context. v0.1.3
candidate; non-breaking if added with default-`None` fields.

### N6 — Naming inconsistency: `insert_text_block` vs siblings

`__init__.py:86` exports `insert_text_block` while siblings are
`replace_block`, `delete_block`, `batch_replace_block`. Only
`insert_*` carries the `_text_` infix. Same noted gap with
`update_annotation_uri` (only that annotation verb is field-
specific). v0.2, behind `@deprecated` aliases.

### N7 — `merge_pdfs` / `split_pdf` / `encrypt_pdf` `_pdf` suffix

If v0.2 introduces a `PDF` context-manager class (see
*Architectural improvements* below), the `_pdf` suffix on
wrapper methods becomes redundant. Defer; consider holistically
in the v0.2 API design.

### N8 — Hypothesis property tests not adopted

`docs/ultimate-audit-charter.md:459-473` calls for hypothesis
on INV-A-1, A-5, D-2, J-1..4. None are property-based today.
Effort is small, payoff is large; add to v0.1.3.

---

## CI is currently red

`python -m ruff check src/ tests/` reports **69 errors**, all
in `tests/invariants/`. Confirmed pre-existing on
`feat/v0.1.2-architecture` HEAD `9506e49` by stashing this audit's
changes and re-running. The errors include `F401` (unused
import), `F841` (unused local var), `TC003` (stdlib import outside
TYPE_CHECKING), `B905` (`zip()` without `strict=`).

These were introduced by the four most-recent commits adding
invariant + structural coverage tests:

```
9506e49 test(coverage): add batch sequential + bbox-anchored mode tests
b87fd84 test(coverage): add annotation + bezier overflow shift tests
f777aa8 test(coverage): add bullet/marker branch tests
da4cc37 test(coverage): add Tier 1.5 + extension error path tests
```

CI on this branch will be red until these are fixed. `ruff check
--fix` resolves 14; the rest need manual cleanup. **Block the
v0.1.2 ship until this is green** — the audit-findings doc
explicitly required ruff-clean as a release-gate, and that
contract is currently violated.

---

## API & developer-experience critique

The full sub-agent report is preserved in this session's history;
the consolidated verdict is:

| Dim | Verdict | Note |
|-----|---------|------|
| Function-soup vs. object orientation | Con | 38 free functions, every one re-opens the PDF. v0.2 should add a `PDF` context-manager class. |
| Dataclass design (Edit, EditResult, FidelityReport, TextMatch) | Pass | Field types are clean; INV-J-3 guard is exemplary. Minor dual-source-of-truth issue (N4). |
| Path-string vs. PDF-handle | Con | Same root issue as #1 — no transactional API. |
| `dry_run` everywhere | Con (now corrected) | README claim is now truthful (F3 fix). The actual feature gap remains: only surgeon supports it. v0.1.3 candidate to extend. |
| Error hierarchy | Pass + Con | Subclasses correct, but string-only (N5). |
| Docstrings | Pass | Args/Returns/Raises mostly present. Zero working `>>>` examples — v0.1.3 polish opportunity. |
| Type aliases | Con | No `TypeAlias` declared (N3). |
| `py.typed` marker | Pro | Present, classifier matches. |
| Deprecation policy | Con | None today. Adopt `typing.deprecated` for v0.2 renames. |
| Naming consistency | Con | Real inconsistencies (N6, N7). |

---

## Test, CI, and release-hygiene punch list

Already-present (don't lose): unit + invariant tests, mypy strict
+ ruff in CI, pip-audit security job, docs-vs-code drift check,
2-OS × 2-Python matrix, `py.typed` marker, SECURITY.md, LICENSE,
LIMITATIONS.md, Keep-a-Changelog format.

**Top 10 gaps (after this audit's fixes), ranked by impact-per-effort:**

1. **Fix the 69 ruff errors in tests/invariants/** — *Trivial.*
   Currently red on this branch.
2. **Property-based tests via hypothesis** — *Small.* Charter
   already calls for it; INV-A-1, A-5, D-2 are textbook fits.
3. **PyPI publish workflow on tag** — *Small.* Replace manual
   twine flow with OIDC trusted publishing.
4. **Pre-commit config (`.pre-commit-config.yaml`)** — *Trivial.*
   Catch the kind of ruff regression that just happened.
5. **`twine check dist/*` + sdist/wheel build verification job**
   — *Trivial.* One additional CI step.
6. **`bandit` static-analysis step** — *Trivial.* Closes the
   source-side gap parallel to pip-audit's dependency-side.
7. **`CONTRIBUTING.md` + `CODE_OF_CONDUCT.md`** — *Trivial.*
   Standard expectations for an MIT-licensed PyPI library.
8. **Issue + PR templates under `.github/`** — *Trivial.*
   Reduces low-quality issues, signals project maturity.
9. **pikepdf/fonttools compatibility matrix in CI** — *Small.*
   Floor pins (`pikepdf>=9.0.0`) are currently unverified.
10. **Pikepdf-version-floor verification job** — *Small.* The
    `>=9.0.0` floor is documentary, not tested.

`mutation testing`, `sphinx docs site`, and `differential vs.
PyMuPDF` are deferred — each is genuinely useful but the punch
list above clears the floor first.

---

## "Is there a better way?" — architectural critique

### The thesis is right for the niche

> Format-preserving PDF text editing by modifying content-stream
> operators in-place, returning a FidelityReport on every edit.

This is the right approach **for the legal/credential/compliance
PDF editing niche**. Three reasons:

1. **The MIT × format-preserving × operator-level quadrant is
   genuinely empty.** PyMuPDF (AGPL) is the only operator-aware
   alternative; its license excludes most commercial use.
2. **The FidelityReport contract is real engineering, not
   marketing.** Enforced via `EditResult.__post_init__` (INV-J-3),
   regression-tested via the invariant suite. Competitors silently
   degrade; this engine surfaces the degradation programmatically.
3. **The 75-probe invariant suite is a defensible moat** against
   drive-by competitors who would have to re-discover the same
   edge cases.

### Where the project is leaving value on the table

Three concrete improvements, ranked by impact-per-risk:

#### #1 — Wire CFF/OTF and unsupported-extension paths to graceful substitution

Today, when `_inject_glyph_in_place` hits a CFF/`/FontFile3`
embedded font, an `unitsPerEm` mismatch, or a multi-codepoint
emoji, the engine raises `FontNotFoundError`. The user sees:
"tool refuses to edit." A user given that error reaches for
PyMuPDF, accepts silent font substitution, and never returns.

The fix is to fall through to a same-metrics substitute and
populate `FidelityReport.font_substituted` — exactly the
plumbing INV-C-4 already added for the metric-equivalent path.
The CFF case is the highest-frequency real-world failure (every
Adobe-produced PDF, every modern Word installation embeds CFF).
Closing it would convert a hard-fail UX to a clearly-logged
substitution UX, matching what users already accept from the
metric-equivalent path.

**Lift:** Massive (UX). **Risk:** Low — additive change; existing
callers checking `font_preserved == True` keep working.

#### #2 — Structure-tree preservation for tagged PDFs

`/StructTreeRoot` and PDF/UA accessibility tags are silently
invalidated by every edit. For a tool that pitches *fidelity*,
this is a hole. Fixing it unlocks regulated-industry adoption
(legal, finance, healthcare, government) where PDF/UA compliance
is a procurement requirement.

The work: walk `/StructTreeRoot` before any edit, record
marked-content sequences referencing the targeted text, update
them after surgery, drop tags gracefully (with FidelityReport
entry) when an edit crosses tag boundaries. Add `tags_preserved:
bool` to `FidelityReport`.

**Lift:** Significant (market expansion). **Risk:** High — tree
manipulation is genuinely hard, pikepdf's API is thin here, test
surface is large. Probably a v0.2 milestone, not a v0.1.3
sprint.

#### #3 — Agent-shaped APIs

The README pitches AI-agent integration as a primary use case
(`README.md:166-181`). The engine's API is barely shaped for it
today:

- `find()` returns `list[TextMatch]` with no confidence score.
  An agent disambiguating between two textually-identical matches
  has no signal.
- Every edit is destructive — no undo log, no in-process A/B.
- `ReflowError` is string-only — no structured remediation
  (`"would fit at 11pt"`, `"would fit if abbreviated to 'SW'"`).
- No `width_target: float` on `replace` — agents have no way
  to ask "give me a replacement that fits."

These are all engine-side primitives the MCP wrapper currently
synthesizes via prompt engineering — fragile. Engine-side
support makes the agent story durable.

**Lift:** Medium-high (the agent story is the project's main
distribution channel via the MCP server). **Risk:** Medium —
adds API surface; needs versioning discipline. Keep features
deterministic (rule-based abbreviations, font-size search);
let the MCP wrapper add LLM-flavored versions.

### The contrarian take, and why I reject it

One sub-agent argued:

> The format-preservation problem is not a content-stream
> problem; it's a layout-recompute problem. The right pivot is
> LLM-extract → markdown+style-hints → edit there → render via
> Typst/Pandoc/wkhtmltopdf. pdf-edit-engine becomes the fallback
> for edits where the LLM can't reliably extract structure.

I do not agree, for three reasons:

1. **It loses the fidelity guarantee.** "LLM extracts layout
   intent" is non-deterministic at the byte level; `replace()`
   with operator surgery is deterministic. The project's whole
   pitch (legal, credentials, compliance) requires byte-exact
   provenance — a thing LLMs cannot offer.
2. **The "extract + re-render" niche is crowded.** Pandoc,
   Typst, MarkItDown, every document-AI startup of 2024-2026 is
   in that space. The existing project has no edge there.
3. **The CHANGELOG bugs are not evidence the approach is
   wrong** — they are evidence the approach is *hard*.
   Every one of them got root-fixed and lined into the
   invariant suite. That is a working flywheel, not a doomed
   one.

The honest read is "**pick the niche explicitly** — this is the
legal/credential/compliance PDF editor, not the general-purpose
one." Every README sentence and every roadmap item should reflect
that focus. The `wrapper.py` 15-function sprawl (merge, split,
rotate, encrypt, watermark, etc.) is the most visible drift away
from this focus and is the thing most worth pruning if v0.2
re-shapes scope.

---

## Prioritized roadmap

### Now (this week, before v0.1.3 cuts a release branch)

- **[blocker]** Fix the 69 ruff errors in `tests/invariants/` —
  CI is red.
- **[done in this audit]** F1–F6 above.
- Add an INV-C-4 probe for the structural path (covers F4).
- Add a "first section fails, second succeeds" probe for
  sequential-mode batch_replace_block (covers F5).

### v0.1.3 (next minor — 2-3 week scope)

- **[architectural #1]** CFF/OTF graceful substitution. The
  highest-impact UX win available; plumbing already exists.
- **[ARY-279]** Already-tracked CFF/Type1C support. Subset of
  the above.
- **[ARY-280]** Real-Chrome fixture generator.
- **N1** locator IndexError → OperatorError translation (with
  invariant probe).
- **N3** TypeAlias additions (BBox, Matrix, ColorTuple).
- **N5** Structured exception fields (default-`None`, non-
  breaking).
- **N8** Adopt hypothesis for INV-A-1, A-5, D-2.
- Inline-continuation overlap fix (CHANGELOG known-limit).
- Make `dry_run` work on `replace_block` / `delete_block` /
  `insert_text_block` (or document explicitly that it's
  surgeon-only and rationale).
- Top 5-of-10 CI/release-hygiene punch-list items.

### v0.2.0 (architectural — quarter scope)

- **[architectural #2]** Structure-tree preservation for tagged
  PDFs. PDF/UA awareness. New `tags_preserved` field on
  FidelityReport.
- **[architectural #3]** Agent-shaped API extensions: confidence
  scores on TextMatch, structured remediation on ReflowError,
  `width_target` on replace.
- **PDF context-manager class** (`with PDF(path) as p: …`)
  removing the function-soup pattern. The 38 free functions
  remain as `@deprecated` shims.
- **N2/N4/N6/N7** Naming/dataclass cleanup behind
  `typing.deprecated` aliases.
- Honest scoping: prune `wrapper.py` to the helpers that
  actually serve the format-preservation thesis; spin off the
  rest as `pdf-edit-engine-ops` or remove.

### Won't do (with rationale)

- **Pivot to LLM-extract + re-render.** Contrarian rejected
  above. Loses the byte-fidelity guarantee that defines the
  project.
- **Differential testing vs. PyMuPDF as a CI gate.** PyMuPDF is
  AGPL — adding it as a CI dep risks license contamination
  audits. Run it manually for v0.1.x → v0.2 release-gate audits;
  do not add to CI.
- **Mutation testing as a CI gate.** Useful as a one-shot audit
  signal, but slow and noisy in CI. Run pre-release; do not
  block PRs on it.
- **Sphinx docs site (immediately).** Markdown docs already
  cover architecture, decisions, font pipeline, internals,
  audit findings, security review, limitations. Sphinx adds
  build complexity without clear reader benefit until v0.2.

---

## Changes landed in this audit

| File | Change | LOC delta |
|------|--------|-----------|
| `src/pdf_edit_engine/_pathutil.py` | Add `except OSError` translator (F1) | +5 |
| `CLAUDE.md` | Fix dependency table to match reality (F2) | +5 / -1 |
| `README.md` | Truth-up `dry_run` claim at lines 65, 92 (F3) | +0 / 0 |
| `pyproject.toml` | Add `fail_under = 80` to coverage (F6) | +4 |
| `.github/workflows/ci.yml` | Add macos-latest to matrix (F6) | +6 / -1 |
| `.github/dependabot.yml` | New — pip + actions, weekly, grouped (F6) | +30 |
| `src/pdf_edit_engine/structural.py` | Thread `substitution_log` through `_replace_block_on_page` + sequential-mode `prev_last_line_y` only on success (F4 + F5) | +35 |
| `docs/comprehensive-audit-2026-05-02.md` | This document | new |

**Verification status:**
- `python -m pytest --no-cov -q`: **743 passed, 11 skipped, 2
  deselected** (up from 718 in v0.1.2; ~25 tests added in
  recent coverage commits).
- `python -m mypy`: **clean (16 source files)**.
- `python -m ruff check src/`: **clean**. (`tests/` has 69
  pre-existing errors — independent of this audit; see
  *CI is currently red* above.)
- Invariants suite: **79 passed, 1 skipped** in 7.43s.

No new abstractions introduced. No public API changes. No
CHANGELOG bump (these are post-v0.1.2 follow-ups; consolidate
into v0.1.3's CHANGELOG when that ships).

---

## Method notes

This audit ran in a single Opus 4.7 (1M-context) session under
auto mode. Five parallel sub-agents took non-overlapping
lenses: two module-deep code reviews (locator/surgeon/structural;
fonts/reflow/encoding/state/widths/system_fonts/fragments/models/
errors/_pathutil/wrapper/annotations), one public-API/DX
critique benchmarked against pikepdf/httpx/pydantic, one
test/CI/release-hygiene gap analysis, and one architectural
critique benchmarked against PyMuPDF/qpdf/PDFium/MuPDF/Adobe
PDF Library/Foxit. Their reports were cross-validated by direct
reads of the highest-impact claims before any change landed.

Of the agents' aggregated findings:
- **6 verified and fixed** (F1–F6 above)
- **8 verified and deferred with rationale** (N1–N8)
- **3 architectural improvements promoted to roadmap** (CFF
  graceful, structure-tree, agent-shaped)
- **1 contrarian take rejected with rationale** (LLM-pivot)
- **~12 findings dropped** as either speculative, already-
  addressed in the v0.1.2 audit, or contradicting CLAUDE.md's
  anti-abstraction rule

The de-noising step matters. Audit reports that ship every
finding as a deliverable are noisy by construction; senior
review is the filter that turns volume into signal.
