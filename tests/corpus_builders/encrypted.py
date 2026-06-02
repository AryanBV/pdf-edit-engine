"""Deterministic corpus builders for the encryption-round-trip probes.

Net-new test tooling (no ``src/`` changes). Supports the A2.3
encryption detect-and-preserve invariant probes
(``test_w_5_encryption_round_trip``).

An *encrypted* PDF stores its content under a password-derived key; pikepdf
detects this on an opened document via ``pdf.is_encrypted`` and re-emits it
via ``pdf.save(..., encryption=pikepdf.Encryption(...))``. The A2.3 invariant
is that the engine, which today *always* saves UNencrypted (every edit verb
calls ``pdf.save(output)`` with no ``encryption=`` kwarg), must DETECT an
encrypted input and PRESERVE that property on save — re-encrypting with the
caller-supplied password rather than silently emitting a plaintext output.

All three builders emit the SAME logical document — a single WinAnsi
standard-14 Helvetica paragraph the engine can locate and replace — and
differ ONLY in the encryption applied at save time. Helvetica is a
standard-14 font requiring no host-font discovery, so these builders always
succeed (never ``None``) and the bytes are deterministic on every host.

The builders share :func:`_build_base_pdf` so the *content* is byte-identical;
only the final ``save`` encryption flag differs. That keeps the encrypted /
unencrypted pair a clean A/B control: any output-state difference a probe
observes is attributable to the save-time encryption alone, not to a content
divergence.

* :func:`build_encrypted_pdf` — owner==user==``password`` (the common single-
  password case), R=6 (AES-256). The A2.3 subject.
* :func:`build_encrypted_pdf_distinct` — distinct owner!=user passwords, for
  the documented owner-collapse boundary probe.
* :func:`build_unencrypted_pdf` — the unencrypted control.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write

if TYPE_CHECKING:
    from pathlib import Path

PAGE_WIDTH: float = 612.0
PAGE_HEIGHT: float = 792.0
BODY_LEFT: float = 72.0
BODY_FONT_SIZE: float = 12.0
BODY_BASELINE: float = 700.0
# The logical text the probe passes to find() to locate the editable run.
BODY_TEXT: str = "Confidential encrypted body text here"
# A locatable substring anchoring the replace; short so a same-or-shorter
# replacement keeps the simple (non-reflow) splice path that preserves the
# rest of the document untouched.
FIND_ANCHOR: str = "encrypted"
REPLACEMENT: str = "preserved"

# The default single password used by the primary encrypted fixture and the
# probes that edit it. Exported so the probe and the builder cannot drift.
PASSWORD: str = "secret"


def _build_base_pdf() -> pikepdf.Pdf:
    """Construct the shared single-paragraph Helvetica document (unsaved).

    Returns:
        An open ``pikepdf.Pdf`` whose single page carries one WinAnsi
        Helvetica text run. The caller chooses the save-time encryption; the
        in-memory object is identical for every variant.
    """
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(PAGE_WIDTH, PAGE_HEIGHT))

    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))

    escaped = BODY_TEXT.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = (
        "BT",
        f"/F1 {BODY_FONT_SIZE:g} Tf",
        f"1 0 0 1 {BODY_LEFT:g} {BODY_BASELINE:g} Tm",
        f"({escaped}) Tj",
        "ET",
    )
    page.Contents = pdf.make_stream(("\n".join(ops)).encode("latin-1"))
    return pdf


def build_encrypted_pdf(password: str = PASSWORD, out_path: Path | None = None) -> bytes:
    """Build the deterministic ENCRYPTED fixture (owner==user==``password``).

    Saved with ``encryption=pikepdf.Encryption(owner=password, user=password,
    R=6)`` so a re-opened copy reports ``is_encrypted is True`` and requires
    ``password`` to open. This is the A2.3 subject: editing it through the
    engine (with ``password`` supplied) must keep the output encrypted.

    Args:
        password: The owner and user password (the same string for both, the
            common single-password case).
        out_path: Optional destination; when given the bytes are written
            there before being returned.

    Returns:
        The encrypted PDF bytes. Never ``None``.
    """
    pdf = _build_base_pdf()
    buf = io.BytesIO()
    # ``static_id`` keeps the /ID reproducible; the encryption= kwarg is the
    # only thing that distinguishes this from the control builder. R=6 selects
    # AES-256 (V=5). We do NOT use ``save_pdf_deterministic`` here because that
    # helper hard-codes an unencrypted save.
    pdf.save(
        buf,
        static_id=True,
        encryption=pikepdf.Encryption(owner=password, user=password, R=6),
    )
    return emit_or_write(buf.getvalue(), out_path)


def build_encrypted_pdf_distinct(
    owner: str = "own-pw", user: str = "usr-pw", out_path: Path | None = None
) -> bytes:
    """Build an ENCRYPTED fixture with DISTINCT owner!=user passwords.

    Used by the owner-collapse boundary probe: the engine re-encrypts an
    edited document with the caller-supplied password for BOTH owner and user
    (the original owner password is not recoverable from an opened pikepdf
    document), so a distinct owner!=user pair documents that boundary.

    Args:
        owner: The owner password.
        user: The user password (distinct from ``owner``).
        out_path: Optional destination; when given the bytes are written
            there before being returned.

    Returns:
        The encrypted PDF bytes. Never ``None``.
    """
    pdf = _build_base_pdf()
    buf = io.BytesIO()
    pdf.save(
        buf,
        static_id=True,
        encryption=pikepdf.Encryption(owner=owner, user=user, R=6),
    )
    return emit_or_write(buf.getvalue(), out_path)


def build_unencrypted_pdf(out_path: Path | None = None) -> bytes:
    """Build the deterministic UNENCRYPTED control fixture.

    Byte-content-identical to :func:`build_encrypted_pdf` except saved with no
    encryption, so a re-opened copy reports ``is_encrypted is False``. This is
    the A2.3 control: editing it must leave the output unencrypted and emit NO
    ``encryption_dropped``.

    Args:
        out_path: Optional destination; when given the bytes are written
            there before being returned.

    Returns:
        The unencrypted PDF bytes. Never ``None``.
    """
    pdf = _build_base_pdf()
    buf = io.BytesIO()
    pdf.save(buf, static_id=True)
    return emit_or_write(buf.getvalue(), out_path)
