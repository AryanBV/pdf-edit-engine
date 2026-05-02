"""INV-H-4: fill_form on a PDF with no AcroForm raises PDFEditError."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine import fill_form
from pdf_edit_engine.errors import PDFEditError

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_h_4_fill_form_no_acroform(reportlab_simple: Path, tmp_path: Path) -> None:
    """`fill_form(p_no_acroform, {}, out)` raises a PDFEditError subclass (or specific
    error). Use reportlab_simple (no AcroForm)."""
    src = str(reportlab_simple)
    out = str(tmp_path / "h4_form.pdf")

    with pytest.raises(PDFEditError):
        fill_form(src, {}, out)
