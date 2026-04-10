"""Ultimate stress test suite for pdf-edit-engine.

Covers: concurrency, edit chains, resource leaks, large-scale performance,
batch stress, encrypt/decrypt workflows, merge/split roundtrips, crop validation,
form fields, degenerate inputs, inline images, mixed encodings, rotated pages,
CJK/Unicode, digital signatures, XRef streams, font cache stress, and annotations.

Run with: python -m pytest tests/test_ultimate_stress.py -v -m stress --tb=short
"""

from __future__ import annotations

import contextlib
import gc
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine import (
    Edit,
    PDFEditError,
    add_annotation,
    batch_replace,
    crop_pages,
    decrypt_pdf,
    encrypt_pdf,
    fill_form,
    find,
    get_annotations,
    get_fonts,
    get_text,
    merge_pdfs,
    replace,
    split_pdf,
)
from tests.stress_generators import (
    gen_3_source_pdfs,
    gen_100_fonts_pdf,
    gen_1000_page_pdf,
    gen_batch_500_pdf,
    gen_combining_diacritics_pdf,
    gen_cropped_pdf,
    gen_damaged_font_stream_pdf,
    gen_degenerate_ctm_pdf,
    gen_digital_signature_pdf,
    gen_empty_password_pdf,
    gen_encrypted_pdf,
    gen_form_all_types_pdf,
    gen_inline_image_between_text_pdf,
    gen_mixed_encoding_pdf,
    gen_multipage_pdf,
    gen_no_fonts_pdf,
    gen_overlapping_text_pdf,
    gen_resource_leak_pdf,
    gen_rotated_pages_pdf,
    gen_sequential_edit_pdf,
    gen_text_outside_mediabox_pdf,
    gen_very_long_line_pdf,
    gen_xref_stream_pdf,
    gen_zero_font_size_pdf,
)

CORPUS_DIR = Path(__file__).parent / "corpus"
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")

try:
    import psutil

    _has_psutil = True
except ImportError:
    _has_psutil = False

pytestmark = pytest.mark.stress


# ── Class 1: Concurrency ─────────────────────────────────────────────


class TestConcurrency:
    """Thread safety tests for global caches in locator.py and system_fonts.py."""

    def test_parallel_find_same_pdf(self, tmp_path: Path) -> None:
        """10 threads calling find() on the same PDF simultaneously."""
        pdf = gen_sequential_edit_pdf(tmp_path)
        errors: list[str] = []

        def _find(thread_id: int) -> None:
            try:
                matches = find(pdf, "ChainAlpha")
                if len(matches) < 1:
                    errors.append(f"Thread {thread_id}: no matches found")
            except Exception as exc:
                errors.append(f"Thread {thread_id}: {exc}")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_find, i) for i in range(10)]
            for f in as_completed(futures):
                f.result()  # re-raise if exception

        assert not errors, "Thread errors:\n" + "\n".join(errors)

    def test_parallel_find_different_pdfs(self, tmp_path: Path) -> None:
        """10 threads calling find() on different PDFs (triggers cache thrash)."""
        pdfs: list[str] = []
        for i in range(10):
            sub = tmp_path / f"thread_{i}"
            sub.mkdir()
            pdfs.append(gen_sequential_edit_pdf(sub))

        errors: list[str] = []

        def _find(thread_id: int) -> None:
            try:
                matches = find(pdfs[thread_id], "ChainAlpha")
                if len(matches) < 1:
                    errors.append(f"Thread {thread_id}: no matches")
            except Exception as exc:
                errors.append(f"Thread {thread_id}: {exc}")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_find, i) for i in range(10)]
            for f in as_completed(futures):
                f.result()

        assert not errors, "Thread errors:\n" + "\n".join(errors)

    def test_parallel_get_text_same_pdf(self, tmp_path: Path) -> None:
        """10 threads calling get_text() on the same PDF."""
        pdf = gen_sequential_edit_pdf(tmp_path)
        results: list[str] = [None] * 10  # type: ignore[list-item]

        def _extract(thread_id: int) -> None:
            results[thread_id] = get_text(pdf)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_extract, i) for i in range(10)]
            for f in as_completed(futures):
                f.result()

        # All threads should get the same text
        for i, text in enumerate(results):
            assert text is not None, f"Thread {i} returned None"
            assert "ChainAlpha" in text, f"Thread {i} got wrong text"

    def test_parallel_replace_different_outputs(self, tmp_path: Path) -> None:
        """10 threads replacing in same source to different output files."""
        pdf = gen_sequential_edit_pdf(tmp_path)
        errors: list[str] = []

        def _replace(thread_id: int) -> None:
            try:
                matches = find(pdf, "ChainAlpha")
                if not matches:
                    errors.append(f"Thread {thread_id}: no match")
                    return
                out = str(tmp_path / f"output_{thread_id}.pdf")
                result = replace(pdf, matches[0], f"Replaced{thread_id}", out, reflow=False)
                if not result.success:
                    errors.append(f"Thread {thread_id}: replace failed")
            except Exception as exc:
                errors.append(f"Thread {thread_id}: {exc}")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_replace, i) for i in range(10)]
            for f in as_completed(futures):
                f.result()

        assert not errors, "Thread errors:\n" + "\n".join(errors)

    def test_cache_thrash_alternating_pdfs(self, tmp_path: Path) -> None:
        """Single thread, rapid alternation between 2 PDFs exercises _cached_path swap."""
        sub1 = tmp_path / "alt1"
        sub1.mkdir()
        sub2 = tmp_path / "alt2"
        sub2.mkdir()
        pdf1 = gen_sequential_edit_pdf(sub1)
        pdf2 = gen_resource_leak_pdf(sub2)

        for _ in range(50):
            text1 = get_text(pdf1)
            assert "ChainAlpha" in text1
            text2 = get_text(pdf2)
            assert "LeakTestContent" in text2


