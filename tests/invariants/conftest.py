"""Shared fixtures for invariant probes."""

from __future__ import annotations

from pathlib import Path

import pytest

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


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


@pytest.fixture
def chrome_webpage(corpus: Path) -> Path:
    """Chrome-printed PDF (per-glyph Tm+Tj title pattern)."""
    return corpus / "chrome_webpage.pdf"


@pytest.fixture
def resume_pdf(corpus: Path) -> Path:
    """Aryan's resume — multi-font Identity-H test artifact."""
    return corpus / "Aryan_BV_Resume_2026.pdf"


@pytest.fixture
def gdocs_document(corpus: Path) -> Path:
    """Google Docs export — multi-font Identity-H."""
    return corpus / "gdocs_document.pdf"
