# Font-extension bug — M10 case

> **[v0.1.3 Phase 13 correction (2026-05-07)]** This bundle was captured
> on `design/v013-implementation` and originally framed the M10 fix path
> as **Tier 1 (CMap-only extension)** on the assumption that the
> `/FontFile2` binary's 6,954 `glyf` slots represented physically-present
> outlines. Block 3 pre-flight (`experiments/v013_block_3_preflight/REPORT.md`
> Section 4B) re-inspected F1 with a stricter probe and found that
> **only 116 of those 6,954 slots have real named glyphs**; the
> remaining **6,838 are anonymous `glyph0XXXX` placeholders** the
> embedder retained for slot-index consistency. **Neither `oslash` nor
> `udieresis` exists in F1's glyph table.** The correct fix path is
> therefore **Tier 1.5 (system-font sourcing)**, not Tier 1 — and
> Phase 13 implements that. Inline corrections below mark the specific
> sentences that drift from this updated empirical truth; the original
> analysis is preserved for the reasoning-trail audit.
>
> Reference numbers (locked from preflight 4B) for /F1 (Calibri-Bold; hosts "Sarah Chen"):
> - `glyf_total_slots = 6954`
> - `real_glyph_names = 116`
> - `placeholder_glyph0XXXX = 6838`
> - `cmap_size = 118`
> - `unitsPerEm = 2048`

> Evidence for the design doc Section 1 verification gate (a) claim:
> `docs/v0.1.3-implementation-design.md` lines 72–82 state that for the M10
> case, "the engine returns `font_action='kept'` (`extend_subset` not
> invoked), but the embedded Calibri subset's `/Widths` range and
> `/FontFile2` glyph slots are missing the high-byte chars."
>
> All findings below are reproducible from `inspect_font_dict.py`
> (script in this bundle, raw output in `font_extension_bug_trace.txt`).
> Branch: `design/v013-implementation` at the time of capture.

## Reproduction

- **Input PDF**: `experiments/v013_kerning_compare/input.pdf`
  (the M10 SOW, 173 604 bytes, Word/Calibri, single page).
- **Engine call**:
  ```python
  matches = pdf_edit_engine.find("input.pdf", "Sarah Chen")
  result = pdf_edit_engine.replace(
      "input.pdf", matches[0], "Søren Müller", "out.pdf", reflow=False,
  )
  ```
- **Engine version**: source on `design/v013-implementation`
  (`src/pdf_edit_engine/`). The bundled `inspect_font_dict.py` imports
  `pdf_edit_engine` directly via `sys.path` insert against `src/`, so
  there is no installed-package version skew.

## Observed engine behaviour

Captured by `inspect_font_dict.py`, section 6 of the trace:

| Field | Value |
|---|---|
| `result.success` | `True` |
| `result.font_action` | `'kept'` |
| `result.warnings` | `[]` |
| `fidelity_report.font_preserved` | `True` |
| `fidelity_report.font_substituted` | `None` |
| `fidelity_report.glyphs_missing` | `[]` |
| `fidelity_report.overflow_detected` | `False` |

The engine reports a fully-successful, fully-preserved edit. No
warnings, no missing glyphs, no font substitution. By every metric the
engine surfaces, the edit is clean.

The rendered output PDF (independently verified in
`experiments/v013_kerning_compare/m10_verification.png`) shows ø and ü
as missing/blank glyphs. The engine's own report contradicts the visual
evidence.

## Root cause — code path

The `font_action="kept"` decision lives at one site, with one gate:

- **`src/pdf_edit_engine/surgeon.py:541`** —
  `can_enc, missing = resolver.can_encode(new_text)`
- **`src/pdf_edit_engine/surgeon.py:542`** —
  `font_action: Literal["kept", "extended", "substituted", "failed"] = "kept"`
  (default).
- **`src/pdf_edit_engine/surgeon.py:548`** —
  `if not can_enc:` is the *only* branch that calls `extend_subset()`
  (lines 549–588) or sets `font_action = "extended"` (line 583). When
  `can_enc=True`, the entire extension block is skipped and
  `font_action` keeps its default of `"kept"`.

So the question reduces to: *why does `can_encode("Søren Müller")`
return `True` when the embedded font cannot actually render ø or ü?*

## `can_encode` is an encoding-map check, not a glyph-coverage check

- **`src/pdf_edit_engine/encoding.py:211–240`** — `FontResolver.can_encode()`.
  The non-CID branch (lines 236–239) is:
  ```python
  for ch in text:
      if ch not in self._unicode_to_byte:
          missing.append(ch)
  return (len(missing) == 0, missing)
  ```