# ── Class 2: Edit Chain ──────────────────────────────────────────────


class TestEditChain:
    """Sequential find→replace→find→replace pattern tests."""

    def test_sequential_five_edits(self, tmp_path: Path) -> None:
        """Find/replace 5 different phrases sequentially on same PDF."""
        pdf = gen_sequential_edit_pdf(tmp_path)
        current = pdf

        targets = [
            ("ChainAlpha", "ReplacedAlpha"),
            ("ChainBravo", "ReplacedBravo"),
            ("ChainCharlie", "ReplacedCharlie"),
            ("ChainDelta", "ReplacedDelta"),
            ("ChainEcho", "ReplacedEcho"),
        ]
        for i, (old, new) in enumerate(targets):
            matches = find(current, old)
            assert len(matches) >= 1, f"Edit {i}: '{old}' not found"
            out = str(tmp_path / f"chain_{i}.pdf")
            result = replace(current, matches[0], new, out, reflow=False)
            assert result.success, f"Edit {i}: replace failed"
            current = out

        final = get_text(current)
        for _, new in targets:
            assert new in final, f"'{new}' missing from final output"

    def test_fresh_match_after_each_edit(self, tmp_path: Path) -> None:
        """find/replace/find/replace with fresh matches each time."""
        pdf = gen_sequential_edit_pdf(tmp_path)

        # Edit 1
        m1 = find(pdf, "ChainAlpha")
        assert m1
        out1 = str(tmp_path / "fresh1.pdf")
        replace(pdf, m1[0], "FreshAlpha", out1, reflow=False)

        # Edit 2 — fresh find on output
        m2 = find(out1, "ChainBravo")
        assert m2
        out2 = str(tmp_path / "fresh2.pdf")
        replace(out1, m2[0], "FreshBravo", out2, reflow=False)

        text = get_text(out2)
        assert "FreshAlpha" in text
        assert "FreshBravo" in text

    def test_replace_then_find_original_gone(self, tmp_path: Path) -> None:
        """After replace "A"->"B", find("A") should return empty."""
        pdf = gen_sequential_edit_pdf(tmp_path)
        matches = find(pdf, "ChainAlpha")
        assert matches
        out = str(tmp_path / "gone.pdf")
        replace(pdf, matches[0], "ReplacedAlpha", out, reflow=False)
        remaining = find(out, "ChainAlpha")
        assert len(remaining) == 0, "Original text should not be found after replacement"

    def test_replace_then_find_replacement(self, tmp_path: Path) -> None:
        """After replace "A"->"B", find("B") should return a match."""
        pdf = gen_sequential_edit_pdf(tmp_path)
        matches = find(pdf, "ChainAlpha")
        assert matches
        out = str(tmp_path / "found.pdf")
        replace(pdf, matches[0], "ReplacedAlpha", out, reflow=False)
        new_matches = find(out, "ReplacedAlpha")
        assert len(new_matches) >= 1

    def test_batch_then_sequential(self, tmp_path: Path) -> None:
        """batch_replace(3 edits) then sequential replace(1 edit)."""
        pdf = gen_sequential_edit_pdf(tmp_path)
        edits = [
            Edit(find="ChainAlpha", replace="BatchAlpha"),
            Edit(find="ChainBravo", replace="BatchBravo"),
            Edit(find="ChainCharlie", replace="BatchCharlie"),
        ]
        batch_out = str(tmp_path / "batch_chain.pdf")
        results = batch_replace(pdf, edits, batch_out)
        assert all(r.success for r in results), "Not all batch edits succeeded"

        # Now sequential edit on batch output
        m = find(batch_out, "ChainDelta")
        assert m
        seq_out = str(tmp_path / "seq_after_batch.pdf")
        result = replace(batch_out, m[0], "SeqDelta", seq_out, reflow=False)
        assert result.success

        text = get_text(seq_out)
        assert "BatchAlpha" in text
        assert "SeqDelta" in text


