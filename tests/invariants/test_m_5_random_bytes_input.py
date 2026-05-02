"""INV-M-5: a malformed (random-bytes) input raises a clean PDFEditError subclass."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine import get_text
from pdf_edit_engine.errors import PDFEditError

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_m_5_random_bytes_clean_error(tmp_path: Path) -> None:
    """get_text() on a malformed file (non-PDF random bytes) raises a PDFEditError subclass
    rather than a raw pikepdf.PdfError or any other library exception.
    """
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"NOT A PDF\xff\xfe\xfd" * 100)
    with pytest.raises(PDFEditError):
        get_text(str(junk))
