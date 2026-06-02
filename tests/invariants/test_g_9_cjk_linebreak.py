"""INV-G-9: CJK (UAX#14) line-break segmentation on reflow.

Roadmap item E.7 (CJK / UAX#14 line-break segmentation, reflow-write-side).

The INV-G-10 Latin regression lock (``test_inv_g_10_*``) lives in its own file
``test_g_10_latin_reflow_lock.py`` (one-invariant-per-file); this file is the
INV-G-9 CJK-wrap surface only.

THE BUG (INV-G-9). ``reflow.break_into_lines`` atomizes each segment with
``segment.split(" ")`` and the greedy width loop ALWAYS adds the first word of
a line unconditionally. A CJK ideographic run has NO inter-word spaces, so the
entire run is ONE 40-glyph atom that becomes one line which never wraps — a
paragraph wider than its column silently overflows (confirmed empirically:
``"今日は世界"*8`` = 40 ideographs at 11pt ≈ 440pt in a ~110pt column renders as
ONE Tj line with ``overflow_detected=False`` and zero degradations). The fix is
a stdlib-only ``pdf_edit_engine.linebreak`` leaf exposing a reduced ~8-class
UAX#14 classifier and ``break_opportunities`` / ``segment_by_opportunities`` so
spaceless ideographic runs wrap at ideograph boundaries.

THE HONEST FALLBACK (``scriptless_reflow_unsupported``). A spaceless run with NO
UAX#14 break opportunity (Thai / Lao / Khmer / Myanmar — dictionary-segmented
scripts the stdlib ``unicodedata`` East-Asian-width classifier cannot break) is
left honestly UNWRAPPED and surfaced via a NEW ``info`` Degradation. CJK (which
has ID↔ID opportunities) and Latin (which has spaces) must NOT over-fire it.

PRIMARY RED→GREEN GATE: the PURE unit probes on ``pdf_edit_engine.linebreak``
(cases a-unit, d, e-unit). They need no font and run on every platform.

CORROBORATION (skipif-gated on a CJK CID font): the width/wrap e2e probes
(a-e2e, f) build a self-contained synthetic Identity-H CID PDF carrying a few
CJK ideographs and assert the wide CJK paragraph wraps onto > 1 line with no
horizontal overflow. Today it renders as one unwrapped line.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pikepdf
import pytest

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


# ── CJK CID font fixture (self-contained, .ttc-aware) ─────────────────────
#
# The CJK-capable fonts on Windows (MS Gothic, Yu Gothic, SimSun, MS YaHei)
# are .ttc *collections*: fontTools' ``TTFont(path)`` raises
# ``TTLibFileIsCollectionError`` without a ``fontNumber``, so the shared
# corpus ``_font_covers`` / ``embed_identity_h_font`` helpers (which open
# without one) cannot be reused. This fixture handles the collection index
# itself and follows the existing skipif-on-missing-host-font precedent.

_CJK_FONT_CANDIDATES: tuple[tuple[str, int], ...] = (
    ("msgothic.ttc", 0),
    ("YuGothR.ttc", 0),
    ("YuGothM.ttc", 0),
    ("simsun.ttc", 0),
    ("msyh.ttc", 0),
    ("malgun.ttf", 0),
)

# Anchor present in the source PDF (short, fits the narrow column).
_CJK_ANCHOR = "世界平和論"  # 5 ideographs
# Wide replacement that must WRAP in the narrow column.
_CJK_WIDE = "今日は世界" * 8  # 40 ideographs
_CJK_CORPUS = _CJK_ANCHOR + _CJK_WIDE

# Narrow page: media width 200, left margin 72 -> ~110pt usable. At 11pt a
# full-width ideograph is ~11pt: 5 fit (~55pt), 40 overflow (~440pt).
_FONT_SIZE = 11.0
_MEDIA_W = 200.0
_LEFT_X = 72.0
_TOP_Y = 700.0


def _find_cjk_font() -> tuple[Path, int] | None:
    """Return ``(path, fontNumber)`` of a CJK font covering the corpus, or None.

    Loads each candidate (collections via ``fontNumber``) and checks its
    Windows-BMP cmap covers every codepoint in :data:`_CJK_CORPUS`.
    """
    from fontTools import ttLib  # type: ignore[import-untyped]

    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name, font_number in _CJK_FONT_CANDIDATES:
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
            if all(ord(ch) in cmap for ch in _CJK_CORPUS):
                return path, font_number
        except Exception:
            continue
        finally:
            font.close()
    return None


def _build_cjk_identity_h_pdf(out_path: Path, font_path: Path, font_number: int) -> str:
    """Write a 1-page Identity-H CID PDF showing :data:`_CJK_ANCHOR`.

    Returns the anchor text (so the caller can ``find()`` it). The font is
    subsetted to :data:`_CJK_CORPUS` so the wide replacement encodes without
    needing font extension.
    """
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
        for ch in sorted(set(_CJK_CORPUS)):
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
        ps_name = str(full["name"].getDebugName(6) or "CJKCorpusFont").replace(" ", "")
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
            f"/F1 {_FONT_SIZE} Tf",
            f"1 0 0 1 {_LEFT_X} {_TOP_Y} Tm",
            f"<{encode(_CJK_ANCHOR)}> Tj",
            "ET",
        ]
        content = "\n".join(ops).encode("latin-1")
        page = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Page"),
                "/MediaBox": pikepdf.Array([0, 0, _MEDIA_W, 792]),
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
    return _CJK_ANCHOR


_CJK_FONT = _find_cjk_font()
_no_cjk_font = pytest.mark.skipif(
    _CJK_FONT is None,
    reason="no CJK CID font installed covering the test corpus",
)


def _count_tj_ops(pdf_path: Path) -> int:
    """Count Tj/TJ text-show operators on page 0 (rendered line count proxy).

    PDF opens route through the engine's canonical ``open_pdf`` entry point.
    """
    from pdf_edit_engine._pathutil import open_pdf

    pdf = open_pdf(str(pdf_path))
    try:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
    finally:
        pdf.close()
    return sum(1 for _operands, op in ops if str(op) in ("Tj", "TJ"))


# ── (a) INV-G-9 — CJK wraps at ideograph boundaries ───────────────────────


def test_inv_g_9_cjk_break_opportunities_unit() -> None:
    """A spaceless CJK run exposes a break opportunity between every ideograph.

    PURE classifier probe (no font, always runs). A 40-ideograph run must yield
    >= 39 break opportunities (one per adjacent ID↔ID pair) — exactly the wrap
    points the greedy width loop needs to break the run into lines.

    RED today: ``pdf_edit_engine.linebreak`` does not exist, so the import
    fails. GREEN: the classifier exposes the ID↔ID opportunities.
    """
    from pdf_edit_engine import linebreak

    opps = linebreak.break_opportunities(_CJK_WIDE)
    assert len(opps) >= len(_CJK_WIDE) - 1, (
        f"a {len(_CJK_WIDE)}-ideograph run must expose >= {len(_CJK_WIDE) - 1} "
        f"break opportunities (ID↔ID); got {len(opps)}"
    )
    # And segmenting the run yields more than one atom (it is wrappable).
    atoms = [a for a in linebreak.segment_by_opportunities(_CJK_WIDE, opps) if a]
    assert len(atoms) > 1, f"CJK run must segment into > 1 atom; got {atoms!r}"


@_no_cjk_font
def test_inv_g_9_cjk_paragraph_wraps_no_overflow(tmp_path: Path) -> None:
    """A wide CJK paragraph wraps onto > 1 line with no overflow (e2e).

    Replaces a 5-ideograph anchor with a 40-ideograph run in a ~110pt column.

    RED today: the run is one spaceless ``split(" ")`` atom, so it renders as
    ONE unwrapped Tj line (empirically confirmed) — ``num Tj == 1``. GREEN: it
    wraps at ideograph boundaries onto multiple lines.
    """
    from pdf_edit_engine import find, replace

    assert _CJK_FONT is not None  # gated by skipif
    src = tmp_path / "cjk_src.pdf"
    anchor = _build_cjk_identity_h_pdf(src, _CJK_FONT[0], _CJK_FONT[1])

    matches = find(str(src), anchor)
    assert matches, f"anchor {anchor!r} not found in synthetic CJK PDF"
    out = tmp_path / "cjk_out.pdf"
    result = replace(str(src), matches[0], _CJK_WIDE, str(out), reflow=True)
    assert result.success, f"CJK reflow must succeed: {result!r}"

    n_lines = _count_tj_ops(out)
    assert n_lines > 1, (
        f"wide CJK replacement must WRAP onto > 1 line; got {n_lines} Tj op(s) "
        f"(today the spaceless run is one unwrapped atom)"
    )
    # The wide run replaces a SINGLE-line anchor, so the wrapped paragraph
    # legitimately grows to multiple lines and the engine shifts content below
    # down to make room — ``overflow_detected`` is True by the engine's
    # documented meaning (``extra_lines = len(lines) - paragraph.line_count >
    # 0``). The meaningful "wrapped cleanly, no content lost" condition is the
    # ABSENCE of an ``overflow_shift_*`` clamp/suppress degradation (the
    # blueprint's stated alternative): the shift landed within the page.
    shift_kinds = [
        d.kind for d in result.fidelity_report.degradations if "overflow_shift" in d.kind
    ]
    assert not shift_kinds, (
        f"a wrapped CJK paragraph must shift cleanly (no clamp/suppress); "
        f"got {shift_kinds!r}; report={result.fidelity_report!r}"
    )


# ── (d) NS / CL / CP / OP rule ─────────────────────────────────────────────


def test_inv_g_9_no_break_before_close_after_open() -> None:
    """No break BEFORE a close/ideographic-terminator; no break AFTER an open.

    - ``"世界。今"``: no opportunity immediately BEFORE the ideographic full stop
      ``。`` (U+3002, a CL even though it is W-width); the break AFTER ``。``
      before the next ideograph IS allowed.
    - ``"「世界」"``: no opportunity immediately AFTER the opening bracket ``「``
      and no opportunity immediately BEFORE the closing bracket ``」``.

    RED today: ``pdf_edit_engine.linebreak`` does not exist.
    """
    from pdf_edit_engine import linebreak

    s1 = "世界。今"  # index: 0='世' 1='界' 2='。' 3='今'
    opps1 = set(linebreak.break_opportunities(s1))
    period_idx = s1.index("。")  # == 2
    assert period_idx not in opps1, (
        f"no break opportunity BEFORE the ideographic full stop (CL) at {period_idx}; got {opps1}"
    )
    assert period_idx + 1 in opps1, (
        f"a break opportunity AFTER the full stop (before next ideograph) at "
        f"{period_idx + 1} is required; got {opps1}"
    )

    s2 = "「世界」"  # index: 0='「'(OP) 1='世' 2='界' 3='」'(CL)
    opps2 = set(linebreak.break_opportunities(s2))
    assert 1 not in opps2, (
        f"no break opportunity immediately AFTER the opening bracket; got {opps2}"
    )
    close_idx = s2.index("」")  # == 3
    assert close_idx not in opps2, (
        f"no break opportunity immediately BEFORE the closing bracket at {close_idx}; got {opps2}"
    )


# ── (e) scriptless predicate — Thai surfaces; CJK / Latin do NOT ──────────


def _scriptless_predicate(text: str, linebreak_mod: object) -> bool:
    """Mirror the GREEN ``_has_space``-based scriptless predicate (§4 NOTE).

    Fires for a spaceless run with no UAX#14 break opportunity (Thai / Lao /
    Khmer / Myanmar). MUST NOT gate on ``is_latin_simple`` (a spaceless Thai run
    is all-``AL`` and would be classified Latin-simple).
    """
    has_space = " " in text
    has_opp = linebreak_mod.has_break_opportunity(text)  # type: ignore[attr-defined]
    return (not has_space) and (not has_opp) and bool(text.strip())


def test_inv_g_9_scriptless_predicate_thai_yes_cjk_latin_no() -> None:
    """Thai fires scriptless; CJK and Latin do NOT (over-fire control).

    - Thai ``"ภาษาไทยทดสอบ"`` (no spaces, narrow, no ID): no break opportunity,
      no space -> scriptless predicate True (honest: unwrappable without a
      dictionary).
    - CJK ``"今日は世界今日は"``: has ID↔ID opportunities -> predicate False.
    - Latin ``"hello world"``: has a space -> predicate False.

    RED today: ``pdf_edit_engine.linebreak`` does not exist.
    """
    from pdf_edit_engine import linebreak

    thai = "ภาษาไทยทดสอบ"
    assert linebreak.has_break_opportunity(thai) is False, (
        "a spaceless narrow Thai run must have no UAX#14 break opportunity"
    )
    assert _scriptless_predicate(thai, linebreak) is True, "Thai must fire scriptless"

    cjk = "今日は世界今日は"
    assert linebreak.has_break_opportunity(cjk) is True, (
        "a multi-ideograph CJK run must expose ID↔ID break opportunities"
    )
    assert _scriptless_predicate(cjk, linebreak) is False, "CJK must NOT over-fire scriptless"

    latin = "hello world"
    assert _scriptless_predicate(latin, linebreak) is False, "Latin must NOT over-fire scriptless"


# ── (f) width — wrapped CJK lines each fit the column (e2e) ────────────────


@_no_cjk_font
def test_inv_g_9_wrapped_cjk_lines_fit_width(tmp_path: Path) -> None:
    """After break_into_lines each wrapped CJK line fits the column width.

    Builds the synthetic CJK PDF, resolves the paragraph's own width + metrics,
    and asserts ``break_into_lines`` returns > 1 line, each measuring
    <= paragraph_width (allowing a single-ideograph tolerance since the greedy
    loop breaks BEFORE the overflowing atom and always keeps the first).

    RED today: the spaceless run is one atom -> ``break_into_lines`` returns one
    over-wide line (``len(lines) == 1``), failing the ``> 1`` assertion.
    """
    from pdf_edit_engine import find
    from pdf_edit_engine._pathutil import open_pdf
    from pdf_edit_engine.encoding import FontResolverCache
    from pdf_edit_engine.locator import _build_index
    from pdf_edit_engine.reflow import (
        _detect_paragraphs_from_index,
        _load_widths_from_ref,
        _measure_word,
        break_into_lines,
        find_paragraph_for_match,
    )

    assert _CJK_FONT is not None  # gated by skipif
    src = tmp_path / "cjk_width_src.pdf"
    anchor = _build_cjk_identity_h_pdf(src, _CJK_FONT[0], _CJK_FONT[1])

    matches = find(str(src), anchor)
    assert matches
    match = matches[0]
    pdf = open_pdf(str(src))
    try:
        page = pdf.pages[0]
        elements = _build_index(page, match.page_number)
        para = find_paragraph_for_match(_detect_paragraphs_from_index(elements), match)
        assert para is not None, "CJK paragraph not detected"
        cache = FontResolverCache()
        resolver = cache.get_resolver(page, para.font_name.lstrip("/"))
        font_key = para.font_name if para.font_name.startswith("/") else f"/{para.font_name}"
        font_ref = page["/Resources"]["/Font"][font_key]
        widths = _load_widths_from_ref(font_ref)

        lines = break_into_lines(
            _CJK_WIDE,
            para.paragraph_width,
            resolver,
            font_ref,
            para.font_size,
        )
        assert len(lines) > 1, f"wide CJK run must break into > 1 line; got {len(lines)}: {lines!r}"

        one_glyph = para.font_size  # full-width ideograph tolerance
        too_wide = []
        for i, line in enumerate(lines):
            w = _measure_word(line, resolver, widths, para.font_size, 1.0, 0.0)
            if w > para.paragraph_width + one_glyph:
                too_wide.append(f"line {i}: width={w:.1f} > column={para.paragraph_width:.1f}")
        assert not too_wide, "wrapped CJK line(s) exceed the column width:\n" + "\n".join(too_wide)
    finally:
        pdf.close()
