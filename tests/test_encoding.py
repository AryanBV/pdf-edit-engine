"""Tests for the FontResolver module."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pikepdf
import pytest

from pdf_edit_engine.encoding import FontResolver, FontResolverCache, _build_reverse_map

if TYPE_CHECKING:
    from collections.abc import Generator

CORPUS_DIR = Path(__file__).parent / "corpus"
RESUME_PDF = CORPUS_DIR / "Aryan_BV_Resume_2026.pdf"


def _synth_cid_resolver(forward: dict[int, str]) -> FontResolver:
    """Instantiate a CID FontResolver from a hand-built CID→Unicode map WITHOUT
    a PDF (bypassing __init__). encode/decode/can_encode consult only these
    instance dicts/flags. Used by the B.9-recharacterized ligature tests so
    they can exercise the no-collapse default on a map that carries separate
    component CIDs (the resume's F1 lacks a standalone 'f' CID)."""
    r = FontResolver.__new__(FontResolver)
    r._font_name = "T"
    r._is_cid = True
    r._byte_width = 2
    r._cid_to_unicode = dict(forward)
    built = _build_reverse_map(forward)
    if isinstance(built, tuple):  # GREEN: (primary, ligatures)
        r._unicode_to_cid, r._ligature_to_cid = built
    else:  # RED-state single-dict shape
        r._unicode_to_cid = built
        r._ligature_to_cid = {v: k for k, v in sorted(forward.items()) if len(v) > 1}
    r._max_ligature_len = max((len(v) for v in forward.values()), default=1)
    r._untextable_cidfont = False
    r._tounicode_recovered = False
    return r


pytestmark = pytest.mark.skipif(
    not RESUME_PDF.exists(), reason="Aryan_BV_Resume_2026.pdf not in corpus"
)


@pytest.fixture
def resume_pdf() -> Generator[pikepdf.Pdf, None, None]:
    """Open the resume PDF and keep it alive for the test."""
    pdf = pikepdf.open(RESUME_PDF)
    yield pdf
    pdf.close()


@pytest.fixture
def f1_resolver(resume_pdf: pikepdf.Pdf) -> FontResolver:
    """FontResolver for F1 (Calibri-Bold, Identity-H)."""
    font_dict = resume_pdf.pages[0]["/Resources"]["/Font"]["/F1"]
    return FontResolver(pikepdf.Dictionary(font_dict), "F1")


@pytest.fixture
def f2_resolver(resume_pdf: pikepdf.Pdf) -> FontResolver:
    """FontResolver for F2 (Calibri-Bold, WinAnsi)."""
    font_dict = resume_pdf.pages[0]["/Resources"]["/Font"]["/F2"]
    return FontResolver(pikepdf.Dictionary(font_dict), "F2")


@pytest.fixture
def f5_resolver(resume_pdf: pikepdf.Pdf) -> FontResolver:
    """FontResolver for F5 (SymbolMT, Identity-H)."""
    font_dict = resume_pdf.pages[0]["/Resources"]["/Font"]["/F5"]
    return FontResolver(pikepdf.Dictionary(font_dict), "F5")


class TestIdentityHDecode:
    """Tests for decoding Identity-H CIDFont bytes to Unicode."""

    def test_decode_single_char(self, f1_resolver: FontResolver) -> None:
        # CID 4 -> 'A'
        assert f1_resolver.decode(bytes([0x00, 0x04])) == "A"

    def test_decode_space(self, f1_resolver: FontResolver) -> None:
        # CID 3 -> ' '
        assert f1_resolver.decode(bytes([0x00, 0x03])) == " "

    def test_decode_multiple_chars(self, f1_resolver: FontResolver) -> None:
        # CID 4='A', CID 3=' ', CID 17='B'
        result = f1_resolver.decode(bytes([0x00, 0x04, 0x00, 0x03, 0x00, 0x11]))
        assert result == "A B"

    def test_decode_ligature_fi(self, f1_resolver: FontResolver) -> None:
        # CID 302 (0x012E) -> 'fi'
        assert f1_resolver.decode(bytes([0x01, 0x2E])) == "fi"

    def test_decode_ligature_ft(self, f1_resolver: FontResolver) -> None:
        # CID 332 (0x014C) -> 'ft'
        assert f1_resolver.decode(bytes([0x01, 0x4C])) == "ft"

    def test_decode_symbol_bullet(self, f5_resolver: FontResolver) -> None:
        # CID 120 (0x0078) -> U+2022 (bullet)
        assert f5_resolver.decode(bytes([0x00, 0x78])) == "\u2022"

    def test_decode_unknown_cid_raises(self, f1_resolver: FontResolver) -> None:
        with pytest.raises(KeyError):
            f1_resolver.decode(bytes([0xFF, 0xFF]))


class TestIdentityHEncode:
    """Tests for encoding Unicode to Identity-H CIDFont bytes."""

    def test_encode_single_char(self, f1_resolver: FontResolver) -> None:
        assert f1_resolver.encode("A") == bytes([0x00, 0x04])

    def test_encode_space(self, f1_resolver: FontResolver) -> None:
        assert f1_resolver.encode(" ") == bytes([0x00, 0x03])

    def test_encode_ligature_fi_no_collapse_by_default(self) -> None:
        # B.9 RECHARACTERIZED: the OLD assertion baked in the exact bug
        # INV-B-9 fixes — greedy collapse of typed-separate 'f'+'i' into the
        # single discretionary-ligature CID 0x012E, which corrupts glyph
        # identity AND the width oracle (one ligature width instead of
        # W[f]+W[i]). The CORRECT default is no-collapse: typed 'fi' encodes
        # as the two separate component CIDs so the rendered text matches the
        # typed text. F1 in the resume lacks a standalone 'f' CID (it only
        # ever uses the 'fi' ligature), so per blueprint §7A this no-collapse
        # assertion uses a synthetic forward-map resolver that DOES carry
        # separate 'f'/'i' CIDs (the round-trip + opt-in companion below prove
        # no fidelity and no capability were lost). Against the current greedy
        # code this FAILS (it collapses to 0x012E) = RED for the right reason.
        r = _synth_cid_resolver({0x66: "f", 0x69: "i", 0x12E: "fi"})
        out = r.encode("fi")
        assert out != bytes([0x01, 0x2E])
        assert len(out) == 4  # two 2-byte CIDs
        assert out == bytes([0x00, 0x66, 0x00, 0x69])
        assert r.decode(out) == "fi"

    def test_encode_ligature_fi_opt_in_collapses(self) -> None:
        # B.9: the discretionary ligature CID is still REACHABLE when a caller
        # explicitly opts in — proving no capability was removed, only the
        # unsafe DEFAULT collapse. RED: the kw-only param does not yet exist.
        r = _synth_cid_resolver({0x66: "f", 0x69: "i", 0x12E: "fi"})
        assert r.encode("fi", allow_discretionary_ligatures=True) == bytes([0x01, 0x2E])

    def test_encode_unencodable_raises(self, f1_resolver: FontResolver) -> None:
        with pytest.raises(KeyError):
            f1_resolver.encode("\u4e2d")  # Chinese character


class TestRoundTrip:
    """Tests for decode(encode(text)) == text."""

    def test_roundtrip_identity_h(self, f1_resolver: FontResolver) -> None:
        for text in ["A", "B", "D", " "]:
            assert f1_resolver.decode(f1_resolver.encode(text)) == text

    def test_roundtrip_identity_h_ligature(
        self,
        f1_resolver: FontResolver,
    ) -> None:
        # B.9 RECHARACTERIZED: the resume's F1 maps CID 302 -> "fi" but has NO
        # standalone 'f' CID (the font only ever uses the 'fi' ligature). Under
        # the corrected default-OFF policy, typed "fi" no longer silently reuses
        # the DISCRETIONARY ligature glyph, so on F1 it becomes unencodable
        # (the 'f' component is absent) — the honest result is a KeyError that
        # triggers font extension on the write path. The original assertion
        # baked in the old greedy collapse; the round-trip capability is proven
        # via the explicit opt-in below (the ligature CID is still reachable).
        with pytest.raises(KeyError):
            f1_resolver.encode("fi")
        assert (
            f1_resolver.decode(f1_resolver.encode("fi", allow_discretionary_ligatures=True)) == "fi"
        )

    def test_roundtrip_winAnsi(self, f2_resolver: FontResolver) -> None:
        for text in ["A", "B", " ", "0", "z"]:
            assert f2_resolver.decode(f2_resolver.encode(text)) == text


class TestCanEncode:
    """Tests for can_encode() checking."""

    def test_can_encode_present_chars(
        self,
        f1_resolver: FontResolver,
    ) -> None:
        ok, missing = f1_resolver.can_encode("A")
        assert ok is True
        assert missing == []

    def test_can_encode_missing_char(
        self,
        f1_resolver: FontResolver,
    ) -> None:
        ok, missing = f1_resolver.can_encode("\u4e2d")
        assert ok is False
        assert "\u4e2d" in missing

    def test_can_encode_mixed(self, f1_resolver: FontResolver) -> None:
        ok, missing = f1_resolver.can_encode("A\u4e2d")
        assert ok is False
        assert len(missing) == 1

    def test_can_encode_winAnsi(self, f2_resolver: FontResolver) -> None:
        # v0.1.3 strengthens can_encode to verify glyph coverage, not just
        # encoding-map membership. F2 in the resume is Calibri-Bold/WinAnsi
        # with /FirstChar=/LastChar=32 — only space has a /Widths entry, so
        # only space is encodable from this resolver. The test assertion
        # was pinning the lax v0.1.2 behavior; v0.1.3 correctly reports
        # ABC as missing because their bytes lack /Widths entries (the
        # font dict is heavily subsetted to space). See INV-J-5 probe for
        # the surface contract on the new behavior.
        ok, missing = f2_resolver.can_encode(" ")
        assert ok is True
        assert missing == []
        # And the strengthening contract: chars without /Widths entries
        # report as missing, even though the encoding map has them.
        ok2, missing2 = f2_resolver.can_encode("ABC")
        assert ok2 is False
        assert set(missing2) == {"A", "B", "C"}

    def test_can_encode_ligature_sequence(
        self,
        f1_resolver: FontResolver,
    ) -> None:
        """can_encode stays LOCKSTEP with the default-OFF encode (B.9).

        RECHARACTERIZED: the resume's F1 maps CID 302 -> "fi" via a
        DISCRETIONARY ligature but has NO standalone 'f' CID. The old
        assertion expected can_encode("fi") == (True, []) because the OLD
        greedy encode collapsed typed 'f'+'i' into CID 302. Under the corrected
        default-OFF policy can_encode shares the exact per-step decision with
        the default encode (no discretionary collapse), so it honestly reports
        'f' as missing — exactly matching that encode("fi") would KeyError on
        F1. This pins the lockstep contract rather than the old collapse.

        F1's EXACT glyph set (empirically probed against the resume PDF):
        standalone 'f' is ABSENT, standalone 'i' is PRESENT at CID 0x015D, and
        the 'fi' DISCRETIONARY ligature is PRESENT at CID 0x012E. So default-OFF
        can_encode("fi") refuses ONLY on the missing 'f' (NOT on 'i'); the
        missing == ["f"] result below is the direct consequence of that set.
        """
        ok, missing = f1_resolver.can_encode("fi")
        assert ok is False
        # F1 has 'i' (CID 0x015D) but lacks standalone 'f'; only 'f' is missing.
        assert missing == ["f"]

    def test_can_encode_ligature_in_context(
        self,
        f1_resolver: FontResolver,
    ) -> None:
        """can_encode handles ligatures surrounded by normal characters (B.9).

        RECHARACTERIZED for the same reason as test_can_encode_ligature_sequence:
        'A' has a standalone CID, but F1's typed 'fi' is a discretionary
        ligature whose 'f' component is absent, so default-OFF can_encode
        reports 'f' missing (lockstep with the default encode).
        """
        ok, missing = f1_resolver.can_encode("Afi")
        assert ok is False
        assert missing == ["f"]


class TestWinAnsi:
    """Tests for WinAnsiEncoding decode/encode."""

    def test_decode_ascii(self, f2_resolver: FontResolver) -> None:
        assert f2_resolver.decode(bytes([0x41])) == "A"

    def test_decode_space(self, f2_resolver: FontResolver) -> None:
        assert f2_resolver.decode(bytes([0x20])) == " "

    def test_encode_ascii(self, f2_resolver: FontResolver) -> None:
        assert f2_resolver.encode("A") == bytes([0x41])

    def test_encode_space(self, f2_resolver: FontResolver) -> None:
        """Space must encode to 0x20, not 0xAD (soft hyphen)."""
        assert f2_resolver.encode(" ") == bytes([0x20])

    def test_decode_multiple(self, f2_resolver: FontResolver) -> None:
        assert f2_resolver.decode(bytes([0x48, 0x69])) == "Hi"


class TestEncodingType:
    """Tests for encoding type properties."""

    def test_identity_h_properties(
        self,
        f1_resolver: FontResolver,
    ) -> None:
        assert f1_resolver.encoding_type == "Identity-H"
        assert f1_resolver.is_cid_font is True
        assert f1_resolver.byte_width == 2

    def test_winAnsi_properties(self, f2_resolver: FontResolver) -> None:
        assert f2_resolver.encoding_type == "WinAnsi"
        assert f2_resolver.is_cid_font is False
        assert f2_resolver.byte_width == 1

    def test_symbol_identity_h(self, f5_resolver: FontResolver) -> None:
        assert f5_resolver.encoding_type == "Identity-H"
        assert f5_resolver.is_cid_font is True


class TestFontResolverCache:
    """Tests for FontResolverCache caching behavior."""

    def test_cache_returns_same_instance(
        self,
        resume_pdf: pikepdf.Pdf,
    ) -> None:
        cache = FontResolverCache()
        page = resume_pdf.pages[0]
        r1 = cache.get_resolver(page, "F1")
        r2 = cache.get_resolver(page, "F1")
        assert r1 is r2

    def test_cache_different_fonts(
        self,
        resume_pdf: pikepdf.Pdf,
    ) -> None:
        cache = FontResolverCache()
        page = resume_pdf.pages[0]
        r1 = cache.get_resolver(page, "F1")
        r3 = cache.get_resolver(page, "F3")
        assert r1 is not r3

    def test_cache_resolver_works(
        self,
        resume_pdf: pikepdf.Pdf,
    ) -> None:
        cache = FontResolverCache()
        page = resume_pdf.pages[0]
        r = cache.get_resolver(page, "F1")
        assert r.decode(bytes([0x00, 0x04])) == "A"