- `self._unicode_to_byte` is populated by `_init_winAnsi()`
  (`encoding.py` around line 68; full WinAnsi encoding has byte slots
  for ø=0xF8 and ü=0xFC). Direct probe in section 5 of the trace:
  ```
  can_encode('Søren Müller') = (can_enc=True, missing=[])
    char 'ø' (U+00F8): can_encode = True, missing = []
    char 'ü' (U+00FC): can_encode = True, missing = []
  ```
- The check verifies *the encoding standard knows about these
  Unicode points*, not *the embedded font has a glyph and a width for
  these byte values*.

For an Identity-H CIDFont the same method *would* effectively double as
a coverage check, because `self._unicode_to_cid` is built from the
embedded font's actual ToUnicode CMap (only Unicode points the font
declares are mappable end up in the dict). But for simple WinAnsi
TrueType fonts — the case here — the encoding is the standard table,
not the embedded font's coverage.

## Embedded-font reality (verified)

`inspect_font_dict.py` directly inspected `/F1` (the font that hosts
"Sarah Chen"):

- **`/BaseFont`**: `/BCDEEE+Calibri-Bold`
- **`/Subtype`**: `/TrueType`
- **`/Encoding`**: `/WinAnsiEncoding`

### `/Widths` range — section 3 of the trace

```
/FirstChar=32  /LastChar=122  len(/Widths)=91
  ø (0xF8): in /FirstChar..LastChar range = False, width entry present = False
  ü (0xFC): in /FirstChar..LastChar range = False, width entry present = False
```

Both bytes (0xF8, 0xFC) are out of the `/Widths` range. A renderer
that lands on byte 0xF8 in the content stream has no width for it;
the glyph displacement formula `tx = ((w0 - Tj/1000) * Tfs + ...) * Th`
falls back to `/MissingWidth` (or 0).

### `/FontFile2` cmap — section 4 of the trace

```
/FontFile2 raw size = 141780 bytes
glyphOrder length = 6954 glyphs
cmap entries = 118
  ø U+00F8: NOT in cmap (no glyph)
  ü U+00FC: NOT in cmap (no glyph)
```

A non-obvious detail (worth flagging for Aryan): the `/FontFile2`
binary contains 6 954 glyphs — i.e. the *full* Calibri-Bold outline
table is embedded, not a narrow subset. The narrowing happens entirely
in the cmap layer (only 118 of those glyphs are reachable from any
Unicode codepoint). This matches the project's existing observation
in `CLAUDE.md`: *"`Subsetted` fonts may be full: Check glyph count
before assuming extension is needed. Spike found Calibri 'subset' with
6954 glyphs."* The fix path here would be Tier 1 (CMap-only extension)
in the language of `docs/font-pipeline.md`, *not* Tier 1.5 — the
outlines are physically present.

> **[v0.1.3 Phase 13 correction]** The paragraph above is wrong about
> the outlines. Block 3 preflight 4B re-inspected with a stricter probe
> (`non_cid_extension_scope.py`, in the demo-verification bundle) and
> walked the `glyf` slots: only **116 of 6,954** carry real named
> glyphs (alphabetical Latin + a small punctuation set). The other
> **6,838 are anonymous `glyph0XXXX` placeholders** — empty/null entries
> the embedder retained for slot-index consistency, with no outline
> data and no cmap reachability. **`oslash` and `udieresis` are NOT
> in the 116 real names.** The cmap-only Tier 1 fix path proposed
> here cannot work because there is no outline to map *to*. The
> correct fix is Tier 1.5 (source the outline from a system font and
> inject it), which Phase 13's `_extend_simple_tier_15` implements.
> The CLAUDE.md "subsetted fonts may be full" rule still applies in
> general — it just doesn't describe this specific F1's slot
> population.

## Scope estimate (for Aryan to decide)

A real fix has two parts:

1. **Strengthen `can_encode` to be a coverage check, not just an
   encoding-map check.** For the non-CID branch, verify that
   `self._unicode_to_byte[ch]` lands in `/FirstChar..LastChar` AND
   that the embedded `/FontFile2` cmap maps `ord(ch)` to a glyph name
   present in `getGlyphOrder()`. The resolver doesn't currently carry
   a back-reference to the font dictionary or a parsed
   `TTFont`-like view, so the plumbing is the chunky part.

   Estimated touch: **`encoding.py` ~30–60 LOC** (new helper +
   resolver state + fallback for fonts without a parseable
   FontFile2).

