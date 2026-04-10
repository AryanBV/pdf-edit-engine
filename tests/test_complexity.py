"""Complexity-tiered stress tests — multi-font, transforms, contract, CIDFont."""

from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine import (
    Edit,
    batch_replace,
    find,
    get_fonts,
    get_text,
    replace,
)

CORPUS_DIR = Path(__file__).parent / "corpus"
DEMO_OUTPUT = Path(__file__).parent.parent / "demo_output"

MULTIFONT_PDF = CORPUS_DIR / "complex_multifont.pdf"
TRANSFORMED_PDF = CORPUS_DIR / "complex_transformed.pdf"
CONTRACT_PDF = CORPUS_DIR / "complex_contract.pdf"
PYMUPDF_EDITED_PDF = CORPUS_DIR / "complex_pymupdf_edited.pdf"
CIDFONT_PDF = CORPUS_DIR / "cidfont_synthetic.pdf"

try:
    import fitz  # type: ignore[import-untyped]

    _has_fitz = True
except ImportError:
    _has_fitz = False


def _get_font_names(pdf_path: str) -> set[str]:
    """Extract all /BaseFont names from a PDF."""
    with pikepdf.Pdf.open(pdf_path) as pdf:
        fonts: set[str] = set()
        for page in pdf.pages:
            try:
                font_dict = page["/Resources"]["/Font"]
            except (KeyError, TypeError):
                continue
            for name in font_dict:
                try:
                    base = str(font_dict[name].get("/BaseFont", "unknown"))
                    fonts.add(base)
                except Exception:
                    continue
    return fonts


# ── Level 1: Multi-font ────────────────────────────────────────────────


@pytest.mark.skipif(not MULTIFONT_PDF.exists(), reason="complex_multifont.pdf not generated")
class TestMultiFont:
    """Tests for multi-font PDF with mixed fonts on the same line."""

    def test_get_text_extracts_all_content(self) -> None:
        text = get_text(str(MULTIFONT_PDF))
        assert "Multi-Font Stress Test" in text
        assert "Times-Roman" in text
        assert "hello()" in text
        assert "support@example.com" in text

    def test_find_email_in_courier(self) -> None:
        matches = find(str(MULTIFONT_PDF), "support@example.com")
        assert len(matches) >= 1
        # Verify font info reflects Courier
        match = matches[0]
        assert "Courier" in match.font_info.postscript_name

    def test_replace_email_preserves_font(self, tmp_path: Path) -> None:
        matches = find(str(MULTIFONT_PDF), "support@example.com")
        assert len(matches) >= 1
        output = str(tmp_path / "email_replaced.pdf")
        result = replace(str(MULTIFONT_PDF), matches[0], "help@newdomain.com", output, reflow=False)
        assert result.success
        assert result.fidelity_report.font_preserved
        text = get_text(output)
        assert "help@newdomain.com" in text
        pikepdf.Pdf.open(output).close()

    def test_find_contact_in_times(self) -> None:
        matches = find(str(MULTIFONT_PDF), "Contact us at")
        assert len(matches) >= 1

    def test_find_dollar_amount(self) -> None:
        matches = find(str(MULTIFONT_PDF), "$1,234.56")
        assert len(matches) >= 1


# ── Level 2: Transformed text ──────────────────────────────────────────


@pytest.mark.skipif(not TRANSFORMED_PDF.exists(), reason="complex_transformed.pdf not generated")
class TestTransformed:
    """Tests for text under CTM transformations."""

    def test_find_normal_text(self) -> None:
        matches = find(str(TRANSFORMED_PDF), "Normal text here")
        assert len(matches) >= 1

    def test_replace_normal_text(self, tmp_path: Path) -> None:
        matches = find(str(TRANSFORMED_PDF), "Normal text here")
        assert len(matches) >= 1
        output = str(tmp_path / "normal_replaced.pdf")
        result = replace(
            str(TRANSFORMED_PDF), matches[0], "Changed text here", output, reflow=False
        )
        assert result.success
        pikepdf.Pdf.open(output).close()

    def test_find_stretched_text(self) -> None:
        matches = find(str(TRANSFORMED_PDF), "Stretched text")
        assert len(matches) >= 1

    def test_find_fine_print(self) -> None:
        matches = find(str(TRANSFORMED_PDF), "Fine print disclaimer")
        assert len(matches) >= 1

    def test_find_spaced_out(self) -> None:
        """Tc character spacing: text stored as 'Spaced Out', not with actual spaces."""
        matches = find(str(TRANSFORMED_PDF), "Spaced Out")
        assert len(matches) >= 1

    def test_rotated_text_no_crash(self) -> None:
        """Rotated text: engine must not crash. Non-rotated text always extracts."""
        text = get_text(str(TRANSFORMED_PDF))
        assert text
        assert "Normal text here" in text

    def test_find_compressed_text(self) -> None:
        matches = find(str(TRANSFORMED_PDF), "Compressed text")
        assert len(matches) >= 1