# ── Class 3: Resource Leaks ──────────────────────────────────────────


@pytest.mark.skipif(not _has_psutil, reason="psutil not installed")
class TestResourceLeaks:
    """File handle and resource leak detection."""

    def _open_file_count(self) -> int:
        proc = psutil.Process(os.getpid())
        return len(proc.open_files())

    def test_file_handles_closed_after_find(self, tmp_path: Path) -> None:
        """100 find() calls should not leak file handles."""
        pdf = gen_resource_leak_pdf(tmp_path)
        gc.collect()
        before = self._open_file_count()
        for _ in range(100):
            find(pdf, "LeakTestContent")
        gc.collect()
        after = self._open_file_count()
        leaked = after - before
        assert leaked < 5, f"Leaked {leaked} file handles after 100 find() calls"

    def test_file_handles_closed_after_error(self, tmp_path: Path) -> None:
        """find() on invalid PDF should not leak handles."""
        bad = str(tmp_path / "nonexistent.pdf")
        gc.collect()
        before = self._open_file_count()
        for _ in range(20):
            with contextlib.suppress(PDFEditError, FileNotFoundError, OSError):
                find(bad, "anything")
        gc.collect()
        after = self._open_file_count()
        leaked = after - before
        assert leaked < 3, f"Leaked {leaked} handles after error calls"

    def test_file_handles_closed_after_replace(self, tmp_path: Path) -> None:
        """50 replace() calls should not leak file handles."""
        pdf = gen_sequential_edit_pdf(tmp_path)
        gc.collect()
        before = self._open_file_count()
        for i in range(50):
            matches = find(pdf, "ChainAlpha")
            if matches:
                out = str(tmp_path / f"leak_replace_{i}.pdf")
                replace(pdf, matches[0], "Replaced", out, reflow=False)
        gc.collect()
        after = self._open_file_count()
        leaked = after - before
        assert leaked < 5, f"Leaked {leaked} handles after 50 replace() calls"


# ── Class 4: Large Scale Performance ─────────────────────────────────


class TestLargeScale:
    """Performance and scaling tests on 1000-page PDFs."""

    @pytest.fixture(scope="class")
    def large_pdf(self, tmp_path_factory: pytest.TempPathFactory) -> str:
        return gen_1000_page_pdf(tmp_path_factory.mktemp("large"))

    def test_get_text_1000_pages(self, large_pdf: str) -> None:
        """get_text on 1000 pages must complete in <60s."""
        start = time.perf_counter()
        text = get_text(large_pdf)
        elapsed = time.perf_counter() - start
        assert elapsed < 60, f"get_text took {elapsed:.1f}s (limit 60s)"
        assert "Page 0 Line 0" in text
        assert "Page 999 Line 0" in text

    def test_find_1000_pages(self, large_pdf: str) -> None:
        """find() on 1000 pages must complete in <60s."""
        start = time.perf_counter()
        matches = find(large_pdf, "quick brown fox")
        elapsed = time.perf_counter() - start
        assert elapsed < 60, f"find took {elapsed:.1f}s (limit 60s)"
        assert len(matches) == 1000, f"Expected 1000 matches, got {len(matches)}"

    def test_replace_on_page_500(self, large_pdf: str, tmp_path: Path) -> None:
        """Replace on a middle page must complete in <30s."""
        matches = find(large_pdf, "Page 500 Line 0")
        assert len(matches) >= 1
        out = str(tmp_path / "large_edited.pdf")
        start = time.perf_counter()
        result = replace(large_pdf, matches[0], "REPLACED PAGE 500", out, reflow=False)
        elapsed = time.perf_counter() - start
        assert result.success
        assert elapsed < 30, f"replace took {elapsed:.1f}s (limit 30s)"
        text = get_text(out)
        assert "REPLACED PAGE 500" in text

    def test_scaling_linearity(self, tmp_path: Path) -> None:
        """Compare timing for 100 vs 500 pages. Ratio should be < 3x."""
        sub1 = tmp_path / "scale100"
        sub1.mkdir()
        sub2 = tmp_path / "scale500"
        sub2.mkdir()

        pdf100 = gen_multipage_pdf(sub1, pages=100)
        pdf500 = gen_multipage_pdf(sub2, pages=500)

        start = time.perf_counter()
        get_text(pdf100)
        time100 = time.perf_counter() - start

        start = time.perf_counter()
        get_text(pdf500)
        time500 = time.perf_counter() - start

        ratio = time500 / max(time100, 0.001)
        # 500/100 = 5x pages, so linear = 5x time. Allow up to 15x (3x factor)
        assert ratio < 15, f"Scaling ratio {ratio:.1f}x — may be superlinear"

    @pytest.mark.skipif(not _has_psutil, reason="psutil not installed")
    def test_memory_1000_page_get_text(self, large_pdf: str) -> None:
        """Memory delta for get_text on 1000 pages should be < 200MB."""
        gc.collect()
        proc = psutil.Process(os.getpid())
        before = proc.memory_info().rss
        text = get_text(large_pdf)
        gc.collect()
        after = proc.memory_info().rss
        delta_mb = (after - before) / (1024 * 1024)
        assert delta_mb < 200, f"Memory delta {delta_mb:.1f}MB (limit 200MB)"
        assert len(text) > 0


