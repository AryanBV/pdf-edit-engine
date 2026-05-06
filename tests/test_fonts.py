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


# ── ARY-278: Tier 1.5 in-place glyph injection ───────────────────────────

import io  # noqa: E402

from tests._identity_h_fixture import (  # noqa: E402
    _build_identity_h_pdf,
    _find_ttf_for_cidfont,
    _no_ttf,
    _title_match,
)


@_no_ttf
class TestTier1_5GlyphInjection:
    """ARY-278: Tier 1.5 in-place glyph injection (replaces buggy Tier 2)."""

    def test_ttfont_roundtrip_smoke(self, tmp_path: Path) -> None:
        """fontTools must load -> save the embedded TTF without corruption.

        Fail-fast checkpoint for the whole Tier 1.5 approach. If fontTools
        cannot round-trip a /FontFile2 subset, everything downstream is moot.
        """
        from fontTools.ttLib import TTFont

        src = tmp_path / "in.pdf"
        assert _build_identity_h_pdf(
            src,
            title_text="Acme",
            title_pattern="single_tj",
            extra_corpus="Nova",
        )
        with pikepdf.Pdf.open(str(src)) as pdf:
            font_obj = pdf.pages[0]["/Resources"]["/Font"]["/F1"]
            cid_font_obj = font_obj["/DescendantFonts"][0]
            fd = cid_font_obj["/FontDescriptor"]
            raw_font_bytes = bytes(fd["/FontFile2"].read_bytes())

            embedded = TTFont(io.BytesIO(raw_font_bytes))
            original_glyph_count = len(embedded.getGlyphOrder())

            buf = io.BytesIO()
            embedded.save(buf)
            roundtripped = buf.getvalue()
            embedded.close()

            # Reload the round-tripped font — if save() corrupted it, this fails
            reloaded = TTFont(io.BytesIO(roundtripped))
            try:
                assert len(reloaded.getGlyphOrder()) == original_glyph_count
                # Sanity: cmap is intact
                cmap = reloaded.getBestCmap() or {}
                assert ord("A") in cmap
            finally:
                reloaded.close()

    def test_strip_glyph_hinting_simple_glyph(self) -> None:
        """_strip_glyph_hinting must zero out a glyph's program bytecode."""
        from fontTools.ttLib import TTFont

        from pdf_edit_engine.fonts import _strip_glyph_hinting

        ttf = _find_ttf_for_cidfont()
        if ttf is None:
            pytest.skip("no TTF font available")
        font = TTFont(str(ttf))
        try:
            glyph = font["glyf"]["A"]
            _strip_glyph_hinting(glyph)
            after_bytes = (
                getattr(glyph.program, "bytecode", b"") or b"" if hasattr(glyph, "program") else b""
            )
            assert len(after_bytes) == 0
        finally:
            font.close()

    def test_collect_component_names_simple_glyph(self) -> None:
        """A simple glyph (non-composite) must return an empty component list."""
        from fontTools.ttLib import TTFont

        from pdf_edit_engine.fonts import _collect_component_names

        ttf = _find_ttf_for_cidfont()
        if ttf is None:
            pytest.skip("no TTF font available")
        font = TTFont(str(ttf))
        try:
            glyph = font["glyf"]["A"]  # 'A' is simple in Arial/Liberation/DejaVu
            names = _collect_component_names(glyph, font)
            assert names == []
        finally:
            font.close()

    def test_collect_component_names_composite_glyph(self) -> None:
        """A composite glyph (accented char) must return its component chain."""
        from fontTools.ttLib import TTFont

        from pdf_edit_engine.fonts import _collect_component_names

        ttf = _find_ttf_for_cidfont()
        if ttf is None:
            pytest.skip("no TTF font available")
        font = TTFont(str(ttf))
        try:
            # Try common composite glyph names
            candidates = [
                "Aacute",
                "Eacute",
                "Adieresis",
                "Aring",
                "agrave",
                "eacute",
            ]
            composite = None
            composite_name = None
            for name in candidates:
                if name in font["glyf"].glyphs:
                    g = font["glyf"][name]
                    if g.isComposite():
                        composite = g
                        composite_name = name
                        break
            if composite is None:
                pytest.skip("no recognized composite glyph in font — skipping")
            names = _collect_component_names(composite, font)
            assert len(names) >= 1, f"composite {composite_name!r} returned no components"
        finally:
            font.close()

    def test_inject_glyph_in_place_simple_latin(self, tmp_path: Path) -> None:
        """Inject 'Z' from system font into a subset that lacks it."""
        from fontTools.ttLib import TTFont

        from pdf_edit_engine.fonts import _inject_glyph_in_place

        ttf = _find_ttf_for_cidfont()
        if ttf is None:
            pytest.skip("no TTF font available")

        src = tmp_path / "in.pdf"
        assert _build_identity_h_pdf(
            src,
            title_text="Acme",
            title_pattern="single_tj",
            extra_corpus="",
        )
        with pikepdf.Pdf.open(str(src)) as pdf:
            fd = pdf.pages[0]["/Resources"]["/Font"]["/F1"]["/DescendantFonts"][0][
                "/FontDescriptor"
            ]
            embedded_bytes = bytes(fd["/FontFile2"].read_bytes())
            embedded = TTFont(io.BytesIO(embedded_bytes))
            system = TTFont(str(ttf))
            try:
                assert 0x5A not in (embedded.getBestCmap() or {})

                original_count = len(embedded.getGlyphOrder())
                new_gid = _inject_glyph_in_place(embedded, system, "Z")

                assert new_gid == original_count
                assert len(embedded.getGlyphOrder()) == original_count + 1
                assert 0x5A in (embedded.getBestCmap() or {})

                buf = io.BytesIO()
                embedded.save(buf)
                reloaded = TTFont(io.BytesIO(buf.getvalue()))
                try:
                    assert 0x5A in (reloaded.getBestCmap() or {})
                finally:
                    reloaded.close()
            finally:
                embedded.close()
                system.close()

    def test_inject_glyph_rejects_upem_mismatch(self, tmp_path: Path) -> None:
        """If embedded and system upem differ, abort with FontNotFoundError."""
        from fontTools.ttLib import TTFont

        from pdf_edit_engine.errors import FontNotFoundError
        from pdf_edit_engine.fonts import _inject_glyph_in_place

        ttf = _find_ttf_for_cidfont()
        if ttf is None:
            pytest.skip("no TTF font available")

        src = tmp_path / "in.pdf"
        assert _build_identity_h_pdf(src, title_text="Acme", title_pattern="single_tj")
        with pikepdf.Pdf.open(str(src)) as pdf:
            fd = pdf.pages[0]["/Resources"]["/Font"]["/F1"]["/DescendantFonts"][0][
                "/FontDescriptor"
            ]
            embedded = TTFont(io.BytesIO(bytes(fd["/FontFile2"].read_bytes())))
            system = TTFont(str(ttf))
            try:
                system["head"].unitsPerEm = embedded["head"].unitsPerEm + 1
                with pytest.raises(FontNotFoundError, match="unitsPerEm"):
                    _inject_glyph_in_place(embedded, system, "Z")
            finally:
                embedded.close()
                system.close()

    def test_inject_glyph_rejects_missing_char(self, tmp_path: Path) -> None:
        """Character not in the system font cmap must abort."""
        from fontTools.ttLib import TTFont

        from pdf_edit_engine.errors import FontNotFoundError
        from pdf_edit_engine.fonts import _inject_glyph_in_place

        ttf = _find_ttf_for_cidfont()
        if ttf is None:
            pytest.skip("no TTF font available")

        src = tmp_path / "in.pdf"
        assert _build_identity_h_pdf(src, title_text="Acme", title_pattern="single_tj")
        with pikepdf.Pdf.open(str(src)) as pdf:
            fd = pdf.pages[0]["/Resources"]["/Font"]["/F1"]["/DescendantFonts"][0][
                "/FontDescriptor"
            ]
            embedded = TTFont(io.BytesIO(bytes(fd["/FontFile2"].read_bytes())))
            system = TTFont(str(ttf))
            try:
                if 0xE000 in (system.getBestCmap() or {}):
                    pytest.skip("system font has U+E000 — pick another sentinel")
                with pytest.raises(FontNotFoundError, match="not in system font"):
                    _inject_glyph_in_place(embedded, system, "\ue000")
            finally:
                embedded.close()
                system.close()

    def test_tier2_narrow_subset_succeeds(self, tmp_path: Path) -> None:
        """End-to-end: narrow subset replacement must succeed via Tier 1.5."""
        from pdf_edit_engine.locator import get_text
        from pdf_edit_engine.surgeon import replace

        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        # Narrow subset: omit N, I, v, d, u, s, w, W from the embedded cmap
        assert _build_identity_h_pdf(
            src,
            title_text="Acme Corporation",
            title_pattern="single_tj",
            extra_corpus="",
            omit_chars_from_subset="NIvduswW",
        )
        match = _title_match(str(src), "Acme Corporation")
        result = replace(str(src), match, "Nova Industries", str(out))
        assert result.success is True, (
            f"Tier 1.5 must succeed, got font_action={result.font_action} "
            f"missing={result.fidelity_report.glyphs_missing}"
        )
        text = get_text(str(out))
        assert "Nova Industries" in text

    def test_tier2_narrow_subset_preserves_untouched_text(self, tmp_path: Path) -> None:
        """Injection must not corrupt pre-existing text on the same page."""
        from pdf_edit_engine.locator import get_text
        from pdf_edit_engine.surgeon import replace

        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        # Body line uses ONLY chars that are in the narrow subset (all chars
        # from "Acme Corporation"). If Tier 1.5 corrupts the font, the body's
        # pre-existing CIDs would map to wrong glyphs in extracted text.
        # The body is at 12pt, so _title_match picks the 24pt title match.
        assert _build_identity_h_pdf(
            src,
            title_text="Acme Corporation",
            body_text="Acme Corporation at top",
            title_pattern="single_tj",
            extra_corpus="",
            omit_chars_from_subset="NIvduswW",
        )
        match = _title_match(str(src), "Acme Corporation")
        result = replace(str(src), match, "Nova Industries", str(out))
        assert result.success is True
        text = get_text(str(out))
        assert "Nova Industries" in text
        # Body text must survive the font mutation untouched.
        # After title replacement, body line still reads "Acme Corporation at top".
        assert "Acme Corporation at top" in text, f"body text corrupted after injection: {text!r}"

    def test_glyph_width_cache_evict_public(self) -> None:
        """GlyphWidthCache.evict drops cached widths for a single font."""
        from pdf_edit_engine.widths import GlyphWidthCache

        cache = GlyphWidthCache()
        cache._cache["F1"] = {5: 500.0}  # noqa: SLF001
        assert "F1" in cache._cache  # noqa: SLF001
        cache.evict("F1")
        assert "F1" not in cache._cache  # noqa: SLF001
        cache.evict("F99")  # no-op for missing key — must not raise

    def test_tier1_extension_dedup_cmap_entries(self, tmp_path: Path) -> None:
        """Repeat extend_subset must not duplicate CMap bfchar lines on disk.

        Checks the raw ToUnicode stream bytes, not the parsed dict —
        duplicate bfchar lines still produce a logically-correct parse
        (last write wins) but cause O(n × extensions) on-disk bloat.
        """
        src = tmp_path / "in.pdf"
        assert _build_identity_h_pdf(
            src,
            title_text="Acme",
            title_pattern="single_tj",
            extra_corpus="Z",
        )
        with pikepdf.Pdf.open(str(src), allow_overwriting_input=True) as pdf:
            page = pdf.pages[0]
            extend_subset(pdf, page, "F1", "Z")
            font_dict = pdf.pages[0]["/Resources"]["/Font"]["/F1"]
            stream_after_first = bytes(font_dict["/ToUnicode"].read_bytes())
            size_1 = len(stream_after_first)

            # Second call with same char — must be a true no-op
            extend_subset(pdf, page, "F1", "Z")
            font_dict = pdf.pages[0]["/Resources"]["/Font"]["/F1"]
            stream_after_second = bytes(font_dict["/ToUnicode"].read_bytes())
            size_2 = len(stream_after_second)

            assert size_2 == size_1, (
                f"ToUnicode stream grew from {size_1} to {size_2} bytes "
                f"on repeat call — duplicate bfchar lines appended"
            )


