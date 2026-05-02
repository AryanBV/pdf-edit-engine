# Ultimate Audit Charter — pdf-edit-engine

## Context

This charter is executed in a FRESH Claude Code session (post-v0.1.2
release) with Claude Opus 4.7 at xhigh effort. It defines an
invariant-driven adversarial audit of the engine, designed to
surface root causes (not patches) for a v0.1.3 fix plan.

**This charter is NOT optional. Every section drives a concrete
deliverable. Do not paraphrase; follow each step.**

## Why this exists

The prior audits in this project have been branch-diff reviews
(`/ultrareview`) and code-reading with spot-probes. Both are useful
but bounded: they catch issues in modified code or in paths the
auditor happens to think to probe. They miss:

- Bugs in untouched modules
- Cross-module interaction failures  
- Invariants the engine claims but never tests
- Silent fidelity-contract violations (the class that hurts the
  most — the user sees a "success" that isn't)

This audit shifts the frame from "read code, look for smells" to
"enumerate invariants the engine must hold, construct a probe that
would violate each, run the probe, analyze failures."

## Non-goals

Explicitly OUT of scope — do not spend cycles on these:

1. Performance. Not a v0.1.3 concern.
2. UX / API ergonomics. Public API is stable.
3. Distribution (Smithery / Glama / dev.to). Separate track.
4. pdf-edit-mcp repo (separate repo, coordinated separately).
5. Refactors that don't fix a demonstrated invariant violation.
6. "Could be cleaner" opinions. Only evidence-backed findings.

## Method — invariant-driven adversarial audit

Three shifts over conventional LLM audits:

### 1. Probes, not reading

Every audit target is a **testable invariant**. Each invariant has
a probe — a Python script (5-40 lines) that would FAIL if the
invariant is violated. Probes become permanent `tests/invariants/`
regression tests after the audit.

### 2. Opus-xhigh where it's uniquely best

Division of labor:

| Work | Owner | Why |
|---|---|---|
| Invariant enumeration | Lead (Opus xhigh) | Requires deep cross-module reasoning |
| Probe construction | Lead (primary) + agents (bulk) | Lead designs shape; agents fill skeletons |
| Probe execution | Agents in parallel | Mechanical, parallelizable |
| Root-cause analysis | Lead (Opus xhigh) | Synthesis of failure evidence |
| v0.1.3 plan synthesis | Lead | Clustering root causes by substrate |

Sub-agents are assumed Sonnet-class. Never send Opus-xhigh's core
reasoning work (invariant design, root-cause synthesis) to an agent.

### 3. Probes are permanent artifacts

Every probe lands in `tests/invariants/` as a pytest-collected
test. Audit output isn't a discarded document — it's a regression
suite.

## Invariants to audit (organized by layer)

Each invariant below has:
- **ID**: stable identifier `INV-{layer}-{n}`
- **Claim**: what must be true
- **Probe shape**: how to test it
- **Expected**: should-pass / should-fail / unknown
- **Dispatch**: lead-direct / agent-safe

The lead should ADD invariants discovered during execution. ~40
below is a floor, not a ceiling.

### Layer A: Encoding (encoding.py, FontResolver)

- **INV-A-1** — `decode(encode(s)) == s` for every character in
  every font's cmap across the corpus (Latin-1, Identity-H,
  WinAnsi, MacRoman). Property test. *Expected should-pass.
  Agent-safe.*
- **INV-A-2** — `encode(s)` for an empty string returns empty
  bytes. Trivial but surprisingly often broken. *Agent-safe.*
- **INV-A-3** — CIDFont resolver always has `byte_width == 2`;
  simple-font resolver always `byte_width == 1`. *Agent-safe.*
- **INV-A-4** — Ligature round-trip: when `_max_ligature_len > 1`,
  greedy-longest-match encode of a known ligature produces the
  SAME bytes as the resolver extracted from that position in
  the original content stream. *Lead-direct* — needs
  content-stream walk.
