"""Shared fixtures for invariant probes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CORPUS_DIR = Path(__file__).parent.parent / "corpus"

# tests/ is on sys.path so corpus_builders (a package under it) imports cleanly.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders.rotated_text import (  # noqa: E402
    build_axis_aligned_two_run_pdf,
    build_rotated_text_pdf,
)


@pytest.fixture
def corpus() -> Path:
    """Absolute path to tests/corpus/."""
    return CORPUS_DIR


@pytest.fixture
def reportlab_simple(corpus: Path) -> Path:
    """A simple reportlab-generated PDF with WinAnsi-encoded text."""
    return corpus / "reportlab_simple.pdf"


@pytest.fixture
def cidfont_synthetic(corpus: Path) -> Path:
    """A synthetic CIDFont/Identity-H PDF (deterministically generated)."""
    return corpus / "cidfont_synthetic.pdf"


# Real-world PDF fixtures (Chrome, Word/Google-Docs export, personal
# resume) cannot be auto-generated like the synthetic corpus — they're
# captured artifacts. On CI these files are absent (the .gitignore
# excludes tests/corpus/*.pdf), so each fixture skips its dependent
# tests cleanly when the file isn't present. Locally, the developer
# has all three and the tests run normally. ARY-280 tracks adding a
# reproducible Chrome-fixture generator for v0.1.3.
def _skip_if_missing(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"corpus fixture missing: {path.name}")
    return path


@pytest.fixture
def chrome_webpage(corpus: Path) -> Path:
    """Chrome-printed PDF (per-glyph Tm+Tj title pattern)."""
    return _skip_if_missing(corpus / "chrome_webpage.pdf")


@pytest.fixture
def resume_pdf(corpus: Path) -> Path:
    """Aryan's resume — multi-font Identity-H test artifact."""
    return _skip_if_missing(corpus / "Aryan_BV_Resume_2026.pdf")


@pytest.fixture
def gdocs_document(corpus: Path) -> Path:
    """Google Docs export — multi-font Identity-H."""
    return _skip_if_missing(corpus / "gdocs_document.pdf")


# B.12 rotated-text fixtures (shared by test_b_7_* and test_b_8_*). Both
# builders return None when no host TrueType font is installed; the probes
# gate on that via their own _no_font skipif, so these fixtures assert the
# build succeeded (matching the original per-file fixture bodies).
@pytest.fixture
def rotated_pdf(tmp_path: Path) -> Path:
    """A 90deg-rotated two-run Identity-H PDF (Tm = [0 -1 1 0 x y])."""
    out = tmp_path / "rotated.pdf"
    assert build_rotated_text_pdf(out) is not None
    return out


@pytest.fixture
def axis_pdf(tmp_path: Path) -> Path:
    """An axis-aligned two-run Identity-H PDF (Tm = [1 0 0 1 x y])."""
    out = tmp_path / "axis_aligned.pdf"
    assert build_axis_aligned_two_run_pdf(out) is not None
    return out
