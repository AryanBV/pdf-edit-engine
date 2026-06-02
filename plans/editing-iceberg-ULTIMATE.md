# Editing-Depth ULTIMATE Plan (v3, superseding `editing-iceberg-plan.md` and the v2 draft)

> **Status**: Lead-architect plan for the **v0.2.0+ major-magnitude editing-depth release**.
> **Supersedes**: `plans/editing-iceberg-plan.md` (v1, 12 ranked items, ranks 9–12 deferred wholesale)
> AND the v2 draft of this file (which the ambition critic judged STILL under-reaching — it deferred
> world-script WRITE, RTL WRITE, named-CMap DECODE, Identity-V WRITE wholesale to 0.3.0 even though their
> dependencies all land in 0.2.0, and dropped empty-string-delete / shrink-to-fit / rotated-text /
> inline-image entirely).
> **Scope rule**: editing **DEPTH only** — make `find` / `replace` / `replace_all` /
> `replace_block` / `extend_subset` / reflow **more correct, more universal, more honest**
> on more real-world PDFs. **No new verbs, no new document operations, no scope expansion.**
> **License rule**: no AGPL. PyMuPDF / borb are reference-only, never adopted.
> **Verification base**: every current-state claim is cited to `file:line` at branch tip
> `prereq/v013-honesty-fixes` (HEAD `5c0a436`, v0.1.3 git-tagged); external claims cite URLs/specs.
>
> **v3 ambition-closure delta (what the critic forced IN that v2 deferred or dropped):**
> 1. **World-script WRITE for TrueType-embedded scripts (B.10, NEW)** — the uharfbuzz shaping bridge +
>    per-glyph TJ/Td writer ships in **0.2.0** behind the SAME `shaping_unsupported`/`cff2_unsupported`
>    honest-refusal the engine uses everywhere. v2's own evidence says "TrueType complete; CFF rides the
>    0.2.0 injector" — every dependency lands in 0.2.0, so deferring the writer was textbook timid
>    bundling. Only the CFF complex-script path (needs C.3 CID-keyed CFF, which DOES ship in 0.2.0 — so
>    it ships too) and the genuinely-missing-GSUB / CFF2 cases stay honest refusals.
> 2. **RTL WRITE same-length splice (B.8 deepened)** — `replace('שלום','עולם')` works in 0.2.0 when
>    glyph-count is preserved (no reorder needed; the run is already stored visually). Length-changing
>    RTL → typed `rtl_write_unsupported`, named explicitly, never silent-wrong.
> 3. **Named-CMap READ DECODE (B.4 deepened to Phase A+B-read)** — VERIFIED LIVE on pdfminer 20260107:
>    `CMapDB.get_cmap('90ms-RKSJ-H').decode(b'\x20\x81\x40\x20')` → `[231,633,231]` (real CIDs). The 149
>    bundled named CMaps decode TODAY. Pulled into 0.2.0; only embedded-`/CMap`-stream `code2cid` (pdfminer
>    builds 0 entries) stays a 0.3.0 frontier.
> 4. **Identity-V same-position WRITE (B.4 deepened)** — the READ model already computes correct vertical
>    geometry, so same-position vertical re-emit is buildable and ships in 0.2.0; only length-changing
>    vertical *reflow* (needs a vertical-flow model) defers, with a cited reason.
> 5. **Type1 → CFF transplant on extension (C.5, NEW)** — instead of a flat refuse, convert `/FontFile`
>    Type1 charstrings to CFF via `t1Lib` (already in C.2's introspection scope) and route through the C.3
>    CFF injector; CFF2-style unconvertible cases → honest refusal.
> 6. **Empty-string / full-deletion operator cleanup (B.11, NEW)** — `replace(x,'')` removes the operator
>    structure (no stale TJ positioning, no invisible-glyph residue) so deleted text is truly gone from
>    extraction; un-cleanable spans → typed `deletion_residual_text`.
> 7. **Shrink-to-fit by font-size reduction (E.8, NEW)** — caller-selectable `fit='shrink'` reduces font
>    size to fit a fixed region (Acrobat/Word behavior) as an alternative to reflow/overflow.
> 8. **Rotated / sheared text runs (B.12, NEW)** — non-axis-aligned `Tm` (b/c ≠ 0): same-length splice
>    works; reflow/bbox refuse honestly with `rotated_text_unsupported`.
> 9. **Inline images BI/ID/EI (A1.4, NEW)** — verify `parse_content_stream` indexing on inline-image
>    streams across the corpus; add an `inline_image_present` guard so the most-invasive new traversals
>    (Rank 1 XObject recursion, D.1 marked-content walk) never mis-index `operator_range`.
> 10. **Widow gets a TYPED kind (E.4 firmed)** — `line_break_quality_degraded` is committed, not left as a
>    free-text warning, honoring the machine-readable-honesty moat.

---

## 0. Vision & Ambition Bar

### Vision statement

> **Edit text correctly, with full VISUAL *and* SEMANTIC fidelity, in ANY real-world PDF —
> or refuse honestly with a typed `Degradation`.** Best-in-class versus commercial libraries
> for true *in-place* content-stream text editing, under a permissive (MIT/MPL, no-AGPL) license.

This engine modifies content-stream operators in place and extends embedded font subsets
additively. It does **not** redact-and-overlay (PDFlib TET, PyMuPDF) and does **not** re-render
(borb, fpdf2, reportlab). That mechanism, combined with a machine-readable per-edit
`FidelityReport`, is the moat.

### Ambition bar vs SOTA (cited competitive verdict)

| Capability | Us | Who else | Source |
|---|---|---|---|
| True in-place content-stream edit (modify Tj/TJ, not overlay) | **have** | Aspose.PDF, Foxit, Apryse; NOT iText/PDFlib/PDFBox | iText additive-only ([kb ch.5](https://kb.itextpdf.com/itext/chapter-5-manipulating-an-existing-pdf-document)); PDFlib TET = white-rect overlay ([cookbook](https://www.pdflib.com/tet-cookbook/tet_and_pdflib/search-and-replace-text/)); PDFBox removed `ReplaceText` as an "incorrect illusion" ([migration](https://pdfbox.apache.org/2.0/migration.html)) |
| Additive embedded-subset extension in-place (inject glyph, no re-embed) | **have** | **NONE** of the surveyed engines; Foxit requires a *fully* embedded font | `fonts.py` `_inject_glyph_in_place`; Foxit limitation ([dev hub](https://developers.foxit.com/developer-hub/document/edit-text-pdf-using-foxit-pdf-sdk/)); PDFBox "No glyph for U+540D" on subsets |
| Per-edit machine-readable honesty report (typed degradations + computed `font_preserved`) | **have** | **NONE** — all silently degrade or throw | `models.py:58-141`; no competitor doc surfaces an equivalent |
| Paragraph reflow on length change | **partial → deepened here** | Acrobat Auto-Adjust-Layout, Foxit, Aspose `ReplaceAdjustment` | `reflow.py`; Acrobat ([helpx](https://helpx.adobe.com/acrobat/using/edit-text-pdfs1.html)) |
| Edit text in Form XObjects (letterheads/headers) | **missing → Block B** | Foxit/Aspose/Acrobat | ISO 32000-2 §8.10.2 |
| Non-Identity Type0 decode (named CMap, e.g. 90ms-RKSJ-H) | **missing → Block B (0.2.0)** | Aspose/Foxit/Adobe/PDFlib | ISO 32000-1 §9.7.4.3; pdfminer `CMapDB.get_cmap` verified |
| Identity-V vertical CJK read + same-position write | **missing → Block B (0.2.0)** | Aspose/Foxit/Adobe | ISO 32000-1 §9.7.4.3 |
| ToUnicode-absent Identity-H recovery | **missing → Block B** | Aspose/Adobe (cmap reversal) | — |
| Extend CFF / CIDFontType0C in place | **missing → Block C** | iText/Aspose/Foxit/Adobe author CFF | ARY-279 |
| Extend Type1 (`/FontFile`) in place (via Type1→CFF) | **missing → Block C (0.2.0)** | none in-place; Adobe re-embeds | t1Lib + cffLib transplant |
| Tagged-PDF / ActualText accessibility sync on edit | **missing → Block D** | Aspose (24.7), Acrobat | [Aspose ActualText](https://reference.aspose.com/pdf/net/aspose.pdf.structure/element/actualtext/) |
| Complex-script (Arabic/Indic/Thai) in TrueType-embedded fonts, WRITE-side shaping | **missing → Block B (0.2.0, B.10)** | iText pdfCalligraph (paid), Aspose/HarfBuzz | [iText](https://itextpdf.com/blog/technical-notes/displaying-text-different-languages-single-pdf-document); [uharfbuzz](https://github.com/harfbuzz/uharfbuzz) |
| RTL (Hebrew/Arabic) same-glyph-count replace | **missing → Block B (0.2.0, B.8)** | Aspose/Adobe | UAX #9 |
| Empty-string deletion with operator cleanup | **missing → Block B (0.2.0, B.11)** | Acrobat redaction; iText overlay | — |
| Shrink-to-fit (font-size auto-reduction) | **missing → Block E (0.2.0, E.8)** | Acrobat/Word "shrink text to fit"; AcroForm /Q auto-size | [Acrobat](https://helpx.adobe.com/acrobat/using/edit-text-pdfs1.html) |
| Rotated / sheared text-run edit | **missing → Block B (0.2.0, B.12)** | Aspose/Adobe | ISO 32000-1 §9.4.2 (Tm) |
| Justified-alignment preservation on reflow | **missing → Block E** | Aspose, iText, Adobe | [Aspose](https://docs.aspose.com/pdf/net/replace-text-in-pdf/) |
| Spot/Separation/ICC color fidelity on rebuild | **partial → Block F** | PDFlib, callas, Adobe | — |
| Edit while preserving a digital signature | **UNBUILDABLE on stack** | GdPicture, Apryse, iText (incremental) | pikepdf consolidates on save → detect-and-Degrade only |

**Defensible best-in-class position** = (in-place edit) × (additive subset extension) × (typed
honesty) under no-AGPL — the union **no competitor offers for free**. This release closes the
"missing" rows that are *buildable on the stack* and converts the genuinely-unbuildable rows
(signature-preserving save, Type3, cross-page word-processor reflow, embedded-`/CMap`-stream
`code2cid`) into **honest typed refusals**.

> **v3 closure note.** The v2 draft listed complex-script shaping, RTL write, named-CMap decode, and
> Identity-V write as "→ v0.3.0" in this table. The critic correctly observed that EVERY hard dependency
> for those rows lands in 0.2.0 (TrueType glyf injector exists; C.3 ships CID-keyed CFF; named-CMap decode
> is verified bundled). The honest, ambition-maximal move is to ship each WRITE path in 0.2.0 behind the
> SAME typed-refusal contract Block C uses for CFF2 — so they are now Block B (0.2.0) above. A pillar whose
> dependencies all land in the same release does not get deferred.

---

> **RE-AUDIT RE-SHAPE (post-v3, this revision).** A feasibility re-audit against HEAD `5c0a436`
> tightened the buildable boundary of the v3 plan. Four pillars the v3 draft pulled INTO 0.2.0 are
> over-reaches that do not survive a source-level read: B.10 world-script *write*, B.8 RTL *write*,
> B.4 *named-CMap character-changing write*, and C.5 *Type1 extension* as written. The proven core,
> the deferral, and the six inline corrections are captured in the two new sections immediately
> below (`## 0.2.0 SCOPE` and `## 0.3.0+ — deferred from 0.2.0`) plus a `## Moat` item and an `## M0`
> note. Earlier sections (§0–§9) are retained verbatim as the ambition record; where they conflict
> with the scope sections below, **the scope sections are authoritative for the 0.2.0 build.**

## 0.2.0 SCOPE — proven core (~28 items, ONE XL)

These items survive a source-level feasibility read at HEAD `5c0a436` and ship in 0.2.0. The single
XL is **C.3 (CFF / Type1C glyph injection)**; everything else is S/M/L. Items are grouped by the
existing block letters; the per-item designs live in §4 above (read them WITH the inline corrections
flagged here).

- **A1 — Robustness/honesty foundation** (ships first):
  - **A1.1** — Centralize parse/unparse exception translation in `_pathutil` (+ `content_stream_unparseable`).
  - **A1.2** — q/Q graphics-state stack depth cap.
  - **A1.3** — Bound decompressed font/CMap stream size (Flate-bomb pre-gate).
  - **A1.4** — Inline-image BI/ID/EI operator-index integrity guard. **GATING** — the probe runs in
    M0 (see M0 note) and gates B.2 / D.1; if it shows index drift it escalates from a guard to an
    interpreter fix BEFORE the invasive traversals ship.

- **A2 — Save-side honesty** (shared `_SaveOutcome` contract): A2.1 signature-invalidation detect-
  before-write + `force` gate; A2.2 linearization preserve+detect; A2.3 encrypted decrypt-edit-
  re-encrypt round-trip. *(Included in 0.2.0 per §2; the re-audit did not contest these.)*

- **B — Correctness / total text reachability** (the world-script/RTL *write* halves move OUT; the
  decode/read halves and the splice-only edits stay):
  - **B.1** — Rank 5 GlyphWidthCache objgen re-key (**RETAIN evict** — in-place `/W` mutation does
    not change objgen, so re-key fixes aliasing but NOT staleness). Lands first/with B.2.
  - **B.2** — Rank 1 Form XObject text editing WITH the per-element `/Parent`-walk resource-scope
    chain (all 14 sites). **CORRECTION (see inline):** the copy-on-write branch must **deep-clone the
    touched font objects** (`/FontDescriptor` + `/FontFile2` + `/ToUnicode` + `/W` + `/CIDToGIDMap`),
    not just duplicate the content stream, and must read the stream with **`read_bytes()` (decoded),
    NOT `read_raw_bytes()`**.
  - **B.3 & B.5 (ToUnicode)** — B.3 ToUnicode-absent Identity-H recovery (PHASED: read-recovery +
    PUA-purity gate + write-path `/ToUnicode` synthesis-or-refuse fixing the `fonts.py:306` raw
    `read_bytes()` KeyError); B.5 ToUnicode read-side integrity reconciliation (Identity-H).
  - **B.6 (symbol-cmap)** — (3,0)/(1,0)-only dual-lookup, all 6 fontTools cmap sites.
  - **B.7 (NFC/NFD)** — normalization-aware find & replace + combining-mark character model (stdlib
    `unicodedata`).
  - **B.9 (ligatures)** — mandatory-vs-discretionary ligature round-trip integrity (default-OFF
    discretionary collapse).
  - **B.11 (empty-delete)** — empty-string / full-deletion operator cleanup + compensating advance;
    cross-boundary → `deletion_residual_text`.
  - **B.12 (rotated-splice)** — rotated/sheared same-length splice + honest refusal. **CORRECTION
    (see inline):** ships WITH the positioning gate — `surgeon._adjust_subsequent_positioning`
    (surgeon.py ~486–537, caller ~909–918) is hard-coded horizontal and MUST be gated so a rotated
    (b/c ≠ 0) run does not get its x-advance compensated as if axis-aligned. The splice itself is
    rotation-safe; the *positioning compensation* is not, so any rotated match that would trigger the
    adjuster refuses with `rotated_text_unsupported` rather than mis-shift.
  - **B.4 — DECODE + fixed-width Identity-V WRITE ONLY.** Named-CMap **DECODE** (READ) via
    `CMapDB.get_cmap` → `PyCMap.decode` (verified bundled) + Identity-V **READ** geometry +
    **same-position fixed-width Identity-V WRITE**. **CORRECTION (see inline):** the variable-width
    *character-changing* WRITE is NOT in 0.2.0 (deferred — see B.4 in §0.3.0+); and Identity-V
    same-position write inherits the B.12 positioning-gate requirement (the horizontal-only adjuster
    must be generalized/gated for vertical before vertical write ships).

- **C — Font universality** (dispatch + introspection + the XL CFF transplant):
  - **C.1 / C.2 (dispatch + introspection)** — CM-missing-2 `/FontFile3` outline-table dispatch split
    (ships first, closes the raw-KeyError leak) + truthful `glyph_count` / subset-vs-full
    introspection (cffLib / t1Lib).
  - **C.3 — CFF / Type1C glyph injection (the SINGLE XL).** Name-keyed THEN CID-keyed in the same
    milestone; `setGlyphOrder(list(charset))` reconciliation (V4); CFF2 → `cff2_unsupported`.
    **CORRECTION (see inline):** there is NO `setupCFFCID` helper in installed fontTools — the
    CID-keyed path needs **manual ROS / FDArray / FDSelect promotion plus the hmtx entry**, hand-built
    on `cffLib`.
  - **C.4 (Rank 6 UPEM rescale)** — `scale_upem` donor rescale (covers glyf AND CFF per V5); root-fixes
    the latent `fonts.py:1071` width bug. *(Pairs with C.3; included in the proven core.)*

- **E — Typographic-quality reflow** (the dependency-free depth): E.1 justified + alignment knob
  (corpus-calibrated FPR); E.2 first-line/hanging/flush indent; E.3 TL leading capture+re-emit; E.4
  widow/orphan post-pass (typed `line_break_quality_degraded`); E.5 hyphenation (Pyphen, permissive
  allowlist); E.6 `line_height_compressed` actually emitted (V7); E.7 CJK UAX #14 segmentation
  (stdlib; SA → optional `[thai]`); E.8 shrink-to-fit font-size auto-reduction (V17).

- **F — Color / graphics-state fidelity** (ONE co-designed `GraphicsStateSnapshot` extension):
  F.1 Separation/DeviceN/ICC capture+replay; F.2 Pattern/shading; F.3 Tr re-emit + clip refusal;
  F.4 Ts per-run; F.5 stroke + ExtGState.

> **Count note.** The ~28 figure counts the items above (A1.1–A1.4, A2.1–A2.3, B.1/B.2/B.3/B.4/B.5/
> B.6/B.7/B.9/B.11/B.12, C.1/C.2/C.3/C.4, E.1–E.8, F.1–F.5). Block D (semantic/tagged-PDF), Block G
> (AcroForm/annotation), Block H (scale), and the four deferred WRITE pillars are NOT in this count —
> see the deferral section next.

## 0.3.0+ — deferred from 0.2.0

Each deferral is code-cited, not timidity. The four WRITE pillars below were pulled into 0.2.0 by the
v3 draft; the re-audit returns them to 0.3.0+ because a source-level read shows the buildable half is
the READ/decode side, not the character-changing WRITE side.

- **B.10 — world-script shaping WRITE** (Arabic/Indic/Thai). **Why deferred:** the v3 draft assumed
  the existing `_inject_glyph_in_place` (fonts.py:558+) could inject shaped glyphs, but that injector
  is **codepoint-indexed** (`cp = ord(ch)`; indexes `system.getBestCmap()[cp]`, fonts.py:593–596) and
  HarfBuzz emits **GIDs**, not codepoints. B.10 therefore needs a **NET-NEW GID-keyed injector** (see
  inline correction). It also needs relative-`Td` inter-glyph bracketing rather than absolute `Tm`,
  and `y_offset` mark positioning is **not reachable through the TJ array** (TJ numbers are horizontal
  displacements only). Deferred to 0.3.0 with the GID-keyed injector + a `Td`/offset writer.
- **B.8 — RTL WRITE** (same-glyph-count Hebrew/Arabic splice). **Why deferred:** the find-side
  reorder is buildable, but the write-side logical→visual slot mapping rides the same shaping/
  positioning machinery as B.10; without B.10's writer the "same-glyph-count" claim degrades to a
  silent-wrong risk on any contextual form. Find-side reorder (read) can ship earlier as a B.8a if
  scoped read-only; the WRITE half defers.
- **B.4 — named-CMap CHARACTER-CHANGING WRITE.** **Why deferred:** `PyCMap.decode` yields a flat CID
  list and **discards byte boundaries**, so the engine cannot reconstruct the exact byte width each
  source CID consumed in a variable-width codespace (1-byte ASCII vs 2-byte kanji in 90ms-RKSJ-H).
  A character-changing write that re-encodes to a different CID cannot know how many bytes to emit
  without a from-scratch codespace parser. **DECODE + fixed-width Identity-V same-position write stay
  in 0.2.0** (see scope); the variable-width character-changing write is 0.3.0.
- **C.5 — Type1 (`/FontFile`) extension. REFUSED / stays deferred.** As written, C.5 is an effective
  no-op: it relies on a Type1 *system donor* feeding C.3's injector, but `system_fonts` globs only
  `**/*.ttf` / `**/*.otf` / `**/*.ttc` (system_fonts.py:270) — there is no `.pfb`/`.pfa` discovery,
  and `fontTools.t1Lib.T1Font` is a **path-only API** with no in-memory/bytes constructor wired into
  the engine. No Type1 donor can be surfaced, so the "donor case (b)" cannot fire. Type1 extension
  stays a **honest refusal** until a Type1 font-discovery path exists (0.3.0+).
- **Block D — tagged-PDF / semantic fidelity** (D.1 marked-content stack, D.2 structure-tree/
  ActualText, D.3 length-changing MCID integrity, D.4 OCG awareness). Deferred wholesale from 0.2.0
  to keep the release font-and-text-correctness focused; D.1 is the load-bearing primitive when it
  lands.
- **Block G — AcroForm / annotation depth** (G.1 simple-font `/ToUnicode` synthesis, G.2 AcroForm
  `/V`+`/AP`, G.3 annotation `/AP`+`/Contents`). G.1 is independently shippable (it shares the splice
  helper with B.3); G.2/G.3 depend on Block C (CFF `/DA`) and Rank 1 off-page interpreter — deferred.
- **Block H — scale / memory** (bounded + streamed `ContentElement` index). Deferred; tune
  `MAX_ELEMENTS_SCANNED` after Rank 1 lands.

> **SIX INLINE FEASIBILITY CORRECTIONS** (each also annotated at its item in §4):
> 1. **B.10** needs a NET-NEW GID-keyed injector (the existing one is codepoint-indexed) + relative
>    `Td` inter-glyph bracketing (NOT `Tm`); `y_offset` mark positioning is not TJ-reachable.
> 2. **C.3** needs manual ROS / FDArray / FDSelect promotion + the hmtx entry — there is no
>    `setupCFFCID` in installed fontTools.
> 3. **C.5** is an effective no-op as written (system_fonts globs only ttf/otf/ttc; `T1Font` is a
>    path-only API) → stays deferred / refused.
> 4. **B.2** copy-on-write must **deep-clone the touched font objects** (`/FontDescriptor` +
>    `/FontFile2` + `/ToUnicode` + `/W` + `/CIDToGIDMap`) and use **`read_bytes()`** (decoded), NOT
>    `read_raw_bytes()`.
> 5. **B.4** variable-width character-changing WRITE is not buildable as written (`PyCMap.decode`
>    discards byte boundaries) → 0.3.0. DECODE + fixed-width Identity-V write stay in 0.2.0.
> 6. **`surgeon._adjust_subsequent_positioning`** is hard-coded horizontal (surgeon.py ~486–537,
>    caller ~909–918) → MUST be gated/generalized for vertical/rotated BEFORE B.4-vertical & B.12 ship.

## Moat — pre-flight typed `can_edit()` + frozen/versioned DegradationKind taxonomy

The differentiator is machine-readable honesty. Two moat investments make it durable across the
~28-item surface (and the ~35 new DegradationKinds the blocks add):

- **Pre-flight typed `can_edit()`.** A read-only pre-flight that, given a `(pdf, match, new_text)`
  intent, returns the SAME typed `Degradation` set the real edit would emit — WITHOUT mutating the
  document. AI-agent consumers can ask "would this edit be honest, and how?" before committing. It
  reuses the dry-run machinery already wired through the edit verbs (dry_run parity is an existing
  contract) and surfaces the would-be degradations + a buildable/refuse verdict per typed kind.
- **Frozen / versioned DegradationKind taxonomy.** Today `DegradationKind` is a flat 13-value
  `Literal` (`models.py:58-77`) with no version stamp. As ~35 kinds land across A–F, freeze the
  taxonomy with an explicit version (e.g. a `DEGRADATION_TAXONOMY_VERSION` constant) and a
  `get_args(DegradationKind)` count probe, so consumers can pin a known kind-set and detect additions.
  Pairs with the existing J-5 canonical set + J-8 `FONT_AFFECTING_KINDS` discipline.

## M0 — release-mechanics + cross-cutting fixes (run BEFORE block work)

- **Run the A1.4 gate.** Execute the A1.4 inline-image BI/ID/EI probe against the corpus FIRST (it is
  GATING for B.2 / D.1). If `parse_content_stream` mis-indexes BI/ID/EI, A1.4 escalates from a guard
  to an interpreter fix before the invasive traversals ship — halt-and-flag, not a silent assumption.
- **Fix the stale "twelve" docstring at `models.py:97`.** The `Degradation` docstring says `kind` is
  "one of twelve canonical values" but `DegradationKind` has **13** values (`models.py:58-77`;
  `font_substituted_from_user_fonts` added in v0.1.3). Update the prose to 13 (or to a
  count-by-reference phrasing).
- **Reconcile the CHANGELOG CFF version target.** `CHANGELOG.md:247` says "ARY-279 (CFF / OpenType
  Tier 1.5) deferred to **v0.2.0**" while `CHANGELOG.md:279` says "CFF support is tracked in ARY-279
  for **0.2.0**". Pick one (CFF injection IS in this 0.2.0 scope as C.3, so 0.2.0 is the consistent
  target) and make both lines agree.

---

## 1. Why this is MAJOR-magnitude, not an increment

The v1 plan (`editing-iceberg-plan.md`) ranked 12 items and **deferred ranks 9–12 wholesale**
(justified reflow, multi-column/table reflow, complex-script shaping, Knuth-Plass) plus left the
entire **semantic-fidelity** (tagged-PDF/ActualText), **color/graphics-state**, **bidi**,
**normalization**, and **save-side honesty** dimensions unaddressed. That is the under-reach.

This v2 plan is demonstrably deeper and more ambitious on five axes:

1. **It brings the v1-deferred AND the v2-deferred items IN.** Justified reflow (Block E) and the full
   font-universality pillar through **CID-keyed CFF** (Block C) ship in 0.2.0 — as does, after the
   ambition critic's review, the **world-script WRITE path** (B.10: uharfbuzz shaping bridge for
   TrueType-embedded Arabic/Indic/Thai + CFF complex-script riding the 0.2.0 injector), **RTL
   same-glyph-count replace** (B.8), **named-CMap DECODE** (B.4, verified bundled), **Identity-V
   same-position write** (B.4), and **Type1→CFF extension** (C.5). Only the *genuinely dependency-blocked*
   frontier now defers: complex-script *reflow* with cluster line-breaking and table reflow both need a
   **new CTM-composition step the locator does not have**; embedded-`/CMap`-stream `code2cid` is
   unbuildable on pdfminer (0 entries); Knuth-Plass needs justification to render its benefit. Every
   deferral is **code-cited**, not timidity — and the count of deferred pillars dropped from five to two.

2. **It elevates SEMANTIC fidelity to first-class.** v1 had only a single Phase-1 ActualText row;
   v2 builds the marked-content interpreter primitive (BDC/EMC/MCID), structure-tree repair
   (MCID-reuse), optional-content layer awareness, and length-changing-edit MCID integrity
   (Block D). No FOSS in-place editor touches this.

3. **It adds the save-side and robustness honesty layer** v1 ignored: signature-invalidation
   detection, linearization preservation, encrypted-document round-trip, content-stream parse/unparse
   translation, q/Q and font-stream DoS bounds, and document-scale memory bounding (Blocks A1/A2/H).

4. **It adds color/graphics-state fidelity** (Spot/Separation/ICC, Pattern/shading, Tr/Ts, stroke,
   ExtGState) with a single co-designed `GraphicsStateSnapshot` extension (Block F) and the
   dependency-free reflow depth (indent geometry, leading, widow/orphan, hyphenation, CJK
   segmentation) v1 punted (Block E).

5. **It corrects v1's verification errors** (see §3): the resource-scope chain touches **14**
   page-bound lookups not 9; `Page.resources` does **not** walk Pages-node inheritance (manual
   `/Parent` walk required); `scale_upem` **does** cover CFF; the CFF reconciliation recipe is
   `setGlyphOrder(list(charset))` **not** a manual `maxp`/`hhea` bump; named CMaps resolve to
   `PyCMap` not `FileCMap`; `line_height_compressed` is declared-but-never-emitted; the
   `DegradationKind` Literal is **13 values today** with a stale "twelve" docstring.

The result: a single major release with the substance of a 1.0 for in-place editing depth, where
only the three truly hard-blocked frontier items remain — each with a verified, code-cited reason.

---

## 2. Roadmap (judged)

### v0.2.0 — Trust + Correctness + Font Universality + dependency-free typographic/semantic depth

| Block | Theme | Key items |
|---|---|---|
| **M0** | Release mechanics + cross-cutting discipline | PyPI-verify already-tagged v0.1.3; build fixture corpus; fix stale "twelve" docstring (`models.py:97`); reconcile CLAUDE.md pdfminer dep-table (already contradicted by `encoding.py:9` + `fonts.py:14`) |
| **A1** | Robustness/honesty foundation (ships first) | Centralize parse/unparse exception translation in `_pathutil` (+ `content_stream_unparseable`); q/Q stack depth cap; bound decompressed font-stream size (Flate-bomb pre-gate); **inline-image BI/ID/EI index-integrity guard (A1.4, NEW)** |
| **A2** | Save-side honesty (shared `_SaveOutcome` contract) | `signature_invalidated` detect-before-write + `force` gate; linearization preserve+detect; encrypted decrypt-edit-re-encrypt round-trip |
| **B** | Correctness / total text reachability + world-script | Rank 5 width-cache objgen re-key (**retain evict**); Rank 1 Form XObject editing + explicit `/Parent`-walk resource-scope chain (all 14 sites); Rank 2.5 ToUnicode recovery (PHASED, PUA gate, write-path fix); **Rank 2 Type0 named-CMap DECODE + Identity-V READ + same-position WRITE** (B.4, decode verified bundled); ToUnicode read-side integrity; symbol-cmap dual-lookup; NFC/NFD + combining-mark model; **bidi find + RTL same-glyph-count WRITE (B.8)**; ligature round-trip integrity; **world-script shaping WRITE for TrueType-embedded scripts (B.10, NEW — uharfbuzz)**; **empty-string deletion operator cleanup (B.11, NEW)**; **rotated/sheared-text splice + honest refusal (B.12, NEW)** |
| **C** | Font universality (dispatch on ACTUAL outline table) | CM-missing-2 dispatch split (ships first, closes raw-KeyError leak); `embedded_type` de-collapse + truthful glyph_count; Rank 3 CFF injection name-keyed THEN CID-keyed (corrected `setGlyphOrder(list(charset))` recipe); Rank 6 donor `scale_upem` across all injectors; **Type1→CFF transplant on extension (C.5, NEW)** |
| **D** | Semantic fidelity | Marked-content BDC/EMC stack (shared primitive); Rank 4 structure-tree/ActualText (Phase 1 + MCID-reuse Phase 2); length-changing MCID integrity; optional-content (OCG) awareness |
| **E** | Typographic-quality reflow | Rank 9 justified + alignment knob (corpus-calibrated detector, two-renderer proof); first-line/hanging/flush indent; TL leading capture+re-emit; widow/orphan post-pass (**typed `line_break_quality_degraded`**); Rank 7 hyphenation; `line_height_compressed` ACTUALLY emitted; CJK UAX #14 segmentation; **shrink-to-fit font-size auto-reduction (E.8, NEW)** |
| **F** | Color/graphics-state fidelity | ONE co-designed `GraphicsStateSnapshot` extension: Separation/DeviceN/ICC + Pattern/shading + Tr + Ts + stroke + ExtGState |
| **G** | Semantic text surfaces | Simple-font `/ToUnicode` synthesis; AcroForm `/V` findability + `/AP` regen; non-form annotation `/AP` text edit + `/Contents` sync + text-preserving flatten |
| **H** | Scale / memory | Bounded + streamed `ContentElement` index |

### v0.3.0 — Structural / cluster-aware reflow (the TWO genuinely CTM-blocked pillars)

> The v2 draft parked five pillars here. After the critic's review, four moved INTO 0.2.0 (B.4 named-CMap
> decode + Identity-V same-position write; B.8 RTL write; B.10 world-script shaping write). Only the two
> pillars that genuinely require the **NEW CTM-composition step the locator lacks** remain — each
> code-cited at V10/V13 in §3.

- **Rank 10 table / multi-column reflow** — REQUIRES a NEW CTM-composition step (locator stores RAW
  operands today, does NOT apply the CTM — V10) + a CTM-aware `_shift_content_below`. Re-run build-vs-buy
  survey as a kill-switch gate.
- **Identity-V vertical *length-changing reflow*** — the same-position vertical WRITE ships in 0.2.0
  (B.4); only re-flowing a vertical column when the replacement length changes needs a vertical-flow
  model (the WMode-1 analogue of `_build_paragraph` + a `-x` column-advance `_shift_content_below`),
  which does not exist today.
- **Complex-script *reflow*** — B.10 ships single-line / same-region shaping in 0.2.0; multi-line
  shaped reflow needs cluster-aware UAX #14 line breaking that the greedy breaker cannot express
  (a cluster boundary ≠ a codepoint boundary), and the cluster-width feedback needs the CTM-composition
  step Rank 10 builds.
- **Caller-selectable `ReplaceAdjustment` policy enum** on the mature reflow + Tz machinery (the
  shrink-to-fit `fit=` knob from E.8 generalized into a unified adjustment policy).

### v0.1.6+ — Optimal breaking + cross-document flow (corpus-gated frontier)

- **Knuth-Plass optimal line breaking** — gated strictly behind 0.3.0 cluster-aware reflow AND a justified
  corpus proving greedy is visibly worse. 7-site dispatch cascade (`reflow.py:1004` + six
  `structural.py` sites).
- **From-scratch embedded-`/CMap`-stream codespace parser** — VERIFIED frontier: pdfminer's
  `CMapDB.get_cmap` decodes the 149 BUNDLED named CMaps (0.2.0 B.4), but an EMBEDDED `/Encoding` CMap
  stream builds **0** `code2cid` entries; a separate ~150-LOC codespace parser is the only path → stays
  `unsupported_cmap` until then.
- Cross-page / cross-textbox word-processor reflow.
- CFF2 variable-charstring investigation.
- **FRONTIER HONESTY (never build, detect-and-Degrade only):** signature-preserving incremental save
  (pikepdf consolidates on save); Type3 procedural glyphs; cross-page word-processor reflow;
  embedded-`/CMap`-stream `code2cid` (until the 0.1.6 codespace parser).

---

## 3. Verification corrections (vs v1 plan and the original designs)

These are the load-bearing corrections, each verified at HEAD `5c0a436`:

| # | Claim in v1 / designs | Correction (verified) |
|---|---|---|
| V1 | `Page.resources` walks inherited Pages-node `/Resources` | **FALSE on pikepdf 10.5.1.** Both `page["/Resources"]` and `page.resources` return the leaf's own (empty) resources, NOT inherited ancestor resources. Must implement an explicit cycle-guarded `/Parent` walk (ISO 32000 §7.7.3.4). The companion claim "existing hardcodes already subtly buggy, scope chain via Page.resources fixes that" is struck. |
| V2 | Resource-scope chain touches **9** page-bound lookups | **14.** Verified: `encoding.py:392,422`; `locator.py:973,1043`; `widths.py:127`; `structural.py:786,974,992,1069,1680`; `surgeon.py:1231,1339`; + the bare `["/Resources"]` reads at `locator.py:487` and `widths.py`. The invariant probe ("no `page['/Resources']['/Font']` literal survives outside the scope module") requires all 14 routed. |
| V3 | `DegradationKind` is a "12-kind locked Literal" | **13 values** today (`models.py:58-77`); `font_substituted_from_user_fonts` added in v0.1.3. Docstring at `models.py:97` still says "twelve" — fix it. |
| V4 | CFF reconciliation = manual `maxp`/`hhea` bump (the "hhea bump mandatory, IndexError hit twice" recipe) | **WRONG; not reproducible.** That recipe `save()`s but RELOADS with `IndexError` in `_h_m_t_x.py`. Correct recipe: `emb.setGlyphOrder(list(topDict.charset))` and do NOT manually pin `maxp`/`hhea` (fontTools derives them). Add a probe asserting reloaded `maxp.numGlyphs == len(charset)`. |
| V5 | `scale_upem` source "does NOT reference CFF in 4.62.1" | **FALSE.** `inspect.getsource(scaleUpem)` DOES reference CFF in the installed 4.62.1; Rank 6's claim is better-supported than feared. |
| V6 | Named CMaps resolve via `FileCMap.decode()`; ref vector `[1,633,1]` | **`PyCMap`, not `FileCMap`.** `CMapParser`+`FileCMap` build **0** `code2cid` entries (the `ENDCIDRANGE` handler calls `add_cid2unichr`, which is a no-op on `FileCMap`). Named CMaps → `CMapDB.get_cmap` → `PyCMap`. **RE-VERIFIED LIVE at HEAD on pdfminer 20260107**: `CMapDB.get_cmap('90ms-RKSJ-H')` → `PyCMap`; `.decode(b'\x20\x81\x40\x20')` → `[231,633,231]` (real CIDs), not `[1,633,1]`. Because the decode works TODAY, named-CMap READ decode is pulled INTO 0.2.0 (B.4); only embedded-stream `code2cid` (0 entries) is unbuildable → 0.1.6 codespace parser. |
| V13 | (v2) pdfminer ships ~148 named CMaps as `.pickle.gz` | **149 resources as `.json.gz`** in pdfminer 20260107 (`site-packages/pdfminer/cmap/*.json.gz`; the format changed from `.pickle.gz` in this release — do NOT hardcode the extension; use `CMapDB.get_cmap(name)`, never a glob). The "∩ 4 bundled Adobe to-unicode collections" framing from v2 is unnecessary: `get_cmap` already returns a `PyCMap` whose `decode()` yields CIDs directly; ToUnicode is a SEPARATE concern handled by B.3/B.5. |
| V14 | (v2) complex-script shaping HARD-blocked on 0.2.0 CID-keyed CFF → defer to 0.3.0 | **OVER-DEFERRED.** The TrueType glyf injector (`_inject_glyph_in_place`, `fonts.py:558+`) exists; CID-keyed CFF (C.3) IS in 0.2.0; `unicodedata` (B.7/B.8) is stdlib. The ONLY thing forcing the v2 deferral was bundling the surgeon-side per-glyph TJ/Td shaping writer separately. Pulled INTO 0.2.0 (B.10) behind the SAME `cff2_unsupported`/`shaping_unsupported`/missing-GSUB refusal Block C uses for CFF. uharfbuzz (Apache-2.0) is the shaper. |
| V15 | (v2) Identity-V WRITE deferred wholesale to 0.3.0 | **PARTIALLY over-deferred.** The READ model (B.4) computes correct vertical geometry (`/W2`,`/DW2`, `-y` advance, position-vector origin). Same-position vertical re-emit (replacement that keeps the run's vertical extent) is therefore buildable and ships in 0.2.0 (B.4). Only length-CHANGING vertical reflow (needs a vertical-flow model / `-x` column-advance shift) stays in 0.3.0 — that IS code-cited (no WMode-1 `_build_paragraph` analogue today). |
| V16 | `replace(x, '')` handled | **NO empty-string path.** surgeon's same-length branch (`surgeon.py:715,725`) and distribution branch (`:739-742`) both index `new_text[idx:idx+n]` — for `new_text=''` they write EMPTY slices, leaving the TJ/operator structure (and its positioning) in place. No operator-removal path exists. Deleted text may remain in the operator skeleton. B.11 adds true operator cleanup + `deletion_residual_text` for the un-cleanable case. |
| V17 | `compute_uniform_layout` can shrink-to-fit | **Clamps LINE-HEIGHT only, never font size** (`structural.py:733-742`: `min_line_height = font_size*1.05`, loop reduces `section_gap`, final clamp keeps `font_size`). No caller-facing font-size auto-reduction. E.8 adds `fit='shrink'`. |
| V18 | Inline images BI/ID/EI parsed/indexed safely | **UNHANDLED & UNTESTED.** `_dispatch` (`locator.py:126-173`) has no BI/ID/EI case; grep for `BI`/`InlineImage` in src = 0. pikepdf `parse_content_stream` MAY surface inline-image binary data as operands that shift `operator_index`, corrupting `_assert_match_addressable` / the surgeon write offset and the Rank 1 / D.1 stream walks. A1.4 adds a corpus probe + `inline_image_present` guard. |
| V19 | Rotated/sheared text (Tm b/c ≠ 0) edited correctly | **Tracked but not honored on write.** `state.py:78,158,255-264` tracks the full `(a,b,c,d,e,f)` text matrix and `snapshot()` carries it (`:208`), but `reflow._build_replacement_ops` (`reflow.py:685+`) re-emits axis-aligned `Tm`/`Td` only; bbox math assumes axis-aligned. Same-length splice (operands replaced in place) is rotation-safe by construction; reflow/bbox on a rotated run must refuse → B.12 adds `rotated_text_unsupported`. |
| V20 | Type1 (`/FontFile`) extension is a pure refuse-case | **A buildable transplant exists.** `_extract_font_bytes` already labels `/FontFile` "Type1" (`fonts.py:250-251`) and C.2 already loads it via `t1Lib.T1Font`. fontTools `t1Lib` reads Type1 charstrings → convertible to CFF (the same `T2CharStringPen`/`cffLib` path C.3 builds). Flat refuse (`fonts.py:835-838`) is a stub where Type1→CFF→C.3-injector is available → C.5 commits to it (CFF2-style unconvertible → honest refusal). |
| V7 | `line_height_compressed` is a live Degradation | **Declared-only** (`models.py:67`), **never emitted** in src. Block E wires the two real `structural.py` compression sites via the `coverage_tier_log` out-param idiom (keeps `compute_uniform_layout` pure / INV-F-5). |
| V8 | Rank 2.5 is a FULL solution, no infeasible sub-part | **REFUTED.** `replace()` on a recovered font **crashes**: `fonts.py:306` does a raw `font_dict["/ToUnicode"].read_bytes()` (KeyError when absent, also an INV-L-1 leak). And a PUA-encoded embedded cmap produces **silent garbage** today. Block B Rank 2.5 is PHASED: read-path recovery + **PUA-purity gate** (all-PUA → `untextable_cidfont`, never a silent `tounicode_recovered` lie) + write-path `/ToUnicode` synthesis or honest refusal. |
| V9 | CLAUDE.md "pdfminer ONLY in locator" holds | **Already contradicted by checked-in code:** `encoding.py:9-10` and `fonts.py:14` both import `pdfminer.cmapdb`/`encodingdb`. Reconcile the dep-table (mark encoding+fonts as sanctioned pdfminer zones) before Block B Phase work reads as a violation. |
| V10 | Table-reflow primitives reusable verbatim; rulings page-space | **REFUTED.** Locator stores RAW path/Tm operands and does NOT apply the CTM; `reportlab_table.pdf` rulings are in LOCAL space under `cm [1 0 0 1 90 600]`. `_shift_content_below_inplace` is CTM-unaware. Table reflow needs NEW CTM-composition + a CTM-aware shift → deferred to 0.3.0 (correctly, with cited reason). |
| V11 | `_extract_font_bytes` correctly types CFF | Labels EVERY `/FontFile3` "CFF" by slot, no glyf/CFF2 inspection (`fonts.py:246-251`). CM-missing-2 dispatch split is needed before any CFF work. |
| V12 | `_inject_glyph_in_place` / UPEM hard-fail | Confirmed: glyf-required reject + `unitsPerEm` mismatch hard-fail (`fonts.py:575-590`) — the Rank 3 / Rank 6 chokepoints. |

---

## 4. The full iceberg — per-item implementation-grade design

> Effort: **S** ≤½ day · **M** ~1–2 days · **L** ~3–5 days · **XL** ~1–2 weeks.
> Each item: current-state (file:line) → approach → build-vs-buy → full/phased → deps → tests → risk.

### Block A1 — Robustness / honesty foundation (ships FIRST)

#### A1.1 — Centralize content-stream parse/unparse exception translation
- **Current**: ~17 `pikepdf.parse_content_stream` / `unparse_content_stream` sites across
  locator (1), surgeon (6: `1183,1399,1433,1532,1568` + unparse `1289,1463,1599`), structural
  (5+4), reflow (3+1). Flate-garbage opens cleanly then raises raw `pikepdf.PdfError` /
  `DataDecodingError` at parse — leaking past `find`/`get_text`/`replace_all`/`replace_block`
  (INV-L-1 hole). `pikepdf.PdfError` and `pikepdf.DataDecodingError` are **disjoint** (both direct
  `Exception` subclasses) so the translator must catch **both**.
- **Approach**: add `_pathutil.parse_content_stream` / `unparse_content_stream` translators
  (mirror `open_pdf`/`_save_pdf` discipline: forensic detail to `logger.error(exc_info=True)`,
  user-visible = bare type name only — INV-W0-9). Catch `(pikepdf.PdfError, pikepdf.DataDecodingError)`,
  raise `OperatorError`. Route all 17 sites through them. Read paths (`find`/`get_text`) **raise**
  `OperatorError` (honest typed exception); edit paths return `EditResult(success=False, …)` with a
  `content_stream_unparseable` Degradation. Add a CI grep guard: those primitives appear ONLY in
  `_pathutil.py` (mirrors the `pikepdf.Pdf.open` convention) so Rank 1's XObject parse sites inherit
  translation for free.
- **Build-vs-buy**: build_own (internal exception translation; pikepdf-only; no new dep, no boundary
  change — `_pathutil` already imports pikepdf+errors).
- **Full/phased**: FULL. ~17 sites + 1 Literal + 2 helpers + CI guard. Must precede Rank 1.
- **Tests**: Flate-garbage builder; each public verb raises/Degrades, never leaks `PdfError`/
  `DataDecodingError`/`TypeError`; `DataDecodingError`-direct probe (PdfError-only catch misses it);
  `test_l_2_parse_unparse_centralized` grep guard.
- **Effort S · risk low. New deps: none.**

#### A1.2 — q/Q graphics-state stack depth cap
- **Current**: `GraphicsStateTracker.save()` (`state.py:120-134`) appends unconditionally; no cap.
  `restore()` guards underflow only. The single driver is `interpret()` (`locator.py:112`); the only
  tracker instantiation is `locator.py:102`. reflow/structural do NOT drive the tracker.
- **Approach**: `MAX_GRAPHICS_STATE_DEPTH = 128` (higher than `MAX_COMPOSITE_DEPTH=64` — q/Q nests
  legitimately deeper: form XObjects, clipping, vector groups). `if len(self._state_stack) >= cap:
  raise OperatorError(...)` before append. Import `OperatorError` from the no-dep `errors` leaf.
  Propagation is automatic via `_build_index` (it raises `OperatorError` natively); do NOT add
  `OperatorError` to its except tuple (would clobber the precise message).
- **Build-vs-buy**: build_own (~10 LOC; mirrors `MAX_COMPOSITE_DEPTH` precedent).
- **Full/phased**: FULL. **Tests**: `test_w0_11` — constant lock, `(cap+1)`-th save raises, at-cap
  no-raise (inclusive `>=`-before-append off-by-one), balanced-q/Q-unbounded, e2e `(b"q "*(cap+5))`
  page raises `OperatorError` not `MemoryError`.
- **Effort S · risk low. New deps: none.**

#### A1.3 — Bound decompressed FontFile2/FontFile3/FontFile/ToUnicode/CIDToGIDMap stream size
- **Current**: every embedded-font/CMap stream is `read_bytes()`-decoded with no bound
  (`fonts.py:157,247,249,251,269,306,387,1037,1293`; + `encoding.py:141`; + `locator.py:743`).
  Verified: a 1KB→100MB Flate bomb peaks at 100MB RAM inside `read_bytes()` (qpdf does NOT
  stream-bound). `read_raw_bytes()`/`/Length` return the compressed size at ~0MB.
- **Approach**: `_pathutil.read_stream_bounded(stream, *, max_decoded, label)` — raw-length PRE-gate
  (`/Length` or `read_raw_bytes()`), `/Length1` declared-length gate, ratio gate
  (`raw*MAX_DECOMPRESSION_RATIO=200`), post-decode absolute cap. Constants in fonts.py:
  `MAX_FONT_STREAM_BYTES=32MiB`, `MAX_TOUNICODE_BYTES=8MiB`. `FontStreamTooLargeError(FontNotFoundError)`
  → already in `_FONT_EXTEND_FAIL_EXCS` with zero tuple edit → surfaces as `font_extension_failed`.
  New Degradation `font_stream_too_large` (NOT font-affecting). Read-only paths
  (`font_has_codepoint`, `get_fonts`) catch and return best-effort. Primitive lands in `_pathutil`
  (pikepdf-only home; keeps encoding fonttools-free, avoids fonts↔encoding cycle).
- **Build-vs-buy**: build_own (pikepdf raw/decoded length split; no new dep; pikepdf StreamDecodeLevel
  offers no size-capped decode so the raw-length pre-gate is the only defense).
- **Full/phased**: FULL (the per-call re-parse cache is a separate concern; bounding caps its blast
  radius). **Tests**: `test_w0_10` — value-lock; 100MB bomb raises with tracemalloc peak <10MB
  (proves PRE-gate fired); honest `/Length1` reject; benign font passes; off-by-one; e2e bomb-font
  fixture → `success=False` + `font_stream_too_large` + `font_extension_failed` + `font_preserved=False`.
- **Effort M · risk low. New deps: none.**

#### A1.4 — Inline-image (BI/ID/EI) operator-index integrity guard (NEW, per V18)
- **Current**: `_dispatch` (`locator.py:126-173`) has NO BI/BMC/ID/EI case (grep `BI`/`InlineImage` in
  src = 0). pikepdf `parse_content_stream` MAY surface inline-image binary payload as an operand attached
  to the `ID`/`EI` operator pair; if the operator-tuple count differs from the assumption baked into
  `operator_index`, then `_assert_match_addressable` (`surgeon.py:70-182`) and the surgeon write offset
  mis-index — and so do the MOST invasive new traversals (Rank 1 XObject recursion B.2, D.1 marked-content
  walk). This is the one untested assumption underlying everything that walks the full op stream.
- **Approach** (probe-first, then guard — does NOT add inline-image EDITING, which is out of scope):
  - **Phase 0 (probe, before any guard code)**: build a fixture page with a `BI … ID <bytes> EI` block
    interleaved with `Tj` text (reportlab `drawInlineImage` or a hand-assembled stream) and assert that
    `parse_content_stream` → `unparse_content_stream` round-trips it AND that the `Tj` operator's index is
    what the interpreter expects. Record the ground truth: how many operator tuples pikepdf emits for an
    inline image, and whether the binary rides as an operand or as a distinct `EI` token.
  - **Guard**: the interpreter recognises `BI` (and pairs to `EI`); between them it does NOT attempt text
    decode and does NOT increment the text-element index incorrectly — it counts the inline-image span as
    the exact number of operator tuples pikepdf actually emits (from the probe), so downstream
    `operator_index` stays correct. A page containing inline images that an edit would CROSS sets
    `inline_image_present`; surgeon refuses a same-stream edit whose `operator_range` SPANS an `EI`
    boundary it cannot prove stable (`inline_image_present`, warning) rather than risk a mis-indexed write.
  - New kind `inline_image_present` (warning, NOT font-affecting). Corpus scan records how many of the 15
    fixtures actually contain inline images (likely 0 today — same blind spot as text-bearing XObjects).
- **Build-vs-buy**: build_own (pikepdf-only; the probe is the deliverable; no new dep, no boundary change).
- **Full/phased**: FULL (probe + guard; inline-image text EDITING is explicitly out of editing-depth scope
  — inline images carry image data, not editable text). **Tests**: probe round-trip + index-stability
  assertion (the load-bearing test — if it FAILS, A1.4 escalates from a guard to an interpreter fix BEFORE
  B.2/D.1 ship); edit crossing an `EI` boundary → `inline_image_present` refuse not silent mis-write;
  text-only page byte-identical regression; e2e Rank-1 XObject containing an inline image still indexes
  the surrounding text correctly.
- **Effort S · risk low (but GATING for B.2/D.1). New deps: none. Sequence: probe runs in M0/A1, gates Rank 1.**

### Block A2 — Save-side honesty (share ONE `_SaveOutcome` / `_save_pdf` contract revision)

#### A2.1 — Digital-signature invalidation detect-before-write
- **Current**: `_save_pdf` (`_pathutil.py:298-343`) full-rewrites + renumbers, destroying any `/Sig`
  ByteRange. ZERO detection (grep for `SigFlags|/Sig|/Perms|DocMDP` in src = 0 hits). 30+ save sites
  all route through `_save_pdf`. `DegradationKind` lacks `signature_invalidated`.
- **Approach**: `_signatures.detect_signatures(pdf) -> SignatureInfo` (pikepdf-only leaf) walking
  three surfaces: AcroForm `/SigFlags` bit 1; populated `/FT /Sig` field with non-null `/V` (an
  EMPTY sig field must NOT trip refusal); `/Perms/DocMDP` certification. `_save_pdf` gains
  `signature_policy: Literal["ignore","warn","refuse"]="refuse"` + `on_signature` callback. Thread
  `force: bool=False` through every write verb (`force=False`→refuse→`PDFEditError`/`success=False`+
  `signature_invalidated` error Degradation; `force=True`→warn, still surfaced). New
  `INTEGRITY_BREAKING_KINDS` frozenset + computed `FidelityReport.signature_preserved` property.
  Signature **PRESERVATION** stays unbuildable (pikepdf consolidates incremental updates) — detect-
  and-Degrade only.
- **Build-vs-buy**: build_own (pikepdf dict-walk; no new dep). Preservation = neither (qpdf can't;
  PyMuPDF AGPL).
- **Full/phased**: FULL for detection (the buildable half). **Tests**: SigFlags-only, populated-field,
  EMPTY-field (must NOT refuse), DocMDP, unsigned control, malformed AcroForm (no crash); INV: every
  EditResult-verb on signed + `force=False` → `success=False` + `signature_invalidated`; force=True
  still surfaced; dry_run parity; wrapper str-verb refuses by raising.
- **Effort M · risk medium. New deps: none.**

#### A2.2 — Linearization (Fast Web View) detect + preserve
- **Current**: `_save_pdf` passes no `linearize=` → silently de-linearizes every linearized input
  (verified: `save(linearize=True)` round-trips; plain save yields `is_linearized=False`).
  `pikepdf.Pdf.is_linearized` reflects the INPUT state and survives in-memory mutation.
- **Approach**: `_save_pdf` reads `was_linearized = getattr(pdf,"is_linearized",False)` and injects
  `save_kwargs["linearize"]=True` when preserving (caller override wins). On `PdfError` during a
  linearize save, retry once without (B1 fallback). Return `_SaveOutcome(was_linearized,
  linearization_preserved)`. Edit-path save sites append `linearization_dropped` (info, NOT
  font-affecting) only on the fallback. ~5% size, ~0ms cost; composes with `encryption=`.
- **Build-vs-buy**: build_own (native `is_linearized` + `linearize=` kwarg; no new dep).
- **Full/phased**: FULL. **Tests**: preserve round-trip; no-gratuitous-linearize; explicit override;
  composes-with-encryption; `_SaveOutcome` contract; missing-property graceful; dry_run parity
  (kind absent on success path).
- **Effort S · risk low. New deps: none.**

#### A2.3 — Encrypted decrypt-edit-re-encrypt round-trip
- **Current**: surgeon refuses ALL encrypted input (`surgeon.py:1164,1385,1509`); structural/wrapper
  have NO guard → silently re-save DECRYPTED (default `pdf.save()` strips). `open_pdf` already
  accepts `password`; no edit verb exposes it. Owner-only-encrypted PDFs already open with `""`.
  `EncryptionInfo` lacks `owner_password` → manual reconstruction impossible → `encryption=True`
  (copy-from-source) is the ONLY mechanism.
- **Approach**: add `*, password: str|None=None` (keyword-only) to all read/edit verbs (thread into
  `open_pdf` + internal `find` calls). Replace surgeon's 3 refusals with `enc = encryption_for_save(pdf)`
  → `_save_pdf(..., encryption=enc)`. Same for structural/wrapper EDIT verbs; EXCLUDE `encrypt_pdf`,
  `decrypt_pdf`, `merge_pdfs`, `split_pdf` (documented strip). New `encryption_preserved` (info) +
  `encryption_dropped` (warning). User-password without password → `open_pdf` already raises (INV-M-1
  honored). Permission flags advisory (pikepdf bypasses by design) — surface via Degradation detail,
  no DRM enforcement.
- **Build-vs-buy**: build_own (pikepdf-native; `password`+`encryption=True` already plumbed; no new dep).
- **Full/phased**: FULL. **Tests**: owner-only edit preserves encryption + permissions + R/V/bits;
  user-password+correct-pw; no/wrong-pw raises (INV-M-1 extended to replace/replace_block); silent-strip
  regression (was `is_encrypted=False`, now True); plaintext parity; decrypt/merge exceptions;
  INV-M-7/8/9.
- **Effort M · risk medium. New deps: none.**

### Block B — Correctness / total text reachability

#### B.1 — Rank 5: GlyphWidthCache objgen re-key (land FIRST/with Rank 1)
- **Current**: `GlyphWidthCache._cache: dict[str, dict]` keyed on bare `font_name` (`widths.py:84`);
  `evict(font_name)` str-keyed, sole caller `surgeon.py:619`. surgeon threads ONE cache across pages
  (`replace_all` 1383, `batch_replace` 1507) → two different `/F1` font dicts on different pages alias
  TODAY. reflow does NOT use the cache (`_measure_word` takes a pre-parsed dict, `reflow.py:105-108`);
  structural has zero references. Blast radius = `widths.py` + `surgeon.py:619` only.
- **Approach**: `_make_key(resources, font_name) -> (objgen0, objgen1, font_name)` mirroring
  `encoding.py:381-397` (inline/direct dict → `(0,0)`, fold `id(scope.resources)` to disambiguate two
  inline `/F1`). Re-key `_cache`. **RETAIN `evict`, re-keyed `evict(page, font_name)`** — CRITICAL:
  an in-place `/W` mutation does NOT change objgen (verified: `_append_w_entries` reassigns the same
  indirect dict), so objgen re-key fixes ALIASING but NOT STALENESS; the post-extend evict at
  `surgeon.py:619` is still required or newly-injected CIDs return `DEFAULT_WIDTH=600`. Public
  `get_width(page, font_name, char_code)` signature unchanged; resolve `resources=page['/Resources']`
  internally (Rank-1-ready). The `_make_key(resources, …)` signature means Rank 1 needs no second
  refactor.
- **Build-vs-buy**: build_own (project-internal cache hygiene; pikepdf-only; mirrors `FontResolverCache`).
- **Full/phased**: FULL (~30 LOC + 1 call-site). **Tests**: 2-page aliasing probe (page0 `/F1`=500,
  page1 `/F1`=900 distinct objgen — fails today, passes after); staleness probe (inject CID, evict,
  width≠600); inline-dict `(0,0)`; INV-W width-cache-key-is-objgen.
- **Effort S · risk low. New deps: none. Deps: Rank 1 (land first/together).**

#### B.2 — Rank 1: Form XObject text editing WITH per-element resource-scope chain
- **Current**: `_handle_do` (`locator.py:483-524`) records a Form XObject as ONE opaque
  `ContentElement` with no recursion; `find()` filters `e.type=="text"` (`locator.py:940`) so XObject
  text is unaddressable. 14 page-bound `/Resources` lookups (see V2). `_assert_match_addressable`
  (`surgeon.py:70-182`) validates op_idx only against page-stream ops. `state.GraphicsStateTracker`
  has `_mat_mult` CTM math + q/Q stack but NO initial-CTM seeder. Surgeon writes `page.Contents`
  (`surgeon.py:1290,1464,1600`). **Verified**: `parse_content_stream(xobj_ref)` parses a Form-XObject
  stream and `xobj_ref.write(unparse_content_stream(...))` round-trips (re-ran
  `experiments/v013_block_3_preflight/xobj_verify_probe.py` → PASS); COW via stream-duplication +
  repoint-only-target-page works; nested Do reachable. No corpus PDF has text-bearing Form XObjects
  (scan: `form_with_text=0` across all 15) → an XObject regression silently passes today.
- **Approach** (FULL, four conditions a–d; partial would re-introduce the v0.1.0 cross-font corruption):
  - **(a) Resource-scope chain** — new `ResourceScope` dataclass (`resources`, `origin`, `depth`) in a
    new pikepdf-only leaf module `resources.py` (or models). `font_dict(font_name)`: innermost-defining
    wins — XObject-local `/Resources/Font` → **explicit cycle-guarded `/Parent` walk** for the
    inherited-Pages-node tier (**NOT `Page.resources`** — V1: verified it does NOT resolve inheritance;
    manual walk reaches the ancestor `/Resources`). Returns `(font_obj, font_obj.objgen)`. Route ALL 14
    page-bound lookups through it.
  - **(b) Shared-XObject COW** — dedup by stream objgen; default policy `copy_on_write`: duplicate via
    `make_stream(bytes(xobj_ref.read_raw_bytes()))` (NOT `copy_foreign` for same-pdf), deep-copy/carry
    `/Resources`, repoint only the target page's `/XObject/<name>`. `policy: Literal["edit_all",
    "copy_on_write"]` internal kwarg; `edit_all` emits `xobject_edited_all_instances`.
    > **RE-AUDIT CORRECTION #4 (B.2 COW).** The duplication above is INSUFFICIENT. COW must
    > **deep-clone every font object the edit touches** — `/FontDescriptor`, `/FontFile2`,
    > `/ToUnicode`, `/W`, and `/CIDToGIDMap` — not just the content stream. Otherwise a glyph
    > injection / `/W` mutation done for the COW copy aliases back into the shared original font dict
    > and corrupts the un-edited instances (the exact cross-instance corruption COW exists to prevent).
    > And the stream content must be read with **`read_bytes()` (decoded), NOT `read_raw_bytes()`**:
    > re-wrapping still-compressed raw bytes in `make_stream` (whose default stores them as a fresh
    > uncompressed stream) yields a stream whose declared filters no longer match its contents.
  - **(c) Container re-indexing** — `ContentElement.container_ref`/`resource_scope`,
    `TextMatch.container_objgen` (additive, default None = page stream → INV-B-3 backward-compatible).
    `_assert_match_addressable` parses the CORRECT stream; surgeon writes
    `container_ref.write(...)` vs `page.Contents`.
  - **(d) Nested-Do CTM compose** — refactor `interpret()` → `_interpret_stream(source, base_ctm,
    scope, depth, visited)`; child base_ctm = `_mat_mult(form_matrix, ctm)`; new `set_initial_ctm`
    seeder on the tracker; cycle guard (`visited_objgens`); `MAX_XOBJECT_DEPTH=12` →
    `xobject_depth_exceeded`.
  - New DegradationKinds: `xobject_shared_copied` (info), `xobject_edited_all_instances` (warning),
    `xobject_depth_exceeded` (warning), `xobject_resource_unresolved` (warning — refuse rather than
    mis-resolve a same-named page font).
  - **Refuse-honestly**: a font name resolvable in NO scope → `xobject_resource_unresolved`, skip; never
    silently grab the page's same-named font.
- **Build-vs-buy**: build_own (no non-AGPL lib does in-place XObject text editing; primitives all
  verified on pikepdf 10.5.1; no new dep, no boundary change).
- **Full/phased**: FULL (a–d ship together; partial corrupts). Land Rank 5 first/together.
- **Tests**: builder `tests/_xobject_fixture.py` (reportlab beginForm/doForm, NOT committed): BASE
  (XObject-own font), XOBJECT-LOCAL-FONT cross-font tripwire (page `/F1`=Helvetica vs XObject
  `/F1`=CID Arial — must resolve against the XObject's), SHARED COW (2 pages, edit page1 only),
  OMITS-/Resources (deprecated page fallback), INHERITED-PAGES-RESOURCES (the `/Parent`-walk edge),
  NESTED-DO + self-referential cycle + 13-deep depth-exceeded, CTM-transformed bbox. INV: extend
  `test_b_3` for container staleness; scope-chain font-identity probe; grep guard "no
  `page['/Resources']['/Font']` literal outside `resources.py`".
- **Effort L · risk medium. New deps: none. Deps: Rank 5 (hard).**

#### B.3 — Rank 2.5: ToUnicode-absent Identity-H recovery (PHASED, per V8)
- **Current**: every `/Type0` → `_init_identity_h`; ToUnicode-absent → bare `logger.warning;return`
  leaving maps EMPTY (`encoding.py:136-138`) → decode KeyError swallowed at `locator.py:204,243,350`
  → `find()` silent zero-match. Reversal machinery loaded (`font_has_codepoint` opens `/FontFile2`,
  `getBestCmap`/`getGlyphID`, `fonts.py:157-163`; `_extend_tier1` already does cid=getGlyphID at
  `fonts.py:922-926`). `fonts.py:306` raw `font_dict["/ToUnicode"].read_bytes()` → KeyError on
  recovered fonts (verified the project spike: `replace_success:false, KeyError:'/ToUnicode'`).
  PUA-encoded embedded cmap → recovery yields `'...'` garbage (verified spike `fixture_pua_no_tounicode`).
- **Approach** (PHASED):
  - **READ path**: `fonts.reverse_embedded_cmap(font_dict) -> dict[int,str]|None` — descend to
    descriptor, open `/FontFile2`, invert `getBestCmap()` via `getGlyphID` (GID==CID under Identity-H),
    lowest-codepoint-wins on collision (matches `_build_reverse_map`). Guard `getBestCmap()` raise →
    None. **PUA-PURITY GATE**: if ALL recovered codepoints are Private-Use-Area → `untextable_cidfont`,
    do NOT populate maps (closes the LaTeX/dvipdfmx silent-lie hole); mixed → drop only PUA entries.
    **CIDToGIDMap-stream guard**: skip recovery when DescendantFonts[0]/CIDToGIDMap is a Stream
    (CID≠GID). Lazy `from pdf_edit_engine.fonts import reverse_embedded_cmap` (encoding stays
    fonttools-free). Two flags `tounicode_recovered`/`tounicode_untextable`.
  - **WRITE path (the V8 fix)**: `replace()` on a recovered font MUST synthesize `/ToUnicode` before
    the `fonts.py:306` subscript (make `_append_to_unicode_cmap`/`_parse_existing_tounicode`
    create-if-absent) OR honestly refuse with `success=False` + a typed Degradation (NOT a raw KeyError,
    which also violates INV-L-1).
  - New kinds: `tounicode_recovered` (info), `untextable_cidfont` (warning). Neither in
    FONT_AFFECTING_KINDS. Surfaced at `surgeon._apply_single_replacement` / `reflow.reflow_paragraph`.
- **Build-vs-buy**: build_own (~25 LOC over loaded machinery; no new dep; no boundary change).
- **Full/phased**: PHASED — read-recovery + PUA gate + write-path fix all in 0.2.0; full decode of
  cmap-ALSO-stripped fonts is the honest-refusal case, not a gap.
- **Tests**: RECOVERABLE / UNTEXTABLE (cmap stripped) / NON-IDENTITY-CIDToGIDMap-stream / **PUA-only**
  (LaTeX case → untextable, not garbage) variants; e2e `find('Acme')` 0→≥1 then `replace()` → success
  with synthesized `/ToUnicode` OR honest typed refusal (NOT crash). INV: ToUnicode-absent-but-recoverable
  is findable; untextable surfaces a diagnosable signal never a silent empty.
- **Effort M · risk medium. New deps: none. Deps: B.4 (shared CIDToGIDMap-stream detect + untextable taxonomy; co-built).**

#### B.4 — Rank 2: Non-Identity Type0 named-CMap DECODE + Identity-V READ & same-position WRITE (DEEPENED per V6/V13/V15)
- **Current**: `encoding.py:114-115` force-routes EVERY `/Type0` to `_init_identity_h`, hardcodes
  `_byte_width=2`, reads ONLY `/ToUnicode`, never `/Encoding`. decode/encode assume CID==GID; zero
  CIDToGIDMap-stream indirection on the read side (the WRITE side honors it at `fonts.py:382-396` —
  read/write asymmetry). `_walk_cids` consumes a FIXED `byte_width` (`locator.py:247,305,323,339`). No
  `/W2`/`/DW2`/`/WMode` handling. `state.py:191` confirms Tw inert for 2-byte CID. **VERIFIED LIVE**:
  `CMapDB.get_cmap('90ms-RKSJ-H').decode(b'\x20\x81\x40\x20')` → `[231,633,231]` — 149 named CMaps decode
  TODAY (V6/V13).
> **RE-AUDIT CORRECTION #5 (B.4 scope split).** The v3 approach below pulls "same-glyph-count replace
> on the named-CMap run" into 0.2.0. The variable-width **character-changing WRITE is NOT buildable as
> written**: `PyCMap.decode` returns a flat CID list and **discards the byte boundaries** of the source
> codespace, so the engine cannot know how many bytes a given source CID consumed (1-byte ASCII vs
> 2-byte kanji in 90ms-RKSJ-H) and therefore cannot correctly re-encode a *different* CID to the right
> byte width. **0.2.0 ships DECODE (READ) + fixed-width Identity-V same-position WRITE ONLY**; the
> variable-width named-CMap character-changing write defers to 0.3.0 (needs the from-scratch codespace
> parser, the same dependency the embedded-`/CMap`-stream `code2cid` work needs). Additionally, the
> Identity-V same-position write below inherits **correction #6**: the horizontal-only
> `surgeon._adjust_subsequent_positioning` must be gated/generalized for vertical before vertical write
> ships.
- **Approach** (named-CMap DECODE + Identity-V READ + same-position WRITE all in 0.2.0):
  - **Classifier**: `_classify_type0(font_dict)` → `type0_kind` ∈ {identity-h, identity-v, named-cmap,
    embedded-cmap-stream, tounicode-absent} and `cid_to_gid_kind` ∈ {identity, stream, absent}. WMode from
    3 surfaces: name suffix `-V`/`/Identity-V`; named-CMap `is_vertical()` via `CMapDB.get_cmap`; explicit
    `/WMode` int.
  - **NAMED-CMAP DECODE (pulled IN per V6 — the v2 "Phase B" decode, minus the unbuildable part)**: when
    `/Encoding` is a NAME resolvable by `CMapDB.get_cmap`, build a `PyCMap` and use its `.decode(bytes)`
    to yield CIDs. This REQUIRES the `_walk_cids` refactor the v2 plan cited as the blocker — but that is
    ENGINEERING, not a code-block, so it ships: refactor the fixed-`byte_width` chunker
    (`locator.py:247,305,323,339`) into `_iter_cids(raw, resolver)` that delegates to the CMap's own
    multi-byte codespace (`PyCMap.decode` already segments variable-width input — 1-byte ASCII vs 2-byte
    kanji in 90ms-RKSJ-H). CID→Unicode for `find()`'s flat string still goes through `/ToUnicode` (or B.3
    recovery). LTR named CMaps (`...-H`) decode for find + same-glyph-count replace; vertical named CMaps
    (`...-V`) use the vertical write path below.
  - **EMBEDDED-`/CMap`-STREAM**: pdfminer's `CMapParser`/`FileCMap` builds **0** `code2cid` entries
    (V6) → `unsupported_cmap` (warning), honest refusal, the genuine 0.1.6 frontier.
  - **ToUnicode-absent** → route to B.3 recovery; else `untextable_cidfont`.
  - **Non-Identity CIDToGIDMap STREAM under Identity-H**: THREAD the stream (decode is correct since
    ToUnicode is CID-keyed; only encode/extension GID allocation must respect the indirection — reuse
    `fonts.py:382-396`); refuse `cidtogidmap_nonidentity` only for CFF (blocked on Rank 3).
  - **Vertical READ model**: `_classify_type0` sets `is_vertical`; `widths.parse_cid_w2` for `/W2`,`/DW2`
    (default `[880 -1000]`); `state` advances `-y` for WMode=1 with a position-vector origin correction
    (`get_glyph_origin(vx,vy)`); wmode-aware bbox (tall narrow strip). FontInfo gains `writing_mode`.
  - **Identity-V same-position WRITE (pulled IN per V15)**: because the READ model now yields correct
    `/W2`/`/DW2` advances and origin, a replacement that preserves the run's vertical extent (same glyph
    count, or shorter) re-emits in place: the surgeon same-length splice path is writing-mode-AGNOSTIC (it
    swaps CID bytes inside the existing Tj/TJ without touching positioning), so it ALREADY works once
    decode is correct; the only new code is letting the surgeon's addressability check accept a vertical
    match. LENGTH-CHANGING vertical edits (which need a `-y` re-flow / `-x` column advance) REFUSE with
    `identity_v_reflow_unsupported` (the v0.3.0 vertical-flow item, V15-cited) — NOT a blanket
    vertical refusal.
  - New kinds: `unsupported_cmap`, `untextable_cidfont`, `cidtogidmap_nonidentity`,
    `identity_v_reflow_unsupported` (all warning, none font-affecting).
- **Build-vs-buy**: extend_library (pdfminer `CMapDB.get_cmap` → `PyCMap.decode` for named CMaps — already
  imported in encoding `:9` and fonts `:14`; reconcile dep-table per V9); build_own for the `_iter_cids`
  refactor + W2/y-advance arithmetic + vertical splice gate. No new dep.
- **Full/phased**: FULL for named-CMap DECODE + Identity-V READ + same-position WRITE; only
  embedded-`/CMap`-stream `code2cid` (0 entries, V6) and length-changing vertical reflow (V15) defer to
  0.3.0/0.1.6 — both code-cited.
- **Tests**: 90ms-RKSJ-H `find('日本')` returns a match (today 0) via `PyCMap.decode` + the `_iter_cids`
  variable-width refactor; same-glyph-count replace on the named-CMap run succeeds; embedded-stream →
  `unsupported_cmap` (not silent zero); ToUnicode-absent → B.3; non-Identity-CIDToGIDMap-stream → decode
  correct + extend threads (glyf) / refuses (CFF); Identity-V → `is_vertical` via WMode (not suffix),
  tall-narrow bbox, SAME-glyph-count vertical replace SUCCEEDS, length-changing → `identity_v_reflow_unsupported`;
  `/W2`,`/DW2` parse with explicit DW2 default; `_iter_cids` byte-identical regression on Identity-H 2-byte.
  Update J-5 canonical set per kind.
- **Effort L · risk medium/high (the `_iter_cids` refactor touches the read hot path). New deps: none.
  Deps: B.3 (co-built), Rank 3 (CFF CIDToGIDMap refuse), Rank 5.**

#### B.5 — ToUnicode read-side integrity (PRESENT-but-WRONG/PARTIAL)
- **Current**: decode trusts the parsed `/ToUnicode` verbatim (`encoding.py:145`); no validation against
  the embedded cmap; `_build_reverse_map` silently drops many-CID→one-Unicode collisions
  (`encoding.py:25-26`). `_append_to_unicode_cmap`'s `existing.get(cid) != ustr` override branch
  (`fonts.py:302`) already supports corrections but is never called with any.
- **Approach** (Identity-H only): `_reconcile_tounicode(tu_map, embedded_map)` — (a) both agree → trust
  ToUnicode; (b) embedded-only gap → ADD; (c) divergence → prefer embedded ONLY on a positive
  placeholder signal (U+0000/U+FFFD, shared-target ≥ threshold, or unrenderable-by-embedded-cmap);
  multi-char ToUnicode (ligature) ALWAYS kept (never "corrected"); (d) ToUnicode-only → keep. WRITE-side
  repair via the existing `_append_to_unicode_cmap` override path for the edited run's corrected CIDs.
  New kinds `tounicode_partial_repaired` (info), `tounicode_diverged` (warning). Shares
  `reverse_embedded_cmap` with Rank 2.5.
- **Build-vs-buy**: build_own (asymmetric reconciliation policy is domain logic; no library validates a
  PDF ToUnicode against the font; no new dep).
- **Full/phased**: FULL (Identity-H). **Tests**: PARTIAL (gaps filled, get_text complete), WRONG/placeholder
  (embedded preferred + `tounicode_diverged`), LIGATURE-protection (multi-char kept), authoritative-when-
  plausible (ToUnicode wins), write-side round-trip (reopen → edited line extractable), regression no-op
  on clean fonts.
- **Effort M · risk medium. New deps: none.**

#### B.6 — Symbol-cmap (3,0)/(1,0)-only dual-lookup
- **Current**: every fonttools cmap read uses `getBestCmap()` which returns None for (3,0)Symbol/(1,0)Mac-
  only fonts (6 sites in `fonts.py`: `137-174,592,617-627,854,1062,1557`); `_inject_glyph_in_place`
  raises a bare KeyError at `:1069`. `analyze_subset` reports ALL chars missing for a symbol font.
- **Approach**: `_resolve_cmap(tt) -> (cmap, offset)` — `getBestCmap()` fast path (offset 0, Unicode
  fonts byte-identical) else first (3,0) (PUA 0xF000 base) then (1,0) (byte-direct). `_symbol_lookup(cmap,
  offset, cp)` tries `cp+offset` then raw. Route all 6 sites; add the (3,0)/(1,0) write-back third branch
  for idempotency; None-guard the injection raise → clean `FontNotFoundError`→`font_extension_failed`.
  Simple-font partition reads the embedded (3,0) cmap when `/Encoding` absent.
- **Build-vs-buy**: build_own (fontTools has no "best symbol cmap"; ~30 LOC; no new dep).
- **Full/phased**: FULL. **Tests**: (3,0)+offset, (3,0)-raw, (1,0)-Mac, simple-symbol-no-/Encoding,
  idempotent re-extend, honest-refusal no-glyph; Unicode-font regression byte-identical; INV-F-symbol grep
  guard (no `getBestCmap()` coverage read without the fallback). Real Wingdings/Webdings PDF gate.
- **Effort M · risk medium. New deps: none.**

#### B.7 — NFC/NFD normalization-aware find & replace + combining-mark character model
- **Current**: `find()` does raw substring search, no normalization (`locator.py:946-950`). A single CID
  decoding to an NFD `base+mark` emits MULTIPLE TextCharacters sharing one `byte_position`/`operator_index`
  (`locator.py:355-378`) — structurally identical to a ligature; surgeon's `cid_slots` heuristic
  (`surgeon.py:711-715`) already routes these to the rebuild path. Even split `sub_width=width_ts/n_sub`
  is geometrically wrong for marks (marks have zero advance).
- **Approach**:
  - **Matching**: `_build_normalized_view(flat, char_map)` — segment into grapheme clusters
    (`unicodedata.combining`), NFC each cluster, re-expand char_map 1:1 so logical positions map back to
    the cluster's base TextCharacter (all share the CID slot). Normalize the needle to NFC; search the
    normalized view; report `matched_text` from the ORIGINAL flat. Both NFD-doc/NFC-needle and reverse work.
  - **Character model**: `TextCharacter.cluster_role: Literal["base","mark","ligature_part","standalone"]`
    (additive). `_walk_cids` gives marks `width=0.0`+base position; base full advance; true ligature even-
    split among non-mark parts only. Surgeon's same-length splice clusters op_chars by CID slot, zips
    against `_iter_graphemes(replacement)`, one `_splice_bytes` per cluster (fixes the double-write-same-slot
    bug + codepoint-vs-grapheme skew). Distribution counts clusters not codepoints.
  - **Output form**: `_choose_encode_form(resolver, new_text)` — try NFC `can_encode`; else NFD +
    `unicode_renormalized` (info); else let extend inject precomposed. NFKC/NFKD EXCLUDED (changes glyph
    identity).
  - New kind `unicode_renormalized` (info, not font-affecting). stdlib `unicodedata` only.
- **Build-vs-buy**: build_own (stdlib `unicodedata.combining`; ~15-LOC base+mark segmenter; no PyICU/grapheme
  dep — UAX-29 emoji clustering out of scope for CIDFont text).
- **Full/phased**: FULL (NFKC excluded on correctness grounds, not deferred). **Tests**: NFD-doc/NFC-needle
  both directions; base+mark width=0/base-full; true-ligature even-split unchanged; café→cafe grapheme
  splice no double-write; round-trip; INV combining-mark-cluster (base+mark is NOT a ligature); strengthen
  `test_a_4`.
- **Effort M · risk low/medium. New deps: none.**

#### B.8 — Bidi find-side implicit-reorder + RTL same-glyph-count WRITE (DEEPENED per critique under-reach #2)
- **Current**: `find()` sorts geometrically L→R (`locator.py:941`), concatenates visual-order
  (`locator.py:882-884`); zero bidi handling (grep=0). Surgeon zips replacement against
  `match.characters` in stored (visual) order — so a logical reorder must be matching-surface-only and
  must NOT mutate `characters`/`byte_position`/`matched_text` orientation. **VERIFIED**: the same-length
  splice (`surgeon.py:715,725-730`) swaps CID bytes slot-for-slot in stored order without re-positioning.
- **Approach** (READ reorder + WRITE same-glyph-count splice):
  - **READ**: new pure-stdlib `bidi.py` leaf (only `unicodedata.bidirectional`). Reorder-only UBA
    (UAX #9 P2-P3, X9, W1-W7, N0-N2, I1-I2, L2). `has_rtl` gate → LTR pages byte-identical. For RTL pages:
    per-paragraph-segment compute base level + L2 permutation; build a logical-order view + permuted
    char_map for substring search; on a hit, RE-SORT real_chars back to ORIGINAL visual order before
    building the TextMatch (preserves surgeon's contract); `matched_text` stays the visual slice.
  - **WRITE (the under-reach-#2 fix)**: when the replacement preserves glyph count AND the matched run is
    already stored visually (the common case for an RTL run laid out by the original producer — Hebrew/
    Arabic in a CIDFont is stored in visual order in the content stream), `replace('שלום','עולם')` needs
    NO reorder: map each logical replacement character to its visual slot via the SAME L2 permutation used
    for the match, then the existing same-length splice swaps CID bytes in visual order. The surgeon's
    `cid_slots` clustering (already used for ligatures/marks, `surgeon.py:711-715`) handles the
    logical→visual slot mapping. This delivers the bulk of real-world RTL editing (name/date/label
    corrections that keep length) with the machinery that already exists.
  - **REFUSE honestly**: length-CHANGING RTL (needs full re-shaping + re-ordering = B.10 territory) →
    `rtl_write_unsupported` (warning), named explicitly as a 0.2.0 known edge, never silent-wrong output.
    Arabic that requires CONTEXTUAL SHAPING on the replacement (initial/medial/final forms differ from the
    stored glyphs) routes to B.10's shaper when the font is TrueType; CFF/missing-GSUB → `shaping_unsupported`.
    Explicit X1-X8 isolate/override format chars (do not appear as glyphs in content streams) → honest
    `bidi_unsupported`.
  - New kind `rtl_write_unsupported` (warning, not font-affecting). `bidi_unsupported` (warning) for
    explicit isolates.
- **Build-vs-buy**: build_own (reject python-bidi: LGPL; ~150-250 LOC pure-stdlib; UAX #9 BidiCharacterTest.txt
  conformance oracle; no new dep, no boundary change — `bidi.py` is a stdlib leaf consumed by locator;
  WRITE reuses the existing same-length splice + the B.10 shaper for contextual cases).
- **Full/phased**: FULL for find-reorder + same-glyph-count visual-stored RTL write; length-changing /
  contextual-shaping-required RTL honestly refused (or routed to B.10 for TrueType). **Tests**: vendored
  BidiCharacterTest.txt slice (Hebrew, Arabic, RTL+EN, brackets); synthetic Identity-H Hebrew word stored
  visual-reversed → `find('שלום')` returns 1 match addressing correct slots; **`replace('שלום','עולם')`
  (same 4 glyphs) → success, reopen extracts 'עולם'**; length-changing RTL → `rtl_write_unsupported`;
  LTR byte-identical regression; explicit-format-char → `bidi_unsupported`.
- **Effort M · risk medium. New deps: none. Deps: B.10 (contextual-shaping route), B.7 (cluster slots).**

#### B.9 — Mandatory-vs-discretionary ligature round-trip integrity
- **Current**: `encode()` greedy longest-match collapses any plain `f`+`i` whenever ToUnicode maps a
  ligature CID to `'fi'` (`encoding.py:239-257`); `_max_ligature_len` from multi-char ToUnicode values
  (`encoding.py:148-151`); lowest-CID tie-break can route a single char to a ligature CID
  (`encoding.py:22-27`). Collapse fires on full-string encode (surgeon `:323`, reflow `:128,670`),
  corrupting both glyph identity AND the Tz width target.
- **Approach**: `_build_reverse_map` two-pass (single-codepoint entries first, then ligatures in a
  SEPARATE `_ligature_to_cid`). `_classify_ligature(uval)` via NFKC (discretionary iff NFKC decomposes to
  plain Latin; mandatory = Arabic presentation forms / non-decomposable). `encode(text, *,
  allow_discretionary_ligatures=False, _observed=None)` — default OFF (format-preserving); mandatory
  always applied; round-trip self-verify `decode(encode(x))` NFKC-equals x else `EncodingError`.
  `can_encode` shares `_encode_step` (lockstep). New kind `ligature_substituted` (info). Default-OFF is
  correct: the original stream already encodes its own ligatures (preserved on the same-length path);
  collapsing typed-separate replacement text is a guess that breaks width targets.
- **Build-vs-buy**: build_own (stdlib `unicodedata.normalize('NFKC')`; reject fontTools.agl in encoding —
  boundary; uharfbuzz deferred to 0.3.0; no new dep).
- **Full/phased**: FULL (default-OFF is the full correct answer for a format-preserving editor). **Tests**:
  no-collapse-by-default; opt-in collapse; mandatory always; single-char never→ligature-CID; round-trip
  raises on defect; can_encode↔encode lockstep; Tz uses separate-glyph widths; strengthen INV-A-4 from
  closure to fidelity; re-characterize `test_encoding.py:95-115`.
- **Effort M · risk medium. New deps: none.**

#### B.10 — World-script WRITE-side shaping for TrueType-embedded scripts (NEW — closes critique under-reach #1)
- **Current**: `surgeon.encode` (`encoding.py:239`) maps codepoint→CID 1:1 via the reverse ToUnicode/cmap
  with NO shaping (no joining, no reordering, no GSUB ligature/mark positioning). For Arabic/Devanagari/
  Thai this produces dotted-form / unjoined / mis-positioned output — so today the only honest behavior is
  refuse. The TrueType glyf injector EXISTS (`_inject_glyph_in_place`, `fonts.py:558+`); CID-keyed CFF
  injection ships in C.3 (same release); `unicodedata` (B.7/B.8) is stdlib. **The v2 plan's own roadmap
  said "TrueType complete; CFF rides the 0.2.0 injector" — every dependency lands in 0.2.0 (V14), so the
  ONLY thing that deferred this was bundling the per-glyph TJ/Td writer separately.** That is exactly the
  timid deferral the user rejected; it is pulled IN.
> **RE-AUDIT CORRECTION #1 (B.10) + DEFERRAL.** B.10 is moved OUT of 0.2.0 to 0.3.0 (see
> `## 0.3.0+`). Two source-level blockers refute the v3 "reuse the existing injector" premise:
> (1) `_inject_glyph_in_place` (fonts.py:558+) is **codepoint-indexed** — it does `cp = ord(ch)` and
> indexes `system.getBestCmap()[cp]` (fonts.py:593–596). HarfBuzz emits **GIDs**, not codepoints, and
> a shaped GID has no codepoint to look up, so B.10 needs a **NET-NEW GID-keyed injector**
> (`_inject_glyph_by_gid(embedded, donor, gid)`) that copies `donor['glyf'][donor.getGlyphName(gid)]`
> directly. (2) The writer must use **relative `Td` inter-glyph bracketing, NOT absolute `Tm`**, and
> **`y_offset` (mark positioning) is NOT reachable through the TJ array** (TJ numbers are horizontal
> displacements only) — a vertical mark nudge needs a `Td`/`Ts` pair, not a TJ number. The bridge +
> GID-keyed injector + `Td`/offset writer are the 0.3.0 deliverable.
- **Approach** (shaping bridge + per-glyph positioned writer, behind the same refusal contract as CFF):
  - **Shaper**: add `uharfbuzz` (Apache-2.0, no-AGPL clean). New `shaping.py` leaf in the FONTS dep zone
    (fonts already = fonttools+pikepdf; uharfbuzz becomes a new dep-table column there). `shape(text,
    font_bytes, script, lang, direction) -> list[ShapedGlyph]` where `ShapedGlyph` is a PLAIN dataclass
    (`gid`, `cluster`, `x_advance`, `y_advance`, `x_offset`, `y_offset`). HarfBuzz does the joining,
    reordering, GSUB/GPOS, mark positioning — the correct, complete shaping engine (iText pdfCalligraph is
    the paid equivalent; we use the FOSS shaper directly).
  - **Glyph coverage**: each shaped `gid` must exist in the embedded font. Under Identity-H CID==GID, the
    shaped GID IS the CID to emit. If a shaped GID is absent from the embedded subset, inject it via the
    EXISTING injector (TrueType → `_inject_glyph_in_place`; CFF → C.3's `_inject_cff_glyph_in_place`),
    indexing the donor system font by GID (the shaper ran against the SAME font bytes, so GIDs align). For
    CID-keyed CFF the chosen CID = shaped GID per C.3.
  - **WRITER (surgeon stays pikepdf-only)**: surgeon consumes the plain `ShapedGlyph` list (no fonttools
    import — boundary preserved exactly as the v2 plan promised). It emits a positioned `TJ`: for each
    glyph write its 2-byte CID and insert the inter-glyph adjustment `-(x_advance - default_advance)/upem*1000`
    as a TJ number; `x_offset`/`y_offset` (mark positioning) emit as a bracketed `Tm` nudge or a
    `Ts`+horizontal-shift pair when non-zero. RTL direction reverses the visual emission order (composes
    with B.8). This generalizes the existing per-glyph CID TJ emitter (`reflow._encode_line_as_tj`,
    `:650-682`) rather than inventing a new one.
  - **Trigger**: `find`/`replace` detect a complex script in the replacement via `unicodedata` block
    ranges (Arabic, Devanagari + the 9 Indic blocks, Thai/Lao, etc.); simple-Latin replacements NEVER hit
    the shaper (byte-identical fast path). A `shape=True/False/'auto'` kw-only param (`auto` default).
  - **REFUSE honestly (same contract as CFF2)**: embedded font is CFF2 → `cff2_unsupported`; embedded font
    lacks the GSUB/GPOS tables HarfBuzz needs for the script (HarfBuzz falls back to no-shaping, detectable
    by comparing shaped vs unshaped cluster output) → `shaping_unsupported` (the glyphs would render
    unjoined, so refusing is honest); Type3 → refuse. The 0.2.0 TrueType+CFF path covers the vast majority
    of real-world Devanagari/Thai/Arabic (embedded as TrueType or CIDFontType0C); the refusals are the
    genuine frontier, not a deferred pillar.
  - New kinds: `text_shaped` (info — a complex-script run was shaped), `shaping_unsupported` (warning).
    `text_shaped` is NOT font-affecting (the glyphs ARE the font's own, correctly positioned).
- **Build-vs-buy**: use_library for the shaper (uharfbuzz — the canonical FOSS HarfBuzz binding;
  building a shaping engine is infeasible and wrong); build_own for the ShapedGlyph bridge + TJ/offset
  writer + script-detection trigger. Reject pdfCalligraph (paid), python-bidi (LGPL, and it does not shape).
- **Full/phased**: FULL for TrueType-embedded + CID-keyed-CFF complex-script WRITE (single-region /
  same-or-shorter length); multi-LINE shaped *reflow* (cluster-aware UAX #14 line breaking + CTM-composed
  cluster widths) is the 0.3.0 complex-script-reflow item (code-cited: cluster boundary ≠ codepoint
  boundary, the greedy breaker cannot express it). CFF2 / missing-GSUB → honest refusal.
- **Tests**: builder `tests/_shaping_fixture.py` — embed a TrueType Devanagari/Arabic/Thai font (or use a
  bundled metric-equivalent under skipif), shape `replace('क','कि')` and assert the output TJ emits the
  HarfBuzz cluster (matra reordered, conjunct formed); Arabic `replace` joins initial/medial/final forms;
  CID-keyed-CFF complex-script glyph injected + shaped; CFF2 → `cff2_unsupported`; missing-GSUB →
  `shaping_unsupported`; simple-Latin replacement does NOT invoke the shaper (byte-identical regression);
  RTL+shaping composes with B.8. Render spot-check (`.diagnostic-venv` pypdfium2) of a shaped Devanagari
  word vs the donor's reference rendering. INV: surgeon imports NO fonttools (boundary grep guard);
  `text_shaped` not font-affecting → font_preserved True.
- **Effort XL · risk high. New deps: `uharfbuzz` (Apache-2.0; fonts dep zone). Deps: C.1 (dispatch),
  C.3 (CID-keyed CFF injection), Rank 6 (UPEM), B.8 (RTL order), B.7 (cluster slots).**

#### B.11 — Empty-string / full-deletion operator cleanup (NEW — closes critique missed item #1)
- **Current** (V16): surgeon has NO empty-string path. The same-length branch (`surgeon.py:715,725-730`)
  and the distribution branch (`:739-742`) both index `new_text[idx:idx+n]`; for `new_text=''` they write
  EMPTY slices, leaving the TJ array / Tj operator (and its positioning numbers) structurally in place. The
  deleted glyphs may persist in the operator skeleton — a correctness gap (stale positioning) AND a
  privacy/honesty gap (the user believes the text is gone; an extractor following a different splice could
  still see residue). `structural.delete_block` exists but is bbox-scoped, not a `replace(x,'')` path.
- **Approach** (true operator removal, not zero-width substitution):
  - Detect `new_text == ''` at the surgeon entry. For a match whose `operator_range` covers WHOLE Tj/TJ
    operators, REMOVE those operator tuples from the parsed stream entirely (not blank their operands), then
    splice the surrounding positioning so following text does not shift: collapse the deleted run's advance
    by emitting a single compensating `Td`/TJ adjustment equal to the removed run's total advance (so the
    rest of the line keeps its absolute positions — the inverse of the displacement formula in
    `pdf-internals.md`). For a PARTIAL operator (the match is a substring of one Tj), rewrite that Tj to the
    surviving bytes only and add the compensating adjustment.
  - **No invisible-glyph residue, no stale TJ numbers**: the removed CIDs are gone from the byte string;
    `_assert_match_addressable` re-validation confirms the deleted bytes are no longer decodable at the old
    position. ToUnicode/Widths are untouched (deletion needs no font change).
  - **Refuse-honestly**: a deletion that would CROSS an operator boundary it cannot prove safe (interleaved
    marked-content, an inline-image span from A1.4, a clip-mode Tr run) → `deletion_residual_text`
    (warning) + leave the structure intact rather than risk a mis-splice. Annotation/AcroForm deletes route
    through G.2/G.3.
  - New kind `deletion_residual_text` (warning, not font-affecting). Successful clean deletion emits no
    Degradation (it is a faithful edit).
- **Build-vs-buy**: build_own (pikepdf operator-list surgery; reuses the displacement math already in
  surgeon/state; no new dep, no boundary change).
- **Full/phased**: FULL (whole-operator + partial-operator removal + compensating advance; cross-boundary
  honestly refused). **Tests**: `replace('John Smith','')` → reopen, `get_text` does NOT contain 'John
  Smith' AND following text retains position (golden x-coords); partial-operator delete (mid-Tj substring);
  whole-TJ delete; cross-marked-content delete → `deletion_residual_text` refuse not silent residue;
  extraction-residue probe (pdfminer extract after delete finds nothing — the privacy assertion);
  round-trip no-shift of trailing text; dry_run parity.
- **Effort M · risk medium. New deps: none. Deps: A1.4 (inline-image boundary guard), D.1 (marked-content boundary).**

#### B.12 — Rotated / sheared text-run edit (same-length splice) + honest refusal (NEW — closes critique missed item #3)
- **Current** (V19): `state.py:78,158,255-264` tracks the full `(a,b,c,d,e,f)` text matrix incl. rotation/
  shear `b`/`c`, and `snapshot()` carries it (`:208`). But `reflow._build_replacement_ops` (`reflow.py:685+`)
  re-emits axis-aligned `Tm`/`Td` ONLY (discards b/c), and bbox math (`locator` emit + `structural`
  bbox-collect) assumes an axis-aligned advance. So a 90° sidebar label, a diagonal watermark, or a rotated
  table header reflows to the WRONG orientation/position, silently.
- **Approach** (preserve on same-length splice; refuse honestly on reflow/bbox):
  > **RE-AUDIT CORRECTION #6 (B.12 positioning gate).** The "same-length splice WORKS unchanged" claim
  > is true for the byte swap itself, but the same-length path can still call
  > `surgeon._adjust_subsequent_positioning` (surgeon.py ~486–537; caller ~909–918 when
  > `abs(width_delta) > 0.5`). That helper is **hard-coded horizontal** — it adjusts only the x-component
  > of the following `Td`/`TD`/`Tm` (operands[0]/[4]) by `-width_delta`, assuming axis-aligned advance.
  > On a rotated/sheared run (b/c ≠ 0) the advance is NOT along x, so the compensation mis-shifts
  > trailing text. B.12 MUST gate this: when the matched run is rotated, skip the horizontal adjuster and
  > either (a) refuse the width-changing portion with `rotated_text_unsupported`, or (b) once generalized,
  > project the advance through the run's `(a,b,c,d)` before compensating. The same gate is the
  > prerequisite for Identity-V same-position write in B.4.
  - **Detect**: a run is rotated/sheared iff its captured text matrix has `b != 0 or c != 0` (within an
    epsilon). Surface `TextCharacter.is_rotated` / `TextMatch.text_matrix` (additive; the matrix is already
    tracked, just plumb it through).
  - **SAME-LENGTH splice WORKS unchanged**: the surgeon swaps CID bytes inside the existing Tj/TJ without
    touching `Tm` — so a name/date/label correction on a rotated run that keeps glyph count is ALREADY
    correct (the operator's own `Tm` rotation is preserved by construction). Only allow the addressability
    check to accept a rotated match.
  - **REFUSE honestly** for length-changing edits on a rotated run (reflow would re-emit axis-aligned →
    wrong) and for bbox-based structural edits whose region math assumes axis-alignment →
    `rotated_text_unsupported` (warning), targeting the correctly-identified rotated region. A future item
    could rotate-aware re-emit (compose the captured `Tm` rotation into `_build_replacement_ops`), but that
    is a clean follow-on, not a 0.2.0 commitment.
  - New kind `rotated_text_unsupported` (warning, not font-affecting).
- **Build-vs-buy**: build_own (the matrix is already tracked; plumb + gate; no new dep, no boundary change).
- **Full/phased**: FULL for same-length rotated splice + honest refusal on length-change/bbox; rotation-aware
  re-emit deferred as a named follow-on (not a hidden gap). **Tests**: 90°-rotated label `replace` same
  length → reopen, glyphs still rotated 90° at the same origin; sheared run same-length splice; length-changing
  rotated edit → `rotated_text_unsupported`; bbox edit overlapping a rotated run → refuse; axis-aligned
  regression byte-identical (b==c==0 path unchanged); `is_rotated` flag correctness.
- **Effort M · risk low/medium. New deps: none.**

### Block C — Font universality (dispatch on the ACTUAL outline table)

#### C.1 — CM-missing-2: /FontFile3 outline-table dispatch split (ships FIRST/standalone)
- **Current**: `_extract_font_bytes` labels EVERY `/FontFile3` "CFF" by slot (`fonts.py:248-249`); CID
  extension loads `TTFont(BytesIO(...))` regardless and crashes on bare CFF; `_extend_tier2` reads
  `fd["/FontFile2"]` UNCONDITIONALLY (`fonts.py:1037`) → raw KeyError (in NEITHER the translator catch nor
  `_FONT_EXTEND_FAIL_EXCS`) → INV-L-1 leak. Two divergent `embedded_type` producers
  (`fonts.py:709` + `locator._detect_embedded_type` `:782-804`).
- **Approach**: `_classify_outline_table(TTFont) -> Literal["glyf","cff","cff2"]` (sniff `CFF2`→`glyf`→`CFF `
  by table presence, NOT slot). Split `_extract_font_bytes` to emit TrueType/CFF/CFF2/Type1 by actual table.
  Early CID-path guard (symmetric to the simple-font guard at `:820-825`) refusing cff/cff2 cleanly before
  the tier-split, closing the raw-KeyError leak. OpenType-glyf (`/FontFile3 /Subtype /OpenType` wrapping
  glyf, sfntVersion 0x00010000) → reuse the EXISTING `_inject_glyph_in_place`, re-serialize to `/FontFile2`,
  delete `/FontFile3` (canonical slot migration). CFF2 → `cff2_unsupported` (NOT font-affecting). Widen
  `FontInfo.embedded_type` Literal (`models.py:36`) to add `CFF2`/`OpenType-glyf`/`Type3`/`unknown` (additive;
  no exhaustive match in src). Update BOTH producers (`_extract_font_bytes` AND `locator._detect_embedded_type`).
- **Build-vs-buy**: build_own (~15-LOC table-presence dispatch over already-loaded fontTools; no new dep).
- **Full/phased**: FULL. **Tests**: OpenType-glyf CID reroute (SUCCEEDS, /FontFile3 removed, /FontFile2 holds
  extended glyf, pre-existing CIDs unchanged); CFF2 refuse (`success=False`+`cff2_unsupported`); genuine-CFF
  clean refusal (not raw KeyError); `_extract_font_bytes` label units; CID-path regression for the leak.
- **Effort M · risk low. New deps: none. Prerequisite for Rank 3.**

#### C.2 — Truthful glyph_count / subset-vs-full introspection
- **Current**: `analyze_subset` RAISES on bare CFF (`fonts.py:709-714`); `get_fonts` fabricates glyph_count
  from `/W`/`/Widths` length for non-/FontFile2 (`locator.py:738-767`). CLAUDE.md's "check glyph count before
  extension" rule is structurally unavailable for CFF/Type1.
- **Approach**: `_introspect_embedded_font(bytes, slot) -> EmbeddedFontFacts(outline_kind, glyph_count,
  is_cid_keyed, units_per_em, cmap)` — content-sniff dispatch (sfnt magic → TTFont glyf/CFF2/CFF; bare CFF →
  `cffLib.CFFFontSet` charset length + `hasattr(td,'ROS')`; Type1 → `t1Lib.T1Font` CharStrings). Best-effort
  (parse failure → count=0 + `font_subset_introspection_failed`, never raise from a read API). Locator side
  uses a thin `fonts.embedded_glyph_count` wrapper via lazy import (keeps locator pikepdf+pdfminer; fontTools
  in fonts). Optional `FontInfo.outline_kind`/`is_cid_keyed` (defaulted).
- **Build-vs-buy**: extend_library (cffLib + t1Lib + TTFont, all in installed fontTools; no new dep; primitive
  in `_pathutil`/fonts to avoid boundary breach).
- **Full/phased**: FULL. **Tests**: name-keyed CFF / CID-keyed CFF / OpenType-glyf wrapper / CFF2 / Type1 /
  6000-glyph "full subset" CFF / corrupt-CFF graceful / TrueType regression; INV: analyze_subset never raises,
  count is the embedded charstring count never a `/W` length.
- **Effort M · risk low. New deps: none. Prerequisite for Rank 3 tier decision.**

#### C.3 — Rank 3: CFF / Type1C glyph injection (the XL transplant; corrected recipe per V4)
- **Current**: `_inject_glyph_in_place` requires glyf (`fonts.py:575-579`); no additive cffLib API
  (`CharStrings.__setitem__` is overwrite-only → KeyError on new name; Subsetter prune-only). No real CFF
  fixture (only `b'dummy_cff'` rejection tests).
- **Approach** (FULL, name-keyed THEN CID-keyed in the SAME milestone, gated on the C.1 dispatch + fixture corpus):
  - **Name-keyed CFF** `_inject_cff_glyph_in_place`: `DecomposingRecordingPen(donor.getGlyphSet())` → replay →
    `T2CharStringPen.getCharString(private=embeddedPrivate, globalSubrs=embeddedGlobalSubrs)` (subr-flatten
    VERIFIED: 0 callsubr/callgsubr in output across 113 glyphs). **seac/composites** route through the
    DecomposingRecordingPen (a bare T2CharStringPen has no `addComponent` and would crash on accented seac
    glyphs); set `new_cs.private` before any re-draw. Insert: `charStringsIndex.append(cs)` +
    `charStrings[name]=len-1` + `charset.append(name)` + `hmtx.metrics[name]=(adv,0)`. **RECONCILIATION (V4
    correction)**: `emb.setGlyphOrder(list(topDict.charset))` and do NOT manually pin `maxp`/`hhea` (fontTools
    derives them; the manual-bump recipe RELOADS with IndexError). Re-serialize to `/FontFile3`. PDF-level
    ToUnicode/W/CIDToGIDMap reuse the Tier-1 helpers (CID==new GID under Identity-H).
  - **CID-keyed CFF (CIDFontType0C)**: ROS detect via `hasattr(td,'ROS')`; charset = CID integers
    (`cidNNNNN`); new GID==chosen CID; `charset.append(cid)`; `FDSelect.append(fdIndex)`; pass THAT FDArray
    FontDict's `.Private` to `getCharString`. NEVER `fontTools.merge` (renumbers GIDs → v0.1.0 corruption).
    > **RE-AUDIT CORRECTION #2 (C.3 CID-keyed).** There is **no `setupCFFCID` (or equivalent
    > one-call) helper** in installed fontTools — `fontBuilder.setupCFF` builds a name-keyed CFF only.
    > The CID-keyed path must be built by **manual ROS / FDArray / FDSelect promotion**: set
    > `topDict.ROS = (registry, ordering, supplement)`, construct/extend the `FDArray` of FontDicts (each
    > with its own `Private`), append the per-glyph `FDSelect` entry, AND write the matching **hmtx
    > entry** for the new GID (the width is not derived for free under CID-keying). This is hand-built on
    > `cffLib`, not a library convenience call — budget it inside the XL.
  - **CFF2** → `cff2_unsupported` honest refusal.
  - **Width sourcing**: `_glyph_advance_1000em(font, name)` via `getGlyphSet()[name].width` (outline-agnostic;
    works for glyf AND CFF via T2 leading-width operand); `_units_per_em` derives from CFF FontMatrix when head
    absent. Replaces the four hmtx-hardcoded read sites; CFF write side calls `cffLib.width.optimizeWidths`.
  - **Coverage**: extend `font_has_codepoint` to open `/FontFile3` CFF (charset/cmap) so `can_encode` stops
    best-effort-True on CFF.
  - **Never-merge guard**: grep invariant "no `fontTools.merge` in src" (mirrors INV-L-1).
- **Build-vs-buy**: build_own (cffLib + DecomposingRecordingPen + T2CharStringPen, all in installed fontTools;
  no additive API exists; no new dep; surgeon stays pikepdf-only via the tier-string contract). Buy impossible
  (no non-AGPL CFF in-place subset extension; PyMuPDF/borb AGPL).
- **Full/phased**: FULL for name-keyed + CID-keyed + CFF2-refuse; only CFF2 actual support is deferred (no
  transplant analogue for variable charstrings).
- **Tests**: builder `tests/_cff_fixture.py` (fontBuilder.setupCFF — name-keyed, CID-keyed, OpenType-glyf,
  CFF2, **seac/composite**, subr-bearing; CID-keyed needs NO system font → no skipif). INDEPENDENT verifier:
  RecordingPen outline-equality donor-original vs reloaded-embedded; **reload `maxp.numGlyphs==len(charset)`
  probe** (catches the V4 off-by-one); 0-subr-token scan; CID-keyed no-renumber (every pre-existing CID
  still decodes); CFF2 refuse; corrupt-CFF→`font_extension_failed`. Render spot-check Arabic/CJK glyph vs donor.
- **Effort XL · risk high. New deps: none. Deps: C.1 (dispatch), C.2 (count), Rank 6 (UPEM).**

#### C.4 — Rank 6: unitsPerEm-mismatch DONOR rescale (per V5: scale_upem covers CFF)
- **Current**: UPEM mismatch is a hard `FontNotFoundError` (`fonts.py:586-590`), hit by both glyf callers
  (`_extend_tier2` `:1064`, `_extend_simple_tier_one_five` `:1304,1310`). The CID width math at `fonts.py:1071`
  already divides donor raw advance by the EMBEDDED upem — a latent width bug the hard-fail currently masks.
  `scale_upem` is present in fontTools 4.62.1 and **DOES cover CFF** (V5).
- **Approach**: `_scale_donor_to_embedded(system, embedded_upem)` — `if donor_upem != embedded_upem:
  scale_upem(system, embedded_upem)` (whole-donor, ONCE per extend call, BEFORE the inject loop, inside the
  existing `_with_fonttools_translation` block). Scales glyf + CFF + hmtx + head in one pass → every downstream
  read (outline copy, hmtx, width division) becomes correct with ZERO per-loop change, including the latent
  `:1071` width bug (root-fix). Convert the `fonts.py:586-590` hard-fail to a defensive "donor not pre-scaled"
  assert. Reject per-glyph TransformPen (scales outline but NOT hmtx → leaves the width bug). Degenerate
  `upem<=0` stays a hard refusal. New kind `font_rescaled` (info, NOT font-affecting — glyph rendered, merely
  resampled). Used by Rank 3's CFF injector too.
- **Build-vs-buy**: use_library (`scale_upem`, already a dep; no new dep, no boundary change).
- **Full/phased**: FULL (glyf + CFF in one helper). **Tests**: 1000↔2048 mismatch pair (both directions);
  INV-C-6 embedded upem UNCHANGED post-rescale; injected `/W` == round(donor_adv*1000/donor_upem) ±1;
  non-integer-ratio contour count>0; repurpose the two existing refusal tests to assert SUCCESS; degenerate
  upem=0 still raises; both CID + simple paths; `font_rescaled` Degradation present, font_preserved True.
- **Effort S · risk low. New deps: none. Deps: Rank 3 (CFF donor reuse).**

#### C.5 — Type1 (`/FontFile`) extension via Type1→CFF transplant (NEW — closes critique weak-design #1)
- **Current** (V20): `_extract_font_bytes` already labels `/FontFile` "Type1" (`fonts.py:250-251`); C.2's
  introspection already loads it via `t1Lib.T1Font`; but extension FLAT-REFUSES at `fonts.py:835-838`
  ("Type1 font extension is not supported (would require Adobe Type1...)"). The v1 plan correctly noted
  `T2CharStringPen` is for CFF/Type2, not Type1's Type1 charstrings — but it stopped at refusal instead of
  evaluating the documented alternative: convert the Type1 charstring to a Type2/CFF charstring and route
  through C.3's CFF injector. Type1 still appears in legacy/academic/print-workflow PDFs.
> **RE-AUDIT CORRECTION #3 (C.5) + DEFERRAL.** C.5 as written is an **effective no-op** and is
> **refused / deferred out of 0.2.0**. The "donor case (b)" depends on a Type1 *system font* being
> surfaced as a donor, but `system_fonts._scan_font_directory` globs only `**/*.ttf` / `**/*.otf` /
> `**/*.ttc` (system_fonts.py:270) and the filename heuristic generates only `.ttf` / `.otf`
> candidates — **there is no `.pfb` / `.pfa` discovery path**, so no Type1 donor can ever reach C.3's
> bridge. Compounding this, `fontTools.t1Lib.T1Font` is a **path-only API** (it reads from a filename;
> the `t1Lib.T1Font(BytesIO(...))` call below is not how the engine has it wired). Until a Type1
> font-discovery path exists, Type1 extension stays an **honest refusal** (`fonts.py:835-838`),
> tracked for 0.3.0+. The case-(a) embedded-Type1→CFF migration was already a cited follow-on.
- **Approach** (Type1→CFF on extension, gated on C.3 landing first):
  - **Read**: `t1Lib.T1Font(BytesIO(fontfile_bytes))` (already in C.2). Its `getGlyphSet()` exposes Type1
    charstrings as a pen-drawable glyph set.
  - **Transplant via the outline-agnostic pen path C.3 already builds**: `DecomposingRecordingPen(t1.
    getGlyphSet())` → replay → `T2CharStringPen.getCharString(...)`. Because the recording pen captures
    pure outline moves (Type1 hint/flex operators are decomposed by drawing through the glyph set), the
    resulting T2 charstring is correct — this is the SAME bridge C.3 uses for CFF donors, applied to a
    Type1 donor. The Type1 charstring interpreter that fontTools' `t1Lib` glyph set already implements does
    the Type1→outline step; the recording pen does the outline→T2 step.
  - **Target placement**: the EMBEDDED font is the question, not the donor. Two cases: (a) embedded font is
    ALSO Type1 — building a brand-new CFF `/FontFile3` to REPLACE a `/FontFile` is a font-format MIGRATION
    (changes `/Subtype` semantics, /FontFile→/FontFile3) and is heavier than additive injection; (b) the
    common real case is a Type1 SYSTEM font being used as the DONOR for a CFF/TrueType embedded subset —
    here C.5 just lets a Type1 system font feed C.3's `_inject_cff_glyph_in_place` / `_inject_glyph_in_place`
    via the recording-pen bridge, no migration needed. **Commit to (b) in 0.2.0** (the buildable, additive,
    low-risk case); **evaluate-and-cite (a)**: full Type1-embedded→CFF-embedded migration is documented as
    a clean follow-on with a stated reason (it rewrites the font dict's outline slot, raising the same
    pre-existing-CID-preservation risk C.3 manages, plus a `/Subtype` change), gated behind a Type1-embedded
    corpus fixture — NOT a flat unanalyzed refuse.
  - **Refuse-honestly**: a Type1 charstring using operators the recording pen cannot decompose (rare flex/
    seac edge) → `type1_uncovertible` (warning) honest refusal; Type1-embedded migration (case a) until its
    follow-on → `type1_embedded_extension_deferred` (warning) with the cited reason.
  - New kinds: `type1_transplanted` (info, donor case b succeeded), `type1_uncovertible` (warning),
    `type1_embedded_extension_deferred` (warning).
- **Build-vs-buy**: build_own/extend_library (`t1Lib` + `DecomposingRecordingPen` + `T2CharStringPen`, all
  in installed fontTools; reuses C.3's bridge verbatim; no new dep). Buy impossible (no non-AGPL in-place
  Type1 extension).
- **Full/phased**: PHASED — Type1-DONOR-into-CFF/TrueType-embedded (case b) FULL in 0.2.0; Type1-EMBEDDED→
  CFF migration (case a) evaluated + cited + fixture-gated as a clean follow-on, not a hidden refuse.
- **Tests**: builder reuses `_cff_fixture.py` + a Type1 donor (fontTools `t1Lib` can round-trip a synthetic
  Type1, or skipif on a system Type1); Type1-donor glyph injected into a CFF embedded subset, RecordingPen
  outline-equality donor-vs-embedded; CID-keyed no-renumber preserved; uncovertible charstring →
  `type1_uncovertible`; replace the flat `fonts.py:835-838` refuse-test to assert the donor-case SUCCESS;
  Type1-embedded migration → `type1_embedded_extension_deferred` (documents the frontier, not silent).
- **Effort M · risk medium. New deps: none. Deps: C.3 (the recording-pen→T2 bridge), C.2 (t1Lib introspection).**

### Block D — Semantic fidelity (the dimension no FOSS in-place editor touches)

#### D.1 — Marked-content operator stack (BDC/BMC/EMC/DP/MP) — the load-bearing primitive
- **Current**: `_dispatch` (`locator.py:126-173`) has NO BDC/BMC/EMC/DP/MP case → marked-content silently
  no-ops; `ContentElement` carries no MCID (grep StructTree/ActualText/MCID in src = 0). Verified:
  `parse_content_stream` surfaces BDC with operands `/P` + `Dictionary{/MCID:0}` and EMC as ordinary ops.
- **Approach**: add a `_mc_stack` to the interpreter + BDC/BMC push / EMC pop / DP/MP point handlers; resolve
  the BDC property (inline Dict or Name→page `/Resources/Properties`, via the Rank 1 scope walker). Stamp each
  emitted text ContentElement with `mcid`, `mc_tag`, `actual_text`, `is_artifact`, `oc_hidden`. **ActualText
  override** (ISO 32000-2 §14.9.4): within an ActualText scope, the element's logical text used by `find()`'s
  flat string is the ActualText value, not the per-CID decode (fixes find on glyph-remapped runs). **/OC
  gating**: resolve OCG/OCMD against `/OCProperties /D` once; OFF-layer elements `oc_hidden=True`, excluded
  from find by default. Stack lives OUTSIDE q/Q save/restore and outside BT/ET reset (per spec). Underflow/
  depth guards. TextMatch gains `mcid`/`marked_content_tags` (additive).
- **Build-vs-buy**: build_own (pikepdf has no tagged-PDF API, issue #461; all primitives probe-confirmed on
  10.5.1; no new dep).
- **Full/phased**: FULL (additive — text with no marked content sees `()` tags, behavior unchanged). Shared
  primitive consumed by D.2 + D.4.
- **Tests**: BDC/EMC balance + underflow guard; ActualText overrides decode in find; OC-hidden excluded;
  artifact flagged; DP/MP emit marked_point; no-marked-content byte-identical regression.
- **Effort L · risk medium. New deps: none. Deps: Rank 1 (Properties scope walker).**

#### D.2 — Rank 4: Structure-tree / ActualText honesty (Phase 1 + MCID-reuse Phase 2)
- **Current**: every edit-completion site mutates + saves without touching the tag tree; `FidelityReport`
  has no `tags_preserved`; `DegradationKind` has no structure kind. Verified live on pikepdf 10.5.1: full
  chain `is_tagged → page.StructParents → NumberTree(ParentTree)[k] → StructElem array by MCID → read/rewrite
  ActualText → save/reopen` persists; K-resolution over int/MCR/array/OBJR; `pikepdf.String('CJK')` auto-
  promotes to UTF-16BE-with-BOM.
- **Approach**:
  - **Phase 1**: `is_tagged`, `resolve_structelems_for_page`, `mcids_of` (int/MCR/array/OBJR-ignore/Pg-guard;
    **guard null array entries** per ISO §14.7.5.4), `correlate`, centralized `_reconcile_structure` before
    each `_save_pdf` (~9 sites). Untagged → no-op. Update ActualText on the StructElem OR its BDC property list;
    `tags_preserved` field; `structure_desynced` on mcid-crossing/unresolved.
  - **Phase 2 (re-scoped IN — edited-RUN text repair, NOT a tree rewrite)**: D1 — StructElems with no
    ActualText (Word/LibreOffice): repair = guarantee MCID preservation; reflow re-brackets rebuilt Tj/TJ
    inside the SAME BDC/EMC via **MCID-REUSE** (ParentTree/StructElem `/K` stay valid by construction, no
    ParentTree rewrite). D2 — multi-MCID joined ActualText recomputed. Still REFUSES (`structure_desynced`)
    for re-parent edits, new-MCID inserts (tagged authoring, out of scope), delete-orphans.
- **Build-vs-buy**: build_own (pikepdf NumberTree + raw K-resolution in surgeon's pikepdf-only zone; no new dep).
- **Full/phased**: FULL for Phase 1 + re-scoped Phase 2; tagged-PDF AUTHORING honestly refused.
- **Tests**: ActualText-on-elem update; LibreOffice no-ActualText MCID-bracket preserved; reflow grow-rewrap
  stays in same BDC/EMC; K-array+MCR; OBJR ignored; mcid-crossing → `tags_preserved=False`+`structure_desynced`;
  untagged no-op; insert/delete on tagged → `structure_desynced`. Real Word + Acrobat tagged PDFs. Differential:
  re-walk ParentTree, no dangling MCID.
- **Effort L · risk medium. New deps: none. Deps: D.1 (MCID stack), Rank 1.**

#### D.3 — Length-changing-edit MCID integrity (repair-or-refuse)
- **Current**: same-length splices keep MCIDs stable; reflow removes whole BT/ET blocks (`reflow.py:611-647`)
  and inserts an UNTAGGED block (`reflow.py:935`) → BDC/MCID deleted, replacement untagged, StructElem `/K`
  dangling.
- **Approach**: classify the edit — same-MCID/same-count (ActualText sync only); single-op different-length
  (no count drift, ActualText sync); count-changing/reflow → sub-classify: no-MCID-in-span (safe);
  single-sequence text sub (REPAIR by **re-emitting the original MCID** inside the rebuilt block — reuse, not
  renumber, so ParentTree stays valid); else REFUSE (`structure_invalidated` error) with an optional
  `allow_structure_break` clean-downgrade path (`structure_downgraded` warning, prune the dangling `/K`).
  PROBE-FIRST GATE: confirm pikepdf round-trips BDC/EMC + property-list operands before building.
- **Build-vs-buy**: build_own (MCID-reuse, NOT a ParentTree rewrite; pikepdf NumberTree; no new dep).
- **Full/phased**: FULL via MCID-reuse (avoids the deferred full-tree rewrite). **Tests**: same-length
  preserves MCID; reflow-repair reuses MCID (one BDC wraps all rebuilt lines, ParentTree slot + StructElem
  `/K` unchanged, `structure_preserved` info); multi-sequence refuses (`structure_invalidated`; force →
  `structure_downgraded` + valid PDF); untagged zero-overhead byte-identical. Optional veraPDF gate.
- **Effort L · risk high. New deps: none. Deps: D.1, D.2 (page-stream MCIDs; XObject MCIDs → after Rank 1).**

#### D.4 — Optional-content (OCG) layer awareness
- **Current**: zero OC handling (grep OCProperties/OCG/OCMD = 0); `find()` returns ALL runs incl. invisible
  OFF-layer text; structural/wrapper edits on hidden text. `_handle_do` ignores XObject `/OC`.
- **Approach**: pikepdf-only `optional_content.py` leaf — `resolve_visibility(pdf) -> OCVisibility` (parse
  `/D` BaseState⊕ON⊕OFF; OCMD `/P` AnyOn/AllOn/AnyOff/AllOff; `/VE` → ambiguous; key OCGs by objgen). Reuse
  the D.1 BDC/EMC stack to compute current on/off/ambiguous (AND of layers). `find(..., include_hidden=False)`
  excludes OFF chars from flat-string + matches. Edit on OFF → `optional_content_layer` (error) refuse; mixed
  bbox → edit only ON + warn; ambiguous → proceed + warn. XObject stream-dict `/OC` ORed in.
- **Build-vs-buy**: build_own (no per-run-visibility library; pikepdf dict-walk; no new dep). ISO 32000-2
  §8.11 verified.
- **Full/phased**: FULL (`/VE` honest-ambiguous; non-default Configs out of scope — file edit uses `/D`).
  **Tests**: OFF-layer (find hides + edit refuses), BaseState-OFF algebra, OCMD-AnyOff policy, nested AND,
  XObject `/OC`, `/VE` ambiguous; no-OCProperties byte-identical regression.
- **Effort L · risk medium. New deps: none. Deps: D.1 (shared stack), Rank 1 (XObject /Properties), Rank 5.**

### Block E — Typographic-quality reflow (the prior plan's biggest under-reach, now IN)

#### E.1 — Rank 9: Justified-text reflow + alignment knob (per V1: detector is heuristic, calibrate FPR)
- **Current**: `_build_replacement_ops` hardcoded left-aligned (`reflow.py:685-937`); `_encode_line_as_tj`
  emits per-glyph TJ for CID (`:650-682`); negative TJ widens gap (`state.py:174`); Tw inert for 2-byte CID
  (`state.py:191`). No detector, no alignment param.
- **Approach**: `_detect_alignment(paragraph)` pure function over existing per-line right edges + `right_margin`
  — JUSTIFIED iff non-last lines reach the right margin within `RIGHT_EDGE_TOL` AND ≥1 non-last line was
  actually stretched (condition iii guards coincidental flush) AND ≥2 interior lines (last line excluded —
  justified paragraphs leave it ragged). `_distribute_slack(n_gaps, total_slack)` even split + deterministic
  remainder on first k gaps (zero drift). CID path: insert NEGATIVE TJ per inter-word gap; simple path: `Tw`
  bracket (valid for single-byte 0x20) reset to 0. RIGHT/CENTER = pure positioning shift. `alignment`/
  `right_margin` kw-only params (default left = byte-identical). `alignment_preserved` (info), `alignment_lost`
  (warning, single-word lines). **CALIBRATION (V1)**: the detector is heuristic (the cloned S5 detector is
  FP=0/recall=64% on its corpus); state an explicit target FPR (re-justifying left-aligned text is the worst
  failure) and corpus-calibrate `RIGHT_EDGE_TOL` like the S5 thresholds; explicit `alignment='justify'` always
  honored.
- **Build-vs-buy**: build_own (no MIT/MPL in-place justification lib; PyMuPDF/borb AGPL + re-render; pure
  arithmetic over existing primitives; no new dep).
- **Full/phased**: FULL (justified/right/center/ragged-last-line); RTL *alignment* in (positioning), RTL
  *shaping* is B.10 (0.2.0). **Tests**: detector unit (justified/coincidental-flush/right/center/single-line);
  slack sum==−slack_fu exactly; last-line ragged; round-trip CID + simple right-edge within TOL; left-twin no
  accidental re-justification; two-renderer right-edge proof (`.diagnostic-venv` pypdfium2 + PyMuPDF as
  verification oracle only) with a LEFT-aligned CONTROL that must fail the metric; INV justified-preserved-or-
  alignment_lost.
- **Effort M · risk medium. New deps: none.**

#### E.2 — First-line / hanging / flush indent geometry preservation
- **Current**: `_compute_x_mode` (`reflow.py:151-165`) collapses to ONE `left_margin` = mode of per-line
  x-starts → first-line/hanging indents discarded as noise. Only the bullet `marker_x` survives (and is INERT
  on the find/replace reflow route — `reflow.py:1210` omits `style_palette`).
- **Approach**: `_detect_indent_style(x_starts, lines, font_size)` pure 3-way classifier (first_line/hanging/
  flush) with `MIN_INDENT=font_size*0.6` noise floor. `Paragraph` gains `indent_style`/`first_line_indent`/
  `hanging_indent` (defaulted). Render via the bullet path's proven absolute-Tm-per-line scheme generalized:
  line 1 at `left_margin+first_line_indent`, lines 2..N at `left_margin+hanging_indent`; FLUSH keeps the
  cheaper relative-Td (byte-identical regression). New `indent_flattened` (info) when ambiguous.
- **Build-vs-buy**: build_own (pure geometry over existing inputs; no new dep). No FOSS in-place indent lib.
- **Full/phased**: FULL (paragraph-level; per-line independent indents are Rank 10 territory). **Tests**:
  first-line / hanging / flush / single-line / ambiguous-fallback / MIN_INDENT-boundary; e2e growing edit
  re-extracts correct line-1 + continuation x; FLUSH byte-identical; INV indent-style round-trip stability.
- **Effort M · risk medium. New deps: none. Deps: Rank 9 (shared absolute-Tm scheme); Rank 1 (XObject coverage).**

#### E.3 — Original leading (TL) capture + TL+T* re-emit
- **Current**: `_leading` tracked (`state.py:282,305`) but DROPPED from `snapshot()` (`state.py:197-209`);
  reflow re-synthesizes line_height from y-gaps with a `font_size*1.2` single-line fallback.
- **Approach**: add `leading`/`leading_active` to `GraphicsStateSnapshot` + `Paragraph` (defaulted). Track
  `leading_active = _leading_is_authoritative AND _used_tstar` (the stale-register trap: TD mutates `_leading`
  even without T*). `_build_paragraph` precedence: `leading_active`+`leading>0` → use it; else multi-line gap;
  else `font_size*1.2`. When active, the in-place reflow path emits `[leading] TL` once + `[] T*` for body
  lines (bullets keep absolute Tm). Structural gets the corrected line_height VALUE but keeps literal-Td (its
  uniform-layout helpers compute-to-fit).
- **Build-vs-buy**: build_own (plumb an already-tracked value; no library; no new dep).
- **Full/phased**: FULL. **Tests**: snapshot carries leading; q/Q save/restore; single-line uses TL not 1.2×;
  multi-line emits one TL + T*; Td-only stays leading_active=False; INV TL re-emitted when source used TL+T*.
- **Effort M · risk medium. New deps: none.**

#### E.4 — Widow/orphan control post-pass
- **Current**: `break_into_lines` greedy first-fit (`reflow.py:491-575`), only a punctuation-orphan guard
  (`:561-565`); no widow check.
- **Approach**: `_apply_widow_orphan_repair(lines, width, *, measure, ...)` pure post-pass operating PER hard-
  newline segment. Widow = ≥2 lines AND last line is a single short token; pull-down the penultimate's last
  word (never grows line count; re-measure via the existing closure) IFF penultimate stays ≥`min_words`; else
  leave unchanged. NEVER grows line count (the invariant that keeps overflow math stable). Orphan suppression
  is architecturally N/A (no cross-page paragraph split).
  - **TYPED kind, committed (per critique weak-design #3)**: surface an unrepairable widow via a TYPED
    `line_break_quality_degraded` (info) DegradationKind, NOT the free-text `warnings` channel. The whole moat
    is machine-readable honesty for AI-agent consumers; routing a quality event through an untyped string while
    other quality events get typed kinds is inconsistent with the contract. `detail` carries the specifics
    (e.g. `"widow:1-word-last-line,penultimate-too-thin"`). NOT font-affecting.
- **Build-vs-buy**: build_own (~30-LOC pure function; Pyphen/K-P orthogonal; no new dep).
- **Full/phased**: FULL (orphan honestly N/A). **Tests**: pull-down happens / refused-when-penultimate-too-thin
  → emits typed `line_break_quality_degraded` / width-gated / punctuation-not-joined / hard-newline-not-widow /
  line-count invariant (the load-bearing probe); e2e overflow unchanged; J-5 canonical set includes the new kind.
- **Effort S · risk low. New deps: none.**

#### E.5 — Rank 7: Hyphenation in the greedy breaker (Pyphen)
- **Current**: greedy first-fit, no hyphenation; no Pyphen dep; width oracle `_measure_word`/`_get_space_width`
  present.
- **Approach**: add `pyphen>=0.17,<1` (pure-Python, adopt under its MPL-1.1 option). Hook the ELSE at
  `reflow.py:566-570`: when a word overflows, `split_to_fit` via `pyphen.iterate` (longest head that fits with
  the hyphen char, honoring left≥2/right≥2). Hyphen char rides the EXISTING `extend_subset` path (break runs
  before can_encode). Per-language **permissive allowlist** `_PERMISSIVE_HYPH_LANGS` (en_US/en_GB BSD, es via
  MPL disjunctive); **fr/de LibreOffice dicts are LGPL-only** (verified) → NOT shipped in the MIT wheel →
  graceful `hyphenation_unavailable` + greedy fallback (or an optional `[hyphenation-extra]` group). Language
  from catalog/page `/Lang` (TR35 fallback). New kinds `word_hyphenated`/`hyphenation_unavailable` (info).
- **Build-vs-buy**: use_library (Pyphen the engine; build_own the allowlist + hook + Degradations). Reject
  build-own Liang patterns (Pyphen wraps the canonical LibreOffice patterns).
- **Full/phased**: FULL (LGPL-dict languages excluded on license grounds, not technical). **Tests**: long
  compound word splits / no-regression with hyphenator=None / no-infinite-loop / left-right honored / language
  gate (en default, de→unavailable, es permitted) / hyphen-glyph extension; allowlist license-audit guard.
- **Effort M · risk low. New deps: `pyphen>=0.17,<1` (MPL, reflow module only).**

#### E.6 — line_height_compressed Degradation ACTUALLY emitted (per V7)
- **Current**: `line_height_compressed` declared at `models.py:67`, NEVER emitted (the test scaffold says
  "deferred to v0.2.0"). Two real compression sites: inline overflow-fit `structural.py:1244-1248`; clamp
  cascade in `compute_uniform_layout` `:733-742`.
- **Approach**: ratio = compressed/natural; ≥0.98 no-emit, 0.90–0.98 info, <0.90 warning (mirrors Algo A
  deadzone). Inline site appends to the existing `shift_degradations` list (no signature change). Clamp site:
  add a PURE `compute_uniform_layout_detailed` returning natural_line_height (keeps INV-F-5 purity); the IMPURE
  `_auto_compute_layout`/`batch_replace_block` emits via the `coverage_tier_log` out-param idiom and fans the
  region-wide Degradation into every per-section EditResult. NOT font-affecting.
- **Build-vs-buy**: build_own (~32 LOC; pure-core/impure-shell; no new dep).
- **Full/phased**: FULL (the purity tension the prior deferral cited is resolved by the detailed-sibling +
  out-param). **Tests**: detailed returns natural; min-clamp fires ratio<1 → info/warning by threshold; roomy
  → no Degradation; INV-F-5 still pure; inline + clamp e2e; fan-out into every section; font_preserved True;
  dry_run parity.
- **Effort M · risk low. New deps: none.**

#### E.7 — CJK UAX #14 line-break segmentation (stdlib, no shaping)
- **Current**: `break_into_lines` splits on ASCII space only; CJK is one unbreakable "word" → silent overflow.
  Tw inert for CID. `unicodedata.east_asian_width`/`category` available (15.0.0).
- **Approach**: new stdlib `linebreak.py` — `segment(text, thai_breaker=None) -> list[BreakAtom]` implementing
  the ~8 UAX #14 classes that occur in body text (ID via eaw W/F + letter; CL/CP/NS no-break-before; OP
  no-break-after; CM glued; SP; mandatory). Break between any two ID ideographs (CJK needs NO shaping —
  refutes the prior plan's CFF/uharfbuzz conflation). `break_into_lines` consumes atoms with a segmentation-
  aware join (space for AL, empty for ID). Thai/Lao/Khmer (SA class) REQUIRES dictionary analysis → optional
  Apache-2.0 PyThaiNLP via callback under `[thai]` extra; absent → `scriptless_reflow_unsupported` + spec-
  sanctioned AL fallback (never truncates). `/Lang` shared with Rank 7.
- **Build-vs-buy**: build_own for CJK (stdlib `unicodedata`, no dep); optional PyThaiNLP for SA (callback,
  no core dep, lands only in `linebreak.py`).
- **Full/phased**: FULL for CJK; SA honest-refusal + optional extra (UAX #14 itself mandates dictionary
  analysis + AL fallback). **Tests**: classify golden table; pure-Latin atoms == str.split (regression lock);
  CJK overflow now multi-line; Thai no-breaker → one `scriptless_reflow_unsupported` non-truncated; e2e
  Japanese paragraph reflows (font-probe skipif); INV not-font-affecting / font_preserved True.
- **Effort L · risk medium. New deps: optional `pythainlp>=4` under `[thai]` extra (Apache-2.0); core none.**

#### E.8 — Shrink-to-fit: font-size auto-reduction as a length-change policy (NEW — closes critique missed item #2)
- **Current** (V17): the length-change story is reflow (Block E) + overflow Degradations. `compute_uniform_layout`
  (`structural.py:711-742`) clamps LINE-HEIGHT only (`min_line_height = font_size*1.05`, reduces `section_gap`,
  never the font size). There is no caller-facing way to fit longer replacement text into a FIXED region (table
  cell, form field, fixed-width heading) by REDUCING the font size — the Acrobat/Word "shrink text to fit" and
  AcroForm `/Q` auto-size behavior. Today the engine either reflows (changes layout / line count) or overflows/
  refuses, when shrink-to-fit would preserve fidelity best for a fixed region.
- **Approach** (a caller-selectable fit policy, NOT a new verb — it tunes existing `replace_block`/reflow):
  - `fit: Literal['reflow','shrink','overflow'] = 'reflow'` kw-only param on the bbox/region edit paths
    (`replace_block`, `batch_replace_block`) and an analogous knob where a fixed-width region is known.
    Default `'reflow'` = today's behavior (byte-identical regression).
  - `'shrink'`: binary-search the largest font size in `[min_pt, original_pt]` (default `min_pt = max(4.0,
    original_pt*0.5)`) at which the measured replacement text fits the region's width AND height, using the
    EXISTING width oracle (`_measure_word` / glyph-width tables) and the line-count math. Re-emit at the
    reduced size (`Tf` size operand changes; positioning unchanged). When even `min_pt` overflows → fall back
    to `'reflow'` (or `'overflow'` if reflow is N/A) and emit the existing overflow Degradation.
  - New kind `font_size_reduced` (info, NOT font-affecting — the FONT identity is preserved, only the size
    knob changed; `detail` = `"12pt->9.5pt,fit=shrink"`). Emit `font_size_reduced` whenever the size drops; if
    it hits `min_pt` and STILL overflows, the existing overflow Degradation also fires (honest: "shrunk to
    floor and still does not fit").
  - This is a length-change ALTERNATIVE that preserves the region's geometry (no reflow, no layout shift) —
    exactly the Acrobat shrink-to-fit semantics, surfaced as an honest typed policy rather than a silent guess.
- **Build-vs-buy**: build_own (binary search over the existing width oracle + the existing `Tf` re-emit; no new
  dep, no boundary change). No FOSS in-place shrink-to-fit library; PyMuPDF/borb AGPL + re-render.
- **Full/phased**: FULL (the three-way `fit` policy + floor + honest fallback). **Tests**: longer text fits at
  reduced size (reopen, measured width ≤ region, single line preserved); `font_size_reduced` emitted with
  correct before/after; floor-hit still-overflows → both `font_size_reduced` AND overflow Degradation;
  `fit='reflow'` default byte-identical regression; `fit='overflow'` parity with today; dry_run parity;
  size never below `min_pt`.
- **Effort M · risk low. New deps: none. (Generalized into the 0.3.0 ReplaceAdjustment policy enum.)**

### Block F — Color / graphics-state fidelity (ONE co-designed GraphicsStateSnapshot extension)

> All of F.1–F.5 extend `GraphicsStateSnapshot` + replay on the rebuild path (reflow/structural). The
> same-stream surgeon path preserves all of these BY CONSTRUCTION (it splices operands in place). Design
> the snapshot extension ONCE to avoid repeated dataclass churn.

#### F.1 — Spot/Separation/DeviceN/ICCBased fill-color-space capture + verbatim replay
- **Current**: only re-emission site `reflow.py:749-756` guesses operator by component count (1→g/3→rg/4→k);
  captured value is `fill_color: tuple[float,...]` with NO space name; state.py has no cs/CS handler;
  `_handle_sc` drops the trailing Pattern Name via `_safe_floats`.
- **Approach**: capture `fill_cs_name` + raw `fill_color_ops` (verbatim operand pairs incl. Name) in the
  tracker (cs/scn handlers; q/Q-persist; verified pikepdf round-trips Names AND the trailing `/P0`). Replay
  verbatim on rebuild when a non-device space was captured; byte-identical device fast path otherwise
  (regression-safe); `color_space_approximated` (warning) when the cs/Pattern resource is unresolvable →
  device fallback. New kind. NOT font-affecting (font_preserved unaffected). Thread through
  `_detect_font_from_elements` + both `_build_replacement_ops` call sites + insert path.
- **Build-vs-buy**: build_own (verbatim operand replay via pikepdf parse/unparse — the only primitive needed;
  no ICC conversion; no new dep). PyMuPDF/borb AGPL.
- **Full/phased**: FULL (overprint replay gated as an explicit follow-on). **Tests**: Separation/DeviceN/
  ICCBased replay verbatim (not collapsed to device); Pattern Name survives or `color_space_approximated`;
  device_gray byte-identical regression; q/Q-separation survives the boundary; dry_run parity.
- **Effort M · risk low/medium. New deps: none.**

#### F.2 — Pattern / shading text-fill capture + replay
- **Approach** (folds into F.1's capture): `scn /PatternName` and `sh` recorded; verbatim cs+scn replay is
  geometrically correct for page-level text (ISO 32000-1 §8.7.3.1 — pattern `/Matrix` is parent-stream-
  default-CS-relative, independent of the text matrix); UNSAFE for relocated XObject text or bare `sh` →
  `pattern_fill_flattened` (warning). Resource-resolution guard before replay (refuse if `/Pattern/<name>`
  no longer resolves).
- **Build-vs-buy/Full**: build_own; FULL (page-level replay) + honest refuse for relocated/sh. **Tests**:
  tiling-pattern text reflow replays `/P0 scn`; XObject-relocation → `pattern_fill_flattened`; `_safe_floats`
  Name-drop fixed.
- **Effort M (shared with F.1) · risk medium. New deps: none.**

#### F.3 — Text rendering mode (Tr) re-emit + clip-mode refusal
- **Current**: Tr tracked (`state.py:307-308`) but DROPPED from snapshot; rebuild never emits Tr → mode-0.
- **Approach**: add `rendering_mode` to snapshot; emit `[mode] Tr` after BT when non-default and not a clip
  mode. Clip modes 4–7 add glyphs to the clip path consumed by DOWNSTREAM ops → re-layout corrupts the clip →
  REFUSE (`text_clip_mode_unsupported`, error) rather than silently emit mode-0. Modes 1/2/3 preserved
  (stroke color caveat documented). Default-0 elision keeps the common case byte-identical.
- **Build-vs-buy/Full**: build_own; FULL (0–3 preserve, 4–7 refuse). **Tests**: mode-3 invisible re-hidden;
  mode-1 outlined; mode-0 no Tr emitted; clip mode → refuse + no mode-0 over the clip region.
- **Effort S · risk low. New deps: none. Deps: F.1 (shared snapshot).**

#### F.4 — Text rise (Ts) PER-RUN segmentation
- **Current**: Ts in `_STATE_OPS` but no handler in state.py; not in snapshot; reflow never emits Ts →
  superscripts/subscripts collapse to baseline.
- **Approach**: re-track Ts (mirror Tr); `TextCharacter.text_rise` + snapshot field. Ts is INTRA-RUN not
  intra-paragraph — segment each reflowed line into same-rise runs, emit `Ts` deltas between runs + a trailing
  `0 Ts` reset (else rise leaks). `text_rise_approximated` (info) when a length-changing edit splits a rise run.
- **Build-vs-buy/Full**: build_own; FULL (per-run, not paragraph-level). **Tests**: footnote-marker superscript
  + subscript retain offset, surrounding baseline unchanged; q/Q round-trip; no-rise control byte-identical;
  same-stream surgeon untouched; INV trailing-reset.
- **Effort M · risk low. New deps: none. Deps: F.1 (shared snapshot).**

#### F.5 — Stroke-color/mode + ExtGState (gs) replay
- **Approach**: capture stroke color + the cs/CS name for stroked text (modes 1/2); ExtGState `gs` resolved
  against `/Resources/ExtGState` → `ca/CA/BM/OP/op/OPM` + SMask presence captured (q/Q-persist). Rebuild
  re-emits `/GSx gs` (re-using the existing resource entry on the same page). Verbatim replay is lossless for
  alpha/blend/overprint; SAME-position SMask replays; REPOSITIONED SMask → `soft_mask_realignment_unsupported`
  (warning) refuse (a soft mask bound to original text coords can't follow moved glyphs). `stroke_color_dropped`
  (warning) when a stroking mode lacks a captured stroke color.
- **Build-vs-buy/Full**: build_own; FULL (alpha/blend/overprint + same-position SMask; repositioned-SMask
  refuse). **Tests**: mode-1 stroke color survives reflow; `gs` ca/BM survives; watermark alpha non-opaque
  post-edit; SMask reposition → refuse; same-position SMask replays; stroke-state q/Q balance.
- **Effort M · risk medium. New deps: none. Deps: F.1 (shared snapshot).**

### Block G — Semantic text surfaces (depth on existing edit paths, no new verbs)

#### G.1 — Simple-font (non-CID) Tier 1.5 /ToUnicode synthesis
- **Current**: `_extend_simple_tier_one_five` updates `/Differences` + `/Widths` but NOT `/ToUnicode`
  (`fonts.py:1129-1131`) → newly-edited chars render yet are unextractable/uncopyable by external extractors
  (Acrobat/Chrome/pdfminer prefer `/ToUnicode`).
- **Approach**: factor `_splice_bfchar_into_cmap` shared by CID + simple paths (kills the asymmetry at the
  root). For both group A (healed) and group B (new) byte→char assignments: APPEND bfchar when `/ToUnicode`
  exists; SYNTHESIZE a minimal spec-valid `/ToUnicode` from the effective byte→unicode map when absent
  (over-delivers vs the CID path). `untextable_simple_font` (warning) only for non-round-trippable glyph names
  (PUA). Reuse `_parse_existing_tounicode` + the `_append_to_unicode_cmap` mechanics.
- **Build-vs-buy**: build_own (reuse + generalize in-repo helpers; no new dep; lands in fonts.py — encoding
  untouched).
- **Full/phased**: FULL. **Tests**: append branch (Word-style with /ToUnicode), heal branch, synthesize branch
  (no /ToUnicode → stream now exists), pdfminer round-trip (external-extractor proxy), dedup idempotence,
  PUA boundary → `untextable_simple_font`, dry_run parity. Real Word/LibreOffice resume gate.
- **Effort M · risk low. New deps: none. (Shares splice helper with Rank 2.5.)**

#### G.2 — AcroForm field-value (/V) findability + /AP /N regeneration
- **Current**: `get_annotations` reads only `/Subtype,/Rect,/A/URI,/Contents`; `find()` never visits
  `/Annots` → field text invisible. `fill_form` sets `/V` + `NeedAppearances=True` but NEVER regenerates
  `/AP /N` → /V↔/AP desync. pikepdf 10.5.1 ships a `pikepdf.form` API.
- **Approach**: `_collect_form_field_elements` via `pikepdf.AcroForm.get_form_fields_for_page` → synthesize
  text ContentElements (Rect bbox, `value_as_string`) merged into find/get_text, tagged with an annot locus.
  Edit via `pikepdf.form.Form` + a generator: WinAnsi/MacRoman simple → `ExtendedAppearanceStreamGenerator`
  (qpdf-native); Identity-H CIDFont `/DA` → NEW `CidFontAppearanceGenerator` reusing `encoding.encode` +
  `fonts.extend_subset` + surgeon-style content-stream write (qpdf CANNOT do CID appearance). Set
  `NeedAppearances=False` after real regen; flatten guard. CFF `/DA` → honest `form_appearance_stale`
  (blocked on Block C). New kinds `form_appearance_regenerated` (info), `form_appearance_stale` (warning).
- **Build-vs-buy**: extend_library (pikepdf.form for value+simple appearance; build the CID generator on the
  engine's own font pipeline; no new dep). PyMuPDF/borb AGPL.
- **Full/phased**: FULL for find + WinAnsi/MacRoman + Identity-H regen; CFF `/DA` honestly refused (blocked on
  Block C). **Tests**: find('<field value>') returns a match (today 0); /V↔/AP sync round-trip (reopen,
  `/AP` decoded == /V); CID field regen via the engine font path; signature/password/rich-text → refuse;
  fill_form back-compat regression; flatten guard.
- **Effort L · risk medium. New deps: none. Deps: Block C (CFF /DA), Rank 1 (XObject scope), Rank 5.**

#### G.3 — Non-form annotation appearance-stream text (FreeText/Stamp/Redaction /AP /N) + /Contents sync
- **Current**: annotation `/AP` text invisible to find/edit; `flatten_annotations` `del /Annots` DESTROYS
  appearance text (`wrapper.py:373-391`).
- **Approach**: generalize the interpreter off-Page (shared with Rank 1) to run over the `/AP /N` Form XObject
  with the appearance's own `/Resources` scope chain; find + in-place operator edit via `appearance.write(...)`;
  **/Contents↔/AP sync** (ISO 32000-2 §12.5.5: `/AP` renders, `/Contents` is the AT/extraction text). FreeText
  `/RC` rich-text can't round-trip from glyph edits → drop /RC to /DA-plain + `annotation_richtext_dropped`
  (info) keeping /Contents==/AP consistent. Redaction edits `/OverlayText`+`/AP`. Desync-impossible (image-only
  /AP, multi-substate ambiguous) → `annotation_appearance_desynced` (warning) refuse. **True text-preserving
  flatten** (`q/cm/Do/Q` bake-in + Resources registration) replacing the destructive strip;
  `annotation_flatten_text_lost` for unmappable. `Annotation` gains `appearance_text`/`contents_matches_appearance`.
- **Build-vs-buy**: extend_library (Rank 1 off-page interpreter + scope chain; pikepdf-only; reuses
  fonts.extend_subset if the appearance font needs a glyph; no new dep).
- **Full/phased**: FULL for FreeText/Stamp/Redaction-overlay; image-only/Type3 /AP honestly refused. **Tests**:
  find FreeText caption (today 0); replace updates /AP AND /Contents; Stamp /Contents synthesized; Redaction
  overlay+/AP+/Contents consistent; appearance-local-font cross-font canary; multi-substate ambiguous refuse;
  image-only refuse; flatten preserves text (today GONE); /Contents-disagrees honesty flag.
- **Effort L · risk medium. New deps: none. Deps: Rank 1 (hard), Rank 5 (soft), Block C (CFF /DA).**

### Block H — Scale / memory

#### H.1 — Bound + stream the whole-document ContentElement index
- **Current**: module-global `_cached_elements: dict[int, list]` (`locator.py:538`) holds N pages of indices
  simultaneously during a whole-document call; `find` accumulates ALL matches; `replace_all` materializes all
  matches up front (`surgeon.py:1375`). One-glyph-per-op PDFs make each page ~one ContentElement+TextCharacter
  per glyph. Only cap anywhere is `MAX_COMPOSITE_DEPTH`.
- **Approach**: replace the unbounded dict with a 1-slot window keyed on `(path, page_number)` — `_build_index`
  overwrites the slot, evicting the prior page's index for GC the moment the loop advances. Stream get_text/
  get_text_layout per-page (heavy index dropped before the next page). Deterministic ceilings
  `MAX_PAGES_SCANNED`/`MAX_ELEMENTS_SCANNED` (count-based, no psutil at runtime; tuned AFTER Rank 1 XObject
  recursion so the count includes recursed elements). Read paths raise a typed `PDFEditError` at the ceiling
  (silent partial = lying success); edit paths emit `document_scan_truncated` (warning). Update both
  `_invalidate_locator_cache` sites.
- **Build-vs-buy**: build_own (internal cache lifecycle; pikepdf pages are lazy; no new dep). 1-slot window
  (each page visited once per loop) over LRU (no hit-rate gain).
- **Full/phased**: FULL (A bounded-window + C ceiling ship together; either alone is incoherent). **Tests**:
  large-doc memory regression (tracemalloc peak ≈ single-page, fails against the global-dict); eviction unit
  (slot holds exactly one page); cache-correctness regression; ceiling → typed error (read) / Degradation (edit);
  ceiling-override; INV "whole-doc op is O(single page) resident".
- **Effort M · risk medium. New deps: none. Deps: Rank 1 (ceiling tuning).**

---

## 5. Build-vs-buy summary (reflecting VERIFY corrections)

| Item | Verdict | Tool / approach | Why (and VERIFY correction applied) |
|---|---|---|---|
| Parse/unparse translation | build_own | `_pathutil` translators | Internal; pikepdf-only; no dep |
| q/Q + font-stream + index bounds | build_own | constants + pre-gate | Internal DoS bounds |
| Signature / linearization / encryption | build_own | pikepdf-native | `is_linearized`/`password`/`encryption=True` plumbed; preservation of sig unbuildable |
| Rank 5 width-cache | build_own | objgen key + RETAIN evict | In-place /W does not change objgen → evict still required |
| Rank 1 XObject scope chain | build_own | explicit `/Parent` walk | **V1: `Page.resources` does NOT walk inheritance**; 14 sites not 9 |
| Rank 2.5 recovery | build_own | reverse cmap + PUA gate + write-path fix | **V8: write path crashes today; PUA → silent garbage** |
| Rank 2 Type0 named-CMap DECODE + Identity-V read/write | extend_library | pdfminer `CMapDB.get_cmap`→`PyCMap.decode` (already imported) | **V9: dep-table already contradicted — reconcile**; **V6/V13: decode VERIFIED LIVE `[231,633,231]`, pulled INTO 0.2.0**; **V15: same-position vertical write buildable** |
| ToUnicode read-side integrity | build_own | asymmetric reconciliation | No library validates ToUnicode vs font |
| Symbol cmap | build_own | (3,0)/(1,0) dual-lookup | fontTools has no "best symbol cmap" |
| NFC/NFD + bidi find + RTL same-len write + ligature | build_own | stdlib `unicodedata` + UBA + same-len splice | Reject python-bidi (LGPL); reject PyICU; **under-reach #2: RTL same-glyph-count write IN 0.2.0** |
| **World-script shaping WRITE (B.10)** | use_library + build_own | **uharfbuzz (Apache-2.0)** + ShapedGlyph bridge + TJ writer | **under-reach #1: TrueType+CFF shaping write IN 0.2.0** (deps all land in 0.2.0 per V14); surgeon stays pikepdf-only via plain ShapedGlyph |
| Empty-string deletion cleanup (B.11) | build_own | operator removal + compensating advance | **missed #1: V16 — no empty-string path today** |
| Rotated/sheared text (B.12) | build_own | matrix already tracked; plumb + gate | **missed #3: V19 — tracked but write discards b/c** |
| Inline-image BI/ID/EI guard (A1.4) | build_own | probe + index-integrity guard | **missed #4: V18 — unhandled; gates B.2/D.1** |
| Type1→CFF transplant (C.5) | build_own/extend_library | t1Lib + C.3 recording-pen bridge | **weak-design #1: V20 — buildable, not a flat refuse** |
| Shrink-to-fit (E.8) | build_own | binary search over existing width oracle | **missed #2: V17 — clamp is line-height-only today** |
| CM-2 dispatch / glyph_count | build_own/extend_library | table-presence sniff; cffLib/t1Lib | **V11: slot-collapse real** |
| Rank 3 CFF injection | build_own | cffLib + DecomposingRecordingPen + T2CharStringPen | **V4: `setGlyphOrder(list(charset))`, NOT manual maxp/hhea bump**; seac via decomposing pen |
| Rank 6 UPEM rescale | use_library | `scale_upem` (donor) | **V5: scale_upem DOES cover CFF**; whole-donor fixes latent width bug |
| Marked-content / Rank 4 / MCID / OCG | build_own | pikepdf NumberTree + raw K-resolution | pikepdf has no tagged-PDF API; MCID-reuse avoids tree rewrite |
| Rank 9 justified | build_own | negative-TJ slack | No MIT/MPL in-place justification; detector is heuristic — **V1: calibrate FPR** |
| Indent / leading / widow / line_height_compressed | build_own | pure functions over existing inputs | **V7: line_height_compressed declared-but-unemitted** |
| Rank 7 hyphenation | use_library | **Pyphen (MPL)** | fr/de dicts LGPL-only → permissive allowlist + `[hyphenation-extra]` |
| CJK segmentation | build_own | stdlib UAX #14 | CJK needs NO shaping (refutes the prior CFF/uharfbuzz conflation); SA → optional PyThaiNLP `[thai]` |
| Color/Tr/Ts/stroke/ExtGState | build_own | snapshot capture + verbatim replay | No FOSS in-place color re-emission; PyMuPDF/borb AGPL |
| Simple-font /ToUnicode synth | build_own | shared `_splice_bfchar_into_cmap` | Symmetric completion of an existing path |
| AcroForm / annotation /AP | extend_library | pikepdf.form + Rank 1 off-page interpreter | qpdf cannot do CID appearance; build the CID generator on our pipeline |
| **0.3.0: complex-script REFLOW** | build_own | cluster-aware UAX #14 + CTM-composed cluster widths | Multi-line shaped reflow needs cluster≠codepoint line breaking + the CTM step Rank 10 builds (B.10 ships single-region shaping in 0.2.0) |
| **0.1.6: Knuth-Plass** | build_own | ~150-LOC stdlib DP | texlib MIT but abandoned — reference only, do NOT vendor |

---

## 6. New dependency analysis (dep-boundary compliance)

CLAUDE.md dep table: locator=pikepdf+pdfminer; surgeon=pikepdf-only; fonts=fonttools+pikepdf;
reflow=fonttools+pikepdf; encoding=NO fonttools (lazy-import boundary); `_pathutil.open_pdf` is the
only `pikepdf.Pdf.open` site.

**Reconciliation first (V9)**: the table's "pdfminer ONLY in locator" is ALREADY contradicted by checked-in
`encoding.py:9-10` + `fonts.py:14`. M0 updates the table to mark encoding+fonts as sanctioned pdfminer zones
(they only consume `pdfminer.cmapdb`/`encodingdb`/`glyphlist` for CMap/encoding tables, never content-stream
parsing), so Block B's pdfminer use is not a new violation.

| New dep | License | Lands in | Boundary impact |
|---|---|---|---|
| `uharfbuzz` (**0.2.0**, B.10) | Apache-2.0 (no-AGPL clean) | `shaping.py` in the FONTS dep zone (new dep-table column) | **Pulled INTO 0.2.0** (under-reach #1 / V14 — its deps all land in 0.2.0). surgeon stays pikepdf-only (consumes plain `ShapedGlyph` dataclasses — no fonttools/uharfbuzz import); encoding script-detection uses stdlib `unicodedata` only. C-extension wheel; no system HarfBuzz needed. |
| `pyphen>=0.17,<1` (E.5) | tri-license, adopt **MPL-1.1** (no-AGPL clean) | reflow module only | Pure-Python; no native dep; reflow already fonttools+pikepdf. fr/de LGPL dicts NOT shipped (permissive allowlist; optional `[hyphenation-extra]`). |
| `pythainlp>=4` **optional** (E.7) | Apache-2.0 | `linebreak.py` only, via callback under `[thai]` extra | NOT a core dep; CJK path is stdlib-only; touches no dep-table column. |

New internal pure-stdlib leaf modules (no third-party dep): `bidi.py` (B.8), `linebreak.py` (E.7),
`resources.py` (Rank 1 scope), `_signatures.py` (A2.1), `optional_content.py` (D.4). New fonts-zone leaf
`shaping.py` (B.10) imports uharfbuzz ONLY (its single third-party dep) + fontTools; surgeon consumes its
plain `ShapedGlyph` dataclasses so surgeon stays pikepdf-only. All other leaves are pikepdf-only or
stdlib-only; none breach the dep table.

**Rejected (no-AGPL / posture):** PyMuPDF, borb (AGPL); python-bidi (LGPL — replaced by stdlib UBA, and it
does not shape); PyICU (heavyweight native — `unicodedata` suffices for script detection + CJK segmentation);
littlecms (color CONVERSION, wrong — we REPLAY operators); iText pdfCalligraph (paid — uharfbuzz is the FOSS
shaper we use directly).

---

## 7. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Rank 3 CFF transplant ships corrupt /FontFile3 (the V4 recipe trap) | medium | high | Use `setGlyphOrder(list(charset))`, NOT manual maxp/hhea; reload `maxp.numGlyphs==len(charset)` probe is a hard gate; INV no-renumber on every pre-existing CID; RecordingPen outline-equality independent verifier; never `fontTools.merge`. |
| Rank 1 partial path re-introduces v0.1.0 cross-font corruption | medium | high | FULL or detect-and-refuse only; XOBJECT-LOCAL-FONT cross-font tripwire test; grep invariant (no `page['/Resources']['/Font']` literal outside scope module); land Rank 5 first. |
| Rank 9 detector re-justifies a left-aligned paragraph (worst failure) | medium | high | Condition-iii "was-actually-stretched" guard; corpus-calibrated `RIGHT_EDGE_TOL` with a stated target FPR; explicit `alignment='justify'` always honored; two-renderer right-edge proof with a LEFT-aligned CONTROL that must fail the metric. |
| Rank 2.5 silent garbage on PUA-encoded embedded cmaps (V8) | medium | high | PUA-purity gate → `untextable_cidfont`, never a silent `tounicode_recovered`; write-path `/ToUnicode` synthesis or honest refusal (never a raw KeyError). |
| B.10 shaping write blocked if C.3 CID-keyed CFF slips | medium | medium | B.10's TrueType path is independent of CFF (the glyf injector exists today); only CFF complex-script needs C.3. If C.3 slips, CFF complex-script degrades to `shaping_unsupported` (honest) while TrueType shaping still ships — the pillar is NOT all-or-nothing. uharfbuzz no-shaping fallback is DETECTABLE (shaped==unshaped cluster) → emit `shaping_unsupported` not silent unjoined output. |
| B.10 emits a shaped glyph absent from the embedded subset | medium | high | Inject via the EXISTING injector indexed by GID (shaper ran against the same font bytes → GIDs align); render spot-check (pypdfium2) every shaped fixture; `shaping_unsupported` refuse if a required GID cannot be injected (CFF2/missing donor). |
| B.4 `_iter_cids` refactor regresses the Identity-H read hot path | medium | high | The variable-width chunker must be byte-identical to the fixed-2-byte path on Identity-H input (the dominant case) — an explicit byte-identical regression probe gates the refactor; named-CMap decode is additive behind the `type0_kind` classifier. |
| Table reflow (0.3.0) "verbatim primitive reuse" premise (V10 refuted) | high | medium | Defer to 0.3.0; build NEW CTM-composition + CTM-aware shift first; re-run build-vs-buy survey as a kill-switch; regime-specific column inference (ruling post-CTM / x-cluster / filled-rect borders). |
| B.11 deletion mis-splices trailing-text positioning | medium | medium | Compensating-advance is the inverse of the documented displacement formula; golden trailing-x-coord round-trip test; cross-boundary (marked-content / inline-image / clip) → `deletion_residual_text` refuse rather than risk a mis-splice. |
| A1.4 inline-image probe FAILS (parse_content_stream mis-indexes BI/ID/EI) | medium | high | A1.4 is GATING for B.2/D.1; if the probe shows index drift, A1.4 escalates from a guard to an interpreter fix BEFORE the invasive traversals ship — surfaced as a halt-and-flag, not a silent assumption. |
| DegradationKind schema churn across ~35 new kinds | high | low | Each kind: widen Literal + extend J-5 canonical set + J-8 FONT_AFFECTING_KINDS where applicable + decisions.md row; fix the stale "twelve" docstring (`models.py:97`) in M0; add an explicit `get_args(DegradationKind)` count probe. New v3 kinds beyond v2: `inline_image_present`, `rtl_write_unsupported`, `text_shaped`, `shaping_unsupported`, `deletion_residual_text`, `rotated_text_unsupported`, `type1_transplanted`, `type1_uncovertible`, `type1_embedded_extension_deferred`, `font_size_reduced`, `line_break_quality_degraded`, `identity_v_reflow_unsupported` (replaces v2's `identity_v_unsupported`). |
| Subscription throughput ceiling (undocumented Max caps) | medium | medium | Structure the BUILD as multiple ~5-hour sessions across days with disk-checkpointed state; reserve 1,000-agent fleets for bounded parallel verification fan-outs (differential-render every corpus PDF per phase), not the default work mode; treat weekly caps as the true ceiling. |
| Color/Tr/Ts/ExtGState snapshot dataclass churn | medium | low | ONE co-designed `GraphicsStateSnapshot` extension for all of F.1–F.5; all fields default-valued (non-breaking; only `snapshot()` passes them). |
| Memory ceiling tuned before XObject recursion exists | low | medium | Tune `MAX_ELEMENTS_SCANNED` AFTER Rank 1 lands so the count includes recursed XObject elements. |

---

## 8. Test / corpus additions (cross-cutting)

All corpus PDFs are gitignored — build via per-platform builders + skipif markers (cidfont_synthetic
precedent), NEVER committed. New builders: `_xobject_fixture.py` (shared/local-font/nested-Do),
`_cff_fixture.py` (name-keyed/CID-keyed/OpenType-glyf/CFF2/seac — CID-keyed needs no system font, no skipif),
`_tagged_fixture.py`, `_form_fixture.py`, `_signature_fixtures.py`, `_tounicode_absent_fixture.py` (incl.
PUA-only), `_pattern_fill_fixture.py`, justified + leading + symbol-font + Separation/ICC fixtures.
**v3 additions**: `_shaping_fixture.py` (TrueType Devanagari/Arabic/Thai + CID-keyed-CFF complex-script —
B.10; render-oracle vs donor), `_named_cmap_fixture.py` (90ms-RKSJ-H / UniGB-UCS2-H Type0 — B.4 decode),
`_vertical_fixture.py` (Identity-V with `/W2`/`/DW2` — B.4 read + same-position write), `_rtl_fixture.py`
(visual-stored Hebrew/Arabic Identity-H — B.8 write), `_rotated_fixture.py` (90°/sheared Tm — B.12),
`_inline_image_fixture.py` (BI/ID/EI interleaved with Tj — A1.4 probe, the GATING test), `_type1_fixture.py`
(Type1 donor — C.5), shrink-to-fit fixed-region fixture (E.8).

New invariant probe families (tests/invariants/, mint fresh INV ids): scope-chain font-identity + grep guard;
parse/unparse centralization grep guard; q/Q + font-stream + memory bounds (W0-10/11 + new); signature/
linearization/encryption (S/M series); CFF reload-maxp + no-renumber + never-merge guard; Rank 2 surfaces +
DegradationKind canonical-count; ToUnicode-recovery + PUA-untextable; symbol-cmap coverage; combining-mark
cluster; bidi logical-find; ligature fidelity; structure/MCID-reuse/OCG; justified-preserved; line_height_compressed
emission + INV-F-5 purity; color/Tr/Ts/ExtGState replay-or-refuse; simple-font /ToUnicode round-trip; AcroForm/
annotation /AP↔/Contents sync. Differential gates: pdfminer text round-trip on tagged/symbol/recovered fixtures;
two-renderer right-edge proof for justification; optional veraPDF for structure-tree integrity.

Pytest baseline progression must be stated and gated in each PR (current 824 collected / 838 post-Session-1
per `.claude/rules/block-3.md`).

---

## 9. The honest frontier (never build — detect-and-Degrade only)

These are genuinely unbuildable on the pikepdf/fonttools/pdfminer stack or out of editing-depth scope. The
FidelityReport contract makes the engine **best-in-class where buildable, provably honest everywhere else**.

> **v3 note**: the critic correctly forced four items OFF this frontier list and INTO 0.2.0 — named-CMap
> DECODE (verified bundled, B.4), Identity-V same-position WRITE (B.4), RTL same-glyph-count WRITE (B.8), and
> TrueType+CFF complex-script shaping WRITE (B.10). The list below is now ONLY items with a verified
> code-block or no document model, each cited:

- **Signature-preserving incremental save** — pikepdf consolidates incremental updates and rebuilds on save
  ([docs](https://pikepdf.readthedocs.io/)). Only honest action = `signature_invalidated` Degradation (A2.1).
- **CFF2 variable charstrings** — blend/vsindex have no transplant analogue → `cff2_unsupported` (C.1/C.3/B.10).
- **Type3 procedural (bitmap) glyphs** — out of extension scope → typed refusal.
- **Missing-GSUB complex-script shaping** — when an embedded font lacks the GSUB/GPOS tables HarfBuzz needs,
  the glyphs would render unjoined → `shaping_unsupported` (B.10); detectable (shaped==unshaped cluster).
- **Type1-EMBEDDED → CFF-embedded migration** — the additive Type1-DONOR case ships in 0.2.0 (C.5); migrating
  an embedded `/FontFile` font's outline slot to `/FontFile3` is a `/Subtype`-changing rewrite with the
  pre-existing-CID-preservation risk C.3 manages → cited follow-on (`type1_embedded_extension_deferred`), not
  a silent refuse.
- **Rotation-aware reflow re-emit** — same-length rotated splice ships in 0.2.0 (B.12); re-emitting a
  LENGTH-CHANGED rotated run with the composed `Tm` rotation is a clean follow-on → `rotated_text_unsupported`.
- **Length-changing Identity-V (vertical) reflow** — same-position vertical write ships in 0.2.0 (B.4);
  re-flowing a vertical column on length change needs a WMode-1 `_build_paragraph` analogue + `-x`
  column-advance shift → 0.3.0 (`identity_v_reflow_unsupported`).
- **Cross-page word-processor reflow** — no document-flow model exists; deepest frontier (0.1.6+ investigation).
- **Embedded-`/CMap`-stream `code2cid`** — pdfminer builds 0 entries (V6); a from-scratch codespace parser is a
  separate 0.1.6 build; until then → `unsupported_cmap`. (NAMED CMaps decode fine via `CMapDB.get_cmap` — B.4.)
