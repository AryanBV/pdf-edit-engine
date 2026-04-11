"""Tests for font analysis, extension, and surgeon integration."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.encoding import FontResolverCache
from pdf_edit_engine.fonts import (
    _get_font_objects,
    _parse_existing_tounicode,
    analyze_subset,
    can_render,
    extend_subset,
)
from pdf_edit_engine.locator import find, get_text
from pdf_edit_engine.models import FontInfo
from pdf_edit_engine.surgeon import replace, replace_all
from pdf_edit_engine.system_fonts import find_font

CORPUS = Path(__file__).parent / "corpus"
RESUME = CORPUS / "Aryan_BV_Resume_2026.pdf"

_need_resume = pytest.mark.skipif(
    not RESUME.exists(), reason="Aryan_BV_Resume_2026.pdf not in corpus"
)


# ── TestAnalyzeSubset ────────────────────────────────────────────────────


@_need_resume
class TestAnalyzeSubset:
    """Tests for analyze_subset()."""

    def test_analyze_identity_h_font(self) -> None:
        info = analyze_subset(str(RESUME), "F1")
        assert info.name == "F1"
        assert info.encoding_type == "Identity-H"
        assert info.embedded_type == "TrueType"

    def test_analyze_winAnsi_font(self) -> None:
        info = analyze_subset(str(RESUME), "F2")
        assert info.encoding_type == "WinAnsi"

    def test_analyze_glyph_count(self) -> None:
        info = analyze_subset(str(RESUME), "F1")
        assert info.glyph_count == 6954

    def test_analyze_populates_font_cmap(self) -> None:
        info = analyze_subset(str(RESUME), "F1")
        assert info.font_cmap is not None
        assert isinstance(info.font_cmap, dict)
        assert len(info.font_cmap) > 0

    def test_analyze_subset_detection(self) -> None:
        info = analyze_subset(str(RESUME), "F1")
        assert info.is_subset is True

    def test_analyze_postscript_name(self) -> None:
        info = analyze_subset(str(RESUME), "F1")
        assert info.postscript_name == "Calibri-Bold"

    def test_analyze_cmap_contains_expected_chars(self) -> None:
        info = analyze_subset(str(RESUME), "F1")
        assert info.font_cmap is not None
        # 'A' (U+0041) should be in the embedded font's cmap
        assert ord("A") in info.font_cmap

    def test_analyze_accepts_path_object(self) -> None:
        info = analyze_subset(RESUME, "F1")
        assert info.name == "F1"


# ── TestCanRender ────────────────────────────────────────────────────────


@_need_resume
class TestCanRender:
    """Tests for can_render()."""

    def test_can_render_existing_chars(self) -> None:
        info = analyze_subset(str(RESUME), "F1")
        ok, missing = can_render(info, "Aryan")
        assert ok is True
        assert missing == []

    def test_can_render_missing_chars(self) -> None:
        info = analyze_subset(str(RESUME), "F1")
        # CJK character unlikely to be in Calibri
        ok, missing = can_render(info, "\u4e2d")
        assert ok is False
        assert "\u4e2d" in missing

    def test_can_render_empty_string(self) -> None:
        info = analyze_subset(str(RESUME), "F1")
        ok, missing = can_render(info, "")
        assert ok is True
        assert missing == []

    def test_can_render_no_cmap(self) -> None:
        info = FontInfo(
            name="test",
            postscript_name="test",
            encoding_type="Identity-H",
            is_subset=False,
            glyph_count=0,
            embedded_type="TrueType",
            font_cmap=None,
        )
        ok, missing = can_render(info, "ABC")
        assert ok is False
        assert missing == ["A", "B", "C"]

    def test_can_render_mixed(self) -> None:
        info = analyze_subset(str(RESUME), "F1")
        # 'A' in cmap, 'Z' is NOT in the 94-char embedded cmap
        ok, missing = can_render(info, "AZ")
        assert ok is False
        assert "Z" in missing
        assert "A" not in missing


# ── TestExtendSubsetTier1 ────────────────────────────────────────────────


@_need_resume
class TestExtendSubsetTier1:
    """Tests for CMap-only font extension (Tier 1)."""

    def _get_tier1_char(self) -> str | None:
        """Find a char in embedded font cmap but NOT in ToUnicode."""
        pdf = pikepdf.Pdf.open(str(RESUME))
        page = pdf.pages[0]
        font_dict, _, _ = _get_font_objects(page, "F1")
        tounicode = _parse_existing_tounicode(font_dict)
        tounicode_unicodes = set()
        for _cid, ustr in tounicode.items():
            for ch in ustr:
                tounicode_unicodes.add(ord(ch))

        info = analyze_subset(str(RESUME), "F1")
        pdf.close()
        if info.font_cmap is None:
            return None
        for cp in sorted(info.font_cmap.keys()):
            if cp not in tounicode_unicodes and 0x20 <= cp <= 0xFFFF:
                return chr(cp)
        return None

    def test_tier1_char_exists(self) -> None:
        ch = self._get_tier1_char()
        assert ch is not None, "No Tier 1 candidate found"

    def test_extend_cmap_only_tier(self) -> None:
        ch = self._get_tier1_char()
        assert ch is not None
        pdf = pikepdf.Pdf.open(str(RESUME))
        page = pdf.pages[0]
        tier = extend_subset(pdf, page, "F1", ch)
        assert tier == "cmap_only"
        pdf.close()

    def test_extend_adds_tounicode_entry(self) -> None:
        ch = self._get_tier1_char()
        assert ch is not None
        pdf = pikepdf.Pdf.open(str(RESUME))
        page = pdf.pages[0]

        font_dict, _, _ = _get_font_objects(page, "F1")
        before = _parse_existing_tounicode(font_dict)
        before_count = len(before)

        extend_subset(pdf, page, "F1", ch)

        after = _parse_existing_tounicode(font_dict)
        assert len(after) > before_count
        pdf.close()

    def test_extend_new_char_encodable(self) -> None:
        ch = self._get_tier1_char()
        assert ch is not None
        pdf = pikepdf.Pdf.open(str(RESUME))
        page = pdf.pages[0]

        cache = FontResolverCache()
        resolver = cache.get_resolver(page, "F1")
        ok_before, _ = resolver.can_encode(ch)
        assert ok_before is False

        extend_subset(pdf, page, "F1", ch)

        cache2 = FontResolverCache()
        resolver2 = cache2.get_resolver(page, "F1")
        ok_after, _ = resolver2.can_encode(ch)
        assert ok_after is True
        pdf.close()

    def test_extend_preserves_existing_text(self) -> None:
        ch = self._get_tier1_char()
        assert ch is not None
        pdf = pikepdf.Pdf.open(str(RESUME))
        page = pdf.pages[0]

        extend_subset(pdf, page, "F1", ch)

        cache = FontResolverCache()
        resolver = cache.get_resolver(page, "F1")
        # 'A', 'r', 'y', 'a', 'n' should all still be encodable
        ok, missing = resolver.can_encode("Aryan")
        assert ok is True
        assert missing == []
        pdf.close()


# ── TestExtendSubsetTier2 ────────────────────────────────────────────────


@_need_resume
class TestExtendSubsetTier2:
    """Tests for full font extension (Tier 2)."""

    @pytest.fixture
    def _has_system_font(self) -> bool:
        return find_font("Calibri-Bold") is not None

    def test_extend_full_extension_tier(self) -> None:
        # 'Z' not in embedded font's 94-char cmap → Tier 2
        system_path = find_font("Calibri-Bold")
        if system_path is None:
            pytest.skip("Calibri-Bold not installed")
        pdf = pikepdf.Pdf.open(str(RESUME))
        page = pdf.pages[0]
        tier = extend_subset(pdf, page, "F1", "Z")
        assert tier == "full_extension"
        pdf.close()

    def test_extend_full_preserves_existing(self) -> None:
        system_path = find_font("Calibri-Bold")
        if system_path is None:
            pytest.skip("Calibri-Bold not installed")
        pdf = pikepdf.Pdf.open(str(RESUME))
        page = pdf.pages[0]
        extend_subset(pdf, page, "F1", "Z")

        cache = FontResolverCache()
        resolver = cache.get_resolver(page, "F1")
        ok, missing = resolver.can_encode("Aryan")
        assert ok is True
        assert missing == []
        pdf.close()

    def test_extend_full_new_char_encodable(self) -> None:
        system_path = find_font("Calibri-Bold")
        if system_path is None:
            pytest.skip("Calibri-Bold not installed")
        pdf = pikepdf.Pdf.open(str(RESUME))
        page = pdf.pages[0]
        extend_subset(pdf, page, "F1", "Z")

        cache = FontResolverCache()
        resolver = cache.get_resolver(page, "F1")
        ok, _ = resolver.can_encode("Z")
        assert ok is True
        pdf.close()

    def test_extend_full_w_array_updated(self) -> None:
        system_path = find_font("Calibri-Bold")
        if system_path is None:
            pytest.skip("Calibri-Bold not installed")
        pdf = pikepdf.Pdf.open(str(RESUME))
        page = pdf.pages[0]
        extend_subset(pdf, page, "F1", "Z")

        _, cid_font, _ = _get_font_objects(page, "F1")
        assert cid_font is not None
        assert "/W" in cid_font
        pdf.close()

    def test_extend_full_output_pdf_valid(self) -> None:
        system_path = find_font("Calibri-Bold")
        if system_path is None:
            pytest.skip("Calibri-Bold not installed")
        pdf = pikepdf.Pdf.open(str(RESUME))
        page = pdf.pages[0]
        extend_subset(pdf, page, "F1", "Z")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out = f.name
        pdf.save(out)
        pdf.close()

        # Re-open and verify
        pdf2 = pikepdf.Pdf.open(out)
        assert len(pdf2.pages) >= 1
        pdf2.close()
        Path(out).unlink()

    def test_extend_missing_system_font_raises(self) -> None:
        pdf = pikepdf.Pdf.open(str(RESUME))
        page = pdf.pages[0]
        # Monkey-patch the font descriptor to have a nonexistent PostScript name
        from pdf_edit_engine.errors import FontNotFoundError

        with pytest.raises(FontNotFoundError):
            extend_subset(
                pdf,
                page,
                "F1",
                "Z",
                full_font_path="/nonexistent/font.ttf",
            )
        pdf.close()


# ── TestSurgeonAutoExtension ─────────────────────────────────────────────


@_need_resume
class TestSurgeonAutoExtension:
    """Tests for surgeon.py auto-extension integration."""

    def test_replace_with_missing_char_succeeds(self) -> None:
        if find_font("Calibri-Bold") is None:
            pytest.skip("Calibri-Bold not installed")
        matches = find(str(RESUME), "Aryan")
        assert len(matches) > 0

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out = f.name
        result = replace(str(RESUME), matches[0], "ArZan", out)
        assert result.success is True
        Path(out).unlink()

    def test_replace_font_action_extended(self) -> None:
        if find_font("Calibri-Bold") is None:
            pytest.skip("Calibri-Bold not installed")
        matches = find(str(RESUME), "Aryan")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out = f.name
        result = replace(str(RESUME), matches[0], "ArZan", out)
        assert result.font_action == "extended"
        Path(out).unlink()

    def test_replace_extended_text_in_output(self) -> None:
        if find_font("Calibri-Bold") is None:
            pytest.skip("Calibri-Bold not installed")
        matches = find(str(RESUME), "Aryan")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out = f.name
        result = replace(str(RESUME), matches[0], "ArZan", out)
        assert result.success is True

        text = get_text(out)
        assert "ArZan" in text
        Path(out).unlink()

    def test_replace_all_auto_extends(self) -> None:
        if find_font("Calibri-Bold") is None:
            pytest.skip("Calibri-Bold not installed")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out = f.name
        results = replace_all(str(RESUME), "Aryan", "ArZan", out)
        assert len(results) > 0
        assert results[0].success is True
        assert results[0].font_action == "extended"
        Path(out).unlink()

    def test_dry_run_reports_extension(self) -> None:
        if find_font("Calibri-Bold") is None:
            pytest.skip("Calibri-Bold not installed")
        matches = find(str(RESUME), "Aryan")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out = f.name
        result = replace(str(RESUME), matches[0], "ArZan", out, dry_run=True)
        assert result.success is True
        assert result.font_action == "extended"
        # dry_run should not write output (file may exist but be empty/original)
        Path(out).unlink(missing_ok=True)


# ── TestSystemFonts ──────────────────────────────────────────────────────


class TestSystemFonts:
    """Tests for system font discovery."""

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_find_calibri_bold(self) -> None:
        path = find_font("Calibri-Bold")
        assert path is not None
        assert Path(path).is_file()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_find_calibri_regular(self) -> None:
        path = find_font("Calibri")
        assert path is not None
        assert Path(path).is_file()

    def test_find_nonexistent_font(self) -> None:
        path = find_font("FakeFont-BoldItalicCondensedExtraWide")
        assert path is None

    def test_find_font_returns_string(self) -> None:
        # Even if font not found, return type is str | None
        result = find_font("Arial")
        assert result is None or isinstance(result, str)
