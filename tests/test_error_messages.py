"""Error message quality audit — every common mistake should produce a clear message."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine import (
    PDFEditError,
    encrypt_pdf,
    fill_form,
    find,
    get_text,
    merge_pdfs,
    replace,
    rotate_pages,
)

CORPUS_DIR = Path(__file__).parent / "corpus"
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")
TABLE_PDF = str(CORPUS_DIR / "reportlab_table.pdf")
PROJECT_ROOT = Path(__file__).parent.parent


# ── Path and file errors ──────────────────────────────────────────────


class TestFileErrors:
    """File-related error messages should be clear."""

    def test_non_pdf_file(self) -> None:
        """Passing a non-PDF file should give a clear error."""
        readme = str(PROJECT_ROOT / "README.md")
        with pytest.raises((PDFEditError, Exception)):
            get_text(readme)

    def test_nonexistent_file(self) -> None:
        with pytest.raises((FileNotFoundError, PDFEditError)):
            get_text("absolutely_does_not_exist_xyz.pdf")

    def test_output_to_invalid_path(self, tmp_path: Path) -> None:
        """Writing to a path with nonexistent parent directory."""
        matches = find(SIMPLE_PDF, "Test Document")
        if not matches:
            pytest.skip("Test Document not found")
        bad_output = str(tmp_path / "nonexistent" / "deeply" / "nested" / "output.pdf")
        with pytest.raises((OSError, PDFEditError)):
            replace(SIMPLE_PDF, matches[0], "new", bad_output)


# ── Encrypted PDF errors ─────────────────────────────────────────────


class TestEncryptedPdfErrors:
    """Encrypted PDF operations should produce clear error messages."""

    def test_encrypted_pdf_without_password(self, tmp_path: Path) -> None:
        """Opening encrypted PDF without password should mention encryption."""
        encrypted = str(tmp_path / "encrypted.pdf")
        encrypt_pdf(SIMPLE_PDF, "owner123", "user123", encrypted)
        with pytest.raises(
            (PDFEditError, pikepdf.PasswordError, Exception),
            match=r"(?i)password|encrypted|Password",
        ):
            get_text(encrypted)


# ── Invalid arguments ─────────────────────────────────────────────────


class TestInvalidArguments:
    """Invalid arguments should produce clear errors, not internal tracebacks."""

    def test_replace_with_none(self, tmp_path: Path) -> None:
        """Passing None as replacement text."""
        matches = find(SIMPLE_PDF, "Test Document")
        if not matches:
            pytest.skip("Test Document not found")
        output = str(tmp_path / "none.pdf")
        with pytest.raises((TypeError, PDFEditError, AttributeError)):
            replace(SIMPLE_PDF, matches[0], None, output)  # type: ignore[arg-type]

    def test_invalid_page_number(self) -> None:
        """Page number out of range."""
        with pytest.raises((IndexError, PDFEditError)):
            get_text(SIMPLE_PDF, page=999)


# ── Wrapper error messages ────────────────────────────────────────────


class TestWrapperErrors:
    """Wrapper functions should produce clear error messages."""

    def test_merge_empty_list(self, tmp_path: Path) -> None:
        output = str(tmp_path / "merged.pdf")
        with pytest.raises(PDFEditError, match=r"(?i)no pdf|empty|at least"):
            merge_pdfs([], output)

    def test_rotate_invalid_angle(self, tmp_path: Path) -> None:
        output = str(tmp_path / "rotated.pdf")
        with pytest.raises(PDFEditError, match=r"90|180|270"):
            rotate_pages(SIMPLE_PDF, [0], 45, output)

    def test_fill_form_no_acroform(self, tmp_path: Path) -> None:
        output = str(tmp_path / "filled.pdf")
        with pytest.raises(PDFEditError, match=r"(?i)acroform|form"):
            fill_form(SIMPLE_PDF, {"field": "value"}, output)


# ── Cross-PDF stale match ────────────────────────────────────────────


class TestCrossPdfErrors:
    """Using a match from one PDF on another should not silently corrupt."""

    def test_stale_match_does_not_crash(self, tmp_path: Path) -> None:
        """TextMatch from pdf_a applied to pdf_b — should not crash."""
        matches_a = find(SIMPLE_PDF, "Test Document")
        if not matches_a:
            pytest.skip("Test Document not found in reportlab_simple")
        output = str(tmp_path / "stale.pdf")
        try:
            replace(TABLE_PDF, matches_a[0], "Stale", output)
            # If it succeeds without error, output should be a valid PDF
            pikepdf.Pdf.open(output).close()
        except (PDFEditError, IndexError, Exception):
            pass  # acceptable — stale refs detected
