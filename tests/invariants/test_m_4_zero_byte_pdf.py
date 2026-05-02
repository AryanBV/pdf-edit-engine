"""INV-M-4: a zero-byte PDF input raises a clean PDFEditError subclass."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine import get_text
from pdf_edit_engine.errors import PDFEditError

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_m_4_zero_byte_pdf_clean_error(tmp_path: Path) -> None:
    """get_text() on a zero-byte file raises a PDFEditError subclass — not a raw pikepdf
    exception, OSError, or anything else. The user-facing API must translate.
    """
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(PDFEditError):
        get_text(str(empty))
