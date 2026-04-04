"""Shared test fixtures for pdf-edit-engine."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CORPUS_DIR = Path(__file__).parent / "corpus"
DEMO_OUTPUT = Path(__file__).parent.parent / "demo_output"

_GENERATED_PDFS = [
    "reportlab_simple.pdf",
    "reportlab_table.pdf",
    "reportlab_multipage.pdf",
    "pikepdf_synthetic.pdf",
    "reportlab_forms.pdf",
]

_COMPLEX_PDFS = [
    "complex_multifont.pdf",
    "complex_transformed.pdf",
    "complex_contract.pdf",
]


@pytest.fixture(scope="session", autouse=True)
def ensure_corpus() -> None:
    """Auto-generate corpus PDFs if any are missing."""
    missing = [name for name in _GENERATED_PDFS if not (CORPUS_DIR / name).exists()]
    if missing:
        subprocess.check_call(
            [sys.executable, str(Path(__file__).parent / "generate_corpus.py")],
        )


@pytest.fixture(scope="session", autouse=True)
def ensure_complex_corpus() -> None:
    """Auto-generate complex corpus PDFs if any are missing."""
    import contextlib

    missing = [name for name in _COMPLEX_PDFS if not (CORPUS_DIR / name).exists()]
    if missing:
        with contextlib.suppress(subprocess.CalledProcessError, FileNotFoundError):
            subprocess.check_call(
                [sys.executable, str(Path(__file__).parent / "generate_complex_corpus.py")],
            )


@pytest.fixture
def corpus_dir() -> Path:
    """Return the path to the test corpus directory."""
    return CORPUS_DIR


@pytest.fixture
def demo_output_dir() -> Path:
    """Return the persistent demo_output/ directory (not cleaned up)."""
    DEMO_OUTPUT.mkdir(parents=True, exist_ok=True)
    return DEMO_OUTPUT
