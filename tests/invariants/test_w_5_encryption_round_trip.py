"""INV-W-5: an encrypted input is preserved (stays encrypted) across an edit.

An *encrypted* PDF stores its content under a password-derived key. pikepdf
detects this on an opened document via ``pdf.is_encrypted`` and can re-emit it
via ``pdf.save(..., encryption=pikepdf.Encryption(...))``.

TODAY the engine's single canonical save helper (``_pathutil._save_pdf``)
calls ``pdf.save(output_path)`` with no ``encryption=`` kwarg, so an edited
encrypted input is silently down-converted to a PLAINTEXT output — a fidelity
AND security loss with ZERO caller signal. The surgeon verbs additionally
refuse encrypted input outright today (``raise PDFEditError("Cannot edit
encrypted PDF")``), and no edit verb accepts a ``password`` keyword, so an
encrypted document cannot be edited with its encryption preserved at all
(verified RED below).

Root fix (A2.3, NOT a patch): the 13 public verbs gain a keyword-only
``password`` that flows to ``open_pdf`` (decrypting the in-memory object);
``_save_pdf`` reads ``pdf.is_encrypted`` BEFORE serializing and, when True and
the caller has not pinned an explicit ``encryption``, re-encrypts with the
caller-supplied password (threaded as ``reencrypt_password``) for owner AND
user so the property round-trips. The three surgeon ``is_encrypted`` refusals
are removed. If pikepdf cannot re-encrypt (raises ``pikepdf.PdfError`` on the
``encryption=`` save), the helper falls back to a normal save AND records the
loss so the edit verb can surface a typed ``encryption_dropped`` Degradation
(severity ``"warning"``, NOT in ``FONT_AFFECTING_KINDS`` — an encryption
change does not alter glyph identity, so ``font_preserved`` stays True). A
NON-encrypted input is unaffected (the ``encryption`` kwarg is never injected,
so the save path is byte-identical to today).

INV-W-5 is the next collision-free slot of the ``W`` robustness layer (W-1 =
width-cache objgen hygiene; W-2 = q/Q graphics-state depth cap; W-3 =
linearization preservation; W-4 = embedded-stream decoded-size bound). A grep
of ``tests/invariants/`` for ``INV-W-[0-9]+`` at authoring time returned only
W-1..W-4, so W-5 is free. A save-time FILE-FIDELITY guard (preserve a
structural/security property of the input on write) belongs in the ``W``
robustness layer alongside its save-path siblings (W-3 linearization most
directly), rather than the content-stream-format ``B`` layer, the input-
rejection ``M`` layer, the exception-translation ``L`` layer, or the ``J``
fidelity-report layer.

RED EXPECTATION (this phase, no ``src/`` changes):
- ``test_inv_w_5_encrypted_input_stays_encrypted_after_edit`` is GENUINELY
  RED: the engine cannot preserve encryption today (it has no ``password``
  verb kwarg, the surgeon verbs refuse encrypted input, and ``_save_pdf``
  emits plaintext), so the encryption-round-trip assertion fails. The helper
  converts the not-yet-supported call errors into a clear failure ON the
  round-trip invariant so the RED lands on the real assertion, never an
  opaque ``TypeError`` / collection error.
- ``test_inv_w_5_unencrypted_control_unchanged`` PASSES today (control: an
  unencrypted input already stays unencrypted with no degradation) and must
  keep passing after the fix — it pins the no-over-surfacing /
  byte-identical-default half of the invariant.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pikepdf

from pdf_edit_engine import find, replace
from pdf_edit_engine._pathutil import _save_pdf, open_pdf
from pdf_edit_engine.errors import PDFEditError
from tests.corpus_builders.encrypted import (
    FIND_ANCHOR,
    PASSWORD,
    REPLACEMENT,
    build_encrypted_pdf,
    build_unencrypted_pdf,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from pdf_edit_engine.models import EditResult


def _has_encryption_dropped(result: EditResult) -> bool:
    """True iff ``result``'s FidelityReport carries an ``encryption_dropped``."""
    return any(d.kind == "encryption_dropped" for d in result.fidelity_report.degradations)


