"""Stress tests — PyMuPDF comparison, round-trip editing, clean install."""

from __future__ import annotations

import re
import subprocess
import sys
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
RESUME_PDF = CORPUS_DIR / "Aryan_BV_Resume_2026.pdf"
CIDFONT_PDF = CORPUS_DIR / "cidfont_synthetic.pdf"
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")

try:
    import fitz

    _has_fitz = True
except ImportError:
    _has_fitz = False


# ── Helpers ───────────────────────────────────────────────────────────


def _get_font_names(pdf_path: str) -> set[str]:
    """Extract all /BaseFont names from a PDF using pikepdf."""
    pdf = pikepdf.Pdf.open(pdf_path)
    fonts: set[str] = set()
    for page in pdf.pages:
        try:
            font_dict = page["/Resources"]["/Font"]
        except (KeyError, TypeError):
            continue
        for name in font_dict:
            try:
                font = font_dict[name]
                base_font = str(font.get("/BaseFont", "unknown"))
                fonts.add(base_font)
            except Exception:
                continue
    pdf.close()
    return fonts


# ── Phase 1: PyMuPDF font preservation comparison ────────────────────


@pytest.mark.skipif(not _has_fitz, reason="PyMuPDF (fitz) not installed")
class TestFontPreservation:
    """Prove that pdf-edit-engine preserves fonts while PyMuPDF does not."""

    @pytest.mark.skipif(not RESUME_PDF.exists(), reason="Aryan_BV_Resume_2026.pdf not in corpus")
    def test_resume_font_preservation(self, tmp_path: Path) -> None:
        """Identity-H CIDFont: our edit preserves Calibri, PyMuPDF adds fonts."""
        resume = str(RESUME_PDF)
        original_fonts = _get_font_names(resume)

        # Our edit
        matches = find(resume, "Aryan")
        assert len(matches) >= 1, "'Aryan' not found in resume"
        our_output = str(tmp_path / "ours.pdf")
        replace(resume, matches[0], "SPIKE", our_output, reflow=False)
        our_fonts = _get_font_names(our_output)

        # PyMuPDF edit
        pymupdf_output = str(tmp_path / "pymupdf.pdf")
        doc = fitz.open(resume)
        page = doc[0]
        areas = page.search_for("Aryan")
        assert len(areas) >= 1, "PyMuPDF couldn't find 'Aryan'"
        for area in areas:
            page.add_redact_annot(area, text="SPIKE")
        page.apply_redactions()
        doc.save(pymupdf_output)
        doc.close()
        pymupdf_fonts = _get_font_names(pymupdf_output)

        # Our output should preserve the original font set
        assert original_fonts == our_fonts, (
            f"Font mismatch:\n  Original: {original_fonts}\n  Ours: {our_fonts}"
        )
        # PyMuPDF output should differ (adds its own font)
        assert pymupdf_fonts != original_fonts, "Expected PyMuPDF to alter font set, but it didn't"

        print(f"\n  Original fonts: {sorted(original_fonts)}")
        print(f"  Our output:     {sorted(our_fonts)}  <- SAME")
        print(f"  PyMuPDF output: {sorted(pymupdf_fonts)}  <- DIFFERENT")

    @pytest.mark.skipif(not CIDFONT_PDF.exists(), reason="cidfont_synthetic.pdf not in corpus")
    def test_cidfont_synthetic_font_preservation(self, tmp_path: Path) -> None:
        """Synthetic Identity-H CIDFont: our edit preserves font, PyMuPDF does not."""
        cidfont = str(CIDFONT_PDF)
        original_fonts = _get_font_names(cidfont)

        # Our edit
        matches = find(cidfont, "Software Engineer")
        assert len(matches) >= 1, "'Software Engineer' not found in cidfont_synthetic"
        our_output = str(tmp_path / "ours.pdf")
        replace(cidfont, matches[0], "Senior Engineer", our_output, reflow=False)
        our_fonts = _get_font_names(our_output)

        assert original_fonts == our_fonts, (
            f"Font mismatch:\n  Original: {original_fonts}\n  Ours: {our_fonts}"
        )

        if _has_fitz:
            pymupdf_output = str(tmp_path / "pymupdf.pdf")
            doc = fitz.open(cidfont)
            page = doc[0]
            areas = page.search_for("Software Engineer")
            if areas:
                for area in areas:
                    page.add_redact_annot(area, text="Senior Engineer")
                page.apply_redactions()
                doc.save(pymupdf_output)
                doc.close()
                pymupdf_fonts = _get_font_names(pymupdf_output)
                assert pymupdf_fonts != original_fonts, (
                    "Expected PyMuPDF to alter font set, but it didn't"
                )
                print(f"\n  Original fonts: {sorted(original_fonts)}")
                print(f"  Our output:     {sorted(our_fonts)}  <- SAME")
                print(f"  PyMuPDF output: {sorted(pymupdf_fonts)}  <- DIFFERENT")
            else:
                doc.close()
                print(f"\n  Original fonts: {sorted(original_fonts)}")
                print(f"  Our output:     {sorted(our_fonts)}  <- SAME")
                print("  PyMuPDF: could not find text to edit")
        else:
            print(f"\n  Original fonts: {sorted(original_fonts)}")
            print(f"  Our output:     {sorted(our_fonts)}  <- SAME")

    def test_reportlab_font_preservation(self, tmp_path: Path) -> None:
        """WinAnsi: our edit preserves exact font objects."""
        original_fonts = _get_font_names(SIMPLE_PDF)

        matches = find(SIMPLE_PDF, "Test Document")
        assert len(matches) >= 1
        our_output = str(tmp_path / "ours.pdf")
        replace(SIMPLE_PDF, matches[0], "Best Document", our_output, reflow=False)
        our_fonts = _get_font_names(our_output)

        assert original_fonts == our_fonts, (
            f"Font mismatch:\n  Original: {original_fonts}\n  Ours: {our_fonts}"
        )
        print(f"\n  Original fonts: {sorted(original_fonts)}")
        print(f"  Our output:     {sorted(our_fonts)}  <- SAME")