- **INV-A-5** — `can_encode(text)` and `encode(text)` agree on
  feasibility: if `can_encode` returns `(True, [])`, `encode`
  must not raise; if `can_encode` returns `(False, missing)`,
  `encode` must raise `KeyError` whose `args[0]` contains the
  first missing char. *Agent-safe.*

### Layer B: Content-stream parse/unparse (pikepdf usage)

- **INV-B-1** — `parse_content_stream(unparse_content_stream(ops))`
  is idempotent for the identity case (no modification). Verify
  on every page of every corpus PDF. *Agent-safe.*
- **INV-B-2** — BT/ET blocks are always balanced in parsed ops
  from any corpus PDF. *Agent-safe.*
- **INV-B-3** — `operator_refs` from `find()` on a `TextMatch`
  remain valid until any mutation to the page's content stream;
  after `replace()`, reusing a `TextMatch` from before the
  `replace` raises `OperatorError` (not silent corruption).
  *Lead-direct* — needs careful sequencing.
- **INV-B-4** — `_find_bt_et_blocks` from reflow.py does not
  miss any text-showing operators (`Tj/TJ/'/"`) inside BT/ET.
  Cross-check against parse. *Agent-safe.*

### Layer C: Font extension (fonts.py, system_fonts.py)

- **INV-C-1** — `extend_subset(..., additional_chars="")` is a
  no-op (no PDF mutation, no disk I/O beyond the initial open).
  *Agent-safe.*
- **INV-C-2** — Tier 1 (CMap-only) never modifies the
  `/FontFile2` stream bytes — only `/ToUnicode`, `/W`, and
  `/CIDToGIDMap`. Binary-diff the stream pre/post Tier 1.
  *Lead-direct.*
- **INV-C-3** — Tier 1.5 preserves every pre-existing
  `CID → GID` mapping. For each CID in the pre-extension
  ToUnicode, the post-extension `_inject_glyph_in_place`'s
  updated font resolves the same CID to the same glyph outline
  as before. *Lead-direct* — this is the core ARY-278 promise.
- **INV-C-4** — `_METRIC_EQUIVALENTS` substitution is observable
  to callers: when Tier 1.5 substitutes Carlito for Calibri,
  the `EditResult` must eventually surface this (either via
  `font_substituted`, `warnings`, or a new field). Today this
  is known-broken (v0.1.2 bug_006); probe should FAIL before
  the fix, pass after. *Lead-direct.*
- **INV-C-5** — `find_font` with a `postscript_name` containing
  a subset prefix (`ABCDEF+Calibri`) must strip the prefix
  before lookup. *Agent-safe.*
- **INV-C-6** — After Tier 1.5 extension, the embedded font's
  `unitsPerEm` is unchanged. *Agent-safe.*

### Layer D: Locator (locator.py, find, get_text, get_text_layout)

- **INV-D-1** — `find(pdf, "")` returns `[]`. *Agent-safe.*
- **INV-D-2** — `find(pdf, text)` matches: for every match `m`
  in result, `"".join(c.unicode_char for c in m.characters) == text`
  (per-character consistency with the query). *Agent-safe.*
- **INV-D-3** — `get_text(pdf)` is deterministic: two calls on
  the same path produce byte-identical output. Catches cache
  poisoning. *Agent-safe.*
- **INV-D-4** — `get_text_layout(pdf)` returns TextBlocks whose
  `page` field matches the 0-indexed page they came from.
  *Agent-safe.*

### Layer E: Surgeon (surgeon.py)

- **INV-E-1** — `replace(p, m, Y, out, dry_run=True)` leaves `p`
  byte-identical on disk. *Agent-safe.*
- **INV-E-2** — `replace(p, m, Y, out)` produces an output
  where `get_text(out).count(Y) >= get_text(p).count(m.matched_text)`
  (replacement is observable). *Agent-safe.*
- **INV-E-3** — `replace_all(p, X, Y, out)` returns exactly
  one `EditResult` per `find()` match of `X`. *Agent-safe.*