def _edit_anchor(
    inp: Path, out: Path, *, password: str | bytes | None
) -> EditResult | PDFEditError | TypeError:
    """Locate the anchor in ``inp`` and replace it, writing ``out``.

    Drives the PUBLIC ``find`` + ``replace`` verbs, forwarding ``password``
    when one is supplied. This is the FUTURE (A2.3) call surface: the verbs
    gain a keyword-only ``password``.

    On CURRENT code the verbs do not accept ``password`` and/or refuse
    encrypted input, so the call raises ``TypeError`` (kwarg absent) or
    ``PDFEditError`` (encrypted-refusal / password-protected open). Rather
    than let that opaque error abort the test as a wrong-reason failure, this
    helper RETURNS the exception so the caller can assert ON the encryption-
    round-trip invariant (the RED then reads as "encryption was not
    preserved", which is exactly the bug). After the fix it returns the real
    ``EditResult``.
    """
    find_kwargs: dict[str, object] = {}
    replace_kwargs: dict[str, object] = {}
    if password is not None:
        find_kwargs["password"] = password
        replace_kwargs["password"] = password

    try:
        matches = find(str(inp), FIND_ANCHOR, **find_kwargs)  # type: ignore[arg-type]
        assert matches, f"fixture regression: anchor {FIND_ANCHOR!r} not found"
        return replace(str(inp), matches[0], REPLACEMENT, str(out), **replace_kwargs)  # type: ignore[arg-type]
    except (TypeError, PDFEditError) as exc:
        # Not-yet-supported on current code: surface to the caller so the RED
        # lands on the encryption-round-trip assertion, not on this exception.
        return exc


def test_inv_w_5_encrypted_input_stays_encrypted_after_edit(tmp_path: Path) -> None:
    """An encrypted input edited via the public API yields an encrypted output.

    Regression guard (INV-W-5): the engine must accept the caller ``password``,
    DECRYPT the in-memory object, apply the edit, and RE-ENCRYPT on save so the
    output reports ``is_encrypted is True`` and still opens with the same
    password — while the edit itself landed (anchor replaced).

    RED today: the engine has no ``password`` verb kwarg, the surgeon verbs
    refuse encrypted input, and ``_save_pdf`` emits plaintext — so encryption
    cannot be preserved. The failure is asserted ON the round-trip invariant
    (output not encrypted / could not be produced), the RIGHT reason.
    """
    inp = tmp_path / "encrypted_in.pdf"
    out = tmp_path / "encrypted_out.pdf"
    inp.write_bytes(build_encrypted_pdf(PASSWORD))

    # Sanity: the fixture really is encrypted on input and needs the password.
    with pikepdf.open(str(inp), password=PASSWORD) as pdf:
        assert pdf.is_encrypted, "fixture regression: input is not encrypted"

    result = _edit_anchor(inp, out, password=PASSWORD)

    # On current code the edit path is unavailable (TypeError / PDFEditError) —
    # which IS the bug: encryption round-trip is not supported. Fail ON the
    # invariant with a precise message rather than on the raw exception.
    assert not isinstance(result, BaseException), (
        "INV-W-5 violated: an encrypted input could not be edited with its "
        "encryption preserved (the verb does not accept a `password` or refuses "
        f"encrypted input): {type(result).__name__}: {result}"
    )

    assert result.success, "edit did not succeed on the encrypted fixture"

    # The output MUST still be encrypted AND open with the same password.
    with pikepdf.open(str(out), password=PASSWORD) as pdf:
        assert pdf.is_encrypted, (
            "INV-W-5 violated: an encrypted input was silently saved "
            "UNENCRYPTED (the password protection was dropped with no caller "
            "signal)"
        )

    # An unencrypted reopen must fail — proving the output is genuinely
    # password-protected, not merely flagged.
    try:
        pikepdf.open(str(out)).close()
        raise AssertionError(
            "INV-W-5 violated: the edited output opened with NO password — "
            "encryption was not genuinely preserved"
        )
    except pikepdf.PasswordError:
        pass

    # The edit actually landed: the replacement is LOCATABLE via the engine
    # (encoding-agnostic and a stronger behavioral pin than a raw bytes-repr
    # scan), and the anchor is gone. find(..., password=...) also exercises the
    # password through the public READ path on the encrypted output.
    assert find(str(out), REPLACEMENT, password=PASSWORD), (
        "INV-W-5 violated: the replacement text is not locatable in the "
        "edited (still-encrypted) output — the edit did not apply"
    )
    assert not find(str(out), FIND_ANCHOR, password=PASSWORD), (
        "INV-W-5 violated: the original anchor text survived the edit"
    )

    # Preservation succeeded → no encryption_dropped degradation, and the font
    # identity is untouched regardless (an encryption change is non-font-affecting).
    assert not _has_encryption_dropped(result), (
        "encryption_dropped must NOT be emitted when preservation succeeds"
    )
    assert result.fidelity_report.font_preserved is True


