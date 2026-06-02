"""INV-B-9 (P1): no-collapse-by-default ligature encoding.

B.9 root-fixes the greedy ligature-collapse bug in ``FontResolver.encode``.
Today ``encode("fi")`` greedily collapses a *typed* ``f`` + ``i`` into the
single discretionary-ligature CID (``0x012E`` in the resume's F1), corrupting
both glyph identity AND the width oracle (one ligature width instead of
``W[f] + W[i]``). The decode path (``_cid_to_unicode[302] = "fi"``) is correct
and stays; only the reverse/encode path is wrong.

The spec:
  * Typed-separate Latin runs ("fi", "office") must NOT collapse by default —
    they encode as the separate component CIDs.
  * A *mandatory* ligature (no plain-letter spacing equivalent, e.g. an Arabic
    presentation form) is ALWAYS applied, even default-OFF.
  * ``allow_discretionary_ligatures=True`` opts a caller into the discretionary
    collapse.
  * ``can_encode`` shares the exact same per-step decision with default ``encode``
    (lockstep) so it never mis-predicts encodability.
  * Every ``encode``-driven write self-verifies ``decode(encode(text))`` is
    NFKC-faithful, raising ``EncodingError`` on a genuine glyph-identity loss.
  * A ligature CID actually chosen surfaces a ``ligature_substituted`` info
    Degradation (NOT font-affecting).

These probes are PURE FontResolver unit tests on hand-built forward-map dicts
where possible (no PDF), and corpus-gated only where a resolver from a real
PDF is required.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pikepdf
import pytest

from pdf_edit_engine import find, replace
from pdf_edit_engine.encoding import FontResolver, _build_reverse_map, _classify_ligature
from pdf_edit_engine.errors import EncodingError

if TYPE_CHECKING:
    from pathlib import Path


# ── synthetic-resolver helper ───────────────────────────────────────────────
# Instantiate a CID FontResolver from a hand-built CID→Unicode forward map
# WITHOUT a PDF (bypassing __init__, which requires a pikepdf font dict). The
# decode/encode/can_encode paths consult only these instance dicts/flags, never
# a font binary, so this is sufficient and fast. Documented __new__ bypass per
# blueprint §10 (risk 7: keep in sync with the fields encode/decode read).
def _cid_resolver(forward: dict[int, str]) -> FontResolver:
    r = FontResolver.__new__(FontResolver)
    r._font_name = "T"
    r._is_cid = True
    r._byte_width = 2
    r._cid_to_unicode = dict(forward)
    # GREEN: _build_reverse_map returns (primary, ligatures). RED: it still
    # returns a single dict. Handle both so the probe fails for the RIGHT
    # reason (collapse behaviour / missing symbol), not a helper unpack crash.
    built = _build_reverse_map(forward)
    if isinstance(built, tuple):
        r._unicode_to_cid, r._ligature_to_cid = built
    else:  # pragma: no cover - RED-state single-dict shape
        r._unicode_to_cid = built
        r._ligature_to_cid = {v: k for k, v in sorted(forward.items()) if len(v) > 1}
    r._max_ligature_len = max((len(v) for v in forward.values()), default=1)
    r._untextable_cidfont = False
    r._tounicode_recovered = False
    return r


# Forward maps reused across probes.
FI_MAP = {0x66: "f", 0x69: "i", 0x12E: "fi"}
OFFICE_MAP = {
    0x6F: "o",
    0x66: "f",
    0x69: "i",
    0x63: "c",
    0x65: "e",
    0x12E: "fi",
}
# Arabic Lam-Alef MANDATORY ligature. Components OMITTED so the ONLY way to
# encode the pair is via the ligature CID — proves mandatory is forced even
# default-OFF.
LAM = "ل"
ALEF = "ا"
LAM_ALEF = LAM + ALEF
ARABIC_MANDATORY_ONLY = {0x200: LAM_ALEF}


# ── classify (pure) ─────────────────────────────────────────────────────────
class TestClassifyLigature:
    """Pin _classify_ligature on the two driving cases."""

    def test_inv_b_9_classify_fi_discretionary(self) -> None:
        # Typed 'fi' (f+i) is plain Basic-Latin: NFKC is a no-op but the chars
        # render fine as separate glyphs, so collapsing is OPTIONAL.
        assert _classify_ligature("fi") == "discretionary"

    def test_inv_b_9_classify_arabic_mandatory(self) -> None:
        # Arabic Lam-Alef has no plain-letter spacing equivalent — it MUST be
        # applied or the text is unrenderable.
        assert _classify_ligature(LAM_ALEF) == "mandatory"


# ── _build_reverse_map two-output split ─────────────────────────────────────
class TestBuildReverseMapSplit:
    """The builder splits single-codepoint values (primary) from multi-
    codepoint ligature values (separate ligature map)."""

    def test_inv_b_9_precomposed_is_single_glyph(self) -> None:
        # A precomposed ligature CHARACTER U+FB01 'ﬁ' has len == 1 — it is a
        # single real glyph and belongs in the PRIMARY map, never classified
        # as a dangerous collapse key.
        primary, ligatures = _build_reverse_map({0xFB01: "ﬁ"})
        assert primary == {"ﬁ": 0xFB01}
        assert ligatures == {}

    def test_inv_b_9_typed_pair_routes_to_ligatures(self) -> None:
        # Typed 'fi' (len == 2) is a ligature key; 'f'/'i' (len == 1) are
        # primary.
        primary, ligatures = _build_reverse_map(FI_MAP)
        assert primary == {"f": 0x66, "i": 0x69}
        assert ligatures == {"fi": 0x12E}


# ── default-OFF / opt-in / mandatory ────────────────────────────────────────
class TestEncodeLigaturePolicy:
    def test_inv_b_9_no_collapse_by_default(self) -> None:
        # B.9 CORE BUG: typed 'fi' must encode as the two SEPARATE component
        # CIDs by default, not the collapsed discretionary ligature CID.
        r = _cid_resolver(FI_MAP)
        out = r.encode("fi")
        assert out == bytes([0x00, 0x66, 0x00, 0x69])
        assert out != bytes([0x01, 0x2E])
        assert len(out) == 4  # two 2-byte CIDs

    def test_inv_b_9_opt_in_collapse(self) -> None:
        # The discretionary ligature CID is still REACHABLE when a caller
        # explicitly opts in — no capability lost.
        r = _cid_resolver(FI_MAP)
        assert r.encode("fi", allow_discretionary_ligatures=True) == bytes([0x01, 0x2E])

    def test_inv_b_9_mandatory_always_applied(self) -> None:
        # A mandatory ligature collapses despite default-OFF (the components
        # are not separately encodable here, so it MUST).
        r = _cid_resolver(ARABIC_MANDATORY_ONLY)
        assert r.encode(LAM_ALEF) == bytes([0x02, 0x00])
        # default-OFF passed explicitly is identical (mandatory ignores the flag)
        assert r.encode(LAM_ALEF, allow_discretionary_ligatures=False) == bytes([0x02, 0x00])

    def test_inv_b_9_single_char_never_ligature(self) -> None:
        # A lone component char never resolves to a ligature CID.
        r = _cid_resolver(FI_MAP)
        assert r.encode("f") == bytes([0x00, 0x66])
        assert r.encode("i") == bytes([0x00, 0x69])

    def test_inv_b_9_office_no_collapse(self) -> None:
        # The motivating real case: 'office' must encode as 6 separate glyphs,
        # NOT collapse the inner 'fi' into one ligature CID.
        r = _cid_resolver(OFFICE_MAP)
        out = r.encode("office")
        assert len(out) == 12  # 6 chars × 2-byte CID
        assert r.decode(out) == "office"
        assert bytes([0x01, 0x2E]) not in out


# ── round-trip self-verify ──────────────────────────────────────────────────
class TestRoundtripGuard:
    def test_inv_b_9_roundtrip_raises_on_defect(self) -> None:
        # A CORRUPT mandatory collapse whose decode disagrees with the
        # requested text must raise EncodingError, not silently emit a
        # glyph-identity loss. Forward map maps CID 0x300 to "X"; we force a
        # mandatory ligature entry for the Arabic pair onto that SAME CID, so
        # decode(encode(LAM_ALEF)) == "X" != LAM_ALEF (NFKC differs).
        r = _cid_resolver({0x300: "X"})
        r._ligature_to_cid = {LAM_ALEF: 0x300}
        r._max_ligature_len = max(r._max_ligature_len, len(LAM_ALEF))
        with pytest.raises(EncodingError):
            r.encode(LAM_ALEF)

    def test_inv_b_9_mandatory_precomposed_no_false_refusal(self) -> None:
        # A LEGITIMATE mandatory collapse to a precomposed glyph must NOT
        # raise: NFKC("ﬁ") == "fi", so decode of the precomposed glyph is
        # NFKC-equal to a typed 'fi' source. (Risk 1: false refusal.)
        r = _cid_resolver({0x66: "f", 0x69: "i", 0x4FB: "ﬁ"})
        # Force the precomposed-valued CID to be a MANDATORY ligature keyed on
        # the typed pair so encode picks it; decode returns the precomposed
        # char which is NFKC-equal to "fi".
        r._ligature_to_cid = {"fi": 0x4FB}
        # The forward map's values are all len 1, so _max_ligature_len was set
        # to 1 by the helper; bump it so _encode_step's greedy window actually
        # reaches the injected 2-char ligature key (mirrors the sibling
        # roundtrip-raises test which does the same).
        r._max_ligature_len = max(r._max_ligature_len, len("fi"))
        # monkeypatch classify to mandatory for this synthetic precomposed case
        import pdf_edit_engine.encoding as enc

        orig = enc._classify_ligature
        enc._classify_ligature = lambda s: "mandatory" if s == "fi" else orig(s)
        try:
            out = r.encode("fi")  # must not raise (NFKC-equal round-trip)
            assert out == bytes([0x04, 0xFB])
        finally:
            enc._classify_ligature = orig


# ── lockstep can_encode ↔ encode ────────────────────────────────────────────
class TestCanEncodeLockstep:
    def test_inv_b_9_can_encode_encode_lockstep(self) -> None:
        r = _cid_resolver(OFFICE_MAP)
        for s in ["fi", "office", "f", "i", "o", "e"]:
            ok, _missing = r.can_encode(s)
            raised = False
            try:
                r.encode(s)
            except KeyError:
                raised = True
            assert ok is (not raised), f"lockstep broken for {s!r}: can_encode={ok} raised={raised}"
        # 'fi' specifically: encodable as separate glyphs, no missing.
        ok, missing = r.can_encode("fi")
        assert ok is True
        assert missing == []


# ── width oracle ────────────────────────────────────────────────────────────
class TestWidthOracle:
    def test_inv_b_9_width_uses_separate_glyphs(self) -> None:
        # With default-OFF, encode("fi") yields CIDs f + i, so a width sum over
        # the encoded byte stream reads W[f] + W[i] = 380, NOT the collapsed
        # ligature width 350. This proves the two-pass delivers the width fix
        # with zero edits to the summing loop.
        widths = {0x66: 200, 0x69: 180, 0x12E: 350}
        r = _cid_resolver(FI_MAP)
        encoded = r.encode("fi")
        # Sum widths over the encoded 2-byte CID stride (the exact stride the
        # surgeon width oracle walks).
        total = 0
        for j in range(0, len(encoded), 2):
            cid = (encoded[j] << 8) | encoded[j + 1]
            total += widths[cid]
        assert total == 380


# ── ligature_substituted surfacing (_observed out-param) ────────────────────
class TestLigatureSubstitutedSurfacing:
    def test_inv_b_9_observed_empty_on_default_latin(self) -> None:
        # The DEFAULT path for typed-separate Latin chooses NO ligature CID, so
        # _observed stays empty → no ligature_substituted Degradation fires.
        r = _cid_resolver(OFFICE_MAP)
        obs: list[str] = []
        r.encode("office", _observed=obs)
        assert obs == []

    def test_inv_b_9_observed_records_mandatory(self) -> None:
        # A mandatory collapse records the SOURCE substring it collapsed so the
        # caller can surface a ligature_substituted Degradation.
        r = _cid_resolver(ARABIC_MANDATORY_ONLY)
        obs: list[str] = []
        r.encode(LAM_ALEF, _observed=obs)
        assert obs == [LAM_ALEF]

    def test_inv_b_9_observed_records_opt_in_discretionary(self) -> None:
        # Opt-in discretionary collapse also records the collapsed substring.
        r = _cid_resolver(FI_MAP)
        obs: list[str] = []
        r.encode("fi", allow_discretionary_ligatures=True, _observed=obs)
        assert obs == ["fi"]

    def test_inv_b_9_ligature_substituted_surfaced(self) -> None:
        # End-to-end at the report level: when _observed is non-empty, a
        # caller appends a ligature_substituted info Degradation (NOT in
        # FONT_AFFECTING_KINDS → font_preserved stays True); when empty, none.
        from pdf_edit_engine.encoding import FontResolver as _FR  # noqa: F401
        from pdf_edit_engine.models import FidelityReport

        # Mandatory path → observed populated → Degradation appended.
        r_m = _cid_resolver(ARABIC_MANDATORY_ONLY)
        obs_m: list[str] = []
        r_m.encode(LAM_ALEF, _observed=obs_m)
        report = FidelityReport(
            font_substituted=None,
            overflow_detected=False,
            reflow_applied=False,
            glyphs_missing=[],
        )
        if obs_m:
            from pdf_edit_engine.models import Degradation

            report.degradations.append(
                Degradation(
                    kind="ligature_substituted",
                    severity="info",
                    detail=f"applied ligature(s) {sorted(set(obs_m))} during re-encode",
                )
            )
        kinds = [d.kind for d in report.degradations]
        assert "ligature_substituted" in kinds
        assert report.font_preserved is True  # info kind, not font-affecting

        # Default Latin path → observed empty → no Degradation.
        r_d = _cid_resolver(OFFICE_MAP)
        obs_d: list[str] = []
        r_d.encode("office", _observed=obs_d)
        assert obs_d == []


# ── engine-level threading (public verb end-to-end) ─────────────────────────
# A SELF-CONTAINED synthetic Identity-H CID PDF that drives the surgeon's
# `observed` threading end-to-end through a PUBLIC edit verb. The class above
# only exercises the `encode(_observed=...)` out-param + a MANUALLY constructed
# Degradation; it never drives the surgeon/reflow threading that THREADS that
# out-param into the returned EditResult. This probe closes that gap: it builds
# a minimal glyf font from scratch (no system-font dependency — fontTools is a
# hard engine dependency), embeds it Identity-H with a ToUnicode CMap that maps
# a single CID to the Arabic Lam-Alef MANDATORY ligature value (components
# OMITTED so the ligature is forced even default-OFF), then calls the public
# ``replace`` verb and asserts the FidelityReport surfaces ``ligature_substituted``.
_UPM = 1000


def _square_glyph(width: int) -> object:
    """A trivial filled rectangle outline (any non-empty glyf works)."""
    from fontTools.pens.ttGlyphPen import TTGlyphPen  # type: ignore[import-untyped]

    pen = TTGlyphPen(None)
    pen.moveTo((100, 0))
    pen.lineTo((100, 700))
    pen.lineTo((width - 100, 700))
    pen.lineTo((width - 100, 0))
    pen.closePath()
    return pen.glyph()


def _build_synthetic_lig_font_bytes() -> bytes:
    """Build a 4-glyph TrueType font: .notdef, gA, gB, gLamAlef (CID==GID)."""
    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]

    glyph_order = [".notdef", "gA", "gB", "gLamAlef"]
    fb = FontBuilder(_UPM, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap({0x41: "gA", 0x42: "gB"})
    glyphs = {
        # .notdef is a visible box; outline shape is irrelevant to this probe.
        ".notdef": _square_glyph(600),
        "gA": _square_glyph(600),
        "gB": _square_glyph(600),
        "gLamAlef": _square_glyph(800),
    }
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(
        {".notdef": (600, 0), "gA": (600, 100), "gB": (600, 100), "gLamAlef": (800, 100)}
    )
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "SynthLig", "styleName": "Regular", "psName": "SynthLig"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=700)
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


def _build_synthetic_lig_pdf(path: Path) -> None:
    """Emit a 1-page Identity-H PDF drawing 'A' (CID 0001) at 72,700.

    F1's ToUnicode maps CID 0001->'A', 0002->'B', and 0003->Lam-Alef (a
    MANDATORY 2-codepoint ligature). The Lam/Alef COMPONENTS are NOT mapped to
    any standalone CID, so the only way to encode the Lam-Alef value is the
    single ligature CID — forced even on the default no-collapse path.
    """
    font_bytes = _build_synthetic_lig_font_bytes()
    pdf = pikepdf.Pdf.new()
    try:
        font_stream = pikepdf.Stream(pdf, font_bytes)
        font_stream["/Length1"] = len(font_bytes)
        fd = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/FontDescriptor"),
                    "/FontName": pikepdf.Name("/SynthLig"),
                    "/Flags": 4,
                    "/FontBBox": pikepdf.Array([0, -200, 800, 800]),
                    "/ItalicAngle": 0,
                    "/Ascent": 800,
                    "/Descent": -200,
                    "/CapHeight": 700,
                    "/StemV": 80,
                    "/FontFile2": font_stream,
                }
            )
        )
        w_flat = [1, pikepdf.Array([600]), 2, pikepdf.Array([600]), 3, pikepdf.Array([800])]
        cid_font = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/CIDFontType2"),
                    "/BaseFont": pikepdf.Name("/SynthLig"),
                    "/CIDSystemInfo": pikepdf.Dictionary(
                        {
                            "/Registry": pikepdf.String("Adobe"),
                            "/Ordering": pikepdf.String("Identity"),
                            "/Supplement": 0,
                        }
                    ),
                    "/FontDescriptor": fd,
                    "/DW": 1000,
                    "/W": pikepdf.Array(w_flat),
                    "/CIDToGIDMap": pikepdf.Name("/Identity"),
                }
            )
        )
        bfchar = f"<0001> <0041>\n<0002> <0042>\n<0003> <{ord(LAM):04X}{ord(ALEF):04X}>\n"
        tounicode = (
            "/CIDInit /ProcSet findresource begin\n"
            "12 dict begin\nbegincmap\n"
            "/CIDSystemInfo\n<< /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
            "3 beginbfchar\n" + bfchar + "endbfchar\n"
            "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
        )
        tounicode_stream = pikepdf.Stream(pdf, tounicode.encode("latin-1"))
        type0 = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/Type0"),
                    "/BaseFont": pikepdf.Name("/SynthLig"),
                    "/Encoding": pikepdf.Name("/Identity-H"),
                    "/DescendantFonts": pikepdf.Array([cid_font]),
                    "/ToUnicode": tounicode_stream,
                }
            )
        )
        content = b"BT\n/F1 24 Tf\n1 0 0 1 72 700 Tm\n<0001> Tj\nET\n"
        page = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Page"),
                "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
                "/Resources": pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": type0})}),
                "/Contents": pikepdf.Stream(pdf, content),
            }
        )
        pdf.pages.append(pikepdf.Page(page))
        pdf.save(str(path))
    finally:
        pdf.close()


class TestLigatureSubstitutedEngineThreading:
    """Drive the PUBLIC ``replace`` verb so the surgeon's ``observed`` →
    ``ligature_substituted`` threading is exercised end-to-end (not a manual
    Degradation construction)."""

    def test_inv_b_9_engine_surfaces_ligature_substituted(self, tmp_path: Path) -> None:
        # Replace 'A' (1 char, CID 0001) with the Lam-Alef MANDATORY ligature
        # (2 codepoints → DIFFERENT length → surgeon's kerning/TJ path, which
        # threads `observed` into encode()). encode() MUST collapse to the
        # single ligature CID (the components are unencodable), so the surgeon
        # records it and surfaces a ligature_substituted info Degradation on the
        # returned EditResult — proving the engine threading, not just encode().
        src = tmp_path / "synth_lig.pdf"
        out = tmp_path / "synth_lig_out.pdf"
        _build_synthetic_lig_pdf(src)

        matches = find(str(src), "A")
        assert matches, "find() failed to locate the source 'A'"
        result = replace(str(src), matches[0], LAM_ALEF, str(out), reflow=False)

        assert result.success is True
        kinds = [d.kind for d in result.fidelity_report.degradations]
        assert "ligature_substituted" in kinds, (
            f"engine did not surface ligature_substituted; got {kinds}"
        )
        # ligature_substituted is an info kind (NOT font-affecting) — the
        # typeface is preserved (same embedded font, different glyph).
        assert result.fidelity_report.font_preserved is True

    def test_inv_b_9_engine_no_over_surface_on_plain_latin(self, tmp_path: Path) -> None:
        # CONTROL: replace 'A' with 'AB' (both plain Latin, both have their own
        # CIDs, no ligature) over the SAME different-length kerning path. No
        # ligature CID is chosen, so ligature_substituted MUST NOT fire.
        src = tmp_path / "synth_lig_ctrl.pdf"
        out = tmp_path / "synth_lig_ctrl_out.pdf"
        _build_synthetic_lig_pdf(src)

        matches = find(str(src), "A")
        assert matches, "find() failed to locate the source 'A'"
        result = replace(str(src), matches[0], "AB", str(out), reflow=False)

        assert result.success is True
        kinds = [d.kind for d in result.fidelity_report.degradations]
        assert "ligature_substituted" not in kinds, (
            f"ligature_substituted over-surfaced on plain Latin; got {kinds}"
        )
