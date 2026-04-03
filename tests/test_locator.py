"""Tests for the TextLocator module."""

from __future__ import annotations

import pytest

from pdf_edit_engine.locator import find, get_fonts, get_text


class TestFind:
    """Tests for locator.find()."""

    def test_find_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            find("test.pdf", "hello")


class TestGetText:
    """Tests for locator.get_text()."""

    def test_get_text_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            get_text("test.pdf")


class TestGetFonts:
    """Tests for locator.get_fonts()."""

    def test_get_fonts_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            get_fonts("test.pdf")