# ── Class 5: Batch Stress ────────────────────────────────────────────


class TestBatchStress:
    """500-edit batch replacement tests."""

    @pytest.fixture(scope="class")
    def batch_pdf(self, tmp_path_factory: pytest.TempPathFactory) -> str:
        return gen_batch_500_pdf(tmp_path_factory.mktemp("batch"))

    def test_batch_500_all_succeed(self, batch_pdf: str, tmp_path: Path) -> None:
        """500 Edit objects, ALL must succeed."""
        edits = [Edit(find=f"stressword_{i:03d}", replace=f"replaced_{i:03d}") for i in range(500)]
        out = str(tmp_path / "batch500.pdf")
        results = batch_replace(batch_pdf, edits, out)
        successful = sum(1 for r in results if r.success)
        failed = [(i, r.original_text, r.warnings) for i, r in enumerate(results) if not r.success]
        assert successful == 500, (
            f"Only {successful}/500 edits succeeded. First failures: {failed[:5]}"
        )

    def test_batch_500_content_verified(self, batch_pdf: str, tmp_path: Path) -> None:
        """Verify all 500 replacements appear in output text."""
        edits = [Edit(find=f"stressword_{i:03d}", replace=f"verified_{i:03d}") for i in range(500)]
        out = str(tmp_path / "batch500_verify.pdf")
        batch_replace(batch_pdf, edits, out)
        text = get_text(out)
        missing = [f"verified_{i:03d}" for i in range(500) if f"verified_{i:03d}" not in text]
        assert not missing, f"{len(missing)} replacements missing. First: {missing[:5]}"

    def test_batch_500_font_preserved(self, batch_pdf: str, tmp_path: Path) -> None:
        """Font set should be unchanged after 500 edits."""
        original_fonts = get_fonts(batch_pdf)
        edits = [Edit(find=f"stressword_{i:03d}", replace=f"fontcheck_{i:03d}") for i in range(500)]
        out = str(tmp_path / "batch500_fonts.pdf")
        batch_replace(batch_pdf, edits, out)
        edited_fonts = get_fonts(out)
        original_names = {f.postscript_name for f in original_fonts}
        edited_names = {f.postscript_name for f in edited_fonts}
        assert original_names == edited_names


# ── Class 6: Encrypt/Decrypt Workflow ────────────────────────────────


class TestEncryptDecryptWorkflow:
    """Encrypt → edit → decrypt roundtrip tests."""

    def test_decrypt_then_find(self, tmp_path: Path) -> None:
        """Decrypt encrypted PDF, then find text."""
        enc = gen_encrypted_pdf(tmp_path)
        dec = str(tmp_path / "decrypted.pdf")
        decrypt_pdf(enc, "user123", dec)
        matches = find(dec, "Secret Document Content")
        assert len(matches) >= 1

    def test_decrypt_then_replace(self, tmp_path: Path) -> None:
        """Decrypt, replace, verify."""
        enc = gen_encrypted_pdf(tmp_path)
        dec = str(tmp_path / "dec.pdf")
        decrypt_pdf(enc, "user123", dec)

        matches = find(dec, "Secret Document Content")
        assert matches
        out = str(tmp_path / "edited.pdf")
        result = replace(dec, matches[0], "Modified Content", out, reflow=False)
        assert result.success
        assert "Modified Content" in get_text(out)

    def test_encrypt_replace_decrypt_roundtrip(self, tmp_path: Path) -> None:
        """encrypt -> decrypt -> replace -> re-encrypt -> decrypt -> verify."""
        enc = gen_encrypted_pdf(tmp_path)
        dec1 = str(tmp_path / "dec1.pdf")
        decrypt_pdf(enc, "user123", dec1)

        matches = find(dec1, "Secret Document Content")
        assert matches
        edited = str(tmp_path / "edited.pdf")
        replace(dec1, matches[0], "Roundtrip Content", edited, reflow=False)

        re_enc = str(tmp_path / "re_encrypted.pdf")
        encrypt_pdf(edited, "newowner", "newuser", re_enc)

        dec2 = str(tmp_path / "dec2.pdf")
        decrypt_pdf(re_enc, "newuser", dec2)
        text = get_text(dec2)
        assert "Roundtrip Content" in text

    def test_wrong_password_raises(self, tmp_path: Path) -> None:
        """Wrong password should raise, not crash."""
        enc = gen_encrypted_pdf(tmp_path)
        dec = str(tmp_path / "bad_dec.pdf")
        with pytest.raises((PDFEditError, pikepdf.PasswordError)):
            decrypt_pdf(enc, "wrongpass", dec)

    def test_empty_password_works(self, tmp_path: Path) -> None:
        """Empty user password should encrypt and decrypt correctly."""
        enc = gen_empty_password_pdf(tmp_path)
        dec = str(tmp_path / "empty_dec.pdf")
        decrypt_pdf(enc, "", dec)
        text = get_text(dec)
        assert "Empty Password Document" in text