def test_inv_w_5_save_pdf_preserves_encryption_with_password(tmp_path: Path) -> None:
    """``_save_pdf`` re-encrypts an encrypted input when given the password.

    Pins INV-W-5 at the exact layer the root fix lives. ``_save_pdf`` is the
    single canonical save chokepoint every edit verb routes through. An
    encrypted document opened WITH its password (via ``open_pdf``, the single
    canonical open) is decrypted into the in-memory object but still reports
    ``is_encrypted is True``. ``_save_pdf`` must read that once and re-encrypt
    with the threaded ``reencrypt_password`` so the output stays
    password-protected — not silently down-converted to plaintext.

    (Pre-A2.3 this saved a PLAINTEXT output — the silent-decrypt-on-save bug;
    the RED proof for that lived in the RED phase. This GREEN probe pins the
    fixed behavior at the helper boundary, exercising the ``reencrypt_password``
    parameter directly so a regression that ignored it would be caught.)
    """
    inp = tmp_path / "encrypted_in.pdf"
    out = tmp_path / "encrypted_out.pdf"
    inp.write_bytes(build_encrypted_pdf(PASSWORD))

    pdf = open_pdf(str(inp), password=PASSWORD)
    try:
        assert pdf.is_encrypted, "fixture regression: opened object is not encrypted"
        _save_pdf(pdf, str(out), reencrypt_password=PASSWORD)
    finally:
        pdf.close()

    # The output stays encrypted and opens with the caller password ...
    with pikepdf.open(str(out), password=PASSWORD) as saved:
        assert saved.is_encrypted, (
            "INV-W-5 violated: _save_pdf did not re-encrypt an encrypted input "
            "even when handed the reencrypt_password"
        )

    # ... and an unencrypted reopen genuinely fails (proves it is truly
    # password-protected, not merely flagged).
    try:
        pikepdf.open(str(out)).close()
        raise AssertionError(
            "INV-W-5 violated: the re-saved output opened with NO password — "
            "encryption was not genuinely preserved at the _save_pdf boundary"
        )
    except pikepdf.PasswordError:
        pass


def test_inv_w_5_unencrypted_control_unchanged(tmp_path: Path) -> None:
    """Control: an unencrypted input stays unencrypted, no degradation.

    Regression guard (INV-W-5): the preservation logic must key on the INPUT's
    ``is_encrypted`` — an unencrypted input must follow the byte-identical
    default save path and never gain spurious encryption or a spurious
    ``encryption_dropped`` (no over-surfacing). PASSES today; pins the control
    half of the invariant after the fix lands.
    """
    inp = tmp_path / "plain_in.pdf"
    out = tmp_path / "plain_out.pdf"
    inp.write_bytes(build_unencrypted_pdf())

    with pikepdf.open(str(inp)) as pdf:
        assert not pdf.is_encrypted, "fixture regression: control is encrypted"

    result = _edit_anchor(inp, out, password=None)

    assert not isinstance(result, BaseException), (
        "control regression: editing an unencrypted input must succeed today: "
        f"{type(result).__name__}: {result}"
    )
    assert result.success, "edit did not succeed on the unencrypted control"

    with pikepdf.open(str(out)) as pdf:
        assert not pdf.is_encrypted, (
            "INV-W-5 violated: an unencrypted input was unexpectedly encrypted "
            "on save (over-surfacing the preservation)"
        )

    assert not _has_encryption_dropped(result), (
        "encryption_dropped must NOT be emitted for an unencrypted input"
    )
    assert result.fidelity_report.font_preserved is True