- **INV-E-4** — `batch_replace` with zero edits returns `[]`
  without opening the PDF. *Agent-safe.*
- **INV-E-5** — Cross-font `replace_all`: on a PDF with
  multiple Identity-H fonts per page, `replace_all` does not
  cross-contaminate CIDs (the ARY-276/278 regression fixture).
  *Lead-direct.*

### Layer F: Structural (structural.py)

- **INV-F-1** — `replace_block(p, page, bbox, "", out)` on a
  bbox with text produces an output with that text absent.
  *Agent-safe.*
- **INV-F-2** — `delete_block(p, page, bbox, out)` twice on the
  same bbox is idempotent (second call is a no-op). *Agent-safe.*
- **INV-F-3** — `batch_replace_block` on `[]` is a no-op; does
  NOT open the PDF. *Agent-safe.*
- **INV-F-4** — `insert_text_block` at (x, y) above existing
  content shifts all existing content with `y' < y` downward
  by the inserted height. *Lead-direct.*
- **INV-F-5** — `compute_uniform_layout` is pure — same inputs
  produce same outputs deterministically. *Agent-safe.*

### Layer G: Reflow (reflow.py)

- **INV-G-1** — `detect_paragraphs` returns paragraphs whose
  `operator_indices` are disjoint pairwise. No operator
  appears in two paragraphs. *Agent-safe.*
- **INV-G-2** — `reflow_paragraph` with same-length replacement
  does not change `overflow_detected` from `False` to `True`.
  *Lead-direct.*
- **INV-G-3** — `reflow_paragraph` overflow-shift warnings from
  `_shift_content_below_inplace` appear in the returned
  `EditResult.warnings` (v0.1.2 fix; regression guard).
  *Agent-safe.*
- **INV-G-4** — `break_into_lines` with `paragraph_width`
  infinity returns exactly one line. *Agent-safe.*
- **INV-G-5** — Narrow single-line paragraph with inline
  continuation: reflow MUST produce either (a) correct
  placement, or (b) an explicit warning. Today only the
  shift-below case warns; inline-continuation overlap is
  silent (known limitation). *Probe should FAIL — this is
  v0.1.3's target.* *Lead-direct.*

### Layer H: Wrapper (wrapper.py)

- **INV-H-1** — `merge_pdfs([])` raises or returns a clean
  empty PDF. Never silent no-op that produces nothing.
  *Agent-safe.*
- **INV-H-2** — `encrypt_pdf(p, pw, out)` followed by
  `decrypt_pdf(out, pw, out2)` round-trips: `get_text(p) ==
  get_text(out2)`. *Agent-safe.*
- **INV-H-3** — `rotate_pages` by 360° is identity (up to a
  save round-trip). *Agent-safe.*
- **INV-H-4** — `fill_form` on a PDF without AcroForms raises
  a specific error, not a silent no-op. *Agent-safe.*
- **INV-H-5** — `flatten_annotations` on a PDF with zero
  annotations is a no-op. *Agent-safe.*

### Layer I: Annotations (annotations.py)

- **INV-I-1** — `get_annotations` then `delete_annotation`
  then `get_annotations` decreases count by exactly 1.
  *Agent-safe.*
- **INV-I-2** — `add_annotation` then `get_annotations`
  includes the added annotation with the URI as passed.
  *Agent-safe.*

### Layer J: FidelityReport / EditResult contract

- **INV-J-1** — Every `EditResult` returned from every public
  edit function has a `fidelity_report` attribute of type
  `FidelityReport`. *Agent-safe.*
- **INV-J-2** — `font_action == "extended"` implies at least
  one of `font_preserved==True`, `glyphs_missing==[]`,
  `overflow_detected` is explicitly set. *Agent-safe.*
- **INV-J-3** — `overflow_detected == True` implies at least
  one `warnings` entry referencing the overflow (v0.1.2 clamp
  contract). *Agent-safe.*