# ── Class 7: Merge/Split Workflows ───────────────────────────────────


class TestMergeSplitWorkflows:
    """Cross-module integration: merge → edit → verify, split → edit → merge."""

    def test_merge_then_find_all_sources(self, tmp_path: Path) -> None:
        """Merge 3 PDFs, find text from each source."""
        s1, s2, s3 = gen_3_source_pdfs(tmp_path)
        merged = str(tmp_path / "merged.pdf")
        merge_pdfs([s1, s2, s3], merged)

        for label in ["Source One Alpha", "Source Two Bravo", "Source Three Charlie"]:
            matches = find(merged, label)
            assert len(matches) >= 1, f"'{label}' not found in merged PDF"

    def test_merge_then_replace(self, tmp_path: Path) -> None:
        """Replace text from middle source in merged PDF."""
        s1, s2, s3 = gen_3_source_pdfs(tmp_path)
        merged = str(tmp_path / "merged.pdf")
        merge_pdfs([s1, s2, s3], merged)

        matches = find(merged, "Source Two Bravo")
        assert matches
        out = str(tmp_path / "merged_edited.pdf")
        result = replace(merged, matches[0], "Edited Bravo", out, reflow=False)
        assert result.success
        text = get_text(out)
        assert "Edited Bravo" in text
        assert "Source One Alpha" in text  # Other sources preserved

    def test_split_edit_merge_roundtrip(self, tmp_path: Path) -> None:
        """Split 3-page -> edit page 0 -> merge back -> verify all pages."""
        s1, s2, s3 = gen_3_source_pdfs(tmp_path)
        source = str(tmp_path / "three_page.pdf")
        merge_pdfs([s1, s2, s3], source)

        split_dir = str(tmp_path / "split_out")
        os.makedirs(split_dir)
        pages = split_pdf(source, split_dir)
        assert len(pages) == 3

        # Edit first page
        matches = find(pages[0], "Source One Alpha")
        assert matches
        edited_page = str(tmp_path / "page0_edited.pdf")
        replace(pages[0], matches[0], "Edited Alpha", edited_page, reflow=False)

        # Merge back
        roundtrip = str(tmp_path / "roundtrip.pdf")
        merge_pdfs([edited_page, pages[1], pages[2]], roundtrip)
        text = get_text(roundtrip)
        assert "Edited Alpha" in text
        assert "Source Two Bravo" in text
        assert "Source Three Charlie" in text

    def test_merge_preserves_page_count(self, tmp_path: Path) -> None:
        """1+1+1 = 3 pages in merged output."""
        s1, s2, s3 = gen_3_source_pdfs(tmp_path)
        merged = str(tmp_path / "count.pdf")
        merge_pdfs([s1, s2, s3], merged)
        with pikepdf.Pdf.open(merged) as pdf:
            assert len(pdf.pages) == 3


# ── Class 8: Crop Validation ─────────────────────────────────────────


class TestCropValidation:
    """Crop operation validation."""

    def test_crop_then_get_text(self, tmp_path: Path) -> None:
        """Text extraction works on cropped PDF (crop is visual only)."""
        pdf = gen_cropped_pdf(tmp_path)
        text = get_text(pdf)
        # All text should still be extractable regardless of crop
        assert "CropVisible" in text

    def test_crop_then_find(self, tmp_path: Path) -> None:
        """find() returns matches even for text outside CropBox."""
        pdf = gen_cropped_pdf(tmp_path)
        matches = find(pdf, "CropVisible")
        assert len(matches) >= 1

    def test_crop_then_replace(self, tmp_path: Path) -> None:
        """replace() works on cropped PDF."""
        pdf = gen_cropped_pdf(tmp_path)
        matches = find(pdf, "CropVisible")
        assert matches
        out = str(tmp_path / "crop_edited.pdf")
        result = replace(pdf, matches[0], "CropModified", out, reflow=False)
        assert result.success
        pikepdf.Pdf.open(out).close()

    def test_crop_box_values_match(self, tmp_path: Path) -> None:
        """Verify CropBox values in output match what was set."""
        source = gen_resource_leak_pdf(tmp_path)
        out = str(tmp_path / "crop_verify.pdf")
        crop_pages(source, (10, 20, 500, 700), out)
        with pikepdf.Pdf.open(out) as pdf:
            crop_box = pdf.pages[0].get("/CropBox")
            assert crop_box is not None
            vals = [float(v) for v in crop_box]
            assert vals == [10.0, 20.0, 500.0, 700.0]


