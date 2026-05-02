"""INV-D-3: get_text(pdf) is deterministic — two calls produce byte-identical strings."""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_edit_engine import get_text

CORPUS_DIR = Path(__file__).parent.parent / "corpus"

CANDIDATES = [
    "reportlab_simple.pdf",
    "cidfont_synthetic.pdf",
    "chrome_webpage.pdf",
    "Aryan_BV_Resume_2026.pdf",
    "gdocs_document.pdf",
]


@pytest.mark.parametrize("name", CANDIDATES)
def test_inv_d_3_get_text_deterministic(name: str) -> None:
    """get_text(pdf) is deterministic — two calls produce byte-identical strings."""
    path = CORPUS_DIR / name
    if not path.exists():
        pytest.skip(f"corpus file {name} not present")
    a = get_text(str(path))
    b = get_text(str(path))
    assert a == b, f"{name}: get_text non-deterministic; lengths {len(a)} vs {len(b)}"
