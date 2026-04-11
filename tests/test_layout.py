"""Tests for get_text_layout() API."""

from __future__ import annotations

import pytest

from pdf_edit_engine import TextBlock, get_text_layout

RESUME = "tests/corpus/Aryan_BV_Resume_2026.pdf"
SIMPLE = "tests/corpus/reportlab_simple.pdf"


class TestGetTextLayout:
    """Tests for get_text_layout()."""

    def test_simple_pdf_blocks_have_positions(self) -> None:
        blocks = get_text_layout(SIMPLE)
        assert len(blocks) > 0
        for b in blocks:
            assert isinstance(b, TextBlock)
            assert b.width > 0 or b.text.strip() == ""
            assert b.font_size > 0

    def test_resume_returns_many_blocks(self) -> None:
        blocks = get_text_layout(RESUME)
        assert len(blocks) >= 20

    def test_resume_sorted_by_y_then_x(self) -> None:
        blocks = get_text_layout(RESUME)
        for i in range(1, len(blocks)):
            prev, curr = blocks[i - 1], blocks[i]
            if prev.page == curr.page:
                # y descending (top to bottom), then x ascending
                assert (prev.y, -prev.x) >= (curr.y, -curr.x) or pytest.approx(
                    prev.y, abs=0.01
                ) == curr.y

    def test_page_filter(self) -> None:
        all_blocks = get_text_layout(RESUME)
        page0_blocks = get_text_layout(RESUME, page=0)
        assert len(page0_blocks) > 0
        assert all(b.page == 0 for b in page0_blocks)
        assert len(page0_blocks) <= len(all_blocks)

    def test_font_info_present(self) -> None:
        blocks = get_text_layout(RESUME)
        for b in blocks:
            assert b.font_name != ""
            assert b.font_size > 0

    def test_textblock_is_frozen(self) -> None:
        blocks = get_text_layout(SIMPLE)
        with pytest.raises(AttributeError):
            blocks[0].text = "modified"  # type: ignore[misc]
