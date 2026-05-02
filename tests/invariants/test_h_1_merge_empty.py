"""INV-H-1: merge_pdfs([]) must not silently produce a no-op."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine import merge_pdfs
from pdf_edit_engine.errors import PDFEditError

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_h_1_merge_empty(tmp_path: Path) -> None:
    """`merge_pdfs([], out)` either raises or produces a clean empty PDF.
    Acceptance: must not silently produce nothing/no output file. If raises,
    assert the exception is a PDFEditError subclass."""
    out = str(tmp_path / "h1_empty.pdf")
    try:
        merge_pdfs([], out)
    except PDFEditError:
        # Acceptable: explicit error.
        assert not os.path.exists(out) or os.path.getsize(out) == 0, (
            "raised but also wrote partial output"
        )
        return
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"merge_pdfs raised non-PDFEditError exception: {type(exc).__name__}: {exc}")

    # No exception → must produce a clean valid output file.
    assert os.path.exists(out), "merge_pdfs([]) returned silently with no output (no-op)"
    assert os.path.getsize(out) > 0, "merge_pdfs([]) wrote an empty file"