# ── Phase 4: Round-trip editing integrity ─────────────────────────────


class TestRoundTrip:
    """Test that editing a PDF multiple times produces valid output."""

    def test_five_round_trips(self, tmp_path: Path) -> None:
        """Edit reportlab_simple.pdf 5 times sequentially."""
        initial_text = get_text(SIMPLE_PDF)
        start_phrase = "Test Document"
        if start_phrase not in initial_text:
            pytest.skip(f"'{start_phrase}' not found in reportlab_simple.pdf")

        replacements = [
            ("Test Document", "Round One Title"),
            ("Round One Title", "Round Two Title"),
            ("Round Two Title", "Round Three Title"),
            ("Round Three Title", "Round Four Title"),
            ("Round Four Title", "Final Title"),
        ]

        current_pdf = SIMPLE_PDF
        for i, (old, new) in enumerate(replacements):
            output = str(tmp_path / f"round_{i}.pdf")
            matches = find(str(current_pdf), old)
            assert len(matches) >= 1, f"Round {i}: '{old}' not found"
            result = replace(str(current_pdf), matches[0], new, output, reflow=False)
            assert result.success, f"Round {i}: replace failed"

            # Verify output is valid and contains new text
            pikepdf.Pdf.open(output).close()
            text = get_text(output)
            assert new in text, f"Round {i}: '{new}' not in output"

            current_pdf = output

        # Final checks
        final_text = get_text(str(current_pdf))
        assert "Final Title" in final_text
        final_fonts = get_fonts(str(current_pdf))
        original_fonts = get_fonts(SIMPLE_PDF)
        assert len(final_fonts) >= 1, "No fonts in final output"
        print(
            f"\n  Round-trip: original fonts={len(original_fonts)}, final fonts={len(final_fonts)}"
        )

    @pytest.mark.skipif(not RESUME_PDF.exists(), reason="Aryan_BV_Resume_2026.pdf not in corpus")
    def test_round_trip_with_extension(self, tmp_path: Path) -> None:
        """Edit with a character requiring font extension, then edit again."""
        resume = str(RESUME_PDF)
        matches = find(resume, "Aryan")
        if not matches:
            pytest.skip("'Aryan' not found in resume")

        # Round 1: replace with a char that may need CMap extension
        r1_output = str(tmp_path / "ext_round1.pdf")
        replace(resume, matches[0], "Spike", r1_output)

        # Round 2: replace the extended text back
        matches2 = find(r1_output, "Spike")
        if matches2:
            r2_output = str(tmp_path / "ext_round2.pdf")
            replace(r1_output, matches2[0], "Aryan", r2_output)
            pikepdf.Pdf.open(r2_output).close()  # must be valid

    def test_replace_preserves_other_text(self, tmp_path: Path) -> None:
        """After replacing one phrase, other text remains intact."""
        matches = find(SIMPLE_PDF, "Test Document")
        if not matches:
            pytest.skip("'Test Document' not found")
        output = str(tmp_path / "preserve.pdf")
        replace(SIMPLE_PDF, matches[0], "Changed Title", output, reflow=False)
        text = get_text(output)
        assert "Changed Title" in text
        # Other text should still be present
        assert "simple test document" in text or "reportlab" in text

    def test_output_page_count_unchanged(self, tmp_path: Path) -> None:
        """Editing should not change page count."""
        matches = find(SIMPLE_PDF, "Test Document")
        if not matches:
            pytest.skip("'Test Document' not found")
        output = str(tmp_path / "pagecount.pdf")
        replace(SIMPLE_PDF, matches[0], "New Title", output, reflow=False)
        with pikepdf.Pdf.open(output) as edited, pikepdf.Pdf.open(SIMPLE_PDF) as orig:
            assert len(edited.pages) == len(orig.pages)

    def test_output_valid_pdf_structure(self, tmp_path: Path) -> None:
        """Edited PDF can be re-saved without errors."""
        matches = find(SIMPLE_PDF, "Test Document")
        if not matches:
            pytest.skip("'Test Document' not found")
        output = str(tmp_path / "valid.pdf")
        replace(SIMPLE_PDF, matches[0], "Resaved", output, reflow=False)
        resave = str(tmp_path / "resaved.pdf")
        with pikepdf.Pdf.open(output) as pdf:
            pdf.save(resave)
        pikepdf.Pdf.open(resave).close()

    @pytest.mark.skipif(not CIDFONT_PDF.exists(), reason="cidfont_synthetic.pdf not in corpus")
    def test_round_trip_cidfont(self, tmp_path: Path) -> None:
        """Edit cidfont_synthetic.pdf 3 times sequentially."""
        cidfont = str(CIDFONT_PDF)
        replacements = [
            ("Software Engineer", "Senior Engineer"),
            ("Senior Engineer", "Staff Engineer"),
            ("Staff Engineer", "Principal Engineer"),
        ]

        current_pdf = cidfont
        for i, (old, new) in enumerate(replacements):
            output = str(tmp_path / f"cidfont_round_{i}.pdf")
            matches = find(str(current_pdf), old)
            assert len(matches) >= 1, f"Round {i}: '{old}' not found"
            result = replace(str(current_pdf), matches[0], new, output, reflow=False)
            assert result.success, f"Round {i}: replace failed"
            pikepdf.Pdf.open(output).close()
            text = get_text(output)
            assert new in text, f"Round {i}: '{new}' not in output"
            current_pdf = output

        final_text = get_text(str(current_pdf))
        assert "Principal Engineer" in final_text

    def test_batch_then_verify(self, tmp_path: Path) -> None:
        """batch_replace with multiple edits, all appear in output."""
        text = get_text(SIMPLE_PDF)
        edits: list[Edit] = []
        pairs = [
            ("Test Document", "Changed Document"),
            ("reportlab", "REPORTLAB"),
        ]
        for old, new in pairs:
            if old in text:
                edits.append(Edit(find=old, replace=new))
        assert len(edits) == 2, "Both test phrases must be present in source PDF"

        output = str(tmp_path / "batch.pdf")
        results = batch_replace(SIMPLE_PDF, edits, output, reflow=False)
        successful = sum(1 for r in results if r.success)
        assert successful == len(edits), f"Only {successful}/{len(edits)} edits succeeded"

        # Verify output is valid and contains all replacements
        pikepdf.Pdf.open(output).close()
        text_out = get_text(output)
        for edit in edits:
            assert edit.replace in text_out, f"'{edit.replace}' not found in output"