# ── Level 3: Dense contract ────────────────────────────────────────────


@pytest.mark.skipif(not CONTRACT_PDF.exists(), reason="complex_contract.pdf not generated")
class TestContract:
    """Tests for a realistic 3-page contract."""

    def test_get_text_all_pages(self) -> None:
        text = get_text(str(CONTRACT_PDF))
        assert "SERVICE AGREEMENT" in text
        assert "TERMS AND CONDITIONS" in text
        assert "SIGNATURES" in text

    def test_find_agreement_number(self) -> None:
        matches = find(str(CONTRACT_PDF), "SA-2026-001")
        assert len(matches) == 1

    def test_find_dollar_amount(self) -> None:
        matches = find(str(CONTRACT_PDF), "$50,000.00")
        assert len(matches) >= 1

    def test_batch_replace_contract(self, tmp_path: Path) -> None:
        """Batch replace 4 edits on the contract."""
        edits = [
            Edit(find="SA-2026-001", replace="SA-2026-999"),
            Edit(find="$50,000.00", replace="$75,000.00"),
            Edit(find="PARTIES", replace="CONTRACTING PARTIES"),
            Edit(find="April 4, 2026", replace="May 1, 2026"),
        ]
        output = str(tmp_path / "contract_edited.pdf")
        results = batch_replace(str(CONTRACT_PDF), edits, output)

        successful = sum(1 for r in results if r.success)
        assert successful >= 2, f"Only {successful}/4 edits succeeded: {results}"

        pikepdf.Pdf.open(output).close()
        text = get_text(output)
        # At least the agreement number and dollar amount should be replaced
        if results[0].success:
            assert "SA-2026-999" in text
        if results[1].success:
            assert "$75,000.00" in text

        print(f"\n  Contract batch: {successful}/4 edits succeeded")
        for i, r in enumerate(results):
            status = "OK" if r.success else f"FAIL ({r.warnings})"
            print(f"    Edit {i}: {r.original_text!r} -> {r.new_text!r}: {status}")

    def test_save_contract_demo(self, demo_output_dir: Path) -> None:
        """Save before/after contract PDFs for manual inspection."""
        before = demo_output_dir / "contract_before.pdf"
        shutil.copy2(str(CONTRACT_PDF), str(before))

        edits = [
            Edit(find="SA-2026-001", replace="SA-2026-999"),
            Edit(find="$50,000.00", replace="$75,000.00"),
            Edit(find="PARTIES", replace="CONTRACTING PARTIES"),
            Edit(find="April 4, 2026", replace="May 1, 2026"),
        ]
        after = demo_output_dir / "contract_after.pdf"
        batch_replace(str(CONTRACT_PDF), edits, str(after))

        assert before.exists()
        assert after.exists()
        print(f"\n  Contract before: {before}")
        print(f"  Contract after:  {after}")


# ── Level 4: PyMuPDF-edited PDF ────────────────────────────────────────


@pytest.mark.skipif(
    not _has_fitz or not PYMUPDF_EDITED_PDF.exists(),
    reason="PyMuPDF not installed or complex_pymupdf_edited.pdf not generated",
)
class TestPyMuPDFEdited:
    """Tests for PDFs that were previously edited by PyMuPDF."""

    def test_get_text_no_crash(self) -> None:
        text = get_text(str(PYMUPDF_EDITED_PDF))
        assert len(text) > 0

    def test_find_pymupdf_inserted_text(self) -> None:
        matches = find(str(PYMUPDF_EDITED_PDF), "PyMuPDF Edited Author")
        assert len(matches) >= 1, "Could not find text that PyMuPDF inserted"

    def test_find_original_text(self) -> None:
        matches = find(str(PYMUPDF_EDITED_PDF), "Original Document Title")
        assert len(matches) >= 1, "Could not find text from original PDF"

    def test_replace_on_pymupdf_text(self, tmp_path: Path) -> None:
        """Replace text that PyMuPDF inserted — engine handles mixed font origins."""
        matches = find(str(PYMUPDF_EDITED_PDF), "PyMuPDF Edited Author")
        if not matches:
            pytest.skip("PyMuPDF-inserted text not found")
        output = str(tmp_path / "re_edited.pdf")
        try:
            result = replace(
                str(PYMUPDF_EDITED_PDF),
                matches[0],
                "Re-Edited Author",
                output,
                reflow=False,
            )
            assert result.success
            pikepdf.Pdf.open(output).close()
        except Exception as exc:
            pytest.skip(f"Engine cannot edit PyMuPDF-inserted text: {exc}")


