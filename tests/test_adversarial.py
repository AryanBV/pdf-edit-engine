"""Adversarial input tests — malformed PDFs, pathological content, edge cases."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pikepdf
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from pdf_edit_engine import (
    Edit,
    OperatorError,
    PDFEditError,
    batch_replace,
    find,
    get_text,
    replace,
)

CORPUS_DIR = Path(__file__).parent / "corpus"
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")
RESUME_PDF = CORPUS_DIR / "Aryan_BV_Resume_2026.pdf"
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
    """Malformed PDFs have specific, asserted behaviors per probe."""

    def test_empty_content_stream(self, tmp_path: Path) -> None:
        """Empty content stream extracts to the empty string and finds no matches."""
        pdf = _make_empty_content_stream(tmp_path)
        text = get_text(pdf)
        assert text == ""
        matches = find(pdf, "anything")
        assert matches == []

    def test_missing_resources(self, tmp_path: Path) -> None:
        """Missing /Resources dict: locator skips text it cannot resolve and returns ""."""
        # Verified by probe: ContentStreamInterpreter._on_tf logs a warning and
        # leaves the resolver as None, so subsequent Tj operators are no-ops.
        # No raise; get_text returns "".
        pdf = _make_missing_resources(tmp_path)
        text = get_text(pdf)
        assert text == ""

    def test_garbled_stream(self, tmp_path: Path) -> None:
        """Garbled content stream raises OperatorError (a PDFEditError subclass)."""
        # Verified by probe: pikepdf.parse_content_stream surfaces a
        # UnicodeDecodeError that locator._build_index translates to OperatorError.
        pdf = _make_garbled_stream(tmp_path)
        with pytest.raises(PDFEditError):
            get_text(pdf)

    def test_null_font_reference(self, tmp_path: Path) -> None:
        """Null /Font entry: resolver fails silently, so text decodes to ""."""
        # Verified by probe: _on_tf catches the resolution failure and logs a
        # warning, then Tj operators short-circuit on resolver=None.
        pdf = _make_null_font_ref(tmp_path)
        text = get_text(pdf)
        assert text == ""

    @pytest.mark.parametrize(
        ("maker", "raises"),
        [
            (_make_empty_content_stream, False),
            (_make_missing_resources, False),
            (_make_garbled_stream, True),
            (_make_null_font_ref, False),
        ],
        ids=["empty", "missing_resources", "garbled", "null_font"],
    )
    def test_find_on_malformed_returns_empty_or_error(
        self,
        tmp_path: Path,
        maker: Callable[[Path], str],
        raises: bool,
    ) -> None:
        """Each malformed-PDF kind has a deterministic find() behavior."""
        pdf = maker(tmp_path)
        if raises:
            with pytest.raises(PDFEditError):
                find(pdf, "test")
        else:
            assert find(pdf, "test") == []


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
        """Font size 0 makes glyphs visually invisible but keeps stream text extractable."""
        # Verified by probe: get_text returns "Invisible" because text extraction
        # decodes the Tj operand regardless of font-size scaling.
        stream = b"BT /F1 0 Tf (Invisible) Tj ET"
        out = _make_with_font(tmp_path, stream, "zero_fs.pdf")
        text = get_text(out)
        assert "Invisible" in text


# ── Non-PDF inputs ────────────────────────────────────────────────────


class TestNonPdfInputs:
    """Non-PDF files and invalid paths fail with PDFEditError per INV-L-1."""

    def test_nonexistent_file(self) -> None:
        """Missing file → PDFEditError (translated by _pathutil.open_pdf)."""
        with pytest.raises(PDFEditError):
            get_text("absolutely_does_not_exist_xyz_12345.pdf")

    def test_empty_path(self) -> None:
        """Empty path → PDFEditError (translated by _pathutil.open_pdf)."""
        with pytest.raises(PDFEditError):
            get_text("")

    def test_find_with_none_search(self) -> None:
        """None search text: find() short-circuits via the empty-string guard and returns []."""
        # Verified by probe: locator.find checks `if not search_text: return []`,
        # which treats None the same as an empty string (truthiness).
        result = find(SIMPLE_PDF, None)  # type: ignore[arg-type]
        assert result == []


# ── Unicode edge cases ────────────────────────────────────────────────


class TestUnicodeEdgeCases:
    """Unicode edge cases produce specific FidelityReport / warning behavior."""

    @pytest.mark.skipif(not RESUME_PDF.exists(), reason="Aryan_BV_Resume_2026.pdf not in corpus")
    def test_replace_with_emoji(self, tmp_path: Path) -> None:
        """Emoji U+1F389 is unrenderable in Calibri → reported via FidelityReport.glyphs_missing."""
        # Verified by probe: replace() succeeds (no raise) and the emoji codepoint
        # appears in fidelity_report.glyphs_missing because Calibri (and its
        # metric-equivalent Carlito) lack the glyph.
        matches = find(str(RESUME_PDF), "Aryan")
        if not matches:
            pytest.skip("'Aryan' not found")
        output = str(tmp_path / "emoji.pdf")
        result = replace(str(RESUME_PDF), matches[0], "Test\U0001f389", output)
        assert result is not None
        assert "\U0001f389" in result.fidelity_report.glyphs_missing

    def test_replace_with_empty_string(self, tmp_path: Path) -> None:
        """Empty replacement is a clean no-glyph delete; FidelityReport reports no overflow."""
        # Verified by probe: replace() with "" succeeds, font_preserved=True,
        # overflow_detected=False, warnings=[].
        matches = find(SIMPLE_PDF, "Test Document")
        if not matches:
            pytest.skip("'Test Document' not found")
        output = str(tmp_path / "empty.pdf")
        result = replace(SIMPLE_PDF, matches[0], "", output, reflow=False)
        assert result is not None
        assert result.fidelity_report.font_preserved is True
        assert result.fidelity_report.overflow_detected is False
        assert result.warnings == []
        # And the deleted text is no longer present in the output.
        assert "Test Document" not in get_text(output)

    def test_replace_with_very_long_string(self, tmp_path: Path) -> None:
        """Very long replacement with reflow=False overflows; INV-J-3 ties overflow→warnings."""
        # Verified by probe: replace() succeeds with overflow_detected=True
        # and warnings contains "Overflow detected" (case-insensitive: "overflow").
        matches = find(SIMPLE_PDF, "Test Document")
        if not matches:
            pytest.skip("'Test Document' not found")
        output = str(tmp_path / "long.pdf")
        result = replace(SIMPLE_PDF, matches[0], "A" * 10000, output, reflow=False)
        assert result is not None
        assert result.fidelity_report.overflow_detected is True
        # INV-J-3: overflow_detected=True implies warnings non-empty.
        assert len(result.warnings) > 0
        assert any("overflow" in w.lower() for w in result.warnings)


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
        """INV-B-3: TextMatch from one PDF reused on a different PDF raises OperatorError."""
        # Verified by probe: surgeon detects the operator-index mismatch and
        # raises OperatorError ("Stale TextMatch: operator at index N is 'Tf'").
        # OperatorError is a PDFEditError subclass.
        pdf_a = SIMPLE_PDF
        pdf_b = str(CORPUS_DIR / "reportlab_table.pdf")
        matches_a = find(pdf_a, "Test Document")
        if not matches_a:
            pytest.skip("'Test Document' not found in reportlab_simple")
        output = str(tmp_path / "stale.pdf")
        with pytest.raises(PDFEditError) as excinfo:
            replace(pdf_b, matches_a[0], "Stale", output)
        # Tighter: it should be an OperatorError specifically, not some other
        # PDFEditError flavor.
        assert isinstance(excinfo.value, OperatorError)

    def test_batch_replace_overlapping_matches(self, tmp_path: Path) -> None:
        """Overlapping batch edits succeed; first edit wins for the overlapping span."""
        # Verified by probe: batch_replace accepts overlapping edits and applies
        # them in find-order. With Edit(find="Test", replace="X") followed by
        # Edit(find="Test Document", replace="Y") on text "Test Document", the
        # first edit replaces "Test" → "X", leaving "X Document"; the second
        # edit then has nothing to match. Output contains "X Document".
        text = get_text(SIMPLE_PDF)
        if "Test" not in text or "Test Document" not in text:
            pytest.skip("Required text not found")
        edits = [
            Edit(find="Test", replace="X"),
            Edit(find="Test Document", replace="Y"),
        ]
        output = str(tmp_path / "overlap.pdf")
        results = batch_replace(SIMPLE_PDF, edits, output)
        assert isinstance(results, list)
        assert len(results) == 2
        # Output is a valid PDF with the first edit applied.
        with pikepdf.Pdf.open(output) as out_pdf:
            assert len(out_pdf.pages) >= 1
        out_text = get_text(output)
        assert "X Document" in out_text
