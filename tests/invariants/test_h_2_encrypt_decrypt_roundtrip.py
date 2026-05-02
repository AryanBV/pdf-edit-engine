"""INV-H-2: encrypt then decrypt round-trips the text content."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import decrypt_pdf, encrypt_pdf, get_text

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_h_2_encrypt_decrypt_roundtrip(reportlab_simple: Path, tmp_path: Path) -> None:
    """`encrypt_pdf(p, "secret", out)` then `decrypt_pdf(out, "secret", out2)`:
    `get_text(p) == get_text(out2)`."""
    # Real signature: encrypt_pdf(pdf_path, owner_pass, user_pass, output_path)
    src = str(reportlab_simple)
    encrypted = str(tmp_path / "h2_encrypted.pdf")
    decrypted = str(tmp_path / "h2_decrypted.pdf")
    password = "secret"

    encrypt_pdf(src, password, password, encrypted)
    decrypt_pdf(encrypted, password, decrypted)

    before = get_text(src)
    after = get_text(decrypted)
    assert before == after, (
        f"text changed across encrypt/decrypt:\nbefore={before!r}\nafter={after!r}"
    )