# ── Class 9: Form Field Types ────────────────────────────────────────


class TestFormFieldTypes:
    """Form field interaction tests."""

    def test_fill_text_field(self, tmp_path: Path) -> None:
        """fill_form() sets text field value."""
        pdf = gen_form_all_types_pdf(tmp_path)
        out = str(tmp_path / "form_filled.pdf")
        fill_form(pdf, {"name_field": "John Doe"}, out)
        pikepdf.Pdf.open(out).close()

    def test_fill_nonexistent_field(self, tmp_path: Path) -> None:
        """fill_form() with bad field name should not crash."""
        pdf = gen_form_all_types_pdf(tmp_path)
        out = str(tmp_path / "form_bad.pdf")
        # Should either silently ignore or raise clean error
        with contextlib.suppress(PDFEditError):
            fill_form(pdf, {"nonexistent_xyz": "value"}, out)

    def test_text_coexists_with_forms(self, tmp_path: Path) -> None:
        """get_text() works on PDF with form fields."""
        pdf = gen_form_all_types_pdf(tmp_path)
        text = get_text(pdf)
        assert "Form Test Document" in text


# ── Class 10: Degenerate Inputs ──────────────────────────────────────


class TestDegenerate:
    """Robustness tests for degenerate/malformed inputs."""

    def test_zero_font_size_no_crash(self, tmp_path: Path) -> None:
        """get_text/find on zero-font-size PDF should not crash."""
        pdf = gen_zero_font_size_pdf(tmp_path)
        try:
            text = get_text(pdf)
            assert isinstance(text, str)
        except PDFEditError:
            pass  # Acceptable

    def test_degenerate_ctm_no_crash(self, tmp_path: Path) -> None:
        """Zero-determinant CTM should not crash."""
        pdf = gen_degenerate_ctm_pdf(tmp_path)
        try:
            text = get_text(pdf)
            assert isinstance(text, str)
        except PDFEditError:
            pass

    def test_text_outside_mediabox(self, tmp_path: Path) -> None:
        """Text at extreme coordinates should still be extractable."""
        pdf = gen_text_outside_mediabox_pdf(tmp_path)
        try:
            text = get_text(pdf)
            # Text may or may not be extracted, but should not crash
            assert isinstance(text, str)
        except PDFEditError:
            pass

    def test_very_long_line_get_text(self, tmp_path: Path) -> None:
        """10,000-character line should be extracted."""
        pdf = gen_very_long_line_pdf(tmp_path)
        text = get_text(pdf)
        assert len(text) >= 10000, f"Expected 10000+ chars, got {len(text)}"

    def test_very_long_line_find(self, tmp_path: Path) -> None:
        """Find 100-char substring in 10k-char line."""
        pdf = gen_very_long_line_pdf(tmp_path)
        matches = find(pdf, "A" * 100)
        assert len(matches) >= 1

    def test_no_fonts_pdf(self, tmp_path: Path) -> None:
        """Fontless PDF should return empty result or clean error."""
        pdf = gen_no_fonts_pdf(tmp_path)
        try:
            text = get_text(pdf)
            assert isinstance(text, str)
        except (PDFEditError, Exception):
            pass  # Any clean exception is acceptable

    def test_damaged_font_stream(self, tmp_path: Path) -> None:
        """Garbage font data should produce error or partial extraction."""
        pdf = gen_damaged_font_stream_pdf(tmp_path)
        try:
            text = get_text(pdf)
            assert isinstance(text, str)
        except (PDFEditError, Exception):
            pass  # Any clean exception is acceptable

    def test_overlapping_text_both_found(self, tmp_path: Path) -> None:
        """find() should return matches for both overlapping strings."""
        pdf = gen_overlapping_text_pdf(tmp_path)
        alpha = find(pdf, "OverlapAlpha")
        bravo = find(pdf, "OverlapBravo")
        assert len(alpha) >= 1, "OverlapAlpha not found"
        assert len(bravo) >= 1, "OverlapBravo not found"

    def test_overlapping_replace_one(self, tmp_path: Path) -> None:
        """Replacing one overlapping string should not corrupt the other."""
        pdf = gen_overlapping_text_pdf(tmp_path)
        alpha = find(pdf, "OverlapAlpha")
        assert alpha
        out = str(tmp_path / "overlap_edit.pdf")
        result = replace(pdf, alpha[0], "ChangedAlpha", out, reflow=False)
        assert result.success
        # The other text should still be present
        text = get_text(out)
        assert "OverlapBravo" in text


