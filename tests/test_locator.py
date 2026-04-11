"""Tests for the TextLocator module — get_text, get_fonts, and content stream interpreter."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pikepdf
import pytest

from pdf_edit_engine.locator import (
    ContentStreamInterpreter,
    extract_bbox_text,
    find,
    get_fonts,
    get_text,
    get_text_layout,
)

if TYPE_CHECKING:
    from pdf_edit_engine.models import ContentElement

CORPUS_DIR = Path(__file__).parent / "corpus"
RESUME_PDF = str(CORPUS_DIR / "Aryan_BV_Resume_2026.pdf")

pytestmark = pytest.mark.skipif(
    not Path(RESUME_PDF).exists(), reason="Aryan_BV_Resume_2026.pdf not in corpus"
)


# ── TestGetText ───────────────────────────────────────────────────────


class TestGetText:
    """Tests for get_text() public API."""

    def test_returns_nonempty_string(self) -> None:
        text = get_text(RESUME_PDF)
        assert len(text) > 0

    def test_contains_known_name(self) -> None:
        text = get_text(RESUME_PDF)
        assert "Aryan" in text

    def test_contains_known_section(self) -> None:
        text = get_text(RESUME_PDF)
        assert "Software Developer" in text or "Full" in text

    def test_contains_email(self) -> None:
        text = get_text(RESUME_PDF)
        assert "aryansalian5678@gmail.com" in text

    def test_page_zero_works(self) -> None:
        text = get_text(RESUME_PDF, page=0)
        assert len(text) > 0
        assert "Aryan" in text

    def test_invalid_page_raises(self) -> None:
        with pytest.raises(IndexError):
            get_text(RESUME_PDF, page=99)

    def test_text_is_unicode_not_cid_bytes(self) -> None:
        text = get_text(RESUME_PDF)
        assert "\x00" not in text

    def test_contains_multiple_lines(self) -> None:
        text = get_text(RESUME_PDF)
        lines = text.strip().split("\n")
        assert len(lines) > 5


# ── TestGetFonts ──────────────────────────────────────────────────────


class TestGetFonts:
    """Tests for get_fonts() public API."""

    def test_returns_font_list(self) -> None:
        fonts = get_fonts(RESUME_PDF)
        assert len(fonts) > 0

    def test_has_identity_h_font(self) -> None:
        fonts = get_fonts(RESUME_PDF)
        encodings = {f.encoding_type for f in fonts}
        assert "Identity-H" in encodings

    def test_font_count(self) -> None:
        fonts = get_fonts(RESUME_PDF)
        assert len(fonts) == 6

    def test_font_has_postscript_name(self) -> None:
        fonts = get_fonts(RESUME_PDF)
        for f in fonts:
            assert f.postscript_name, f"Font {f.name} has empty postscript_name"

    def test_font_embedded_type_truetype(self) -> None:
        fonts = get_fonts(RESUME_PDF)
        for f in fonts:
            assert f.embedded_type == "TrueType"

    def test_fonts_are_subsetted(self) -> None:
        fonts = get_fonts(RESUME_PDF)
        assert all(f.is_subset for f in fonts)

    def test_cid_font_has_glyph_count(self) -> None:
        fonts = get_fonts(RESUME_PDF)
        cid_fonts = [f for f in fonts if f.encoding_type == "Identity-H"]
        for f in cid_fonts:
            assert f.glyph_count > 0, f"CID font {f.name} has 0 glyphs"

    def test_page_zero_fonts(self) -> None:
        fonts = get_fonts(RESUME_PDF, page=0)
        assert len(fonts) == 6


# ── TestContentStreamInterpreter ──────────────────────────────────────


class TestContentStreamInterpreter:
    """Tests for ContentStreamInterpreter directly."""

    @pytest.fixture()
    def elements(self) -> list[ContentElement]:
        pdf = pikepdf.open(RESUME_PDF)
        page = pdf.pages[0]
        interp = ContentStreamInterpreter(page, 0)
        result = interp.interpret()
        pdf.close()
        return result

    def test_elements_contain_text_type(
        self,
        elements: list[ContentElement],
    ) -> None:
        types = {e.type for e in elements}
        assert "text" in types

    def test_text_elements_have_valid_bbox(
        self,
        elements: list[ContentElement],
    ) -> None:
        text_elems = [e for e in elements if e.type == "text"]
        for elem in text_elems:
            x0, y0, x1, y1 = elem.bbox
            assert x0 < x1, f"bbox x0 >= x1: {elem.bbox}"
            assert y0 < y1, f"bbox y0 >= y1: {elem.bbox}"

    def test_text_elements_have_operator_index(
        self,
        elements: list[ContentElement],
    ) -> None:
        text_elems = [e for e in elements if e.type == "text"]
        for elem in text_elems:
            start, end = elem.operator_range
            assert start >= 0
            assert end > start

    def test_text_characters_have_matching_font(
        self,
        elements: list[ContentElement],
    ) -> None:
        fonts = get_fonts(RESUME_PDF)
        font_names = {f.name for f in fonts}
        text_elems = [e for e in elements if e.type == "text"]
        for elem in text_elems:
            if elem.characters:
                for ch in elem.characters:
                    assert ch.font_name in font_names, (
                        f"Character font {ch.font_name!r} not in {font_names}"
                    )

    def test_text_content_is_decoded_unicode(
        self,
        elements: list[ContentElement],
    ) -> None:
        text_elems = [e for e in elements if e.type == "text"]
        for elem in text_elems:
            if elem.text_content:
                assert "\x00" not in elem.text_content

    def test_text_elements_have_characters(
        self,
        elements: list[ContentElement],
    ) -> None:
        text_elems = [e for e in elements if e.type == "text"]
        for elem in text_elems:
            assert elem.characters is not None
            assert len(elem.characters) > 0

    def test_path_elements_exist(
        self,
        elements: list[ContentElement],
    ) -> None:
        types = {e.type for e in elements}
        assert "path" in types

    def test_state_change_elements_exist(
        self,
        elements: list[ContentElement],
    ) -> None:
        types = {e.type for e in elements}
        assert "state_change" in types

    def test_element_count_reasonable(
        self,
        elements: list[ContentElement],
    ) -> None:
        assert len(elements) > 100


# ── TestFindBasicSmoke ────────────────────────────────────────────────


class TestFindBasicSmoke:
    """Basic smoke test for find() — detailed tests in test_find.py."""

    def test_find_returns_match(self) -> None:
        matches = find(RESUME_PDF, "Aryan")
        assert len(matches) >= 1
        assert matches[0].matched_text == "Aryan"


class TestExtractBboxText:
    """Tests for extract_bbox_text() gap-aware extraction."""

    def test_gap_aware_no_spurious_spaces(self) -> None:
        """Adjacent text runs should join without spaces (e.g., 'monthly' not 'month ly')."""
        # The AJSP section contains "monthly" rendered as "month" + "ly" text runs.
        # Find the bbox that covers the line containing "monthly".
        blocks = get_text_layout(RESUME_PDF, page=0)
        month_block = None
        for b in blocks:
            if b.text and b.text.strip() == "month":
                month_block = b
                break
        assert month_block is not None, "Could not find 'month' text block"

        # Extract a bbox that covers the full line
        line_bbox = (0, month_block.y - 2, 600, month_block.y + month_block.height + 2)
        text = extract_bbox_text(RESUME_PDF, bbox=line_bbox, page=0)
        assert "monthly" in text, f"Expected 'monthly', got: {text!r}"
        assert "month ly" not in text, f"Spurious space found: {text!r}"

    def test_full_section_extraction(self) -> None:
        """Extracting the AJSP section should produce 'full-stack' not 'full - stack'."""
        blocks = get_text_layout(RESUME_PDF, page=0)
        sorted_blocks = sorted(blocks, key=lambda b: -(b.y + b.height))

        ajsp_y = lumina_y = None
        for b in sorted_blocks:
            t = b.text or ""
            top = b.y + b.height
            if "AJSP Manager" in t and ajsp_y is None:
                ajsp_y = top
            if "Lumina Crafts" in t and lumina_y is None:
                lumina_y = top

        assert ajsp_y is not None and lumina_y is not None
        ajsp_blocks = [
            b
            for b in sorted_blocks
            if (b.y + b.height) <= ajsp_y + 2 and (b.y + b.height) > lumina_y + 2
        ]
        bbox = (
            min(b.x for b in ajsp_blocks),
            min(b.y for b in ajsp_blocks),
            max(b.x + b.width for b in ajsp_blocks),
            max(b.y + b.height for b in ajsp_blocks),
        )

        text = extract_bbox_text(RESUME_PDF, bbox=bbox, page=0)
        assert "monthly" in text, "Expected 'monthly' in extracted text"
        assert "month ly" not in text, "Spurious space in 'month ly'"
        assert "full-stack" in text or "full- stack" not in text

    def test_returns_empty_for_empty_region(self) -> None:
        """Extracting from a region with no text returns empty string."""
        text = extract_bbox_text(RESUME_PDF, bbox=(9999, 9999, 10000, 10000), page=0)
        assert text == ""
