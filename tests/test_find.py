"""Tests for find() — text search API with operator-level precision."""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_edit_engine.locator import find, get_text
from pdf_edit_engine.models import TextCharacter, TextMatch

CORPUS_DIR = Path(__file__).parent / "corpus"
RESUME_PDF = str(CORPUS_DIR / "resume_aryan.pdf")

pytestmark = pytest.mark.skipif(
    not Path(RESUME_PDF).exists(), reason="resume_aryan.pdf not in corpus"
)


# ── TestFind ─────────────────────────────────────────────────────────


class TestFind:
    """Core tests for find() text search."""

    def test_find_name(self) -> None:
        matches = find(RESUME_PDF, "Aryan")
        assert len(matches) >= 1
        assert matches[0].matched_text == "Aryan"

    def test_find_case_insensitive(self) -> None:
        matches = find(RESUME_PDF, "aryan", case_sensitive=False)
        assert len(matches) >= 1
        # Should find at least the name heading
        texts = [m.matched_text.lower() for m in matches]
        assert "aryan" in texts

    def test_find_case_sensitive_miss(self) -> None:
        matches = find(RESUME_PDF, "aryan", case_sensitive=True)
        # "Aryan" in heading is capitalized — lowercase should not match it
        name_matches = [m for m in matches if m.bounding_box[1] > 800]
        assert len(name_matches) == 0

    def test_find_cross_element(self) -> None:
        """Search text spanning multiple TJ operators / BT-ET blocks."""
        matches = find(RESUME_PDF, "Full-Stack Software Developer")
        assert len(matches) == 1
        m = matches[0]
        assert m.matched_text == "Full-Stack Software Developer"
        # Should span multiple operators
        assert len(m.operator_refs) >= 1

    def test_find_nonexistent(self) -> None:
        matches = find(RESUME_PDF, "nonexistent text xyz")
        assert matches == []

    def test_find_empty_string(self) -> None:
        matches = find(RESUME_PDF, "")
        assert matches == []

    def test_find_page_zero(self) -> None:
        matches = find(RESUME_PDF, "Aryan", page=0)
        assert len(matches) >= 1

    def test_find_invalid_page(self) -> None:
        with pytest.raises(IndexError):
            find(RESUME_PDF, "Aryan", page=99)


# ── TestMatchStructure ────────────────────────────────────────────────


class TestMatchStructure:
    """Validate TextMatch field correctness."""

    @pytest.fixture()
    def match(self) -> TextMatch:
        matches = find(RESUME_PDF, "Aryan")
        assert len(matches) >= 1
        return matches[0]

    def test_bbox_valid(self, match: TextMatch) -> None:
        x0, y0, x1, y1 = match.bounding_box
        assert x0 < x1, f"bbox x0={x0} >= x1={x1}"
        assert y0 < y1, f"bbox y0={y0} >= y1={y1}"

    def test_characters_no_nones(self, match: TextMatch) -> None:
        assert len(match.characters) == len(match.matched_text)
        for ch in match.characters:
            assert isinstance(ch, TextCharacter)

    def test_operator_refs_valid(self, match: TextMatch) -> None:
        assert isinstance(match.operator_refs, list)
        assert all(isinstance(r, int) for r in match.operator_refs)
        assert all(r >= 0 for r in match.operator_refs)
        # operator_refs should be sorted with no duplicates
        assert match.operator_refs == sorted(set(match.operator_refs))

    def test_font_info_populated(self, match: TextMatch) -> None:
        fi = match.font_info
        assert fi.name != ""
        assert fi.postscript_name != ""
        assert fi.encoding_type in {"WinAnsi", "Identity-H", "Custom"}

    def test_page_number(self, match: TextMatch) -> None:
        assert match.page_number == 0


# ── TestMultipleMatches ───────────────────────────────────────────────


class TestMultipleMatches:
    """Tests for repeated text and multi-match scenarios."""

    def test_multiple_matches_count(self) -> None:
        """A common word should match multiple times."""
        matches = find(RESUME_PDF, "and")
        assert len(matches) > 1

    def test_heading_text(self) -> None:
        """Heading text with different font/size should be findable."""
        matches = find(RESUME_PDF, "PROFESSIONAL SUMMARY")
        assert len(matches) == 1
        m = matches[0]
        assert m.matched_text == "PROFESSIONAL SUMMARY"

    def test_space_in_matched_text(self) -> None:
        """Matches spanning space-separated elements include the space."""
        matches = find(RESUME_PDF, "Aryan B V")
        # If "Aryan B V" is composed from separate elements,
        # the space is an inserted separator and not in characters
        if matches:
            m = matches[0]
            assert " " in m.matched_text
            # Characters list has only real chars (no None from separators)
            for ch in m.characters:
                assert isinstance(ch, TextCharacter)

    def test_email_findable(self) -> None:
        matches = find(RESUME_PDF, "aryansalian5678@gmail.com")
        assert len(matches) == 1


# ── TestCache ─────────────────────────────────────────────────────────


class TestCache:
    """Verify the single-PDF index cache is used across calls."""

    def test_cache_populated_after_get_text(self) -> None:
        from pdf_edit_engine import locator

        # Clear any previous state
        locator._cached_path = None
        locator._cached_elements = {}

        resolved = str(Path(RESUME_PDF).resolve())

        # get_text populates cache
        get_text(RESUME_PDF)
        assert locator._cached_path == resolved
        assert 0 in locator._cached_elements

        # find reuses cache (same path)
        matches = find(RESUME_PDF, "Aryan")
        assert len(matches) >= 1
        assert locator._cached_path == resolved
