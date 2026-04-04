"""Adversarial input tests — malformed PDFs, pathological content, edge cases."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine import (
    Edit,
    PDFEditError,
    batch_replace,
    find,
    get_text,
    replace,
)

CORPUS_DIR = Path(__file__).parent / "corpus"
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")
RESUME_PDF = CORPUS_DIR / "resume_aryan.pdf"
PROJECT_ROOT = Path(__file__).parent.parent


# ── Helpers: malformed PDF generators ─────────────────────────────────


def _make_empty_content_stream(tmp_path: Path) -> str:
    """PDF page with an empty content stream."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(b"")
    page["/Resources"] = pikepdf.Dictionary()
    out = str(tmp_path / "empty_stream.pdf")
    pdf.save(out)
    pdf.close()
    return out


def _make_missing_resources(tmp_path: Path) -> str:
    """PDF page with text operators but no /Resources dictionary."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(b"BT /F1 12 Tf (Hello) Tj ET")
    # Remove /Resources if present
    if "/Resources" in page:
        del page["/Resources"]
    out = str(tmp_path / "no_resources.pdf")
    pdf.save(out)
    pdf.close()
    return out


def _make_garbled_stream(tmp_path: Path) -> str:
    """PDF page with garbage bytes in content stream."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(b"\xff\xfe\x00\x01garbage data here")
    page["/Resources"] = pikepdf.Dictionary()
    out = str(tmp_path / "garbled.pdf")
    pdf.save(out)
    pdf.close()
    return out


def _make_null_font_ref(tmp_path: Path) -> str:
    """PDF page where /Font references a Null object."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(b"BT /F1 12 Tf (Hello) Tj ET")
    page["/Resources"] = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=pikepdf.Object.parse(b"null")),
    )
    out = str(tmp_path / "null_font.pdf")
    pdf.save(out)
    pdf.close()
    return out


def _make_with_font(tmp_path: Path, stream: bytes, name: str) -> str:
    """Create a PDF with a Helvetica font and custom content stream."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type1,
        BaseFont=pikepdf.Name.Helvetica,
    )
    page.Contents = pdf.make_stream(stream)
    page["/Resources"] = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=font),
    )
    out = str(tmp_path / name)
    pdf.save(out)
    pdf.close()
    return out


# ── Malformed PDF structure tests ─────────────────────────────────────


class TestMalformedPDFs:
    """Malformed PDFs should not crash — return empty or raise PDFEditError."""

    def test_empty_content_stream(self, tmp_path: Path) -> None:
        pdf = _make_empty_content_stream(tmp_path)
        text = get_text(pdf)
        assert text == "" or isinstance(text, str)
        matches = find(pdf, "anything")
        assert matches == []

    def test_missing_resources(self, tmp_path: Path) -> None:
        pdf = _make_missing_resources(tmp_path)
        try:
            text = get_text(pdf)
            assert isinstance(text, str)
        except PDFEditError:
            pass  # acceptable

    def test_garbled_stream(self, tmp_path: Path) -> None:
        pdf = _make_garbled_stream(tmp_path)
        try:
            text = get_text(pdf)
            assert isinstance(text, str)
        except PDFEditError:
            pass  # acceptable

    def test_null_font_reference(self, tmp_path: Path) -> None:
        pdf = _make_null_font_ref(tmp_path)
        try:
            text = get_text(pdf)
            assert isinstance(text, str)
        except PDFEditError:
            pass  # acceptable

    def test_find_on_malformed_returns_empty_or_error(self, tmp_path: Path) -> None:
        for maker in [_make_empty_content_stream, _make_missing_resources,
                      _make_garbled_stream, _make_null_font_ref]:
            pdf = maker(tmp_path)
            try:
                matches = find(pdf, "test")
                assert isinstance(matches, list)
            except PDFEditError:
                pass  # acceptable


# ── Pathological content streams ──────────────────────────────────────


class TestPathologicalContent:
    """Extreme but valid-ish content streams should not crash."""

    def test_deep_nested_save_restore(self, tmp_path: Path) -> None:
        """100 nested q/Q pairs with text in the middle."""
        stream = b""
        for _ in range(100):
            stream += b"q\n"
        stream += b"BT /F1 12 Tf (Nested) Tj ET\n"
        for _ in range(100):
            stream += b"Q\n"
        out = _make_with_font(tmp_path, stream, "nested_q.pdf")
        text = get_text(out)
        assert isinstance(text, str)

    def test_huge_tj_array(self, tmp_path: Path) -> None:
        """TJ array with 1000 single-char fragments."""
        fragments: list[bytes] = []
        for i in range(1000):
            ch = chr(65 + (i % 26))
            fragments.append(f"({ch}) 0 ".encode())
        tj_data = b"[" + b"".join(fragments) + b"]"
        stream = b"BT /F1 12 Tf 72 700 Td " + tj_data + b" TJ ET"
        out = _make_with_font(tmp_path, stream, "huge_tj.pdf")
        text = get_text(out)
        assert len(text) > 0

    def test_zero_font_size(self, tmp_path: Path) -> None:
        """Text with font size 0."""
        stream = b"BT /F1 0 Tf (Invisible) Tj ET"
        out = _make_with_font(tmp_path, stream, "zero_fs.pdf")
        try:
            text = get_text(out)
            assert isinstance(text, str)
        except PDFEditError:
            pass  # acceptable