- **INV-J-4** — `success == False` implies `font_action ==
  "failed"` OR `warnings` is non-empty. Never success=False
  without a reason. *Agent-safe.*

### Layer K: Public API contract

- **INV-K-1** — Every name in `__init__.py::__all__` resolves
  to an actual export. No dead names. *Agent-safe.*
- **INV-K-2** — `__version__` is a valid semver string.
  *Agent-safe.*
- **INV-K-3** — Signature compatibility: for each function in
  `__all__`, its current signature is a superset of the v0.1.1
  signature (only new parameters with defaults). Import the
  package from `pdf_edit_engine==0.1.1` in a sibling venv,
  compare `inspect.signature`. *Lead-direct.*

### Layer L: Error hierarchy

- **INV-L-1** — Every exception raised BY the engine (not
  by pikepdf/fonttools/pdfminer) is a `PDFEditError` subclass.
  Run the full test suite with a monkeypatched
  `PDFEditError.__init_subclass__` that registers classes,
  then assert no non-registered exception class escapes any
  public function. *Lead-direct.*

### Layer M: Security / adversarial inputs

- **INV-M-1** — Encrypted PDF without password → `PDFEditError`
  subclass, not a raw pikepdf exception. *Agent-safe.*
- **INV-M-2** — Path traversal in `output_path` (`../../etc/passwd`)
  → `_pathutil.validate_output_path` raises before any I/O.
  *Agent-safe.*
- **INV-M-3** — Malformed content stream → `OperatorError`, not
  a silent corruption. Test with a fabricated PDF containing
  mis-nested BT/ET. *Lead-direct.*
- **INV-M-4** — Zero-byte PDF file → clean error. *Agent-safe.*
- **INV-M-5** — PDF with non-UTF-8 byte sequences in operators
  → documented rejection per LIMITATIONS.md. *Agent-safe.*

---

That's ~50 invariants. Add more as you discover them.

## Probe protocol

For EACH invariant:

1. **Create** `tests/invariants/test_{layer}_{id}.py` with a
   single pytest function.
2. **Write the probe** — minimal code that exercises the
   invariant. Use fixtures from `tests/corpus/` where possible;
   fabricate PDFs via `_build_identity_h_pdf` or reportlab for
   edge cases.
3. **Mark expected outcome**: `pytest.mark.xfail` if
   should-fail, plain test if should-pass.
4. **Run**: `pytest tests/invariants/test_{layer}_{id}.py -v`.
5. **Classify**:
   - pass + expected → invariant holds; ship the probe as
     regression guard.
   - fail + should-pass → **root-cause finding**. Enter in
     findings table.
   - xfail as expected → known limitation; tracks for fix.
   - inconclusive → re-probe with different inputs or escalate
     to lead-direct investigation.

## Dispatch plan

**Wave 1 (parallel agents)** — the 35 `agent-safe` invariants.
Launch as a single message with 5 agent calls, each responsible
for 7 invariants. Each agent returns a table: `{inv_id, probe_path, pass|fail|xfail|error, evidence}`.

**Wave 2 (lead-direct)** — the 15 `lead-direct` invariants.
Execute sequentially in this session. Each gets careful
cross-module reasoning.

**Wave 3 (synthesis)** — lead aggregates findings across waves,
clusters them by architectural substrate (like v0.1.2's 3
clusters), writes v0.1.3 plan.

## Deliverables

The fresh session MUST produce:

1. **`tests/invariants/` directory** populated with one test
   file per invariant. Each test annotated with the invariant
   claim in its docstring.
2. **`docs/audit-findings-v0.1.3.md`** — findings table:
   `| Invariant | Status | Evidence | Root cause | Fix shape |`.
3. **`.claude/plans/v0.1.3-plan.md`** — phased fix plan
   clustered by architectural substrate, priority-ordered by
   blast radius.
4. **Updated Linear project summary** — with audit completion
   note.

## Success criteria

