"""INV-G-10: the Latin reflow path stays byte-identical (the E.7 regression lock).

Roadmap item E.7 (CJK / UAX#14 line-break segmentation, reflow-write-side). E.7
added a CJK breaker to ``reflow.break_into_lines`` / ``reflow._join_atoms`` /
``reflow.reflow_paragraph``'s ``\\n`` re-join. INV-G-10 is the contract that the
new code MUST NOT regress the Latin path: for pure-Latin / space-delimited text
``linebreak.is_latin_simple`` is True and the existing untouched
``str.split(" ")`` atomization + ``" ".join`` re-join is used verbatim, so the
wrapped + joined output is the same bytes it was before E.7.

THE E.7 BRACKET REGRESSION. ``linebreak._char_class`` classified the ASCII
brackets ``(`` ``[`` ``{`` as ``OP`` (and ``)`` ``]`` ``}`` as ``CL`` / ``AL``),
so ``is_latin_simple`` returned FALSE for ANY Western text containing a bracket.
That routed Latin-with-bracket text through the EMPTY-string (CJK) join branch
in ``reflow._join_atoms`` (and the ``reflow_paragraph`` ``\\n`` re-join), DELETING
every inter-word space — a citation ``"references [1] and [2]"``, a parenthetical
``"evaluation (conducted across teams)"``, or a call ``"func(x) returns y"``
re-wrapped into one glued run (``"references[1]and[2]"``). Brackets are pervasive
in real Western text, so this silently corrupted mainstream reflow output.

ROOT FIX: ``_char_class`` must classify a char as ``OP`` / ``CL`` / ``CP`` /
``NS`` (the CJK no-break punctuation classes) ONLY when it is East-Asian-width
``W`` / ``F`` (fullwidth CJK brackets ``（ ） ［ ］ ｛ ｝ 「 」`` etc.). ASCII /
narrow brackets must classify as ``AL`` so ``is_latin_simple`` stays True for
Latin text containing them and the existing space-split + space-join Latin path
runs verbatim — while the CJK no-break rules (CJK brackets are W/F, still OP/CL)
are preserved.

The original INV-G-10 coverage used only BRACKET-FREE Latin strings, so the
empty-join branch was never exercised against Latin and the regression slipped
through. This file SPLITS the two INV-G-10 functions out of
``test_g_9_cjk_linebreak.py`` (one-invariant-per-file) and ADDS the missing
Latin-with-ASCII-bracket regression coverage (unit + e2e) so a future bracket
regression is caught.

THE E.7 MIXED-SCRIPT REGRESSION (this batch). A single line MIXING Latin words
+ CJK ideographs + spaces silently loses its Latin inter-word spaces. Root
cause: the join separator is chosen PER-SEGMENT via the BINARY
``is_latin_simple`` flag — a mixed segment is NOT Latin-simple, so
``reflow._join_atoms`` (and the ``reflow_paragraph`` ``"\\n"`` re-join
``_join_sep``) take the blanket EMPTY-string (CJK) join, while
``segment_by_opportunities`` strips the SP-boundary atoms. Reproduced today:
``"report 報告 done"`` -> ``"report報告done"``; ``"Manager 経理 2020-2024"`` ->
``"Manager経理2020-2024"``; ``"Email 電話 555 ext"`` -> glued. Pre-E.7 these
kept their spaces (``str.split`` / ``" ".join``). Mixed Latin+CJK lines are
common in CJK business / resume documents.

ROOT FIX (the GENERAL per-gap model — subsumes both prior E.7 special-cases so
no further script-mix regression is possible): the separator between two
adjacent atoms must be chosen PER-GAP, NOT per-segment. A gap that was
WHITESPACE in the original text (a Latin word boundary) re-joins with a single
space ``" "``; a gap that was a true ID↔ID (ideograph) no-width break re-joins
with EMPTY ``""``. So ``"report 報告 done"`` -> ``"report 報告 done"`` (the
spaces around ``報告`` preserved, no space between ``報`` and ``告``). Applied at
BOTH join sites (``break_into_lines`` per-line atom join AND ``reflow_paragraph``
``"\\n"`` re-join). The PURE-LATIN fast path stays byte-identical (collapsing
runs of spaces to one — INV-G-10), the CJK-only path stays the empty-join, and
the LATIN-WITH-ASCII-BRACKET path stays Latin-simple.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


# ── (b) INV-G-10 — LATIN REGRESSION LOCK (byte-identical) ──────────────────


@pytest.mark.parametrize(
    "text",
    [
        "the quick brown fox jumps over the lazy dog",
        "a b c",
        "hello   world",  # multiple consecutive spaces
        "one-word",  # hyphenated, single token
        "Revenue grew 24% year over year.",  # digits + punctuation
        "",  # empty
    ],
)
def test_inv_g_10_latin_atoms_equal_str_split(text: str) -> None:
    """Pure-Latin segmentation reproduces ``str.split(" ")`` atoms exactly.

    THE regression lock (INV-G-10). For Latin / space-delimited text the new
    classifier must be byte-equivalent to the existing ``str.split(" ")`` path:
    ``is_latin_simple`` True, identical non-empty atom list, and ``_join_atoms``
    equals ``" ".join``.
    """
    from pdf_edit_engine import linebreak

    assert linebreak.is_latin_simple(text) is True, (
        f"pure-Latin / space text must be Latin-simple; {text!r}"
    )
    expected = [w for w in text.split(" ") if w]
    got = [
        a
        for a in linebreak.segment_by_opportunities(text, linebreak.break_opportunities(text))
        if a
    ]
    assert got == expected, (
        f"Latin atomization must equal str.split(' '): expected {expected!r}, got {got!r}"
    )


# ── (c) JOIN correctness — empty for CJK, space for Latin ──────────────────


def test_inv_g_10_join_empty_for_cjk_space_for_latin() -> None:
    """``reflow._join_atoms`` joins CJK atoms with EMPTY, Latin with SPACE.

    For CJK a ``" ".join`` would inject spurious spaces between ideographs; for
    Latin the join must be byte-identical to ``" ".join``.
    """
    from pdf_edit_engine import reflow

    cjk_seg = "今日は世界"
    cjk_atoms = ["今日", "は世", "界"]
    assert reflow._join_atoms(cjk_atoms, cjk_seg) == "今日は世界", (
        "CJK atoms must re-join with no spurious spaces"
    )

    latin_seg = "foo bar"
    latin_atoms = ["foo", "bar"]
    assert reflow._join_atoms(latin_atoms, latin_seg) == "foo bar", (
        "Latin atoms must re-join with a space (byte-identical to ' '.join)"
    )
    assert reflow._join_atoms(latin_atoms, latin_seg) == " ".join(latin_atoms)


# ── (g) E.7 BRACKET REGRESSION — Latin-with-ASCII-bracket stays Latin-simple ─
#
# These cases are the coverage hole: the original INV-G-10 lock used only
# bracket-FREE strings, so the empty-join branch was never exercised against
# Latin text containing "(", "[", "{". They FAIL against the current code
# (ASCII brackets -> is_latin_simple False -> empty join -> glued).

_BRACKET_CASES: tuple[str, ...] = (
    "evaluation (conducted across teams)",  # parenthetical
    "see references [1] and [2]",  # square-bracket citations
    "func(x) returns y",  # code-style call with curly absent
    "config {key value} pair",  # curly braces
)


@pytest.mark.parametrize("text", _BRACKET_CASES)
def test_inv_g_10_latin_with_ascii_bracket_is_latin_simple(text: str) -> None:
    """Western text containing ASCII ``(`` ``[`` ``{`` is still Latin-simple.

    ROOT-FIX contract: only East-Asian-WIDTH (W/F) brackets are CJK no-break
    punctuation. ASCII / narrow brackets are ``AL`` (normal), so
    ``is_latin_simple`` stays True and the space-split + space-join Latin path
    runs verbatim.

    RED today: ``_char_class`` classifies the ASCII brackets as ``OP`` / ``CL``,
    so ``is_latin_simple`` returns False -> the empty (CJK) join branch glues the
    words.
    """
    from pdf_edit_engine import linebreak

    assert linebreak.is_latin_simple(text) is True, (
        f"Latin text containing an ASCII bracket must stay Latin-simple; {text!r} "
        f"classes={[linebreak._char_class(c) for c in text]}"
    )


@pytest.mark.parametrize("text", _BRACKET_CASES)
def test_inv_g_10_latin_bracket_atoms_equal_str_split(text: str) -> None:
    """Latin-with-bracket segmentation reproduces ``str.split(" ")`` atoms.

    The space-split atom boundaries must be identical whether or not the words
    carry ASCII brackets — the brackets are ordinary AL characters glued to
    their word, not break points.

    RED today: ``is_latin_simple`` False does not by itself change atom
    boundaries (the breaker still emits space opportunities), so this unit
    probe may already pass; it is paired with the join/e2e probes which carry
    the load-bearing RED. Kept as an explicit lock on the atom contract.
    """
    from pdf_edit_engine import linebreak

    expected = [w for w in text.split(" ") if w]
    got = [
        a
        for a in linebreak.segment_by_opportunities(text, linebreak.break_opportunities(text))
        if a
    ]
    assert got == expected, (
        f"Latin-with-bracket atomization must equal str.split(' '): "
        f"expected {expected!r}, got {got!r}"
    )


@pytest.mark.parametrize("text", _BRACKET_CASES)
def test_inv_g_10_join_atoms_preserves_spaces_for_latin_bracket(text: str) -> None:
    """``reflow._join_atoms`` re-joins Latin-with-bracket atoms WITH spaces.

    This is the direct, font-free RED proof of the blocker: today the bracket
    forces ``is_latin_simple`` False, so ``_join_atoms`` takes the EMPTY (CJK)
    separator branch and deletes every inter-word space, gluing the run.

    RED today: ``_join_atoms(atoms, text)`` == the glued ``"".join(atoms)`` (no
    spaces) instead of the space-joined ``" ".join(atoms)``.
    """
    from pdf_edit_engine import linebreak, reflow

    atoms = [
        a
        for a in linebreak.segment_by_opportunities(text, linebreak.break_opportunities(text))
        if a
    ]
    joined = reflow._join_atoms(atoms, text)
    expected = " ".join(atoms)
    assert joined == expected, (
        f"Latin-with-bracket atoms must re-join with spaces (byte-identical to "
        f"' '.join): expected {expected!r}, got {joined!r} — the empty (CJK) join "
        f"branch fired because is_latin_simple({text!r}) is "
        f"{linebreak.is_latin_simple(text)}"
    )


def test_inv_g_10_reflow_preserves_spaces_around_brackets_e2e(tmp_path: Path) -> None:
    """Public ``replace(reflow=True)`` of bracketed Latin text keeps spaces (e2e).

    Drives the full reflow write-side through the public API on a deterministic
    Helvetica fixture (always builds — no host-font skip). A bracketed
    replacement must re-wrap WITHOUT gluing adjacent words; ``get_text`` of the
    output must show ``"evaluation (conducted"`` with its inter-word space
    intact, NOT the glued ``"evaluation(conducted"``.

    PDF opens route through the engine's canonical ``open_pdf`` entry point via
    the public ``replace`` / ``get_text`` API (INV-L-1).

    RED today: the ``(`` forces the ``reflow_paragraph`` ``\\n`` re-join +
    ``break_into_lines`` ``_join_atoms`` onto the empty (CJK) separator, so every
    inter-word space is deleted and the paragraph renders glued.
    """
    from corpus_builders.reflow_quality import (
        BODY_FIND_ANCHOR,
        build_reflow_quality_pdf,
    )

    from pdf_edit_engine import find, get_text, replace

    src = tmp_path / "latin_bracket_src.pdf"
    build_reflow_quality_pdf(src)

    matches = find(str(src), BODY_FIND_ANCHOR)
    assert matches, f"fixture missing find anchor {BODY_FIND_ANCHOR!r}"

    out = tmp_path / "latin_bracket_out.pdf"
    new_text = (
        "the comprehensive evaluation (conducted across multiple teams) "
        "yielded strong measurable results"
    )
    result = replace(str(src), matches[0], new_text, str(out), reflow=True)
    assert result.success, f"bracketed Latin reflow must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied, "edit must route through reflow"

    extracted = get_text(str(out)).replace("\n", " ")

    # The load-bearing RED assertion: the words around the "(" must keep their
    # inter-word space — they must NOT be glued. ``extracted`` carries the
    # rendered spacing, so the glued empty-join output ("evaluation(conducted")
    # is absent and the spaced output ("evaluation (conducted") is present.
    assert "evaluation (conducted" in extracted, (
        "reflow must preserve the space before an opening bracket; expected "
        f"'evaluation (conducted' in output, got: {extracted!r}"
    )
    assert "teams) yielded" in extracted, (
        "reflow must preserve the space after a closing bracket; expected "
        f"'teams) yielded' in output, got: {extracted!r}"
    )
    assert "evaluation(conducted" not in extracted, (
        "reflow must NOT glue words across an ASCII bracket; the space-deleting "
        f"empty (CJK) join branch fired. Extracted: {extracted!r}"
    )


# ── (h) E.7 MIXED-SCRIPT REGRESSION — Latin spaces survive a mixed line ─────
#
# A single line mixing Latin words + CJK ideographs + spaces is NOT
# ``is_latin_simple``, so the BINARY per-segment flag routes it through the
# blanket EMPTY (CJK) join AND ``segment_by_opportunities`` strips the
# SP-boundary atoms — the Latin inter-word spaces vanish ("report 報告 done" ->
# "report報告done"). The ROOT FIX is a PER-GAP separator: a whitespace gap
# re-joins with " ", an ID↔ID gap re-joins with "". These cases FAIL today.
#
# ``expected`` encodes the per-gap contract: a space at every former-whitespace
# boundary, and NO space between two adjacent ideographs.

_MIXED_CASES: tuple[tuple[str, str], ...] = (
    # (source segment, expected per-gap re-join)
    ("report 報告 done", "report 報告 done"),
    ("Manager 経理 2020-2024", "Manager 経理 2020-2024"),
    ("Email 電話 555 ext", "Email 電話 555 ext"),
)

# CJK-ONLY control: all gaps are ID↔ID, so the empty-join is correct and the
# fix must NOT introduce a space. This case must stay GREEN.
_CJK_ONLY_CONTROL = "報告書"


@pytest.mark.parametrize("text", [c[0] for c in _MIXED_CASES])
def test_inv_g_10_mixed_script_not_latin_simple(text: str) -> None:
    """A mixed Latin+CJK line is NOT Latin-simple (documents the root cause).

    This is the precondition that triggers the regression: because the segment
    contains ID-class ideographs, ``is_latin_simple`` is False, so the binary
    per-segment flag drives the join onto the EMPTY (CJK) branch. It is a
    GREEN-today documentation anchor (it stays True->False unchanged by the
    per-gap fix — the fix keeps the predicate but stops branching the SEPARATOR
    on it); the load-bearing RED lives in the join probes below.
    """
    from pdf_edit_engine import linebreak

    assert linebreak.is_latin_simple(text) is False, (
        f"a mixed Latin+CJK line must NOT be Latin-simple (it contains ID "
        f"ideographs): {text!r} "
        f"classes={[linebreak._char_class(c) for c in text]}"
    )


@pytest.mark.parametrize(("text", "expected"), _MIXED_CASES)
def test_inv_g_10_join_atoms_preserves_latin_spaces_in_mixed(text: str, expected: str) -> None:
    """``reflow._join_atoms`` re-joins a MIXED segment with PER-GAP separators.

    THE load-bearing, font-free RED for the mixed-script blocker. The atoms of
    ``"report 報告 done"`` are ``["report", "報", "告", "done"]``; the correct
    re-join applies a space at each former-whitespace gap and an empty string at
    the ID↔ID ``報``|``告`` gap, yielding ``"report 報告 done"``.

    RED today: a mixed segment is not ``is_latin_simple``, so ``_join_atoms``
    takes the blanket ``"".join`` branch and produces the glued
    ``"report報告done"`` — every Latin inter-word space deleted.
    """
    from pdf_edit_engine import linebreak, reflow

    atoms = [
        a
        for a in linebreak.segment_by_opportunities(text, linebreak.break_opportunities(text))
        if a
    ]
    joined = reflow._join_atoms(atoms, text)
    assert joined == expected, (
        f"mixed Latin+CJK atoms must re-join with PER-GAP separators (space at a "
        f"former-whitespace boundary, empty between adjacent ideographs): expected "
        f"{expected!r}, got {joined!r} — the blanket empty (CJK) join branch fired "
        f"because is_latin_simple({text!r}) is {linebreak.is_latin_simple(text)}; "
        f"atoms={atoms!r}"
    )
    # The space-preservation contract, stated positively + negatively so the
    # failure message names the exact loss.
    assert " " in joined, f"mixed-script re-join deleted ALL Latin inter-word spaces: {joined!r}"


@pytest.mark.parametrize(("text", "expected"), _MIXED_CASES)
def test_inv_g_10_mixed_script_no_space_between_ideographs(text: str, expected: str) -> None:
    """The per-gap fix must NOT inject a space between adjacent ideographs.

    The complement of the space-preservation contract: a naive
    ``" ".join(atoms)`` "fix" would WRONGLY space-separate ``報`` and ``告``
    (``"報 告"``). The expected output has the ideographs adjacent.

    RED today for a different reason than the space-loss probe: today the whole
    line is glued (``"report報告done"``), so it does not equal ``expected`` —
    but it ALSO does not contain a spurious ``"報 告"``, so this probe pins the
    *upper* bound (no over-spacing) that the eventual GREEN must satisfy
    alongside the lower bound (spaces preserved).
    """
    from pdf_edit_engine import linebreak, reflow

    atoms = [
        a
        for a in linebreak.segment_by_opportunities(text, linebreak.break_opportunities(text))
        if a
    ]
    joined = reflow._join_atoms(atoms, text)
    # Every adjacent-ideograph pair present in ``expected`` must remain glued.
    for i in range(len(expected) - 1):
        a, b = expected[i], expected[i + 1]
        if linebreak._char_class(a) == "ID" and linebreak._char_class(b) == "ID":
            assert a + b in joined, (
                f"adjacent ideographs {a!r}{b!r} must stay glued (no spurious "
                f"inter-ideograph space); expected {expected!r}, got {joined!r}"
            )
    assert joined == expected, (
        f"mixed re-join must equal the per-gap expected {expected!r}, got {joined!r}"
    )


def test_inv_g_10_cjk_only_control_stays_empty_joined() -> None:
    """CONTROL: a pure-CJK line (all ID↔ID gaps) keeps the EMPTY join.

    Must stay GREEN: the per-gap fix introduces a space ONLY at a former
    whitespace gap. ``"報告書"`` has none, so every gap is ID↔ID -> empty -> the
    re-join is byte-identical to today's ``"".join`` and equals the input.
    """
    from pdf_edit_engine import linebreak, reflow

    atoms = [
        a
        for a in linebreak.segment_by_opportunities(
            _CJK_ONLY_CONTROL, linebreak.break_opportunities(_CJK_ONLY_CONTROL)
        )
        if a
    ]
    joined = reflow._join_atoms(atoms, _CJK_ONLY_CONTROL)
    assert joined == _CJK_ONLY_CONTROL, (
        f"pure-CJK control must stay empty-joined (no spurious spaces): expected "
        f"{_CJK_ONLY_CONTROL!r}, got {joined!r}"
    )
    assert " " not in joined, f"pure-CJK re-join must contain NO space: {joined!r}"


# ── (i) E.7 MIXED-SCRIPT e2e — public replace(reflow=True) keeps spaces ─────
#
# Corroboration on a real Identity-H CID font that covers BOTH Latin and CJK.
# Font-free unit probes above carry the load-bearing RED; this e2e is
# skipif-gated on a host CJK font and drives the full reflow write-side
# (break_into_lines per-line join + reflow_paragraph "\n" re-join) through the
# public API. PDF opens route through the canonical ``open_pdf`` via the public
# ``replace`` / ``get_text`` surface (INV-L-1).

# A WIDE all-CJK anchor sets ``paragraph_width = right_margin - left_margin`` to
# ~165pt. The replacement is a LONGER mixed phrase (~280pt) so the edit (a) is a
# genuine length change that routes through REFLOW (``reflow_applied=True``,
# not the same-length Tz-kerning path) and (b) wraps onto ~2 lines, each
# carrying MULTIPLE atoms — so the per-line ``_join_atoms`` must re-emit a
# separator between adjacent atoms ON THE SAME LINE. A short / single-atom-per-
# line wrap would mask the in-line glue (a one-atom line needs no separator),
# so the column must be wide enough that several atoms share a line.
_MIXED_E2E_ANCHOR = "概要報告書本日記録会議資料一覧"  # 12 ideographs ~165pt column
# Longer than the anchor (forces reflow), mixes Latin + CJK + spaces, and wraps
# with multiple atoms per line. Today it renders glued: "report報告doneandthe経理".
_MIXED_E2E_NEW = "report 報告 done and the 経理 summary 報告 final"
_MIXED_E2E_CORPUS = _MIXED_E2E_ANCHOR + _MIXED_E2E_NEW
_MIXED_FONT_SIZE = 11.0
_MIXED_MEDIA_W = 320.0
_MIXED_LEFT_X = 72.0
_MIXED_TOP_Y = 700.0

_MIXED_FONT_CANDIDATES: tuple[tuple[str, int], ...] = (
    ("msgothic.ttc", 0),
    ("YuGothR.ttc", 0),
    ("YuGothM.ttc", 0),
    ("simsun.ttc", 0),
    ("msyh.ttc", 0),
    ("malgun.ttf", 0),
)


def _find_mixed_font() -> tuple[Path, int] | None:
    """Return ``(path, fontNumber)`` of a font covering Latin + CJK corpus, or None."""
    import os

    from fontTools import ttLib  # type: ignore[import-untyped]

    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name, font_number in _MIXED_FONT_CANDIDATES:
        path = fonts_dir / name
        if not path.exists():
            continue
        try:
            font = ttLib.TTFont(str(path), fontNumber=font_number)
        except Exception:
            continue
        try:
            cmap: dict[int, str] = {}
            for table in font["cmap"].tables:
                if table.platformID == 3 and table.platEncID == 1:
                    cmap = table.cmap
                    break
            if all(ord(ch) in cmap for ch in _MIXED_E2E_CORPUS):
                return path, font_number
        except Exception:
            continue
        finally:
            font.close()
    return None


_MIXED_FONT = _find_mixed_font()
_no_mixed_font = pytest.mark.skipif(
    _MIXED_FONT is None,
    reason="no CID font installed covering the mixed Latin+CJK test corpus",
)


def _build_mixed_identity_h_pdf(out_path: Path, font_path: Path, font_number: int) -> str:
    """Write a 1-page Identity-H CID PDF showing :data:`_MIXED_E2E_ANCHOR`.

    Thin delegator to :func:`_build_mixed_identity_h_pdf_corpus` with the mixed
    Latin+CJK anchor/corpus constants — kept so the existing mixed-script e2e
    stays byte-stable while the recorded-separator probes reuse the generalized
    builder with their own (Korean / ID-ID-space) corpora.
    """
    return _build_mixed_identity_h_pdf_corpus(
        out_path, font_path, font_number, _MIXED_E2E_ANCHOR, _MIXED_E2E_CORPUS
    )


def _build_mixed_identity_h_pdf_corpus(
    out_path: Path,
    font_path: Path,
    font_number: int,
    anchor: str,
    corpus: str,
) -> str:
    """Write a 1-page Identity-H CID PDF whose subset covers ``corpus``.

    Mirrors ``test_g_9_cjk_linebreak._build_cjk_identity_h_pdf`` but subsets an
    arbitrary ``corpus`` (so a wide replacement encodes without font extension)
    and shows ``anchor`` on the page. Returns ``anchor``.

    Args:
        out_path: Destination PDF path.
        font_path: TrueType / collection font path covering ``corpus``.
        font_number: Collection index (0 for a plain ``.ttf``).
        anchor: The text drawn on the page (must be a substring-set of corpus).
        corpus: The full character set to subset (anchor + replacement glyphs).

    Returns:
        The ``anchor`` text.
    """
    import io

    import pikepdf
    from corpus_builders._common import build_tounicode_cmap, pin_font_timestamps
    from fontTools import ttLib  # type: ignore[import-untyped]
    from fontTools.subset import Subsetter  # type: ignore[import-untyped]

    full = ttLib.TTFont(str(font_path), fontNumber=font_number)
    try:
        cmap: dict[int, str] = {}
        for table in full["cmap"].tables:
            if table.platformID == 3 and table.platEncID == 1:
                cmap = table.cmap
                break
        glyph_order = full.getGlyphOrder()
        name_to_gid = {n: i for i, n in enumerate(glyph_order)}
        units_per_em = full["head"].unitsPerEm
        hmtx = full["hmtx"]

        cp_to_gid: dict[int, int] = {}
        used_gids: set[int] = set()
        for ch in sorted(set(corpus)):
            gname = cmap.get(ord(ch))
            if gname and gname in name_to_gid:
                gid = name_to_gid[gname]
                cp_to_gid[ord(ch)] = gid
                used_gids.add(gid)

        sub = ttLib.TTFont(str(font_path), fontNumber=font_number, recalcTimestamp=False)
        try:
            subsetter = Subsetter()
            subsetter.populate(glyphs=[glyph_order[g] for g in sorted(used_gids)])
            subsetter.subset(sub)
            pin_font_timestamps(sub)
            buf = io.BytesIO()
            sub.save(buf)
            font_bytes = buf.getvalue()
        finally:
            sub.close()

        advances: dict[int, int] = {}
        for gid in sorted(used_gids):
            gname = glyph_order[gid]
            try:
                raw = float(hmtx[gname][0])
            except (KeyError, IndexError):
                raw = 500.0
            advances[gid] = round(raw * 1000 / units_per_em)

        bbox = [full["head"].xMin, full["head"].yMin, full["head"].xMax, full["head"].yMax]
        bbox_1000 = [round(v * 1000 / units_per_em) for v in bbox]
        ascent = round(full["OS/2"].sTypoAscender * 1000 / units_per_em)
        descent = round(full["OS/2"].sTypoDescender * 1000 / units_per_em)
        cap_h = round(
            getattr(full["OS/2"], "sCapHeight", full["OS/2"].sTypoAscender) * 1000 / units_per_em
        )
        ps_name = str(full["name"].getDebugName(6) or "MixedCorpusFont").replace(" ", "")
    finally:
        full.close()

    def encode(text: str) -> str:
        data = bytes(
            b
            for cp in (ord(c) for c in text)
            for b in ((cp_to_gid.get(cp, 0) >> 8) & 0xFF, cp_to_gid.get(cp, 0) & 0xFF)
        )
        return data.hex().upper()

    pdf = pikepdf.Pdf.new()
    try:
        font_stream = pikepdf.Stream(pdf, font_bytes)
        font_stream["/Length1"] = len(font_bytes)
        font_descriptor = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/FontDescriptor"),
                    "/FontName": pikepdf.Name("/" + ps_name),
                    "/Flags": 4,
                    "/FontBBox": pikepdf.Array(bbox_1000),
                    "/ItalicAngle": 0,
                    "/Ascent": ascent,
                    "/Descent": descent,
                    "/CapHeight": cap_h,
                    "/StemV": 80,
                    "/FontFile2": font_stream,
                }
            )
        )
        w_flat: list[object] = []
        for gid in sorted(used_gids):
            w_flat.append(gid)
            w_flat.append(pikepdf.Array([advances[gid]]))
        cid_font = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/CIDFontType2"),
                    "/BaseFont": pikepdf.Name("/" + ps_name),
                    "/CIDSystemInfo": pikepdf.Dictionary(
                        {
                            "/Registry": pikepdf.String("Adobe"),
                            "/Ordering": pikepdf.String("Identity"),
                            "/Supplement": 0,
                        }
                    ),
                    "/FontDescriptor": font_descriptor,
                    "/DW": 1000,
                    "/W": pikepdf.Array(w_flat),
                    "/CIDToGIDMap": pikepdf.Name("/Identity"),
                }
            )
        )
        tounicode = build_tounicode_cmap({gid: cp for cp, gid in cp_to_gid.items()})
        type0 = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/" + ps_name),
                "/Encoding": pikepdf.Name("/Identity-H"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
                "/ToUnicode": pikepdf.Stream(pdf, tounicode.encode("latin-1")),
            }
        )
        ops = [
            "BT",
            f"/F1 {_MIXED_FONT_SIZE} Tf",
            f"1 0 0 1 {_MIXED_LEFT_X} {_MIXED_TOP_Y} Tm",
            f"<{encode(anchor)}> Tj",
            "ET",
        ]
        content = "\n".join(ops).encode("latin-1")
        page = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Page"),
                "/MediaBox": pikepdf.Array([0, 0, _MIXED_MEDIA_W, 792]),
                "/Resources": pikepdf.Dictionary(
                    {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(type0)})}
                ),
                "/Contents": pikepdf.Stream(pdf, content),
            }
        )
        pdf.pages.append(pikepdf.Page(page))
        pdf.save(str(out_path))
    finally:
        pdf.close()
    return anchor


@_no_mixed_font
def test_inv_g_10_mixed_script_reflow_preserves_spaces_e2e(tmp_path: Path) -> None:
    """Public ``replace(reflow=True)`` of mixed Latin+CJK text keeps Latin spaces (e2e).

    Drives the full reflow write-side through the public API on a synthetic
    Identity-H CID PDF whose font covers BOTH Latin and CJK. The mixed
    replacement must re-wrap WITHOUT gluing the Latin words around the
    ideographs: ``get_text`` of the output must show ``"report 報告 done"`` with
    its inter-word spaces intact, NOT the glued ``"report報告done"``; and the
    adjacent ideographs ``報``/``告`` must stay un-spaced.

    RED today: the mixed line is not ``is_latin_simple``, so both reflow join
    sites take the empty (CJK) branch and delete every Latin inter-word space.
    """
    from pdf_edit_engine import find, get_text, replace

    assert _MIXED_FONT is not None  # gated by skipif
    src = tmp_path / "mixed_src.pdf"
    anchor = _build_mixed_identity_h_pdf(src, _MIXED_FONT[0], _MIXED_FONT[1])

    matches = find(str(src), anchor)
    assert matches, f"anchor {anchor!r} not found in synthetic mixed PDF"

    out = tmp_path / "mixed_out.pdf"
    result = replace(str(src), matches[0], _MIXED_E2E_NEW, str(out), reflow=True)
    assert result.success, f"mixed-script reflow must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied, "edit must route through reflow"

    # Flatten the line breaks so the in-LINE spacing is what is asserted (the
    # per-line ``_join_atoms`` separator is the thing under test; the line break
    # itself becomes a space here and is not the regression).
    extracted = get_text(str(out)).replace("\n", " ")

    assert "report 報告 done" in extracted, (
        "reflow must preserve the Latin inter-word spaces around the ideographs "
        f"WITHIN a wrapped line; expected 'report 報告 done' in output, got: {extracted!r}"
    )
    assert "report報告done" not in extracted, (
        "reflow must NOT glue Latin words across CJK ideographs; the space-deleting "
        f"empty (CJK) join branch fired. Extracted: {extracted!r}"
    )
    assert "doneandthe" not in extracted, (
        "reflow must NOT glue consecutive Latin words on a wrapped line; the empty "
        f"(CJK) join branch deleted the spaces. Extracted: {extracted!r}"
    )
    assert "報 告" not in extracted, (
        f"reflow must NOT inject a spurious space between adjacent ideographs; got: {extracted!r}"
    )


# ── (j) E.7 RECORDED-SEPARATOR REMEDIATION — class inference is provenance-blind ─
#
# THE BLOCKER (this batch, found by the critic). The per-gap join uses
# ``linebreak.separator_between(left_atom, right_atom)`` which INFERS the gap
# separator from the atom CLASSES. That cannot distinguish:
#   * a former-WHITESPACE gap between two ideographs ("報告 売上" -> keep " ")
#   * a genuinely-adjacent-ideograph gap ("報告売上" -> "")
# because BOTH are ID↔ID. ``segment_by_opportunities`` STRIPS the space-atom
# (the ``.strip(" ")`` + the ``if a`` filter drop the "" space atom), destroying
# the gap provenance BEFORE the join runs.
#
# Two SILENT regressions from pre-E.7 (which did ``[w for w in seg.split(" ") if
# w]`` then ``" ".join`` — preserving every space):
#   (1) a real space between ideographs is DELETED: "報告 売上" -> "報告売上",
#       "図 表 図" -> "図表図";
#   (2) KOREAN word spacing is destroyed on EVERY multi-word reflow — Hangul is
#       East-Asian-width W so ``_char_class == "ID"``, but Korean uses spaces
#       between words: "안녕하세요 세계 평화" -> "안녕하세요세계평화". Korean is a
#       core CJK-trio target.
#
# ROOT FIX: byte-faithful RECORDED separators (NOT class inference). The
# atomization must preserve the ACTUAL inter-atom separator (a whitespace run ->
# a single " "; a no-width ID↔ID / CL→ID break -> "") and the join must re-emit
# it verbatim. The PURE-LATIN fast path stays byte-identical (INV-G-10); the
# CJK-no-space control stays empty-joined.
#
# These (a)/(b) cases MUST FAIL today (class inference glues ID↔ID-with-space +
# Korean). The (c) control + the existing locks above must stay GREEN.

# (a) ID↔ID-WITH-SPACE: a real space BETWEEN two ideographs must be preserved.
# Today's class inference sees (ID, ID) at the gap -> "" -> the space is deleted.
_ID_ID_SPACE_CASES: tuple[tuple[str, str], ...] = (
    ("報告 売上", "報告 売上"),  # inter-ideograph space preserved
    ("図 表 図", "図 表 図"),  # two inter-ideograph spaces preserved
)

# (b) KOREAN word spacing: Hangul syllables are ID-class (East-Asian-width W),
# but Korean separates WORDS with spaces. Every word space must be preserved.
_KOREAN_SPACE_CASES: tuple[tuple[str, str], ...] = (
    ("안녕하세요 세계 평화", "안녕하세요 세계 평화"),
    ("프로젝트 관리자 김철수", "프로젝트 관리자 김철수"),
)

# (c) CONTROL — CJK with NO original space: every gap is a genuine no-width
# ID↔ID break, so the recorded separator is "" -> empty join. Must stay GREEN
# (byte-identical to today): the fix must NOT manufacture a spurious space.
_CJK_NO_SPACE_CONTROL_CASES: tuple[tuple[str, str], ...] = (
    ("報告売上", "報告売上"),
    ("報告書", "報告書"),
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [*_ID_ID_SPACE_CASES, *_KOREAN_SPACE_CASES],
)
def test_inv_g_10_recorded_separator_preserves_id_id_and_korean_space(
    text: str, expected: str
) -> None:
    """A real space between ID-class glyphs survives atomize→join (font-free).

    THE load-bearing RED for the recorded-separator blocker. The atomization
    drops the space-atom and the join INFERS the separator from the two atoms'
    classes; both sides are ID, so the inferred separator is "" and the space
    vanishes ("報告 売上" -> "報告売上"; "안녕하세요 세계 평화" ->
    "안녕하세요세계평화").

    The ROOT FIX records the ACTUAL separator consumed at each gap (a whitespace
    run -> a single " "), so the round-trip reproduces every original space while
    still breaking freely at the no-width ideograph opportunities.

    RED today: ``_join_atoms`` produces the glued, space-stripped string because
    ``separator_between`` cannot see that the ID↔ID gap was originally a space.
    """
    from pdf_edit_engine import linebreak, reflow

    atoms = [
        a
        for a in linebreak.segment_by_opportunities(text, linebreak.break_opportunities(text))
        if a
    ]
    joined = reflow._join_atoms(atoms, text)
    assert joined == expected, (
        f"a recorded inter-atom SPACE between ID-class glyphs must be re-emitted "
        f"verbatim: expected {expected!r}, got {joined!r} — class-inference "
        f"separator_between glued the ID↔ID gap that was originally whitespace. "
        f"atoms={atoms!r}"
    )
    # State the loss positively so the failure names the exact symptom: the
    # input had N spaces and the round-trip must keep all N.
    assert joined.count(" ") == text.count(" "), (
        f"every recorded inter-atom space must survive the round-trip: input had "
        f"{text.count(' ')} space(s), output has {joined.count(' ')}: {joined!r}"
    )


@pytest.mark.parametrize(("text", "expected"), _CJK_NO_SPACE_CONTROL_CASES)
def test_inv_g_10_recorded_separator_cjk_no_space_stays_empty(text: str, expected: str) -> None:
    """CONTROL: a spaceless CJK run keeps the EMPTY join (must stay GREEN).

    The complement of the recorded-space probe: when the original text has NO
    whitespace between ideographs, each gap records "" and the join is
    byte-identical to today's ``"".join``. The recorded-separator fix must NOT
    manufacture a spurious space here (that would be over-correction).
    """
    from pdf_edit_engine import linebreak, reflow

    atoms = [
        a
        for a in linebreak.segment_by_opportunities(text, linebreak.break_opportunities(text))
        if a
    ]
    joined = reflow._join_atoms(atoms, text)
    assert joined == expected, (
        f"a spaceless CJK run must stay empty-joined (no manufactured space): "
        f"expected {expected!r}, got {joined!r}; atoms={atoms!r}"
    )
    assert " " not in joined, (
        f"spaceless CJK control must contain NO space after the recorded-separator fix: {joined!r}"
    )


@pytest.mark.parametrize("text", [c[0] for c in _KOREAN_SPACE_CASES])
def test_inv_g_10_korean_still_wraps_at_syllable_opportunities(text: str) -> None:
    """Korean must STILL offer no-width wrap opportunities between syllables.

    The recorded-separator fix must not disable CJK wrapping: a Korean run is
    East-Asian-width W (ID class), so ``break_opportunities`` must still expose
    interior ID↔ID (and post-space) break points so a wide Korean paragraph can
    wrap. This is GREEN today (Hangul is ID) and must STAY green after the fix —
    it pins that "preserve the space" does not regress into "never break".
    """
    from pdf_edit_engine import linebreak

    opps = linebreak.break_opportunities(text)
    assert opps, (
        f"a multi-syllable Korean run must offer interior break opportunities so it "
        f"can wrap: {text!r} got no opportunities"
    )


# ── (k) E.7 recorded-separator e2e — public replace(reflow=True), Korean + ID-ID ─
#
# Corroboration on a real Identity-H CID font that covers the Korean / CJK
# corpus. Font-free unit probes above carry the load-bearing RED; this e2e drives
# the full reflow write-side (break_into_lines per-line join + reflow_paragraph
# "\n" re-join) through the public API. PDF opens route through the canonical
# ``open_pdf`` via the public ``replace`` / ``get_text`` surface (INV-L-1).

# Korean replacement: a multi-WORD Korean phrase whose word spaces today vanish.
# The anchor must be present in the source and longer-text triggers reflow.
_KOREAN_E2E_ANCHOR = "개요보고서기록회의자료목록정리완료본문"  # spaceless Hangul ~ wide column
# Longer than the anchor (forces reflow), multi-word Korean with spaces. Today it
# renders glued: "안녕하세요세계평화프로젝트관리자김철수보고서완료".
_KOREAN_E2E_NEW = "안녕하세요 세계 평화 프로젝트 관리자 김철수 보고서 완료 최종"
_KOREAN_E2E_CORPUS = _KOREAN_E2E_ANCHOR + _KOREAN_E2E_NEW

_KOREAN_FONT_CANDIDATES: tuple[tuple[str, int], ...] = (
    ("malgun.ttf", 0),
    ("malgunbd.ttf", 0),
    ("batang.ttc", 0),
    ("gulim.ttc", 0),
)


def _find_korean_font() -> tuple[Path, int] | None:
    """Return ``(path, fontNumber)`` of a font covering the Korean corpus, or None."""
    import os

    from fontTools import ttLib  # type: ignore[import-untyped]

    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name, font_number in _KOREAN_FONT_CANDIDATES:
        path = fonts_dir / name
        if not path.exists():
            continue
        try:
            font = ttLib.TTFont(str(path), fontNumber=font_number)
        except Exception:
            continue
        try:
            cmap: dict[int, str] = {}
            for table in font["cmap"].tables:
                if table.platformID == 3 and table.platEncID == 1:
                    cmap = table.cmap
                    break
            if all(ord(ch) in cmap for ch in _KOREAN_E2E_CORPUS):
                return path, font_number
        except Exception:
            continue
        finally:
            font.close()
    return None


_KOREAN_FONT = _find_korean_font()
_no_korean_font = pytest.mark.skipif(
    _KOREAN_FONT is None,
    reason="no CID font installed covering the Korean test corpus",
)


@_no_korean_font
def test_inv_g_10_korean_reflow_preserves_word_spaces_e2e(tmp_path: Path) -> None:
    """Public ``replace(reflow=True)`` of Korean text keeps word spaces (e2e).

    Drives the full reflow write-side through the public API on a synthetic
    Identity-H CID PDF whose font covers the Korean corpus (Hangul is ID-class).
    The multi-word Korean replacement must re-wrap WITHOUT gluing the words:
    ``get_text`` of the output must show ``"안녕하세요 세계 평화"`` with its word
    spaces intact, NOT the glued ``"안녕하세요세계평화"``.

    RED today: every Hangul syllable is ID-class, so the class-inference
    ``separator_between`` infers "" at EVERY gap and both reflow join sites delete
    every Korean word space.
    """
    from pdf_edit_engine import find, get_text, replace

    assert _KOREAN_FONT is not None  # gated by skipif
    src = tmp_path / "korean_src.pdf"
    anchor = _build_mixed_identity_h_pdf_corpus(
        src, _KOREAN_FONT[0], _KOREAN_FONT[1], _KOREAN_E2E_ANCHOR, _KOREAN_E2E_CORPUS
    )

    matches = find(str(src), anchor)
    assert matches, f"anchor {anchor!r} not found in synthetic Korean PDF"

    out = tmp_path / "korean_out.pdf"
    result = replace(str(src), matches[0], _KOREAN_E2E_NEW, str(out), reflow=True)
    assert result.success, f"Korean reflow must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied, "edit must route through reflow"

    extracted = get_text(str(out)).replace("\n", " ")

    assert "안녕하세요 세계 평화" in extracted, (
        "reflow must preserve the Korean inter-word spaces; expected "
        f"'안녕하세요 세계 평화' in output, got: {extracted!r}"
    )
    assert "안녕하세요세계평화" not in extracted, (
        "reflow must NOT glue Korean words (Hangul is ID-class but uses word "
        f"spaces); the class-inference empty join fired. Extracted: {extracted!r}"
    )


@_no_mixed_font
def test_inv_g_10_id_id_space_reflow_preserves_space_e2e(tmp_path: Path) -> None:
    """Public ``replace(reflow=True)`` keeps a real space BETWEEN ideographs (e2e).

    A genuine inter-ideograph space ("報告 売上") must survive a reflow. Today the
    class-inference join sees (ID, ID) and deletes it. Drives the full write-side
    through the public API on the mixed Latin+CJK CID font.

    RED today: the recorded inter-ideograph space is glued
    ("報告 売上" -> "報告売上").
    """
    from pdf_edit_engine import find, get_text, replace

    assert _MIXED_FONT is not None  # gated by skipif
    # Build a PDF whose subset covers the anchor + the ID-ID-space replacement.
    # The replacement must be LONGER than the anchor (so the edit routes through
    # reflow, not the same-length Tz-kerning path) and wide enough to wrap.
    id_anchor = "概要報告書本日記録会議資料一覧"
    id_new = "報告 売上 図 表 図 報告書 完了 概要 本日 記録 会議 資料 一覧 報告 売上"
    corpus = id_anchor + id_new
    src = tmp_path / "id_id_space_src.pdf"
    anchor = _build_mixed_identity_h_pdf_corpus(
        src, _MIXED_FONT[0], _MIXED_FONT[1], id_anchor, corpus
    )

    matches = find(str(src), anchor)
    assert matches, f"anchor {anchor!r} not found in synthetic ID-ID-space PDF"

    out = tmp_path / "id_id_space_out.pdf"
    result = replace(str(src), matches[0], id_new, str(out), reflow=True)
    assert result.success, f"ID-ID-space reflow must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied, "edit must route through reflow"

    extracted = get_text(str(out)).replace("\n", " ")

    assert "報告 売上" in extracted, (
        "reflow must preserve a real space BETWEEN two ideographs; expected "
        f"'報告 売上' in output, got: {extracted!r}"
    )
    # The control fragment "報告書" (NO original space) must remain glued — the
    # fix is provenance-faithful, not a blanket "space every ID gap".
    assert "報告書" in extracted, (
        "the no-space control fragment '報告書' must stay glued (the fix must not "
        f"manufacture spaces at genuinely-adjacent ideograph gaps): {extracted!r}"
    )


# ── (l) E.7 CROSS-LINE "\n" REMEDIATION — grouping-boundary join is provenance-blind ─
#
# THE BLOCKER (this batch, found by the critic). The WITHIN-segment recorded-
# separator model (j/k above) is byte-faithful and correct. But the CROSS-LINE
# "\n" grouping-boundary join (``reflow._rejoin_newline_artifacts`` ->
# ``linebreak.grouping_boundary_separator``) still uses CLASS INFERENCE:
#
#     if right_cls == "ID" and left_cls in ("ID", "CL"):
#         return ""            # <-- glues the two source lines
#     return " "
#
# For a MULTI-LINE paragraph whose source pieces are grouped with "\n", an
# ID|ID boundary returns "" — gluing the LAST word of one source line to the
# FIRST of the next. KOREAN is the silent victim: Hangul is East-Asian-width W
# (so ``_char_class == "ID"``) yet Korean is SPACE-DELIMITED, so a word space
# falling at a source-line boundary is DELETED. Source pieces "프로젝트 관리자"
# + "김철수 책임 연구원" reflow to "…관리자김철수…" — a SILENT, meaning-changing
# join, success=True. Pre-E.7 did a blanket ``"\n" -> " "`` here, so it
# PRESERVED that space (this is a REGRESSION introduced by E.7).
#
# WHY ONLY A HEURISTIC IS POSSIBLE (scope-honest): a "\n" artifact is a
# LOCATOR-inserted element-grouping boundary with NO original-space provenance
# (the per-boundary "was there a space here" info was discarded at extraction —
# recovering it perfectly is a READ-PATH change E.7 explicitly carved out to
# 0.3.0). So the cross-line "\n" join must use the best reflow-side heuristic.
#
# ROOT FIX (space-delimited-aware cross-line join; NO regression vs pre-E.7):
# the "\n" separator is " " when the paragraph (or the adjacent run) is
# SPACE-DELIMITED — i.e. its combined source text contains ANY space anywhere
# (Latin / Korean / mixed / CJK-with-spaces) — and "" only for a PURE no-space
# ideograph paragraph (no space anywhere). This makes:
#   * Korean / Latin / mixed (space-delimited) -> "\n" -> " " : word spaces
#     PRESERVED (matches pre-E.7, fixes the regression);
#   * pure no-space CJK -> "\n" -> "" : no spurious inter-line space (an
#     IMPROVEMENT over pre-E.7, which inserted one).
# => strictly NO space-fidelity REGRESSION vs pre-E.7, and a pure-CJK
# improvement.
#
# These (a) cases MUST FAIL today (cross-line class inference glues Korean at
# the source-line boundary). The (b) pure-CJK control + the (c) Latin parity
# must stay GREEN. The font-free unit probes below carry the load-bearing RED;
# the e2e (skipif-gated on malgun.ttf) corroborates through the public
# ``replace(reflow=True)`` write-side.

# (a) KOREAN cross-line: a word space falls at a source-line "\n" boundary.
# Today the ID|ID boundary infers "" -> the inter-word space between the last
# syllable of one line and the first of the next is DELETED.
_KOREAN_CROSSLINE_CASES: tuple[tuple[str, str], ...] = (
    ("관리자\n김철수", "관리자 김철수"),
    ("프로젝트 관리자\n김철수 책임 연구원", "프로젝트 관리자 김철수 책임 연구원"),
    ("안녕하세요\n세계 평화", "안녕하세요 세계 평화"),
)

# (b) PURE-CJK cross-line CONTROL: the combined paragraph has NO space anywhere,
# so the heuristic keeps "" at the "\n" boundary (no spurious inter-line space).
# Must stay GREEN (this is byte-identical to today, AND an improvement over the
# pre-E.7 blanket " " which would have inserted a space here).
_CJK_CROSSLINE_CONTROL_CASES: tuple[tuple[str, str], ...] = (
    ("今日は\n世界", "今日は世界"),
    ("報告\n売上", "報告売上"),
)

# (c) LATIN cross-line: the Latin-simple fast path already does "\n" -> " ".
# Must stay GREEN (pre-E.7 parity).
_LATIN_CROSSLINE_CASES: tuple[tuple[str, str], ...] = (
    ("the quick\nbrown fox", "the quick brown fox"),
    ("hello world\nfoo bar", "hello world foo bar"),
)


@pytest.mark.parametrize(("text", "expected"), _KOREAN_CROSSLINE_CASES)
def test_inv_g_10_crossline_korean_preserves_word_space(text: str, expected: str) -> None:
    """A Korean word space at a source-line "\\n" boundary survives the re-join.

    THE load-bearing RED for the cross-line "\\n" blocker (font-free). The
    paragraph ``full_text`` joins separately-grouped source lines with "\\n";
    ``reflow._rejoin_newline_artifacts`` must resolve each "\\n" to the inter-
    line separator. For a SPACE-DELIMITED paragraph (any space anywhere — Korean
    qualifies because the multi-word lines carry spaces) the "\\n" boundary is a
    likely WORD boundary and must re-join with a single space.

    RED today: ``grouping_boundary_separator`` infers the separator from the
    adjacent CHARACTER CLASSES; Hangul is ID-class on both sides of the "\\n",
    so the ID|ID rule returns "" and the boundary word space is DELETED
    ("관리자\\n김철수" -> "관리자김철수"). Pre-E.7's blanket "\\n"->" " preserved
    it, so this is an E.7-introduced regression.
    """
    from pdf_edit_engine import reflow

    joined = reflow._rejoin_newline_artifacts(text)
    assert joined == expected, (
        f"a Korean word space at a source-line '\\n' boundary must be preserved: "
        f"expected {expected!r}, got {joined!r} — the cross-line class-inference "
        f"grouping_boundary_separator returned '' for the ID|ID boundary and glued "
        f"the two source lines. A space-delimited paragraph must re-join its '\\n' "
        f"grouping artifacts with a space (pre-E.7 parity)."
    )
    # State the loss positively so the failure names the exact symptom: every
    # source-line "\n" boundary in a space-delimited run becomes a space, so the
    # space count is the original spaces plus the newline count.
    assert joined.count(" ") == expected.count(" "), (
        f"the cross-line re-join must keep every word space: expected "
        f"{expected.count(' ')} space(s), got {joined.count(' ')}: {joined!r}"
    )


@pytest.mark.parametrize(("text", "expected"), _KOREAN_CROSSLINE_CASES)
def test_inv_g_10_crossline_korean_separator_is_space(text: str, expected: str) -> None:
    """``grouping_boundary_separator`` returns a SPACE for a space-delimited run.

    Pins the root-fix decision at the pure-function boundary: for a paragraph
    that is space-delimited (its combined text contains a space), every "\\n"
    grouping boundary resolves to " ". The current class-inference signature
    takes only the two adjacent pieces and cannot see the paragraph's space-
    delimited-ness, so the eventual GREEN may widen the signature or key on the
    pieces themselves — either way the Korean boundary here MUST resolve to " ".

    RED today: ``grouping_boundary_separator("…관리자", "김철수…")`` returns ""
    (ID|ID class inference), gluing the word.
    """
    from pdf_edit_engine import linebreak

    _ = expected  # contract documented via the partner probe; unused here.
    left, _, right = text.partition("\n")
    assert "\n" not in right, "test corpus uses a single cross-line boundary"
    sep = linebreak.grouping_boundary_separator(left, right)
    assert sep == " ", (
        f"a space-delimited Korean cross-line boundary must re-join with a space; "
        f"grouping_boundary_separator({left!r}, {right!r}) returned {sep!r} — class "
        f"inference glued the ID|ID boundary."
    )


@pytest.mark.parametrize(("text", "expected"), _CJK_CROSSLINE_CONTROL_CASES)
def test_inv_g_10_crossline_pure_cjk_stays_empty(text: str, expected: str) -> None:
    """CONTROL: a pure no-space CJK cross-line "\\n" stays empty (must stay GREEN).

    The complement of the Korean probe: when the combined paragraph has NO space
    anywhere, the "\\n" boundary between two genuinely-adjacent ideographs is a
    no-width break and must re-join with "" (no spurious inter-line space). This
    is byte-identical to today AND an improvement over the pre-E.7 blanket
    "\\n"->" " (which would have inserted a space). The space-delimited-aware
    fix must NOT manufacture a space here.
    """
    from pdf_edit_engine import reflow

    joined = reflow._rejoin_newline_artifacts(text)
    assert joined == expected, (
        f"a pure no-space CJK cross-line boundary must stay empty-joined: expected "
        f"{expected!r}, got {joined!r}"
    )
    assert " " not in joined, f"pure-CJK cross-line re-join must contain NO space: {joined!r}"


@pytest.mark.parametrize(("text", "expected"), _LATIN_CROSSLINE_CASES)
def test_inv_g_10_crossline_latin_stays_space(text: str, expected: str) -> None:
    """CONTROL: a Latin cross-line "\\n" re-joins with a space (pre-E.7 parity).

    The Latin-simple fast path already does ``text.replace("\\n", " ")``; the
    cross-line fix must keep it byte-identical. Must stay GREEN.
    """
    from pdf_edit_engine import reflow

    joined = reflow._rejoin_newline_artifacts(text)
    assert joined == expected, (
        f"a Latin cross-line boundary must re-join with a space (pre-E.7 parity): "
        f"expected {expected!r}, got {joined!r}"
    )


def _build_two_line_identity_h_pdf_corpus(
    out_path: Path,
    font_path: Path,
    font_number: int,
    line1: str,
    line2: str,
    corpus: str,
) -> str:
    """Write a 1-page Identity-H CID PDF showing ``line1`` then ``line2`` on
    SEPARATE text lines (distinct y), so the locator groups them into ONE
    paragraph whose ``full_text`` carries a "\\n" at the line boundary.

    Mirrors :func:`_build_mixed_identity_h_pdf_corpus` but emits TWO Tj operators
    at different y-positions (one ``Td`` advance between them) so the cross-line
    "\\n" grouping artifact is genuinely exercised by reflow. Returns the
    space-joined ``line1 + " " + line2`` page text the anchor matching uses.

    Args:
        out_path: Destination PDF path.
        font_path: TrueType / collection font path covering ``corpus``.
        font_number: Collection index (0 for a plain ``.ttf``).
        line1: Text drawn on the first (upper) line.
        line2: Text drawn on the second (lower) line.
        corpus: The full character set to subset.

    Returns:
        ``line1`` (used as the find anchor; it is present verbatim on page 1).
    """
    import io

    import pikepdf
    from corpus_builders._common import build_tounicode_cmap, pin_font_timestamps
    from fontTools import ttLib  # type: ignore[import-untyped]
    from fontTools.subset import Subsetter  # type: ignore[import-untyped]

    full = ttLib.TTFont(str(font_path), fontNumber=font_number)
    try:
        cmap: dict[int, str] = {}
        for table in full["cmap"].tables:
            if table.platformID == 3 and table.platEncID == 1:
                cmap = table.cmap
                break
        glyph_order = full.getGlyphOrder()
        name_to_gid = {n: i for i, n in enumerate(glyph_order)}
        units_per_em = full["head"].unitsPerEm
        hmtx = full["hmtx"]

        cp_to_gid: dict[int, int] = {}
        used_gids: set[int] = set()
        for ch in sorted(set(corpus)):
            gname = cmap.get(ord(ch))
            if gname and gname in name_to_gid:
                gid = name_to_gid[gname]
                cp_to_gid[ord(ch)] = gid
                used_gids.add(gid)

        sub = ttLib.TTFont(str(font_path), fontNumber=font_number, recalcTimestamp=False)
        try:
            subsetter = Subsetter()
            subsetter.populate(glyphs=[glyph_order[g] for g in sorted(used_gids)])
            subsetter.subset(sub)
            pin_font_timestamps(sub)
            buf = io.BytesIO()
            sub.save(buf)
            font_bytes = buf.getvalue()
        finally:
            sub.close()

        advances: dict[int, int] = {}
        for gid in sorted(used_gids):
            gname = glyph_order[gid]
            try:
                raw = float(hmtx[gname][0])
            except (KeyError, IndexError):
                raw = 500.0
            advances[gid] = round(raw * 1000 / units_per_em)

        bbox = [full["head"].xMin, full["head"].yMin, full["head"].xMax, full["head"].yMax]
        bbox_1000 = [round(v * 1000 / units_per_em) for v in bbox]
        ascent = round(full["OS/2"].sTypoAscender * 1000 / units_per_em)
        descent = round(full["OS/2"].sTypoDescender * 1000 / units_per_em)
        cap_h = round(
            getattr(full["OS/2"], "sCapHeight", full["OS/2"].sTypoAscender) * 1000 / units_per_em
        )
        ps_name = str(full["name"].getDebugName(6) or "CrossLineCorpusFont").replace(" ", "")
    finally:
        full.close()

    def encode(text: str) -> str:
        data = bytes(
            b
            for cp in (ord(c) for c in text)
            for b in ((cp_to_gid.get(cp, 0) >> 8) & 0xFF, cp_to_gid.get(cp, 0) & 0xFF)
        )
        return data.hex().upper()

    pdf = pikepdf.Pdf.new()
    try:
        font_stream = pikepdf.Stream(pdf, font_bytes)
        font_stream["/Length1"] = len(font_bytes)
        font_descriptor = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/FontDescriptor"),
                    "/FontName": pikepdf.Name("/" + ps_name),
                    "/Flags": 4,
                    "/FontBBox": pikepdf.Array(bbox_1000),
                    "/ItalicAngle": 0,
                    "/Ascent": ascent,
                    "/Descent": descent,
                    "/CapHeight": cap_h,
                    "/StemV": 80,
                    "/FontFile2": font_stream,
                }
            )
        )
        w_flat: list[object] = []
        for gid in sorted(used_gids):
            w_flat.append(gid)
            w_flat.append(pikepdf.Array([advances[gid]]))
        cid_font = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/CIDFontType2"),
                    "/BaseFont": pikepdf.Name("/" + ps_name),
                    "/CIDSystemInfo": pikepdf.Dictionary(
                        {
                            "/Registry": pikepdf.String("Adobe"),
                            "/Ordering": pikepdf.String("Identity"),
                            "/Supplement": 0,
                        }
                    ),
                    "/FontDescriptor": font_descriptor,
                    "/DW": 1000,
                    "/W": pikepdf.Array(w_flat),
                    "/CIDToGIDMap": pikepdf.Name("/Identity"),
                }
            )
        )
        tounicode = build_tounicode_cmap({gid: cp for cp, gid in cp_to_gid.items()})
        type0 = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/" + ps_name),
                "/Encoding": pikepdf.Name("/Identity-H"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
                "/ToUnicode": pikepdf.Stream(pdf, tounicode.encode("latin-1")),
            }
        )
        # Two Tj operators on SEPARATE lines (one Td advance down) so the locator
        # groups them into one paragraph with a "\n" at the line boundary.
        leading = _MIXED_FONT_SIZE * 1.3
        ops = [
            "BT",
            f"/F1 {_MIXED_FONT_SIZE} Tf",
            f"1 0 0 1 {_MIXED_LEFT_X} {_MIXED_TOP_Y} Tm",
            f"<{encode(line1)}> Tj",
            f"0 {-leading} Td",
            f"<{encode(line2)}> Tj",
            "ET",
        ]
        content = "\n".join(ops).encode("latin-1")
        page = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Page"),
                "/MediaBox": pikepdf.Array([0, 0, _MIXED_MEDIA_W, 792]),
                "/Resources": pikepdf.Dictionary(
                    {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(type0)})}
                ),
                "/Contents": pikepdf.Stream(pdf, content),
            }
        )
        pdf.pages.append(pikepdf.Page(page))
        pdf.save(str(out_path))
    finally:
        pdf.close()
    return line1


@_no_korean_font
def test_inv_g_10_crossline_korean_reflow_preserves_word_space_e2e(tmp_path: Path) -> None:
    """Public ``replace(reflow=True)`` keeps a Korean word space at a CROSS-LINE
    "\\n" boundary (e2e corroboration of the load-bearing font-free RED).

    Builds a synthetic Identity-H CID PDF whose Korean text is drawn across TWO
    source lines ("프로젝트 관리자" then "김철수 책임 연구원"), so the locator
    groups them into ONE paragraph whose ``full_text`` carries a "\\n" at the
    word boundary between "관리자" and "김철수". A reflow replacement that re-wraps
    the paragraph must re-join that "\\n" boundary WITHOUT gluing the words:
    ``get_text`` of the output must show "관리자 김철수" with its inter-word space
    intact, NOT the glued "관리자김철수".

    RED today: every Hangul syllable is ID-class, so the cross-line
    ``grouping_boundary_separator`` infers "" at the "\\n" boundary and
    ``_rejoin_newline_artifacts`` deletes the boundary word space.
    """
    from pdf_edit_engine import find, get_text, replace

    assert _KOREAN_FONT is not None  # gated by skipif
    # line1 (the match) ENDS in "관리자" and line2 STARTS with "김철수", so the
    # cross-line "\n" grouping boundary sits between "관리자" and "김철수". line2 is
    # WIDE, so the locator's right-margin (the rightmost extent of ANY element)
    # gives a WIDE column. new_line1 is WIDER than line1 (a genuine length change
    # -> forces reflow, not the same-length Tz-kerning path) and still ENDS in
    # "관리자". The wide column lets the re-wrapped paragraph break only at WORD
    # (space) boundaries, so "관리자" stays intact: Korean wraps freely at every
    # syllable boundary, so a NARROW column would split "관리자" into "관리"/"자"
    # across a rendered line and the test's "\n"->" " flattening would misread
    # that wrap as a spurious in-word space — a wide column keeps the assertion
    # measuring the CROSS-LINE "\n" join (its stated intent), not wrap geometry.
    line1 = "프로젝트 관리자"
    line2 = "김철수 수석 책임 연구원 최종 보고서 자료 검토 완료"
    new_line1 = "프로젝트 수석 책임 관리자"
    new_line2 = "김철수 수석 책임 연구원 최종"
    corpus = line1 + line2 + new_line1 + new_line2
    src = tmp_path / "crossline_korean_src.pdf"
    anchor = _build_two_line_identity_h_pdf_corpus(
        src, _KOREAN_FONT[0], _KOREAN_FONT[1], line1, line2, corpus
    )

    matches = find(str(src), anchor)
    assert matches, f"anchor {anchor!r} not found in synthetic cross-line Korean PDF"

    out = tmp_path / "crossline_korean_out.pdf"
    # Replace the FIRST line's matched text with a longer phrase; the reflow then
    # re-wraps the whole paragraph (which spans both source lines via the "\n").
    result = replace(str(src), matches[0], new_line1, str(out), reflow=True)
    assert result.success, f"cross-line Korean reflow must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied, "edit must route through reflow"

    extracted = get_text(str(out)).replace("\n", " ")

    # The load-bearing RED: the word space at the source-line boundary between
    # "관리자" and "김철수" must survive — the words must NOT be glued.
    assert "관리자 김철수" in extracted, (
        "cross-line reflow must preserve the Korean word space at the source-line "
        f"'\\n' boundary; expected '관리자 김철수' in output, got: {extracted!r}"
    )
    assert "관리자김철수" not in extracted, (
        "cross-line reflow must NOT glue Korean words across the source-line '\\n' "
        f"boundary; the class-inference grouping_boundary_separator returned '' for "
        f"the ID|ID boundary. Extracted: {extracted!r}"
    )
