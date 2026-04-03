"""Shared test fixtures for pdf-edit-engine."""

from __future__ import annotations

from pathlib import Path

import pytest

CORPUS_DIR = Path(__file__).parent / "corpus"


@pytest.fixture
def corpus_dir() -> Path:
    """Return the path to the test corpus directory."""
    return CORPUS_DIR