- Every invariant has a probe in `tests/invariants/`.
- Every failing probe has a documented root cause in findings.
- Every root cause has a fix-shape proposal in v0.1.3 plan.
- The fresh session's context usage stays under 80%. If
  approaching that limit, commit findings and hand off to
  another fresh session rather than compact.

## Session handoff from v0.1.2 context

State at handoff (feat/v0.1.2-architecture branch, 8 commits
diverged from main):
- 643 tests passing, 15 skipped
- Ruff + mypy strict + docs-vs-code drift all clean
- CHANGELOG has [0.1.2] entry documenting ARY-277, 281, 282, 283
  + 7 ultrareview fixes
- Known limitations tracked in CHANGELOG: ARY-279 (CFF), ARY-280
  (real Chrome fixtures), narrow-paragraph inline continuation,
  font_substituted surfacing

Do NOT ship v0.1.2 before completing this charter. The whole
point is to find unsurfaced issues first.

## How to start the fresh session

After a fresh `/clear` or new Claude Code window, prompt:

> Execute the Ultimate Audit Charter at
> `docs/ultimate-audit-charter.md`. Follow every section.
> I am Aryan. Auto mode on. xhigh effort. Opus 4.7.

The fresh lead reads THIS file end-to-end first INCLUDING the
"Refinements" section below, then begins Wave 0 (coverage-gap
analysis) before any agent dispatch.

---

# Refinements (added after senior re-audit)

The original charter above is a sophisticated one-shot audit but
had eight gaps a senior engineer would catch. This section corrects
them. Treat refinements as authoritative — they override the
original where they conflict.

## 1. Severity stratification

The 50 invariants are NOT equal priority. Apply this triage:

**P0 — release-blocker.** Violation = user-visible silent corruption
or fidelity-contract breach. Must hold to ship.
- INV-J-3 (overflow → warning), INV-J-4 (failure → reason)
- INV-A-1 (encode/decode round-trip on corpus)
- INV-B-3 (operator_refs invalidation post-mutation)
- INV-C-3 (Tier 1.5 preserves pre-existing CIDs)
- INV-C-4 (metric-equiv substitution observable to caller)
- INV-E-5 (cross-font replace_all — ARY-276/278 regression guard)
- INV-G-5 (inline-continuation overlap warns or fixes)
- INV-L-1 (every raised exception is PDFEditError subclass)
- INV-M-3 (malformed content stream → OperatorError)

