"""Tests for the FontResolver module."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pikepdf
import pytest

from pdf_edit_engine.encoding import FontResolver, FontResolverCache

if TYPE_CHECKING:
    from collections.abc import Generator

CORPUS_DIR = Path(__file__).parent / "corpus"
RESUME_PDF = CORPUS_DIR / "Aryan_BV_Resume_2026.pdf"

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

    def test_encode_ligature_fi(self, f1_resolver: FontResolver) -> None:
        # 'fi' should encode as single CID 302
        assert f1_resolver.encode("fi") == bytes([0x01, 0x2E])

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
        assert f1_resolver.decode(f1_resolver.encode("fi")) == "fi"

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
        """can_encode accepts ligature sequences like 'fi' that encode() handles."""
        ok, missing = f1_resolver.can_encode("fi")
        assert ok is True
        assert missing == []

    def test_can_encode_ligature_in_context(
        self,
        f1_resolver: FontResolver,
    ) -> None:
        """can_encode handles ligatures surrounded by normal characters."""
        # 'A' has standalone CID, 'fi' is a ligature — both should pass
        ok, missing = f1_resolver.can_encode("Afi")
        assert ok is True
        assert missing == []


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
