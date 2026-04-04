"""Tests for the OperatorSurgeon module — PDF content stream text replacement."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.errors import OperatorError, PDFEditError
from pdf_edit_engine.locator import find, get_fonts, get_text
from pdf_edit_engine.models import Edit, TextMatch
from pdf_edit_engine.surgeon import batch_replace, replace, replace_all

CORPUS_DIR = Path(__file__).parent / "corpus"
RESUME_PDF = str(CORPUS_DIR / "resume_aryan.pdf")
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")
MULTIPAGE_PDF = str(CORPUS_DIR / "reportlab_multipage.pdf")


# ── Helpers ──────────────────────────────────────────────────────────────


def _first_match(pdf_path: str, text: str) -> TextMatch:
    """Find the first occurrence of text in a PDF."""
    matches = find(pdf_path, text)
    assert matches, f"No match for {text!r} in {pdf_path}"
    return matches[0]


def _validate_output(
    output_path: str,
    expected_text: str,
    original_pdf: str | None = None,
) -> None:
    """Validate a replacement output PDF."""
    # Valid PDF structure
    pdf = pikepdf.Pdf.open(output_path)
    assert len(pdf.pages) > 0
    pdf.close()

    # Contains replacement text
    text = get_text(output_path)
    assert expected_text in text, f"Expected {expected_text!r} in output text"

    # Fonts preserved
    if original_pdf is not None:
        orig_fonts = get_fonts(original_pdf)
        out_fonts = get_fonts(output_path)
        orig_names = sorted(f.postscript_name for f in orig_fonts)
        out_names = sorted(f.postscript_name for f in out_fonts)
        assert orig_names == out_names


# ── Same-length replacement ──────────────────────────────────────────────


class TestSameLengthReplace:
    """Test same-length text replacement preserving kerning and layout."""

    def test_identity_h_same_length(self, tmp_path: Path) -> None:
        """Replace 'Aryan' (5 chars) with 'Bryan' (5 chars) in Identity-H PDF."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "Bryan", out)

        assert result.success is True
        assert result.font_action == "kept"
        assert result.original_text == "Aryan"
        assert result.new_text == "Bryan"
        _validate_output(out, "Bryan", RESUME_PDF)

    def test_winAnsi_same_length(self, tmp_path: Path) -> None:
        """Replace 'Test' (4 chars) with 'Best' (4 chars) in WinAnsi PDF."""
        out = str(tmp_path / "output.pdf")
        match_test = _first_match(SIMPLE_PDF, "Test")
        result = replace(SIMPLE_PDF, match_test, "Best", out)

        assert result.success is True
        _validate_output(out, "Best", SIMPLE_PDF)

    def test_fidelity_report_font_preserved(self, tmp_path: Path) -> None:
        """FidelityReport should show font_preserved=True for same-length."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "Bryan", out)

        assert result.fidelity_report.font_preserved is True
        assert result.fidelity_report.font_substituted is None
        assert result.fidelity_report.reflow_applied is False
        assert result.fidelity_report.glyphs_missing == []

    def test_editresult_fields(self, tmp_path: Path) -> None:
        """EditResult should have correct success fields."""
        match = _first_match(SIMPLE_PDF, "simple")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "sample", out)

        assert result.success is True
        assert result.original_text == "simple"
        assert result.new_text == "sample"
        assert result.font_action == "kept"


# ── Different-length replacement ─────────────────────────────────────────


class TestDifferentLengthReplace:
    """Test replacement where old and new text have different lengths."""

    def test_shorter_replacement(self, tmp_path: Path) -> None:
        """Replace 'Aryan' (5 chars) with 'AB' (2 chars) — shorter."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "AB", out)

        assert result.success is True
        _validate_output(out, "AB", RESUME_PDF)

    def test_longer_replacement(self, tmp_path: Path) -> None:
        """Replace 'Aryan' (5 chars) with 'Aryana' (6 chars) — longer."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "Aryana", out)

        assert result.success is True
        _validate_output(out, "Aryana", RESUME_PDF)

    def test_winAnsi_different_length(self, tmp_path: Path) -> None:
        """Replace 'simple' (6) with 'a' (1) in WinAnsi PDF — much shorter."""
        match = _first_match(SIMPLE_PDF, "simple")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "a", out)

        assert result.success is True
        _validate_output(out, "a", SIMPLE_PDF)

    def test_overflow_detection(self, tmp_path: Path) -> None:
        """Replacing with very long text should flag overflow."""
        match = _first_match(SIMPLE_PDF, "Test")
        out = str(tmp_path / "output.pdf")
        # Use a very long replacement to trigger overflow
        long_text = "A" * 200
        result = replace(SIMPLE_PDF, match, long_text, out)

        assert result.success is True
        assert result.fidelity_report.overflow_detected is True


# ── Cross-element replacement ────────────────────────────────────────────


class TestCrossElementReplace:
    """Test replacement spanning multiple TJ fragments."""

    def test_multi_fragment_replace(self, tmp_path: Path) -> None:
        """Replace text that spans multiple TJ fragments with kerning."""
        match = _first_match(RESUME_PDF, "Email")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "Phone", out)

        assert result.success is True
        _validate_output(out, "Phone", RESUME_PDF)

    def test_cross_fragment_with_space(self, tmp_path: Path) -> None:
        """Replace 'B V' which spans fragments with a space inside."""
        match = _first_match(RESUME_PDF, "B V")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "C D", out)

        assert result.success is True
        _validate_output(out, "C D", RESUME_PDF)


# ── Encoding failure ─────────────────────────────────────────────────────


class TestEncodingFailure:
    """Test behavior when replacement text cannot be encoded."""

    def test_unencodable_returns_failure(self, tmp_path: Path) -> None:
        """Replacing with characters not in font returns success=False."""
        match = _first_match(SIMPLE_PDF, "Test")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "\u4f60\u597d", out)

        assert result.success is False
        assert result.font_action == "failed"
        assert len(result.fidelity_report.glyphs_missing) > 0

    def test_unencodable_no_output_file(self, tmp_path: Path) -> None:
        """Failed encoding should not create an output PDF."""
        match = _first_match(SIMPLE_PDF, "Test")
        out = str(tmp_path / "output.pdf")
        replace(SIMPLE_PDF, match, "\u4f60\u597d", out)

        assert not Path(out).exists()


# ── Dry run ──────────────────────────────────────────────────────────────


class TestDryRun:
    """Test dry_run mode: full analysis, no file modification."""

    def test_dry_run_returns_editresult(self, tmp_path: Path) -> None:
        """dry_run=True should return a valid EditResult."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "SPIKE", out, dry_run=True)

        assert result.success is True
        assert result.original_text == "Aryan"
        assert result.new_text == "SPIKE"
        assert result.font_action == "kept"

    def test_dry_run_no_output_file(self, tmp_path: Path) -> None:
        """dry_run=True should not create output file."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        replace(RESUME_PDF, match, "SPIKE", out, dry_run=True)

        assert not Path(out).exists()

    def test_dry_run_original_unchanged(self) -> None:
        """dry_run=True should not modify the original PDF."""
        original_bytes = Path(RESUME_PDF).read_bytes()
        match = _first_match(RESUME_PDF, "Aryan")
        replace(RESUME_PDF, match, "SPIKE", "/tmp/nope.pdf", dry_run=True)
        assert Path(RESUME_PDF).read_bytes() == original_bytes

    def test_dry_run_fidelity_report(self, tmp_path: Path) -> None:
        """dry_run should still compute FidelityReport."""
        match = _first_match(SIMPLE_PDF, "Test")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "A" * 200, out, dry_run=True)

        assert result.fidelity_report.overflow_detected is True
        assert not Path(out).exists()


# ── replace_all ──────────────────────────────────────────────────────────


class TestReplaceAll:
    """Test replace_all: find and replace all occurrences."""

    def test_multiple_occurrences(self, tmp_path: Path) -> None:
        """Replace a word appearing on multiple pages."""
        out = str(tmp_path / "output.pdf")
        results = replace_all(MULTIPAGE_PDF, "Content", "Section", out)

        assert len(results) == 2
        assert all(r.success for r in results)
        _validate_output(out, "Section", MULTIPAGE_PDF)
        # Original text should be gone
        text = get_text(out)
        assert "Content" not in text

    def test_not_found_returns_empty(self) -> None:
        """Searching for nonexistent text returns empty list."""
        results = replace_all(SIMPLE_PDF, "ZZZZZZZ", "YYYYYYY", "/tmp/nope.pdf")
        assert results == []

    def test_single_occurrence(self, tmp_path: Path) -> None:
        """replace_all with one match behaves like replace."""
        out = str(tmp_path / "output.pdf")
        results = replace_all(SIMPLE_PDF, "simple", "sample", out)

        assert len(results) == 1
        assert results[0].success is True
        _validate_output(out, "sample", SIMPLE_PDF)


# ── batch_replace ────────────────────────────────────────────────────────


class TestBatchReplace:
    """Test batch_replace: multiple find/replace pairs."""

    def test_two_edits(self, tmp_path: Path) -> None:
        """Apply two different replacements in one call."""
        out = str(tmp_path / "output.pdf")
        edits = [
            Edit(find="Page One", replace="Section A"),
            Edit(find="Page Two", replace="Section B"),
        ]
        results = batch_replace(MULTIPAGE_PDF, edits, out)

        assert len(results) == 2
        assert all(r.success for r in results)
        text = get_text(out)
        assert "Section A" in text
        assert "Section B" in text

    def test_one_result_per_edit(self, tmp_path: Path) -> None:
        """Return list has exactly one result per edit."""
        out = str(tmp_path / "output.pdf")
        edits = [
            Edit(find="Content", replace="Material"),
            Edit(find="NONEXISTENT", replace="NOTHING"),
        ]
        results = batch_replace(MULTIPAGE_PDF, edits, out)

        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False


# ── Error handling ───────────────────────────────────────────────────────


class TestErrorHandling:
    """Test error conditions raise appropriate exceptions."""

    def test_encrypted_pdf_raises(self, tmp_path: Path) -> None:
        """Encrypted PDF should raise PDFEditError."""
        # Create an encrypted PDF
        enc_path = str(tmp_path / "encrypted.pdf")
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        pdf.save(enc_path, encryption=pikepdf.Encryption(owner="secret", user=""))
        pdf.close()

        # Fabricate a minimal TextMatch (won't be used — error raised early)
        dummy_match = _first_match(SIMPLE_PDF, "Test")

        with pytest.raises(PDFEditError, match="encrypted"):
            replace(enc_path, dummy_match, "New", str(tmp_path / "out.pdf"))

    def test_stale_operator_index_raises(self, tmp_path: Path) -> None:
        """TextMatch with out-of-bounds operator_index should raise OperatorError."""
        match = _first_match(SIMPLE_PDF, "Test")
        # Mutate the match to have an invalid operator index
        for ch in match.characters:
            ch.operator_index = 99999
        match.operator_refs = [99999]

        with pytest.raises(OperatorError):
            replace(SIMPLE_PDF, match, "New", str(tmp_path / "out.pdf"))


# ── Output validation integration ────────────────────────────────────────


class TestOutputValidation:
    """Integration tests verifying complete output PDF quality."""

    def test_resume_spike_full_validation(self, tmp_path: Path) -> None:
        """Full end-to-end: resume Aryan->SPIKE with complete validation."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "SPIKE", out)

        assert result.success is True

        # PDF opens
        pdf = pikepdf.Pdf.open(out)
        assert len(pdf.pages) == 1
        pdf.close()

        # Text correct
        text = get_text(out)
        assert "SPIKE" in text
        assert "Aryan" not in text

        # Fonts preserved (6 fonts in original)
        orig_fonts = get_fonts(RESUME_PDF)
        out_fonts = get_fonts(out)
        assert len(out_fonts) == len(orig_fonts)
        assert sorted(f.postscript_name for f in orig_fonts) == sorted(
            f.postscript_name for f in out_fonts
        )

    def test_winAnsi_full_validation(self, tmp_path: Path) -> None:
        """Full end-to-end: WinAnsi Test Document -> New Document."""
        match = _first_match(SIMPLE_PDF, "Test Document")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "New Document!", out)

        assert result.success is True
        _validate_output(out, "New Document!", SIMPLE_PDF)