**P1 — next-release.** Violation = latent bugs not yet observed.
- INV-B-1, INV-B-2, INV-B-4 (parse/unparse, BT/ET balance)
- INV-C-2, INV-C-6 (Tier 1 doesn't touch FontFile2; upem stable)
- INV-D-2, INV-D-3 (find consistency, deterministic extraction)
- INV-E-1 (dry_run preserves input bytes)
- INV-F-4 (insert shifts existing content)
- INV-G-1, INV-G-2 (paragraph indices disjoint; same-length no overflow)
- INV-H-2 (encrypt/decrypt round-trip)
- INV-I-1 (annotation CRUD count)
- INV-K-3 (signature-superset compatibility)
- INV-M-1 (encrypted PDF without password → PDFEditError)
- INV-N-1, INV-N-2, INV-N-3 (differential — see Layer N below)

**P2 — quality polish.** All others. Run only if P0+P1 leave budget.

P0 probes execute first. P1 second. P2 only if time allows.

## 2. Wave 0 — coverage-gap analysis (BEFORE invariant dispatch)

The invariant list is theoretical. Some may already be covered by
the existing 643-test suite. Run this before Wave 1:

```bash
python -m pytest --cov=pdf_edit_engine --cov-report=term-missing --cov-report=html
```

For each source file with line-coverage < 80%:
1. Read the missing-line ranges from the report.
2. Identify what BEHAVIOR those lines implement.
3. ADD an invariant targeting that behavior, classify P0/P1/P2.
4. Append to the invariants list.

This grounds the audit empirically. Theoretical invariants list ~50;
real audit list will be 50 + N where N = uncovered code paths.
Without Wave 0, the audit boils the ocean and misses what coverage
already proves clean.

## 3. Layer N — differential testing (added)

The strongest correctness signal is "engine output matches a
reference implementation." Self-validating invariants are weaker.

- **INV-N-1** (P1): For every PDF in `tests/corpus/` with text,
  `engine.get_text(p)` agrees with `pdfminer.high_level.extract_text(p)`
  to ≥99% character identity (after NFC + whitespace normalize).
  Failures localize encoding/decoding bugs. *Lead-direct.*
- **INV-N-2** (P1): For replace operations on
  `tests/corpus/reportlab_simple.pdf`, validate the output via
  `pdfminer.extract_text` rather than `engine.get_text` to break
  self-validation. *Agent-safe.*
- **INV-N-3** (P2, optional): Add `pymupdf` to dev-deps temporarily
  in a separate venv, run differential text extraction across the
  corpus. Disagreements among engine + pdfminer + pymupdf at
  ≥95% page-level agreement. *Lead-direct.*

## 4. Property-based testing

INV-A-1, INV-A-5, INV-D-2, INV-J-1..4 are textbook hypothesis
targets. Add `hypothesis` to dev-deps for the audit; remove before
final v0.1.3 if not retained as regression test infrastructure.

```python
from hypothesis import given, strategies as st

@given(st.text(alphabet=st.sampled_from(font_supported_chars), max_size=50))
def test_round_trip(s: str):
    assert resolver.decode(resolver.encode(s)) == s
```

Apply to every invariant where the input space is enumerable but
hand-crafted inputs would miss adversarial cases.

## 5. Outcome-based success criteria (replaces procedural)

Audit succeeds when AT LEAST ONE of:

- **Bugs found**: ≥1 P0 finding OR ≥3 P1 findings, each with a
  reproducer probe in `tests/invariants/`.
- **Clean assertion**: every P0 invariant probe passes, with
  evidence committed.

Audit fails (and must restart with refined dispatch) if fewer than
40 of the (50 + Wave 0 additions) invariants have probes after
Wave 1+2. That signals dispatch was inadequate.

## 6. Release-blocker handling protocol

If a P0 invariant probe fails during Wave 1 or Wave 2:

1. Fresh session immediately writes the failing probe + evidence to
   `docs/audit-findings-v0.1.3.md` with severity tag P0.
2. Do NOT pause. Other P0 bugs may also exist; halting after the
   first one biases toward partial findings.
3. After all P0 invariants probed (Wave 1 + Wave 2 P0 subset),
   summarize P0 findings to user. User decides:
   - Fold P0 fixes into a v0.1.2-rc patch release before ship.
   - Skip v0.1.2; jump to v0.1.3 with bundled fixes.
   - Ship v0.1.2 as-is and prioritize P0s for v0.1.3.

## 7. Honest characterization

This charter is a **rigorous one-shot audit**. It is NOT the
engine's permanent quality architecture.

The true root-fix architecture would be:
- Invariants embedded as code via a decorator/DSL (e.g.,
  `@invariant("decode(encode(s)) == s")` auto-registers a test)
- Continuous CI integration: invariants run on every commit, not
  audit waves
- Property-based testing as the default, not the exception
- Differential testing (vs pdfminer.six and PyMuPDF) as the
  foundation correctness oracle
- Coverage-driven invariant generation (uncovered lines auto-flag
  as audit targets)

That architecture is a multi-week v0.2.0 project. This charter
catches present-day gaps; the architecture catches future ones.
Don't conflate the two.

## 8. Updated kickoff

Replace the kickoff prompt at the top of this section with:

> Execute the Ultimate Audit Charter at `docs/ultimate-audit-charter.md`.
> READ THE REFINEMENTS SECTION FIRST. Begin with Wave 0
> (coverage-gap analysis) before any agent dispatch. P0 invariants
> first throughout. I am Aryan. Auto mode on. xhigh effort. Opus 4.7.