# ── TestFontFile2CacheEviction (IMP-1) ───────────────────────────────


@_need_resume
class TestFontFile2CacheEviction:
    """IMP-1: ``_FONTFILE2_CACHE`` is evicted after every ``/FontFile2``
    write so subsequent ``font_has_codepoint`` queries observe the
    just-injected glyphs.

    The bug is latent at v0.1.3 — Agent H's compounding diagnostic
    exercised double-extension on the same font dict without observable
    corruption. Likely pikepdf updates ``objgen`` on stream rewrite, so
    the cache key changes naturally and the stale entry is never
    consulted. Per the prompt's IMP-1 directive: "ship the eviction
    defensively". The test is a regression guard against future
    regressions where:

    1. pikepdf changes such that ``objgen`` is preserved across stream
       rewrites (cache key would no longer change naturally), OR
    2. a refactor introduces a code path that calls
       ``font_has_codepoint`` for the just-injected codepoint in the
       same pdf-object scope, making the cache key collision explicit.

    Behavioral test (no white-box poking of ``_FONTFILE2_CACHE``).
    Double-extension scenario per the prompt's spec: ø at step 1, then
    ü on the same logical font dict at step 2. Step 2 must report ü as
    a fresh extension with no stale ø entry leaking from step 1.

    Single-source-of-truth char selection: ø and ü are both verified
    missing from every embedded font on the resume corpus page 0 (F1
    cmap has 94 ASCII-range entries; verified during plan dev). ø is
    shared with ``tests/test_structural.py::TestInsertTextBlockHonesty``
    so future maintainers don't re-derive the choice.
    """

    def test_double_extension_different_codepoints(self, tmp_path: Path) -> None:
        if find_font("Calibri-Bold") is None:
            pytest.skip("Calibri-Bold not installed; cannot exercise Tier 1.5 path")

        step1 = str(tmp_path / "step1.pdf")
        step2 = str(tmp_path / "step2.pdf")

        # Step 1: replace "Aryan" with "Aryanø" — Calibri-Bold subset
        # lacks ø, so extend_subset runs and Tier 1.5 injects ø into
        # /FontFile2.
        r1_list = replace_all(str(RESUME), "Aryan", "Aryanø", step1)
        assert r1_list, "step 1: no matches (corpus invariant broken)"
        r1 = r1_list[0]
        assert r1.success is True
        assert r1.font_action == "extended"
        r1_cov = [
            d
            for d in r1.fidelity_report.degradations
            if d.kind in ("font_coverage_extended", "font_coverage_substituted")
        ]
        assert any("ø" in d.detail for d in r1_cov)

        # Step 2: replace "Aryanø" with "Aryanü" — ü is also missing
        # from the Calibri-Bold subset, so Tier 1.5 must run again as
        # a fresh extension for ü. With cache eviction (the fix), any
        # font_has_codepoint queries during step 2 read the current
        # /FontFile2 freshly. Without eviction, in scenarios where the
        # cache key is preserved, a stale read could spuriously report
        # ø as missing and append a duplicate ø entry to step 2's
        # degradations.
        r2_list = replace_all(step1, "Aryanø", "Aryanü", step2)
        assert r2_list, "step 2: no matches"
        r2 = r2_list[0]
        assert r2.success is True
        # Step 2 must report a fresh extension event for ü:
        assert r2.font_action == "extended"
        r2_cov = [
            d
            for d in r2.fidelity_report.degradations
            if d.kind in ("font_coverage_extended", "font_coverage_substituted")
        ]
        assert any("ü" in d.detail for d in r2_cov), (
            "step 2 did not surface ü as a fresh extension; "
            f"r2 degradations: {r2.fidelity_report.degradations!r}"
        )
        # Step 2 must NOT have an entry whose chars-list is just ø
        # (would indicate a stale-cache leak from step 1).
        # Detail format: "tier=N,chars=<csv>[,source=<name>]".
        for d in r2_cov:
            chars_part = next(
                (p[len("chars=") :] for p in d.detail.split(",") if p.startswith("chars=")),
                "",
            )
            assert chars_part != "ø", (
                "step 2 has a stale ø-only coverage Degradation "
                f"({d!r}) — _FONTFILE2_CACHE may be leaking pre-step-1 state"
            )

    def test_resolver_cache_shared_font_cross_page_evict(self, tmp_path: Path) -> None:
        """Pages sharing a font via indirect ref must share one cache entry.

        After ARY-278 re-keys FontResolverCache by font dict objgen,
        two pages whose /Resources/Font points to the same indirect
        Type0 font object share a single resolver instance. Evicting
        on one page clears it for every page that references the same
        font.
        """
        from pdf_edit_engine.encoding import FontResolverCache

        src = tmp_path / "in.pdf"
        assert _build_identity_h_pdf(
            src,
            title_text="Acme",
            title_pattern="single_tj",
            extra_corpus="Nova",
        )
        # Duplicate page 0 to create a second page sharing /Resources
        with pikepdf.Pdf.open(str(src), allow_overwriting_input=True) as pdf:
            p1 = pdf.pages[0]
            p2_dict = pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Page"),
                    "/MediaBox": p1["/MediaBox"],
                    "/Resources": p1["/Resources"],
                    "/Contents": p1["/Contents"],
                }
            )
            pdf.pages.append(pikepdf.Page(p2_dict))
            pdf.save(str(src))

        with pikepdf.Pdf.open(str(src)) as pdf:
            cache = FontResolverCache()
            r1 = cache.get_resolver(pdf.pages[0], "F1")
            r2 = cache.get_resolver(pdf.pages[1], "F1")
            assert r1 is r2, "shared font must resolve to same cached instance"
            cache.evict(pdf.pages[0], "F1")
            r2_after = cache.get_resolver(pdf.pages[1], "F1")
            assert r2_after is not r1, (
                "evict on one page must clear entries for every page referencing the same font dict"
            )