2. **Update call sites**. `surgeon.py:541` is the headline call.
   `reflow.py` and `structural.py` have the same pattern (search
   `resolver.can_encode`). All sites continue to work with the
   strengthened method; only the result distribution changes
   (some `True`s flip to `False`, triggering `extend_subset`).

   Estimated touch: **~5–15 LOC** spread across `surgeon.py`,
   `reflow.py`, `structural.py`. No new logic.

3. **Possibly `fonts.py`**. `fonts.py` already has helpers that
   parse `/FontFile2` for Tier 1 / Tier 1.5 extension. The new check
   should reuse those, not re-implement.

   Estimated touch: **~0–20 LOC**.

**Order of magnitude: ~50–100 LOC across 2–3 files.** Non-trivial
because of the resolver-needs-fontfile2-back-reference plumbing, but
not large by the project's standards.

## Findings worth Aryan's attention (NOT unilateral revisions)

1. **The font-extension bug is real, and the design doc's
   characterization is accurate** — `font_action="kept"` is what the
   engine returns; `/Widths` is 32–122 with no high-byte coverage;
   `/FontFile2` cmap omits ø/ü. The design doc Section 1 lines 73–82 stand.

2. **The "missing glyph slots" framing is *almost* right but misses
   a nuance the audit may want**: the `/FontFile2` binary actually
   contains 6 954 glyph outlines (the full Calibri-Bold). What's
   missing is the *cmap mapping* from U+00F8 / U+00FC into those
   outlines, plus the corresponding `/Widths` entries. The fix is
   pure Tier 1 (CMap-only extension), not Tier 1.5 — much cheaper than
   the design doc's framing implies. This may be relevant to scope/risk
   discussions about whether to bundle a fix in v0.1.3 or defer.

   > **[v0.1.3 Phase 13 correction]** The "much cheaper than the design
   > doc's framing implies" claim was based on the now-falsified
   > assumption that 6,954 outlines are present. Block 3 preflight 4B
   > showed only 116 are real; 6,838 are anonymous placeholders. The
   > design doc's Tier 1.5 framing was correct (or at minimum was
   > consistent with the actual code path needed); this audit-bundle
   > finding underestimated the work. Phase 13's `_extend_simple_tier_15`
   > does the actual Tier 1.5 work that this case requires.

3. **`fidelity_report.glyphs_missing` is `[]` in this case**, not a
   populated list. The engine has zero observable signal that anything
   went wrong. Whatever v0.1.3 does about lying-success-paths
   (Section 3 of the design doc), this code path also lies but is not
   currently in the §3 mapping table because the outer site is `kept`,
   not `failed`. Worth confirming that v0.1.3's `INV-J-5` probe scope
   covers this site, or explicitly defers it.

4. **The fix unblocks gate (a)**, since `font_action` would flip to
   `"extended"` and ø/ü would render via Tier 1 CMap-only extension,
   removing the variable that currently makes gate (a) inconclusive
   for the M10 visual.

## What was NOT verified

- Whether `reflow.py` and `structural.py` exhibit the identical
  pattern (high confidence from grep, but the call sites weren't
  individually traced through this run). `[unverified]`
- Whether other corpus PDFs exhibit the same bug (the trace only
  exercises `experiments/v013_kerning_compare/input.pdf`).
  `[unverified]`
- Whether a metric-equivalent system font (Carlito/Liberation Sans)
  that *does* have ø/ü cmap entries would have rescued the case via
  Tier 1.5 had `can_encode` returned `False`. The resolver code
  branches downstream, but I did not test the counterfactual.
  `[unverified]`
- Whether `_init_winAnsi()` populates `_unicode_to_byte` exhaustively
  for *all* WinAnsi positions, or just the printable-ASCII subset.
  Section 5 of the trace shows ø and ü are both present, so for the
  M10 case the path is established; full enumeration was not done.
  `[unverified]`

## Citations

| Claim | File:line |
|---|---|
| `font_action` defaults to `"kept"` | `src/pdf_edit_engine/surgeon.py:542` |
| Only `can_enc=False` invokes `extend_subset` | `src/pdf_edit_engine/surgeon.py:548–588` |
| Replace flow uses `resolver.can_encode(new_text)` | `src/pdf_edit_engine/surgeon.py:541` |
| `can_encode` non-CID branch tests only `_unicode_to_byte` | `src/pdf_edit_engine/encoding.py:236–239` |
| WinAnsi initialization populates `_unicode_to_byte` from the standard | `src/pdf_edit_engine/encoding.py:_init_winAnsi` (~line 68 dispatch) |
| Calibri "subset" has full 6954 glyphs (precedent) | `CLAUDE.md` "Subsetted fonts may be full" rule |
| Tier 1 (CMap-only extension) is a cheap fix path when outlines are present | `docs/font-pipeline.md` lines 8–22 |
