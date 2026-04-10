"""Performance benchmarks — detect O(n^2) regressions and measure baselines."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

reportlab = pytest.importorskip("reportlab")

from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from pdf_edit_engine import Edit, batch_replace, find, get_text, replace  # noqa: E402

if TYPE_CHECKING:
    from pathlib import Path


# ── PDF generators ────────────────────────────────────────────────────


def _make_large_pdf(tmp_path: Path, num_pages: int = 100) -> str:
    """Create a PDF with num_pages pages, each with 3 paragraphs."""
    out = str(tmp_path / "large.pdf")
    c = canvas.Canvas(out, pagesize=letter)
    for page_num in range(num_pages):
        y = 750
        for para in range(3):
            c.setFont("Helvetica", 12)
            c.drawString(
                72,
                y,
                f"Page {page_num + 1}, paragraph {para + 1}: "
                f"This is a test paragraph with enough text to be meaningful. "
                f"The word paragraph appears at least once per block.",
            )
            y -= 40
        c.showPage()
    c.save()
    return out


def _make_batch_pdf(tmp_path: Path) -> str:
    """Create a PDF with 'word0' through 'word49', one per line."""
    out = str(tmp_path / "batch.pdf")
    c = canvas.Canvas(out, pagesize=letter)
    y = 750
    for i in range(50):
        c.setFont("Helvetica", 12)
        c.drawString(72, y, f"word{i}")
        y -= 14
        if y < 72:
            c.showPage()
            y = 750
    c.save()
    return out


# ── Benchmark tests ──────────────────────────────────────────────────


@pytest.mark.benchmark
class TestPerformance:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path: Path) -> None:
        self.large_pdf = _make_large_pdf(tmp_path)
        self.batch_pdf = _make_batch_pdf(tmp_path)
        self.tmp_path = tmp_path

    def test_get_text_100_pages(self) -> None:
        start = time.perf_counter()
        text = get_text(self.large_pdf)
        elapsed = time.perf_counter() - start
        assert elapsed < 30.0, f"get_text on 100 pages took {elapsed:.1f}s (limit: 30s)"
        assert len(text) > 1000
        print(f"\n  get_text 100 pages: {elapsed:.1f}s")

    def test_find_100_pages(self) -> None:
        start = time.perf_counter()
        matches = find(self.large_pdf, "paragraph")
        elapsed = time.perf_counter() - start
        assert elapsed < 30.0, f"find on 100 pages took {elapsed:.1f}s (limit: 30s)"
        assert len(matches) > 50
        print(f"\n  find 100 pages: {elapsed:.1f}s, {len(matches)} matches")

    def test_replace_single_page(self) -> None:
        matches = find(self.large_pdf, "paragraph", page=0)
        if not matches:
            pytest.skip("'paragraph' not found on page 0")
        output = str(self.tmp_path / "replace_perf.pdf")
        start = time.perf_counter()
        replace(self.large_pdf, matches[0], "REPLACED", output, reflow=False)
        elapsed = time.perf_counter() - start
        assert elapsed < 10.0, f"replace on 1 page took {elapsed:.1f}s (limit: 10s)"
        print(f"\n  replace single page: {elapsed:.1f}s")

    def test_batch_replace_50_edits(self) -> None:
        edits = [Edit(find=f"word{i}", replace=f"CHANGED{i}") for i in range(50)]
        output = str(self.tmp_path / "batch_perf.pdf")
        start = time.perf_counter()
        results = batch_replace(self.batch_pdf, edits, output)
        elapsed = time.perf_counter() - start
        successful = sum(1 for r in results if r.success)
        assert elapsed < 60.0, f"50 batch replacements took {elapsed:.1f}s (limit: 60s)"
        print(f"\n  batch 50 edits: {elapsed:.1f}s, {successful}/50 successful")


@pytest.mark.benchmark
class TestMemory:
    def test_memory_bounded(self, tmp_path: Path) -> None:
        """Editing a large PDF should not consume more than 500MB."""
        psutil = pytest.importorskip("psutil")
        import os

        large_pdf = _make_large_pdf(tmp_path)
        process = psutil.Process(os.getpid())
        mem_before = process.memory_info().rss
        get_text(large_pdf)
        find(large_pdf, "paragraph")
        mem_after = process.memory_info().rss
        mem_delta_mb = (mem_after - mem_before) / 1024 / 1024
        assert mem_delta_mb < 500, f"Memory grew by {mem_delta_mb:.0f}MB (limit: 500MB)"
        print(f"\n  memory delta: {mem_delta_mb:.0f}MB")
