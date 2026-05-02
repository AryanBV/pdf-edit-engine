"""INV-L-1 (P0): every exception raised BY the engine is a PDFEditError subclass.

Probes the public API surface against an adversarial set of inputs and
asserts that every exception originating from engine code is a
``PDFEditError`` subclass — never a leaked pikepdf/fonttools/pdfminer
exception.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pdf_edit_engine as engine
from pdf_edit_engine.errors import PDFEditError

if TYPE_CHECKING:
    from pathlib import Path


def _scenarios(tmp_path: Path, reportlab_simple: Path) -> list[tuple[str, callable]]:  # type: ignore[type-arg]
    """Return (name, callable) pairs that should raise a PDFEditError
    subclass — but historically may leak underlying-library errors."""
    enc_out = tmp_path / "encrypted.pdf"
    engine.encrypt_pdf(str(reportlab_simple), "secret", "secret", str(enc_out))
    empty_pdf = tmp_path / "empty.pdf"
    empty_pdf.write_bytes(b"")
    junk_pdf = tmp_path / "junk.pdf"
    junk_pdf.write_bytes(b"NOT A PDF\xff\xfe\xfd" * 200)
    missing_pdf = tmp_path / "missing.pdf"

    return [
        ("get_text on encrypted", lambda: engine.get_text(str(enc_out))),
        ("get_text on zero-byte", lambda: engine.get_text(str(empty_pdf))),
        ("get_text on random bytes", lambda: engine.get_text(str(junk_pdf))),
        ("get_text on missing file", lambda: engine.get_text(str(missing_pdf))),
        (
            "find on encrypted",
            lambda: engine.find(str(enc_out), "anything"),
        ),
        (
            "decrypt with wrong password",
            lambda: engine.decrypt_pdf(
                str(enc_out),
                "wrong",
                str(tmp_path / "decrypted.pdf"),
            ),
        ),
        ("merge empty list", lambda: engine.merge_pdfs([], str(tmp_path / "m.pdf"))),
        (
            "fill_form on no-acroform",
            lambda: engine.fill_form(str(reportlab_simple), {}, str(tmp_path / "ff.pdf")),
        ),
    ]


def test_inv_l_1_engine_errors_are_pdfedit_errors(
    reportlab_simple: Path,
    tmp_path: Path,
) -> None:
    """Every public-API entry point that raises must raise a PDFEditError
    subclass — never a raw pikepdf/fonttools/pdfminer exception."""
    leaked: list[tuple[str, str]] = []
    for name, op in _scenarios(tmp_path, reportlab_simple):
        try:
            op()
        except PDFEditError:
            continue  # acceptable
        except (FileNotFoundError, OSError):
            # Filesystem errors are well-known stdlib, allowed by contract.
            continue
        except BaseException as e:  # noqa: BLE001
            leaked.append((name, f"{type(e).__module__}.{type(e).__name__}: {e}"))

    if leaked:
        pretty = "\n".join(f"  - {n}: {err}" for n, err in leaked)
        msg = "Engine leaked underlying-library exceptions to public API:\n" + pretty
        raise AssertionError(msg)