# ── Phase 5: Clean install verification ───────────────────────────────


@pytest.mark.slow
class TestCleanInstall:
    """Verify the wheel installs correctly in an isolated environment."""

    def _find_wheel(self) -> Path:
        dist_dir = Path("dist")
        if not dist_dir.exists():
            pytest.skip("No dist/ directory — run 'python -m build' first")
        wheels = list(dist_dir.glob("*.whl"))
        if not wheels:
            pytest.skip("No wheel found in dist/ — run 'python -m build' first")
        return wheels[0]

    def test_wheel_installs_in_venv(self, tmp_path: Path) -> None:
        """Install wheel in a fresh venv and verify imports."""
        wheel = self._find_wheel()
        venv_dir = tmp_path / "test_venv"

        # Create fresh venv
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            timeout=60,
        )

        if sys.platform == "win32":
            pip = str(venv_dir / "Scripts" / "pip.exe")
            python = str(venv_dir / "Scripts" / "python.exe")
        else:
            pip = str(venv_dir / "bin" / "pip")
            python = str(venv_dir / "bin" / "python")

        result = subprocess.run(
            [pip, "install", str(wheel)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"pip install failed:\n{result.stderr}"

        result = subprocess.run(
            [
                python,
                "-c",
                "from pdf_edit_engine import find, replace, batch_replace, "
                "merge_pdfs, get_text, get_fonts, detect_paragraphs; "
                "print('ALL IMPORTS OK')",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Import failed:\n{result.stderr}"
        assert "ALL IMPORTS OK" in result.stdout

    def test_installed_version_matches(self, tmp_path: Path) -> None:
        """Installed ``__version__`` must match the version embedded in the wheel filename."""
        wheel = self._find_wheel()
        m = re.match(r"pdf_edit_engine-(\d+\.\d+\.\d+)-", wheel.name)
        assert m is not None, f"Could not parse version from wheel: {wheel.name}"
        expected_version = m.group(1)
        venv_dir = tmp_path / "test_venv"

        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            timeout=60,
        )

        if sys.platform == "win32":
            pip = str(venv_dir / "Scripts" / "pip.exe")
            python = str(venv_dir / "Scripts" / "python.exe")
        else:
            pip = str(venv_dir / "bin" / "pip")
            python = str(venv_dir / "bin" / "python")

        subprocess.run(
            [pip, "install", str(wheel)],
            capture_output=True,
            text=True,
            timeout=120,
        )

        result = subprocess.run(
            [python, "-c", "import pdf_edit_engine; print(pdf_edit_engine.__version__)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert expected_version in result.stdout, (
            f"Version mismatch: expected {expected_version}, got {result.stdout!r}"
        )