def test_inv_w_5_reencrypt_failure_surfaces_encryption_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When re-encryption fails, the edit still succeeds + surfaces the loss.

    Regression guard (INV-W-5 honest fallback): if pikepdf raises on the
    ``encryption=`` save, ``_save_pdf`` must retry WITHOUT encryption so the
    edit still lands, AND record the loss so the verb surfaces a typed
    ``encryption_dropped`` (severity ``"warning"``, NOT font-affecting). The
    loss must be HONEST, never silent. Mirrors the W-3 relinearize-failure
    precedent.

    The failure is injected by monkeypatching ``pikepdf.Pdf.save`` to raise
    ``pikepdf.PdfError`` only when called with a truthy ``encryption`` kwarg
    (the injected ``pikepdf.Encryption``); the unencrypted fallback save (whose
    ``encryption`` kwarg was popped) proceeds. This forces exactly the fallback
    path the invariant governs.
    """
    inp = tmp_path / "encrypted_in.pdf"
    out = tmp_path / "encrypted_out.pdf"
    inp.write_bytes(build_encrypted_pdf(PASSWORD))

    original_save = pikepdf.Pdf.save

    def failing_save(self: pikepdf.Pdf, *args: object, **kwargs: object) -> None:
        if kwargs.get("encryption"):
            raise pikepdf.PdfError("forced re-encryption failure (INV-W-5 probe)")
        return original_save(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pikepdf.Pdf, "save", failing_save)

    result = _edit_anchor(inp, out, password=PASSWORD)

    assert not isinstance(result, BaseException), (
        "INV-W-5 violated: re-encryption failure must fall back to an "
        f"unencrypted save, not raise: {type(result).__name__}: {result}"
    )
    # The edit must NOT hard-fail just because re-encryption was impossible.
    assert result.success, (
        "INV-W-5 violated: re-encryption failure must fall back to an "
        "unencrypted save (the edit still succeeds), not abort the edit"
    )

    # The loss must be surfaced as a typed, warning-severity, non-font-affecting
    # degradation — never dropped silently.
    dropped = [d for d in result.fidelity_report.degradations if d.kind == "encryption_dropped"]
    assert dropped, (
        "INV-W-5 violated: re-encryption failure was not surfaced as an "
        "encryption_dropped degradation; got "
        f"kinds={[d.kind for d in result.fidelity_report.degradations]}"
    )
    assert dropped[0].severity == "warning"
    # Non font-affecting: an encryption change leaves glyph identity intact.
    assert result.fidelity_report.font_preserved is True


def test_inv_w_5_decrypt_pdf_still_decrypts(tmp_path: Path) -> None:
    """``decrypt_pdf`` must still produce a PLAINTEXT output (no A2.3 regression).

    Regression guard: A2.3 added auto-encryption-preservation to the canonical
    ``_save_pdf``. ``decrypt_pdf`` opens an encrypted input (so the in-memory
    object reports ``is_encrypted is True``) and MUST strip encryption — it
    opts out via an explicit ``encryption=False``. Without that opt-out,
    ``decrypt_pdf`` would silently RE-ENCRYPT (with an empty password) the file
    the caller asked to decrypt — a silent regression no existing decrypt test
    catches (they verify text extraction, not ``is_encrypted``).
    """
    from pdf_edit_engine import decrypt_pdf

    inp = tmp_path / "encrypted_in.pdf"
    out = tmp_path / "decrypted_out.pdf"
    inp.write_bytes(build_encrypted_pdf(PASSWORD))

    decrypt_pdf(str(inp), PASSWORD, str(out))

    # Opens with NO password and is genuinely not encrypted.
    with pikepdf.open(str(out)) as pdf:
        assert not pdf.is_encrypted, (
            "decrypt_pdf regressed: the output is still encrypted — A2.3 "
            "auto-preservation re-encrypted a file that was meant to be decrypted"
        )


def test_inv_w_5_non_ascii_bytes_password_round_trips(tmp_path: Path) -> None:
    """A non-ASCII BYTES password round-trips under R=6 (AES-256).

    pikepdf's R=6 key derivation hashes a ``bytes`` password from its RAW bytes
    but a ``str`` password from its UTF-8 encoding. ``_save_pdf`` must pass the
    caller's password through WITH ITS ORIGINAL TYPE — decoding bytes->str
    (even losslessly via latin-1) re-encrypts under a DIFFERENT key and silently
    locks the caller out of their own output (success=True, no degradation).
    Regression guard for the adversarial-critic Finding 1.
    """
    pw = b"secr\xe9t"  # non-ASCII byte 0xE9: latin-1 'é'; UTF-8 encodes differently
    inp = tmp_path / "enc_bytes_in.pdf"
    out = tmp_path / "enc_bytes_out.pdf"

    # Build an R=6 file encrypted with the BYTES password (the corpus builder
    # only does str passwords). pikepdf.Encryption accepts bytes at runtime.
    with pikepdf.open(io.BytesIO(build_unencrypted_pdf())) as base:
        base.save(
            str(inp),
            encryption=pikepdf.Encryption(owner=pw, user=pw, R=6),  # type: ignore[arg-type]
        )

    result = _edit_anchor(inp, out, password=pw)
    assert not isinstance(result, BaseException), (
        f"edit failed on a bytes-password input: {type(result).__name__}: {result}"
    )
    assert result.success

    # The output MUST reopen with the SAME bytes password the caller used.
    with pikepdf.open(str(out), password=pw) as pdf:
        assert pdf.is_encrypted, "INV-W-5 violated: bytes-password input lost its encryption"

    # And the engine read path locates the replacement with that same password —
    # proving the key derivation matches the password the caller opened with.
    assert find(str(out), REPLACEMENT, password=pw), (
        "INV-W-5 violated: a non-ASCII bytes password did not round-trip — the "
        "caller is locked out of their own re-encrypted output (Finding 1)"
    )


def test_inv_w_5_delete_block_on_encrypted_preserves_encryption(tmp_path: Path) -> None:
    """``delete_block`` on an encrypted input preserves the CALLER's encryption.

    Regression guard for the structural cascade (adversarial-critic Point-3
    gap): ``delete_block``'s MAIN deletion path must thread
    ``reencrypt_password`` + surface ``encryption_dropped`` — else the output is
    silently re-encrypted with an EMPTY password (the caller's protection
    discarded, success=True). The output must require the caller's password, not
    open for anyone. Pins the structural path, which the surgeon-only probes
    above do not exercise.
    """
    from pdf_edit_engine import delete_block

    inp = tmp_path / "enc_del_in.pdf"
    out = tmp_path / "enc_del_out.pdf"
    inp.write_bytes(build_encrypted_pdf(PASSWORD))

    matches = find(str(inp), FIND_ANCHOR, password=PASSWORD)
    assert matches, f"fixture regression: anchor {FIND_ANCHOR!r} not found"
    m = matches[0]

    delete_block(str(inp), m.page_number, m.bounding_box, str(out), password=PASSWORD)

    # The output stays encrypted under the CALLER's password ...
    with pikepdf.open(str(out), password=PASSWORD) as pdf:
        assert pdf.is_encrypted, (
            "INV-W-5 violated: delete_block dropped encryption on an encrypted input"
        )

    # ... and crucially does NOT open with NO password (which it would if the
    # main path re-encrypted with the empty default — the Point-3 gap).
    try:
        pikepdf.open(str(out)).close()
        raise AssertionError(
            "INV-W-5 violated: delete_block re-encrypted with an EMPTY password — "
            "the caller's password was discarded on the main deletion path"
        )
    except pikepdf.PasswordError:
        pass