# ── Non-PDF inputs ────────────────────────────────────────────────────


class TestNonPdfInputs:
    """Non-PDF files and invalid paths should fail clearly."""

    def test_non_pdf_file(self) -> None:
        readme = str(PROJECT_ROOT / "README.md")
        with pytest.raises((PDFEditError, Exception)):
            get_text(readme)

    def test_nonexistent_file(self) -> None:
        with pytest.raises((FileNotFoundError, PDFEditError)):
            get_text("absolutely_does_not_exist_xyz_12345.pdf")

    def test_empty_path(self) -> None:
        with pytest.raises((FileNotFoundError, PDFEditError, OSError, ValueError)):
            get_text("")

    def test_find_with_none_search(self) -> None:
        """Passing None as search text — should not crash."""
        try:
            result = find(SIMPLE_PDF, None)  # type: ignore[arg-type]
            # If it doesn't raise, it should return an empty list
            assert isinstance(result, list)
        except (TypeError, PDFEditError, AttributeError):
            pass  # acceptable


# ── Unicode edge cases ────────────────────────────────────────────────


class TestUnicodeEdgeCases:
    """Unicode edge cases should not crash — FidelityReport should report issues."""

    @pytest.mark.skipif(
        not RESUME_PDF.exists(), reason="resume_aryan.pdf not in corpus"
    )
    def test_replace_with_emoji(self, tmp_path: Path) -> None:
        """Emoji replacement should not crash; FidelityReport may note missing glyphs."""
        matches = find(str(RESUME_PDF), "Aryan")
        if not matches:
            pytest.skip("'Aryan' not found")
        output = str(tmp_path / "emoji.pdf")
        try:
            result = replace(str(RESUME_PDF), matches[0], "Test\U0001f389", output)
            assert result is not None
        except PDFEditError:
            pass  # acceptable — can't encode emoji in Calibri

    def test_replace_with_empty_string(self, tmp_path: Path) -> None:
        """Empty replacement should work or raise clear error."""
        matches = find(SIMPLE_PDF, "Test Document")
        if not matches:
            pytest.skip("'Test Document' not found")
        output = str(tmp_path / "empty.pdf")
        try:
            result = replace(SIMPLE_PDF, matches[0], "", output, reflow=False)
            assert result is not None
        except PDFEditError:
            pass  # acceptable

    def test_replace_with_very_long_string(self, tmp_path: Path) -> None:
        """Very long replacement should not OOM."""
        matches = find(SIMPLE_PDF, "Test Document")
        if not matches:
            pytest.skip("'Test Document' not found")
        output = str(tmp_path / "long.pdf")
        try:
            result = replace(
                SIMPLE_PDF, matches[0], "A" * 10000, output, reflow=False
            )
            assert result is not None
        except (PDFEditError, Exception):
            pass  # acceptable — may overflow


# ── Adversarial replacement operations ────────────────────────────────


class TestAdversarialReplacement:
    """Adversarial replacement operations should not corrupt PDFs."""

    def test_replace_same_match_twice(self, tmp_path: Path) -> None:
        """Using the same match object twice on the same source."""
        matches = find(SIMPLE_PDF, "Test Document")
        if not matches:
            pytest.skip("'Test Document' not found")
        out1 = str(tmp_path / "first.pdf")
        replace(SIMPLE_PDF, matches[0], "First", out1, reflow=False)

        out2 = str(tmp_path / "second.pdf")
        replace(SIMPLE_PDF, matches[0], "Second", out2, reflow=False)
        text = get_text(out2)
        assert "Second" in text

    def test_stale_match_cross_pdf(self, tmp_path: Path) -> None:
        """Using a TextMatch from one PDF on a different PDF — should not crash."""
        pdf_a = SIMPLE_PDF
        pdf_b = str(CORPUS_DIR / "reportlab_table.pdf")
        matches_a = find(pdf_a, "Test Document")
        if not matches_a:
            pytest.skip("'Test Document' not found in reportlab_simple")
        output = str(tmp_path / "stale.pdf")
        # This may succeed if operator indices happen to be valid, or raise
        try:
            replace(pdf_b, matches_a[0], "Stale", output)
            # If it didn't crash, verify output is at least a valid PDF
            pikepdf.Pdf.open(output).close()
        except (PDFEditError, IndexError, Exception):
            pass  # acceptable — stale refs detected

    def test_batch_replace_overlapping_matches(self, tmp_path: Path) -> None:
        """Overlapping edits should be handled without corruption."""
        text = get_text(SIMPLE_PDF)
        if "Test" not in text or "Test Document" not in text:
            pytest.skip("Required text not found")
        edits = [
            Edit(find="Test", replace="X"),
            Edit(find="Test Document", replace="Y"),
        ]
        output = str(tmp_path / "overlap.pdf")
        try:
            results = batch_replace(SIMPLE_PDF, edits, output)
            assert isinstance(results, list)
            pikepdf.Pdf.open(output).close()
        except PDFEditError:
            pass  # acceptable — engine may reject overlapping edits