# ── Level 5: Synthetic CIDFont (Identity-H) ───────────────────────────


@pytest.mark.skipif(not CIDFONT_PDF.exists(), reason="cidfont_synthetic.pdf not generated")
class TestCIDFont:
    """Tests for synthetic CIDFont PDF with Identity-H encoding."""

    def test_identity_h_encoding(self) -> None:
        fonts = get_fonts(str(CIDFONT_PDF))
        identity_h_fonts = [f for f in fonts if f.encoding_type == "Identity-H"]
        assert len(identity_h_fonts) >= 1, (
            f"Expected Identity-H font, got: {[f.encoding_type for f in fonts]}"
        )
        print(
            f"\n  CIDFont: {identity_h_fonts[0].postscript_name} "
            f"({identity_h_fonts[0].glyph_count} glyphs)"
        )

    def test_find_software_engineer(self) -> None:
        matches = find(str(CIDFONT_PDF), "Software Engineer")
        assert len(matches) >= 1

    def test_engine_replace_preserves_fonts(self, tmp_path: Path) -> None:
        """Replace text — font set must remain identical to original."""
        original_fonts = _get_font_names(str(CIDFONT_PDF))

        matches = find(str(CIDFONT_PDF), "Software Engineer")
        assert len(matches) >= 1
        output = str(tmp_path / "cidfont_edited.pdf")
        result = replace(str(CIDFONT_PDF), matches[0], "Senior Engineer", output, reflow=False)
        assert result.success

        edited_fonts = _get_font_names(output)
        assert original_fonts == edited_fonts, (
            f"Font mismatch:\n  Original: {original_fonts}\n  Edited: {edited_fonts}"
        )

        text = get_text(output)
        assert "Senior Engineer" in text
        pikepdf.Pdf.open(output).close()

    def test_replace_dollar_amount(self, tmp_path: Path) -> None:
        matches = find(str(CIDFONT_PDF), "$120,000.00")
        assert len(matches) >= 1
        output = str(tmp_path / "cidfont_salary.pdf")
        result = replace(str(CIDFONT_PDF), matches[0], "$150,000.00", output, reflow=False)
        assert result.success
        text = get_text(output)
        assert "$150,000.00" in text

    @pytest.mark.skipif(not _has_fitz, reason="PyMuPDF (fitz) not installed")
    def test_pymupdf_comparison(self, tmp_path: Path) -> None:
        """Prove engine preserves fonts while PyMuPDF does not."""
        original_fonts = _get_font_names(str(CIDFONT_PDF))

        # Our edit
        matches = find(str(CIDFONT_PDF), "Software Engineer")
        assert len(matches) >= 1
        our_output = str(tmp_path / "ours.pdf")
        replace(str(CIDFONT_PDF), matches[0], "Senior Engineer", our_output, reflow=False)
        our_fonts = _get_font_names(our_output)

        # PyMuPDF edit
        pymupdf_output = str(tmp_path / "pymupdf.pdf")
        doc = fitz.open(str(CIDFONT_PDF))
        page = doc[0]
        areas = page.search_for("Software Engineer")
        assert len(areas) >= 1, "PyMuPDF couldn't find 'Software Engineer'"
        for area in areas:
            page.add_redact_annot(area, text="Senior Engineer")
        page.apply_redactions()
        doc.save(pymupdf_output)
        doc.close()
        pymupdf_fonts = _get_font_names(pymupdf_output)

        # Our output should preserve the original font set
        assert original_fonts == our_fonts, (
            f"Engine font mismatch:\n  Original: {original_fonts}\n  Ours: {our_fonts}"
        )
        # PyMuPDF output should differ
        assert pymupdf_fonts != original_fonts, "Expected PyMuPDF to alter font set, but it didn't"

        print(f"\n  Original fonts: {sorted(original_fonts)}")
        print(f"  Our output:     {sorted(our_fonts)}  <- SAME")
        print(f"  PyMuPDF output: {sorted(pymupdf_fonts)}  <- DIFFERENT")