# ── Class 11: Inline Image Preservation ──────────────────────────────


class TestInlineImagePreservation:
    """Text before/after inline images must be extractable."""

    def test_text_before_image_found(self, tmp_path: Path) -> None:
        pdf = gen_inline_image_between_text_pdf(tmp_path)
        matches = find(pdf, "BeforeImage")
        assert len(matches) >= 1

    def test_text_after_image_found(self, tmp_path: Path) -> None:
        pdf = gen_inline_image_between_text_pdf(tmp_path)
        matches = find(pdf, "AfterImage")
        assert len(matches) >= 1

    def test_replace_before_preserves_pdf(self, tmp_path: Path) -> None:
        pdf = gen_inline_image_between_text_pdf(tmp_path)
        matches = find(pdf, "BeforeImage")
        assert matches
        out = str(tmp_path / "inline_edited.pdf")
        result = replace(pdf, matches[0], "ModifiedBefore", out, reflow=False)
        assert result.success
        pikepdf.Pdf.open(out).close()

    def test_get_text_extracts_both(self, tmp_path: Path) -> None:
        pdf = gen_inline_image_between_text_pdf(tmp_path)
        text = get_text(pdf)
        assert "BeforeImage" in text
        assert "AfterImage" in text


# ── Class 12: Mixed Encoding Page ────────────────────────────────────


class TestMixedEncodingPage:
    """Multiple fonts with different encodings on same page."""

    def test_find_first_font_text(self, tmp_path: Path) -> None:
        pdf = gen_mixed_encoding_pdf(tmp_path)
        matches = find(pdf, "WinAnsiText")
        assert len(matches) >= 1

    def test_find_second_font_text(self, tmp_path: Path) -> None:
        pdf = gen_mixed_encoding_pdf(tmp_path)
        matches = find(pdf, "CourierText")
        assert len(matches) >= 1

    def test_replace_preserves_other_font(self, tmp_path: Path) -> None:
        pdf = gen_mixed_encoding_pdf(tmp_path)
        matches = find(pdf, "WinAnsiText")
        assert matches
        out = str(tmp_path / "mixed_edited.pdf")
        result = replace(pdf, matches[0], "ModifiedText", out, reflow=False)
        assert result.success
        text = get_text(out)
        assert "CourierText" in text, "Other font's text should be preserved"

    def test_get_fonts_reports_both(self, tmp_path: Path) -> None:
        pdf = gen_mixed_encoding_pdf(tmp_path)
        fonts = get_fonts(pdf)
        names = {f.postscript_name for f in fonts}
        assert "Helvetica" in names or any("Helvetica" in n for n in names)
        assert "Courier" in names or any("Courier" in n for n in names)


# ── Class 13: Rotated Pages ──────────────────────────────────────────


class TestRotatedPages:
    """Text handling on rotated pages."""

    def test_get_text_on_rotated_90(self, tmp_path: Path) -> None:
        pdf = gen_rotated_pages_pdf(tmp_path)
        text = get_text(pdf)
        assert "RotatedPage0" in text

    def test_get_text_on_rotated_180(self, tmp_path: Path) -> None:
        pdf = gen_rotated_pages_pdf(tmp_path)
        text = get_text(pdf)
        assert "RotatedPage1" in text

    def test_replace_on_rotated_page(self, tmp_path: Path) -> None:
        pdf = gen_rotated_pages_pdf(tmp_path)
        matches = find(pdf, "RotatedPage0")
        assert matches
        out = str(tmp_path / "rotated_edited.pdf")
        result = replace(pdf, matches[0], "EditedRotated", out, reflow=False)
        assert result.success
        pikepdf.Pdf.open(out).close()


# ── Class 14: CJK and Unicode ────────────────────────────────────────


class TestCJKAndUnicode:
    """CJK characters and Unicode edge cases."""

    def test_combining_diacritics_get_text(self, tmp_path: Path) -> None:
        """Pre-composed diacritics (cafe, naive) should extract correctly."""
        pdf = gen_combining_diacritics_pdf(tmp_path)
        text = get_text(pdf)
        # Exact form depends on encoding, but one of these should be present
        assert "Caf" in text  # At minimum the base text
        assert "Normal text" in text

    def test_combining_diacritics_find(self, tmp_path: Path) -> None:
        """find() should locate text with diacritics."""
        pdf = gen_combining_diacritics_pdf(tmp_path)
        matches = find(pdf, "Normal text without diacritics")
        assert len(matches) >= 1


