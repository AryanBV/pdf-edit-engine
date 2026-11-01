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
* :func:`build_encrypted_pdf_with` — any scheme pikepdf can write.
* :func:`build_rc4_pdf_pikepdf_cannot_write` — RC4 R 2 / 3 key lengths and
  ``/P`` values pikepdf's writer cannot produce.
"""

from __future__ import annotations

import hashlib
import io
import struct
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


def build_encrypted_pdf_with(
    encryption: pikepdf.Encryption, *, linearize: bool = False, out_path: Path | None = None
) -> bytes:
    """Build the fixture encrypted with exactly ``encryption``.

    Covers the schemes :func:`build_encrypted_pdf` does not (RC4 R 2 / 3 / 4,
    AES-128 R 4, ``/EncryptMetadata false``, an empty user password beside an
    owner password, restricted permissions) for the re-encryption mirror
    probes.

    Args:
        encryption: The encryption to save with.
        linearize: Save linearized ("Fast Web View") as well.
        out_path: Optional destination; when given the bytes are written
            there before being returned.

    Returns:
        The encrypted PDF bytes. Never ``None``.
    """
    pdf = _build_base_pdf()
    buf = io.BytesIO()
    pdf.save(buf, static_id=True, encryption=encryption, linearize=linearize)
    return emit_or_write(buf.getvalue(), out_path)


# ISO 32000-1 7.6.3.3 Algorithm 2 step (a) password padding string.
_RC4_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")


def _rc4(key: bytes, data: bytes) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) & 0xFF
        state[i], state[j] = state[j], state[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) & 0xFF
        j = (j + state[i]) & 0xFF
        state[i], state[j] = state[j], state[i]
        out.append(byte ^ state[(state[i] + state[j]) & 0xFF])
    return bytes(out)


def _rc4_owner_and_user_entries(
    owner: bytes, user: bytes, revision: int, key_bytes: int, permissions: int, id0: bytes
) -> tuple[bytes, bytes]:
    padded_user = (user + _RC4_PAD)[:32]
    digest = hashlib.md5((owner + _RC4_PAD)[:32]).digest()
    if revision >= 3:
        for _ in range(50):
            digest = hashlib.md5(digest[:key_bytes]).digest()
    o_entry = _rc4(digest[:key_bytes], padded_user)
    if revision >= 3:
        for i in range(1, 20):
            o_entry = _rc4(bytes(b ^ i for b in digest[:key_bytes]), o_entry)
    digest = hashlib.md5(padded_user + o_entry + struct.pack("<i", permissions) + id0).digest()
    if revision >= 3:
        for _ in range(50):
            digest = hashlib.md5(digest[:key_bytes]).digest()
    file_key = digest[:key_bytes]
    if revision == 2:
        return o_entry, _rc4(file_key, _RC4_PAD)
    u_entry = _rc4(file_key, hashlib.md5(_RC4_PAD + id0).digest())
    for i in range(1, 20):
        u_entry = _rc4(bytes(b ^ i for b in file_key), u_entry)
    return o_entry, u_entry + bytes(16)


def build_rc4_pdf_pikepdf_cannot_write(
    *, revision: int, key_bits: int, permissions: int, owner: str, user: str
) -> bytes:
    """Build the fixture under an RC4 R 2 / 3 scheme pikepdf's writer cannot produce.

    pikepdf writes R 3 only with a 128-bit key and sets the ``/P`` bits a
    reader ignores at R 2, so a 40- or 64-bit R 3 key, or an R 2 ``/P`` with
    bits 9-12 clear, needs the Standard security handler written by hand (ISO
    32000-1 7.6.3, Algorithms 2, 3, 4 and 5). Only an empty document is
    written by hand; qpdf then encrypts the copied pages under that key with
    ``encryption=True``.

    Args:
        revision: 2 or 3.
        key_bits: The key length in bits (40 for R 2).
        permissions: The signed ``/P`` value.
        owner: The owner password (ASCII).
        user: The user password (ASCII).

    Returns:
        The encrypted PDF bytes. Never ``None``.
    """
    id0 = hashlib.md5(b"build_rc4_pdf_pikepdf_cannot_write").digest()
    o_entry, u_entry = _rc4_owner_and_user_entries(
        owner.encode("ascii"), user.encode("ascii"), revision, key_bits // 8, permissions, id0
    )
    version = 1 if revision == 2 else 2
    objects = [
        b"<</Type /Catalog /Pages 2 0 R>>",
        b"<</Type /Pages /Kids [] /Count 0>>",
        f"<</Filter /Standard /V {version} /R {revision} /Length {key_bits} /P {permissions} "
        f"/O <{o_entry.hex()}> /U <{u_entry.hex()}>>>".encode(),
    ]
    shell = io.BytesIO()
    shell.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(shell.tell())
        shell.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = shell.tell()
    shell.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        shell.write(f"{offset:010d} 00000 n \n".encode())
    shell.write(
        f"trailer\n<</Size {len(objects) + 1} /Root 1 0 R /Encrypt 3 0 R "
        f"/ID [<{id0.hex()}> <{id0.hex()}>]>>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    )
    with pikepdf.open(io.BytesIO(shell.getvalue()), password=user) as pdf:
        pdf.pages.extend(_build_base_pdf().pages)
        buf = io.BytesIO()
        pdf.save(buf, encryption=True)
    return buf.getvalue()
