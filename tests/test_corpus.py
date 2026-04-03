"""Corpus tests — parametrized across all corpus PDFs.

Driven by tests/corpus/manifest.json.  Covers ARY-248.
"""

from __future__ import annotations

import json
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.locator import ContentStreamInterpreter, find, get_fonts, get_text

CORPUS_DIR = Path(__file__).parent / "corpus"


def _load_manifest() -> list[dict[str, object]]:
    return json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))


MANIFEST = _load_manifest()


# ── Parametrized tests (6 PDFs x 4 tests) ─────────────────────────────


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: str(e["filename"]))
def test_get_text_contains_expected(entry: dict[str, object]) -> None:
    pdf_path = str(CORPUS_DIR / str(entry["filename"]))
    text = get_text(pdf_path)
    expected = str(entry["expected_text"])
    assert expected in text, (
        f"Expected {expected!r} in get_text output for {entry['filename']}"
    )


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: str(e["filename"]))
def test_get_fonts_not_empty(entry: dict[str, object]) -> None:
    pdf_path = str(CORPUS_DIR / str(entry["filename"]))
    fonts = get_fonts(pdf_path)
    assert len(fonts) >= 1, f"Expected at least 1 font in {entry['filename']}"


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: str(e["filename"]))
def test_find_expected_text(entry: dict[str, object]) -> None:
    pdf_path = str(CORPUS_DIR / str(entry["filename"]))
    expected = str(entry["expected_text"])
    matches = find(pdf_path, expected)
    assert len(matches) >= 1, (
        f"Expected at least 1 match for {expected!r} in {entry['filename']}"
    )
    for m in matches:
        assert m.bounding_box[0] < m.bounding_box[2], "x0 should be < x1"
        assert m.bounding_box[1] < m.bounding_box[3], "y0 should be < y1"


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: str(e["filename"]))
def test_no_garbled_output(entry: dict[str, object]) -> None:
    pdf_path = str(CORPUS_DIR / str(entry["filename"]))
    text = get_text(pdf_path)
    for ch in text:
        assert ch.isprintable() or ch in "\n\t\r", f"Non-printable char: {ch!r}"


# ── Non-parametrized tests ─────────────────────────────────────────────


class TestMultipage:
    """Tests specific to multi-page PDFs."""

    PDF = str(CORPUS_DIR / "reportlab_multipage.pdf")

    def test_page_zero_has_page_one_content(self) -> None:
        text = get_text(self.PDF, page=0)
        assert "Page One Content" in text
        assert "Page Two Content" not in text

    def test_page_one_has_page_two_content(self) -> None:
        text = get_text(self.PDF, page=1)
        assert "Page Two Content" in text
        assert "Page One Content" not in text

    def test_find_across_pages(self) -> None:
        matches = find(self.PDF, "Page", case_sensitive=True)
        pages_found = {m.page_number for m in matches}
        assert 0 in pages_found, "Expected match on page 0"
        assert 1 in pages_found, "Expected match on page 1"


class TestTablePaths:
    """Verify table PDFs produce path content elements."""

    PDF = str(CORPUS_DIR / "reportlab_table.pdf")

    def test_table_pdf_has_paths(self) -> None:
        with pikepdf.open(self.PDF) as pdf:
            interp = ContentStreamInterpreter(pdf.pages[0], 0)
            elements = interp.interpret()
        path_elements = [e for e in elements if e.type == "path"]
        assert len(path_elements) >= 1, "Table PDF should have path elements from grid"


class TestFormsPdf:
    """Verify form PDFs extract body text without corruption."""

    PDF = str(CORPUS_DIR / "reportlab_forms.pdf")

    def test_forms_pdf_text_not_corrupted(self) -> None:
        text = get_text(self.PDF)
        assert "Please fill out this form" in text
        # Verify no garbled output from AcroForm presence
        for ch in text:
            assert ch.isprintable() or ch in "\n\t\r", f"Non-printable char: {ch!r}"
