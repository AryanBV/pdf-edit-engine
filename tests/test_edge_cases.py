"""Edge case tests for the content stream interpreter.

Covers ARY-249: invisible text, multiple content streams, empty pages,
and non-ASCII WinAnsi encoding.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.locator import ContentStreamInterpreter, find, get_fonts, get_text

CORPUS_DIR = Path(__file__).parent / "corpus"


# ── Edge case 1: Invisible text (Tr mode 3) ───────────────────────────


class TestInvisibleText:
    """Verify the interpreter indexes invisible text (rendering mode 3)."""

    PDF = str(CORPUS_DIR / "pikepdf_synthetic.pdf")

    def test_invisible_text_in_get_text(self) -> None:
        text = get_text(self.PDF)
        assert "Invisible hidden text" in text

    def test_invisible_text_findable(self) -> None:
        matches = find(self.PDF, "Invisible hidden text")
        assert len(matches) >= 1

    def test_invisible_text_rendering_mode(self) -> None:
        matches = find(self.PDF, "Invisible hidden text")
        assert len(matches) >= 1
        for ch in matches[0].characters:
            assert ch.rendering_mode == 3, f"Expected rendering_mode 3, got {ch.rendering_mode}"

    def test_visible_text_rendering_mode_zero(self) -> None:
        matches = find(self.PDF, "Visible text here")
        assert len(matches) >= 1
        for ch in matches[0].characters:
            assert ch.rendering_mode == 0, f"Expected rendering_mode 0, got {ch.rendering_mode}"

    def test_invisible_text_indexed_as_element(self) -> None:
        with pikepdf.open(self.PDF) as pdf:
            interp = ContentStreamInterpreter(pdf.pages[0], 0)
            elements = interp.interpret()
        text_elements = [e for e in elements if e.type == "text" and e.text_content]
        all_text = " ".join(e.text_content or "" for e in text_elements)
        assert "Invisible hidden text" in all_text


# ── Edge case 2: Multiple content streams per page ─────────────────────


class TestMultipleContentStreams:
    """Verify the interpreter handles pages with multiple content streams.

    pikepdf.parse_content_stream(page) automatically coalesces Array
    contents, so this should work transparently.
    """

    @pytest.fixture
    def multi_stream_pdf(self, tmp_path: Path) -> str:
        """Create a PDF where one page has Contents as an Array of streams.

        Strategy: generate a single-page PDF with reportlab, then split
        its content stream into two separate stream objects.
        """
        pytest.importorskip("reportlab")
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        # Generate base PDF
        base_path = tmp_path / "base.pdf"
        c = canvas.Canvas(str(base_path), pagesize=letter)
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, "Stream one text")
        c.drawString(72, 650, "Stream two text")
        c.save()

        # Split into two content streams
        out_path = tmp_path / "multi_stream.pdf"
        with pikepdf.open(base_path) as pdf:
            page = pdf.pages[0]

            # Get the font resource key
            font_dict = page["/Resources"]["/Font"]
            font_key = list(font_dict.keys())[0]

            # Create two separate streams
            stream1 = pdf.make_stream(
                f"BT {font_key} 12 Tf 72 700 Td (Stream one text) Tj ET".encode()
            )
            stream2 = pdf.make_stream(
                f"BT {font_key} 12 Tf 72 650 Td (Stream two text) Tj ET".encode()
            )

            # Replace Contents with an Array of two streams
            page.Contents = pdf.make_indirect(pikepdf.Array([stream1, stream2]))

            pdf.save(str(out_path))

        return str(out_path)

    def test_multi_stream_get_text(self, multi_stream_pdf: str) -> None:
        text = get_text(multi_stream_pdf)
        assert "Stream one text" in text
        assert "Stream two text" in text

    def test_multi_stream_find(self, multi_stream_pdf: str) -> None:
        matches_one = find(multi_stream_pdf, "Stream one text")
        matches_two = find(multi_stream_pdf, "Stream two text")
        assert len(matches_one) >= 1
        assert len(matches_two) >= 1

    def test_multi_stream_elements(self, multi_stream_pdf: str) -> None:
        with pikepdf.open(multi_stream_pdf) as pdf:
            interp = ContentStreamInterpreter(pdf.pages[0], 0)
            elements = interp.interpret()
        text_elements = [e for e in elements if e.type == "text" and e.text_content]
        all_text = " ".join(e.text_content or "" for e in text_elements)
        assert "Stream one text" in all_text
        assert "Stream two text" in all_text


# ── Edge case 3: Empty pages and path-only pages ──────────────────────


class TestEmptyAndPathPages:
    """Verify no crashes on pages with no text or no operators at all."""

    @pytest.fixture
    def empty_page_pdf(self, tmp_path: Path) -> str:
        """Create a PDF with an empty content stream."""
        out = tmp_path / "empty.pdf"
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[0]
        page.Contents = pdf.make_stream(b"")
        page["/Resources"] = pikepdf.Dictionary({})
        pdf.save(str(out))
        pdf.close()
        return str(out)

    @pytest.fixture
    def path_only_pdf(self, tmp_path: Path) -> str:
        """Create a PDF with only path operators (no text)."""
        out = tmp_path / "path_only.pdf"
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[0]
        page.Contents = pdf.make_stream(b"200 200 100 50 re S")
        page["/Resources"] = pikepdf.Dictionary({})
        pdf.save(str(out))
        pdf.close()
        return str(out)

    def test_empty_page_get_text(self, empty_page_pdf: str) -> None:
        text = get_text(empty_page_pdf)
        assert text == ""

    def test_empty_page_get_fonts(self, empty_page_pdf: str) -> None:
        fonts = get_fonts(empty_page_pdf)
        assert fonts == []

    def test_empty_page_find(self, empty_page_pdf: str) -> None:
        matches = find(empty_page_pdf, "anything")
        assert matches == []

    def test_path_only_get_text(self, path_only_pdf: str) -> None:
        text = get_text(path_only_pdf)
        assert text == ""

    def test_path_only_find(self, path_only_pdf: str) -> None:
        matches = find(path_only_pdf, "anything")
        assert matches == []

    def test_path_only_has_path_elements(self, path_only_pdf: str) -> None:
        with pikepdf.open(path_only_pdf) as pdf:
            interp = ContentStreamInterpreter(pdf.pages[0], 0)
            elements = interp.interpret()
        path_elements = [e for e in elements if e.type == "path"]
        text_elements = [e for e in elements if e.type == "text"]
        assert len(path_elements) >= 1, "Should have path elements from rect"
        assert len(text_elements) == 0, "Should have no text elements"


# ── Edge case 4: Non-ASCII in WinAnsiEncoding ─────────────────────────


class TestNonAsciiWinAnsi:
    """Verify accented characters decode correctly from WinAnsi-encoded PDFs."""

    PDF = str(CORPUS_DIR / "reportlab_simple.pdf")

    def test_get_text_contains_cafe(self) -> None:
        text = get_text(self.PDF)
        assert "caf\u00e9" in text, f"Expected 'caf\u00e9' in text, got:\n{text}"

    def test_get_text_contains_naive(self) -> None:
        text = get_text(self.PDF)
        assert "na\u00efve" in text, f"Expected 'na\u00efve' in text, got:\n{text}"

    def test_find_cafe(self) -> None:
        matches = find(self.PDF, "caf\u00e9")
        assert len(matches) >= 1, "Expected at least 1 match for 'caf\u00e9'"

    def test_find_naive(self) -> None:
        matches = find(self.PDF, "na\u00efve")
        assert len(matches) >= 1, "Expected at least 1 match for 'na\u00efve'"