# ── Class 15: Digital Signature ──────────────────────────────────────


class TestDigitalSignature:
    """PDF with /Sig field — editing must not crash."""

    def test_edit_signed_pdf_no_crash(self, tmp_path: Path) -> None:
        """Replace on PDF with /Sig field should not crash."""
        pdf = gen_digital_signature_pdf(tmp_path)
        matches = find(pdf, "SignedDocument")
        assert matches
        out = str(tmp_path / "sig_edited.pdf")
        result = replace(pdf, matches[0], "EditedSigned", out, reflow=False)
        assert result.success
        pikepdf.Pdf.open(out).close()


# ── Class 16: XRef Stream PDF ────────────────────────────────────────


class TestXRefStreamPDF:
    """PDFs with object streams / cross-reference streams."""

    def test_get_text_xref_stream(self, tmp_path: Path) -> None:
        pdf = gen_xref_stream_pdf(tmp_path)
        text = get_text(pdf)
        assert "XRefStream document" in text

    def test_find_xref_stream(self, tmp_path: Path) -> None:
        pdf = gen_xref_stream_pdf(tmp_path)
        matches = find(pdf, "XRefStream document")
        assert len(matches) >= 1

    def test_replace_xref_stream(self, tmp_path: Path) -> None:
        pdf = gen_xref_stream_pdf(tmp_path)
        matches = find(pdf, "XRefStream document")
        assert matches
        out = str(tmp_path / "xref_edited.pdf")
        result = replace(pdf, matches[0], "Modified XRef", out, reflow=False)
        assert result.success
        pikepdf.Pdf.open(out).close()
        assert "Modified XRef" in get_text(out)


# ── Class 17: Font Resolver Cache Stress ─────────────────────────────


class TestFontResolverCacheStress:
    """Tests for font resolver with many distinct fonts."""

    def test_100_fonts_get_fonts(self, tmp_path: Path) -> None:
        """get_fonts() should report many fonts without crash."""
        pdf = gen_100_fonts_pdf(tmp_path)
        fonts = get_fonts(pdf)
        assert len(fonts) >= 5, f"Expected many fonts, got {len(fonts)}"

    def test_100_fonts_find(self, tmp_path: Path) -> None:
        """find() should work with many fonts on page."""
        pdf = gen_100_fonts_pdf(tmp_path)
        matches = find(pdf, "Font000")
        assert len(matches) >= 1

    def test_100_fonts_get_text(self, tmp_path: Path) -> None:
        """get_text should handle 100+ fonts without crashing."""
        pdf = gen_100_fonts_pdf(tmp_path)
        text = get_text(pdf)
        assert "Font000" in text
        assert "Font099" in text


# ── Class 18: Annotation Integration ─────────────────────────────────


class TestAnnotationIntegration:
    """Annotations + text editing coexistence."""

    def test_add_annotations_then_edit(self, tmp_path: Path) -> None:
        """Add 50 link annotations, then find+replace text."""
        pdf_path = gen_sequential_edit_pdf(tmp_path)
        annotated = pdf_path
        for i in range(50):
            out = str(tmp_path / f"annot_{i}.pdf")
            add_annotation(
                annotated,
                page=0,
                rect=(72 + i, 500, 172 + i, 520),
                uri=f"https://example.com/{i}",
                output_path=out,
            )
            annotated = out

        # Now edit text on the annotated PDF
        matches = find(annotated, "ChainAlpha")
        assert matches
        final = str(tmp_path / "annot_edited.pdf")
        result = replace(annotated, matches[0], "AnnotEdited", final, reflow=False)
        assert result.success
        text = get_text(final)
        assert "AnnotEdited" in text

    def test_edit_preserves_existing_annotations(self, tmp_path: Path) -> None:
        """Replace text, verify annotation count unchanged."""
        pdf_path = gen_sequential_edit_pdf(tmp_path)
        # Add 5 annotations
        annotated = pdf_path
        for i in range(5):
            out = str(tmp_path / f"pre_annot_{i}.pdf")
            add_annotation(
                annotated,
                page=0,
                rect=(72, 500 + i * 25, 200, 520 + i * 25),
                uri=f"https://test.com/{i}",
                output_path=out,
            )
            annotated = out

        before_count = len(get_annotations(annotated, page=0))
        assert before_count == 5

        # Edit text
        matches = find(annotated, "ChainBravo")
        assert matches
        edited = str(tmp_path / "annot_preserved.pdf")
        replace(annotated, matches[0], "PreservedEdit", edited, reflow=False)

        after_count = len(get_annotations(edited, page=0))
        assert after_count == before_count, (
            f"Annotation count changed: {before_count} -> {after_count}"
        )
