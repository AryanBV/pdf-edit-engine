"""INV-M-1: encrypted PDF without password raises a PDFEditError subclass."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pdf_edit_engine import encrypt_pdf, get_text
from pdf_edit_engine.errors import PDFEditError

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def test_inv_m_1_encrypted_no_password_raises_pdfediterror(tmp_path: Path) -> None:
    """Calling get_text on an encrypted PDF without a password must raise a PDFEditError
    subclass — never a raw pikepdf.PasswordError or other library exception.
    """
    src = CORPUS_DIR / "reportlab_simple.pdf"
    if not src.exists():
        pytest.skip(f"missing fixture {src}")
    plain = tmp_path / "plain.pdf"
    shutil.copy(src, plain)
    encrypted = tmp_path / "encrypted.pdf"
    encrypt_pdf(str(plain), owner_pass="own", user_pass="usr", output_path=str(encrypted))

    with pytest.raises(PDFEditError):
        get_text(str(encrypted))
