"""INV-W-4 — embedded font / CMap stream decoded-size is bounded (bomb guard).

Every read path that materialises an embedded font binary, a ToUnicode CMap,
or a CIDToGIDMap does ``stream.read_bytes()``, which transparently inflates a
``/FlateDecode`` stream. A *Flate decompression bomb* — a few-KiB compressed
payload that expands to tens of MiB — therefore lets a malformed/adversarial
PDF force unbounded memory materialisation (DoS), with NO upper bound today:
the engine inflates the whole stream before it even knows the decoded size.

Root fix (A1.3, NOT a patch): a single pikepdf-only primitive
``_pathutil.read_stream_bounded(stream, *, max_decoded, label)`` with a
DUAL-PATH design:

* the documented Flate-bomb vector — ``[/FlateDecode]`` with NO predictor,
  the encoding of ~all real font/CMap streams — keeps a CHUNKED incremental
  ``zlib`` decode that aborts the instant the running output crosses the cap
  (memory-hard-bounded; the peak tracks the cap, not the bomb);
* an unfiltered stream bounds its raw bytes directly (raw == decoded);
* EVERYTHING ELSE (a non-Flate single filter such as ``/ASCIIHexDecode`` /
  ``/ASCII85Decode``, a multi-filter chain such as ``[/ASCIIHexDecode
  /FlateDecode]``, or ``/FlateDecode`` + a ``/DecodeParms /Predictor``) goes
  CORRECTNESS-FIRST through pikepdf's complete ``stream.read_bytes()`` decode
  and then bounds the decoded length. Real font/CMap streams do not use these
  exotic encodings; an oversize one is still refused, with a documented
  residual that ``read_bytes()`` may transiently expand it before the size
  check (re-implementing every PDF codec incrementally is deferred hardening).

On the cap being exceeded it raises ``FontStreamTooLargeError`` — a NEW
``FontNotFoundError`` subclass, so it is AUTOMATICALLY inside
``fonts._FONT_EXTEND_FAIL_EXCS`` and every edit verb surfaces it as the
existing ``font_extension_failed`` Degradation (``success=False``). The edit
path ADDITIONALLY surfaces a NEW ``font_stream_too_large`` Degradation
(severity ``"warning"``, NOT in ``FONT_AFFECTING_KINDS`` — the edit was
refused before any glyph surgery; the companion ``font_extension_failed``
already drives ``font_preserved`` False) as the specific-cause annotation.

The detail string is generic (F-C-03 / INV-W0-9: never echo attacker-
controlled stream bytes into user-visible text) — a fixed caller label plus
the cap value.

INV-W-4 is minted as the next collision-free slot of the ``W`` robustness
layer (W-1 = width-cache objgen hygiene; W-2 = q/Q depth cap; W-3 =
linearization preservation). A decompression-bomb / DoS guard on the
font/CMap read path belongs in ``W`` alongside its save/robustness siblings.
"""

from __future__ import annotations

import base64
import binascii
import tracemalloc
import zlib
from typing import TYPE_CHECKING

import pikepdf
import pytest

from pdf_edit_engine._pathutil import (
    MAX_FONT_STREAM_BYTES,
    MAX_TOUNICODE_BYTES,
    open_pdf,
    read_stream_bounded,
)
from pdf_edit_engine.errors import (
    EncodingError,
    FontNotFoundError,
    FontStreamTooLargeError,
    PDFEditError,
)
from pdf_edit_engine.fonts import _FONT_EXTEND_FAIL_EXCS, extend_subset
from pdf_edit_engine.structural import replace_block
from tests.corpus_builders.flate_bomb import (
    BOMB_DECODED_BYTES,
    build_flate_bomb_fontfile2_pdf,
    make_flate_bomb_stream,
)

if TYPE_CHECKING:
    from pathlib import Path

# A modest cap used by the unit probes that want a small, fast payload.
_SMALL_CAP = 1 * 1024 * 1024  # 1 MiB


def _flate_stream(pdf: pikepdf.Pdf, payload: bytes) -> pikepdf.Object:
    """Make a ``/FlateDecode`` stream whose decoded bytes == ``payload``."""
    s = pdf.make_stream(zlib.compress(payload))
    s.Filter = pikepdf.Name("/FlateDecode")
    return s


def _png_up_predictor_stream(
    pdf: pikepdf.Pdf, rows: list[bytes], columns: int
) -> tuple[pikepdf.Object, bytes]:
    """Build a ``/FlateDecode`` + PNG-Up (``/Predictor 12``) stream.

    Encodes ``rows`` with the PNG "Up" row filter (tag byte ``0x02`` per row,
    each data byte = raw - byte-directly-above), zlib-compresses the filtered
    bytes, and tags the stream ``/Filter /FlateDecode`` with
    ``/DecodeParms << /Predictor 12 /Columns columns >>``. Returns the stream
    AND the TRUE un-predicted payload so the test can assert against the real
    decoded bytes (pikepdf ``read_bytes()`` applies the inverse predictor).
    """
    true_data = b"".join(rows)
    prev = bytes(columns)
    filtered = bytearray()
    for row in rows:
        filtered.append(2)  # PNG "Up" predictor row tag
        for i in range(columns):
            filtered.append((row[i] - prev[i]) & 0xFF)
        prev = row
    s = pdf.make_stream(zlib.compress(bytes(filtered)))
    s.Filter = pikepdf.Name("/FlateDecode")
    s.DecodeParms = pikepdf.Dictionary({"/Predictor": 12, "/Columns": columns})
    return s, true_data


# ──────────────────────────────────────────────────────────────────────────
# UNIT probes: read_stream_bounded enforces the decoded-size bound.
# ──────────────────────────────────────────────────────────────────────────


def test_inv_w_4_read_stream_bounded_raises_on_flate_bomb() -> None:
    """INV-W-4: a Flate bomb over the cap raises ``FontStreamTooLargeError``.

    The compressed payload is a few KiB but inflates to 50 MiB — over the
    32 MiB ``MAX_FONT_STREAM_BYTES`` cap — so the bounded read must refuse.
    The exception must be a ``FontNotFoundError`` (so it sits in
    ``_FONT_EXTEND_FAIL_EXCS``) and a ``PDFEditError`` (honest typed surface),
    and its message must be the generic "refused" detail with NO raw payload
    bytes (F-C-03 / INV-W0-9).
    """
    pdf = pikepdf.Pdf.new()
    try:
        raw = zlib.compress(b"\x00" * (50 * 1024 * 1024))
        s = pdf.make_stream(raw)
        s.Filter = pikepdf.Name("/FlateDecode")

        with pytest.raises(FontStreamTooLargeError) as excinfo:
            read_stream_bounded(s, max_decoded=MAX_FONT_STREAM_BYTES, label="font")

        exc = excinfo.value
        assert isinstance(exc, FontNotFoundError), (
            "FontStreamTooLargeError must subclass FontNotFoundError so it is "
            "automatically in _FONT_EXTEND_FAIL_EXCS"
        )
        assert isinstance(exc, PDFEditError)
        message = str(exc)
        assert "refused" in message
        # The generic detail must NOT contain the raw decompressed payload
        # (F-C-03: no attacker-controlled bytes in user-visible text).
        assert "\x00" not in message
    finally:
        pdf.close()


def test_inv_w_4_bomb_decode_peak_memory_bounded_by_cap() -> None:
    """INV-W-4: refusing a 50 MiB bomb must peak near the CAP, never the bomb.

    MEMORY PROOF / regression guard forcing the CHUNKED decode. The stream
    inflates to 50 MiB but the cap here is 8 MiB and the stream carries NO
    ``/Length1`` (so the cheap declared-size pre-gate cannot short-circuit) —
    the bound must therefore be enforced by the chunked incremental decode
    that aborts the instant the running output crosses the cap.

    The peak resident memory must stay BELOW ``cap * 1.6``:

    * the prescribed CHUNKED decode peaks at ~``cap * 1.3-1.4`` (it builds the
      output up to the cap plus ~one 1 MiB chunk, plus the final
      ``bytes(bytearray)`` copy) — comfortably under the ceiling;
    * the REJECTED single-shot ``d.decompress(raw, cap+1)`` alternative peaks
      at ~``cap * 2.0`` (it allocates the full ``cap+1`` output in one
      buffer) — which EXCEEDS the ceiling and FAILS this assertion.

    So this probe both proves the full 50 MiB bomb payload is never
    materialised (peak is bounded by the cap, not the bomb) AND permanently
    discriminates the chunked implementation from the single-shot one.

    A small-but-not-tiny cap (8 MiB) is used deliberately: at a 1 MiB cap both
    strategies stay under any fixed small ceiling (no discrimination), and at
    the full 32 MiB cap the chunked peak (~36 MiB) is itself large — 8 MiB is
    the sweet spot where the ``* 1.6`` ratio cleanly separates the two.
    """
    cap = 8 * 1024 * 1024
    pdf = pikepdf.Pdf.new()
    try:
        raw = zlib.compress(b"\x00" * (50 * 1024 * 1024))
        s = pdf.make_stream(raw)
        s.Filter = pikepdf.Name("/FlateDecode")  # NO Length1 — force chunked path

        tracemalloc.start()
        try:
            with pytest.raises(FontStreamTooLargeError):
                read_stream_bounded(s, max_decoded=cap, label="font")
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        ceiling = int(cap * 1.6)
        assert peak < ceiling, (
            f"INV-W-4 violated: refusing the bomb peaked at {peak} bytes "
            f"(>= cap*1.6 = {ceiling}) — the bound must be enforced by a "
            "CHUNKED incremental decode that aborts as the running output "
            "crosses the cap (peak ~cap*1.4), never a single-shot decode "
            "(peak ~cap*2.0) and never the full 50 MiB decompressed payload."
        )
    finally:
        pdf.close()


def test_inv_w_4_length1_declared_too_large_pre_reject() -> None:
    """INV-W-4: an oversize declared ``/Length1`` rejects BEFORE decompression.

    The compressed payload here decodes to only 100 bytes, so WITHOUT the
    ``/Length1`` pre-gate it would PASS. With ``/Length1`` declared at 64 MiB
    (over the 32 MiB cap), the bounded read must reject it cheaply, before
    any inflate. Pins the pre-gate.
    """
    pdf = pikepdf.Pdf.new()
    try:
        raw = zlib.compress(b"A" * 100)  # decodes to 100 bytes only
        s = pdf.make_stream(raw)
        s.Filter = pikepdf.Name("/FlateDecode")
        s.Length1 = int(64 * 1024 * 1024)  # declared decoded size over the cap

        with pytest.raises(FontStreamTooLargeError):
            read_stream_bounded(s, max_decoded=MAX_FONT_STREAM_BYTES, label="font")
    finally:
        pdf.close()


def test_inv_w_4_benign_stream_byte_identical_to_read_bytes() -> None:
    """INV-W-4: a benign stream round-trips byte-identical to ``read_bytes()``.

    DROP-IN guarantee. A ~1 MiB legit Flate stream (well under cap) returns
    bytes identical to ``stream.read_bytes()``. A non-Flate / unfiltered
    stream returns ``read_bytes()`` unchanged too (raw == decoded). Pins that
    the bound is invisible on legit input — zero behaviour change at every
    swapped call site.
    """
    pdf = pikepdf.Pdf.new()
    try:
        payload = b"The quick brown fox " * 50000  # ~1 MiB, well under cap
        s = _flate_stream(pdf, payload)
        out = read_stream_bounded(s, max_decoded=MAX_FONT_STREAM_BYTES, label="font")
        assert out == s.read_bytes(), (
            "INV-W-4 violated: bounded read of a benign Flate stream must be "
            "byte-identical to read_bytes()"
        )

        # Non-flate / unfiltered control: raw == decoded.
        s2 = pdf.make_stream(b"plain bytes")
        out2 = read_stream_bounded(s2, max_decoded=MAX_FONT_STREAM_BYTES, label="x")
        assert out2 == s2.read_bytes() == b"plain bytes"
    finally:
        pdf.close()


def test_inv_w_4_off_by_one_exact_cap_passes_capplus1_raises() -> None:
    """INV-W-4: the bound predicate is strict ``>`` — exactly-at-cap PASSES.

    A decoded size of exactly ``max_decoded`` must be accepted; one byte over
    must be refused. Pins the off-by-one boundary against a ``>=`` regression.
    """
    pdf = pikepdf.Pdf.new()
    try:
        cap = _SMALL_CAP

        exact = _flate_stream(pdf, b"X" * cap)
        out = read_stream_bounded(exact, max_decoded=cap, label="x")
        assert len(out) == cap, "exactly-at-cap must PASS (strict '>' predicate)"

        over = _flate_stream(pdf, b"X" * (cap + 1))
        with pytest.raises(FontStreamTooLargeError):
            read_stream_bounded(over, max_decoded=cap, label="x")
    finally:
        pdf.close()


# ──────────────────────────────────────────────────────────────────────────
# CORRUPT-STREAM probes (A1.3 INV-L-1 hardening): a CORRUPT (not oversized)
# stream must surface a typed ``EncodingError`` (a ``PDFEditError``), NEVER a
# raw ``zlib.error`` (chunked Flate arm) and NEVER a raw pikepdf decode error
# (step-6 ``read_bytes()`` arm). RED against the current primitive whose two
# decode arms leak the underlying library exception; GREEN after the
# translate-at-the-chokepoint fix.
# ──────────────────────────────────────────────────────────────────────────


def test_inv_w_4_corrupt_flate_stream_raises_encoding_error_not_zlib() -> None:
    """INV-W-4 / INV-L-1: a CORRUPT single ``/FlateDecode`` stream translates.

    A ``/FlateDecode`` stream whose raw bytes are not valid zlib data and whose
    size is well UNDER the cap drives the CHUNKED Flate arm of
    ``read_stream_bounded``. ``zlib.decompressobj().decompress(...)`` raises a
    raw ``zlib.error`` ("incorrect header check") on that arm today — a
    NON-PDFEditError leaking out of the engine's single font/CMap read
    chokepoint (INV-L-1 violation). The bounded read must instead raise a typed
    ``EncodingError`` (a ``PDFEditError``) with a GENERIC message carrying no
    raw stream bytes and no raw exception text (F-C-03 / INV-W0-9).
    """
    pdf = pikepdf.Pdf.new()
    try:
        s = pdf.make_stream(b"not valid zlib data")  # under-cap, corrupt zlib
        s.Filter = pikepdf.Name("/FlateDecode")

        with pytest.raises(EncodingError) as excinfo:
            read_stream_bounded(s, max_decoded=MAX_FONT_STREAM_BYTES, label="font")

        exc = excinfo.value
        assert isinstance(exc, PDFEditError), (
            "INV-L-1 violated: a corrupt Flate stream must surface a typed "
            "PDFEditError, not a raw zlib.error"
        )
        assert not isinstance(exc, zlib.error), (
            "INV-L-1 violated: the raised exception must NOT be a zlib.error"
        )
        # Generic detail: no raw stream bytes, no raw zlib message text.
        message = str(exc)
        assert "not valid zlib data" not in message
        assert "incorrect header check" not in message
    finally:
        pdf.close()


def test_inv_w_4_corrupt_nonflate_stream_raises_encoding_error_not_pikepdf() -> None:
    """INV-W-4 / INV-L-1: a CORRUPT non-Flate filter stream translates.

    An ``/ASCIIHexDecode`` stream whose raw bytes are not valid hex and whose
    size is well UNDER the cap drives the step-6 fallback arm of
    ``read_stream_bounded``, which calls ``stream.read_bytes()``. pikepdf
    raises a raw ``pikepdf.DataDecodingError`` ("character out of range during
    base Hex decode") on that arm today — a NON-PDFEditError leaking out of the
    engine's single font/CMap read chokepoint (INV-L-1 violation). The bounded
    read must instead raise a typed ``EncodingError`` (a ``PDFEditError``) with
    a GENERIC message.
    """
    pdf = pikepdf.Pdf.new()
    try:
        s = pdf.make_stream(b"ZZZZ not hex")  # under-cap, corrupt for ASCIIHex
        s.Filter = pikepdf.Name("/ASCIIHexDecode")

        with pytest.raises(EncodingError) as excinfo:
            read_stream_bounded(s, max_decoded=MAX_FONT_STREAM_BYTES, label="font")

        exc = excinfo.value
        assert isinstance(exc, PDFEditError), (
            "INV-L-1 violated: a corrupt non-Flate stream must surface a typed "
            "PDFEditError, not a raw pikepdf decode error"
        )
        # The raised exception must NOT be the raw pikepdf decode error.
        assert not isinstance(exc, pikepdf.PdfError)
        assert type(exc).__module__ != "pikepdf._core", (
            "INV-L-1 violated: the raised exception must NOT be a raw pikepdf error"
        )
        # Generic detail: no raw stream bytes, no raw pikepdf message text.
        message = str(exc)
        assert "ZZZZ" not in message
        assert "out of range" not in message
    finally:
        pdf.close()


def test_inv_w_4_corrupt_multi_filter_stream_raises_encoding_error() -> None:
    """INV-W-4 / INV-L-1: a CORRUPT multi-filter chain translates too.

    A ``[/ASCIIHexDecode /FlateDecode]`` chain whose raw bytes are not valid
    hex drives the step-6 fallback arm via ``read_bytes()``; pikepdf raises a
    raw ``pikepdf.DataDecodingError`` on the first (hex) filter. The bounded
    read must surface a typed ``EncodingError`` (a ``PDFEditError``), never a
    raw pikepdf error.
    """
    pdf = pikepdf.Pdf.new()
    try:
        s = pdf.make_stream(b"ZZZZ not hex")
        s.Filter = pikepdf.Array([pikepdf.Name("/ASCIIHexDecode"), pikepdf.Name("/FlateDecode")])

        with pytest.raises(EncodingError) as excinfo:
            read_stream_bounded(s, max_decoded=MAX_FONT_STREAM_BYTES, label="font")

        exc = excinfo.value
        assert isinstance(exc, PDFEditError)
        assert not isinstance(exc, pikepdf.PdfError)
        assert type(exc).__module__ != "pikepdf._core"
    finally:
        pdf.close()


# ──────────────────────────────────────────────────────────────────────────
# CORRECTNESS probes (A / B / C): the bounded read must return the TRUE
# pikepdf-decoded payload (filter + predictor correct), NEVER the encoded
# bytes, NEVER a zlib crash, NEVER un-de-predicted garbage. RED against the
# current single-Flate-only reimplementation; GREEN after the dual-path fix.
# ──────────────────────────────────────────────────────────────────────────


def test_inv_w_4_nonflate_single_filter_byte_identical_decoded() -> None:
    """INV-W-4 (A): a NON-Flate single filter returns the TRUE decoded payload.

    Builds an ``/ASCIIHexDecode`` stream whose raw bytes are the hex encoding
    of an sfnt-ish payload (the shape an exotic ``/FontFile2`` could carry),
    and a sibling ``/ASCII85Decode`` stream. The bounded read MUST equal
    ``stream.read_bytes()`` (the real decoded payload), not
    ``stream.read_raw_bytes()`` (the hex/base85 text).

    Fixture soundness is asserted explicitly: ``read_bytes()`` returns the
    correct decoded payload and DIFFERS from ``read_raw_bytes()`` — so the
    assertion target is real.

    RED reason (current buggy primitive): for a non-Flate filter the impl
    takes the ``if not is_flate`` arm and returns ``read_raw_bytes()`` (the
    ENCODED hex/base85 text) silently — ``out != read_bytes()``.
    """
    pdf = pikepdf.Pdf.new()
    try:
        # (A.1) /ASCIIHexDecode — raw == hex(payload) + EOD marker '>'.
        payload = b"sfnt-ish payload bytes for the ascii-hex correctness probe 0123456789"
        hex_raw = binascii.hexlify(payload) + b">"
        s = pdf.make_stream(hex_raw)
        s.Filter = pikepdf.Name("/ASCIIHexDecode")

        # Fixture soundness: read_bytes() is the real decode; raw differs.
        assert s.read_bytes() == payload, "fixture unsound: ASCIIHex decode != payload"
        assert s.read_raw_bytes() != s.read_bytes(), "fixture unsound: raw == decoded"

        out = read_stream_bounded(s, max_decoded=MAX_FONT_STREAM_BYTES, label="font")
        assert out == s.read_bytes(), (
            "INV-W-4 violated: a non-Flate (/ASCIIHexDecode) stream must "
            "return the TRUE decoded payload (read_bytes()), not the raw "
            "encoded hex text (read_raw_bytes())"
        )

        # (A.2) /ASCII85Decode — same contract via a different non-Flate filter.
        payload85 = b"ascii85 correctness payload abcdefghijklmnopqrstuvwxyz"
        a85_raw = base64.a85encode(payload85) + b"~>"
        s85 = pdf.make_stream(a85_raw)
        s85.Filter = pikepdf.Name("/ASCII85Decode")

        assert s85.read_bytes() == payload85, "fixture unsound: ASCII85 decode != payload"
        assert s85.read_raw_bytes() != s85.read_bytes(), "fixture unsound: raw == decoded"

        out85 = read_stream_bounded(s85, max_decoded=MAX_FONT_STREAM_BYTES, label="font")
        assert out85 == s85.read_bytes(), (
            "INV-W-4 violated: a non-Flate (/ASCII85Decode) stream must return "
            "the TRUE decoded payload, not the raw base-85 text"
        )
    finally:
        pdf.close()


def test_inv_w_4_multi_filter_chain_byte_identical_no_zlib_crash() -> None:
    """INV-W-4 (B): a ``[/ASCIIHexDecode /FlateDecode]`` chain decodes correctly.

    Builds a SOUND chained stream: the payload is FLATE-compressed, THEN
    hex-encoded, and stored under ``/Filter [/ASCIIHexDecode /FlateDecode]``
    — meaning a decoder applies ASCIIHexDecode first (hex -> flate bytes),
    then FlateDecode (flate bytes -> payload). The bounded read MUST equal
    ``stream.read_bytes()`` and MUST NOT raise ``zlib.error``.

    Fixture soundness is asserted explicitly: ``read_bytes()`` returns the
    payload, and ``read_raw_bytes()`` (the hex text) differs from it.

    RED reason (current buggy primitive): the impl detects the LAST filter is
    Flate, reads ``read_raw_bytes()`` (the still-HEX-encoded text), and feeds
    that hex text straight into ``zlib.decompressobj().decompress(...)`` —
    which raises an uncaught ``zlib.error`` ("Error -3 ... unknown compression
    method"). That both crashes and escapes the typed-error contract
    (INV-L-1).
    """
    pdf = pikepdf.Pdf.new()
    try:
        payload = b"multi-filter chain correctness payload " * 100
        flate_then_hex = binascii.hexlify(zlib.compress(payload)) + b">"
        s = pdf.make_stream(flate_then_hex)
        s.Filter = pikepdf.Array([pikepdf.Name("/ASCIIHexDecode"), pikepdf.Name("/FlateDecode")])

        # Fixture soundness: pikepdf's full decode returns the payload; raw differs.
        assert s.read_bytes() == payload, "fixture unsound: chained decode != payload"
        assert s.read_raw_bytes() != s.read_bytes(), "fixture unsound: raw == decoded"

        try:
            out = read_stream_bounded(s, max_decoded=MAX_FONT_STREAM_BYTES, label="font")
        except zlib.error as exc:  # pragma: no cover — RED proof on buggy impl
            pytest.fail(
                "INV-W-4 violated: a [/ASCIIHexDecode /FlateDecode] chain fed the "
                f"raw HEX text into zlib and raised an uncaught zlib.error: {exc!r}"
            )
        assert out == s.read_bytes(), (
            "INV-W-4 violated: a multi-filter [/ASCIIHexDecode /FlateDecode] "
            "stream must decode through the full filter chain (== read_bytes())"
        )
    finally:
        pdf.close()


def test_inv_w_4_flate_predictor_byte_identical_decoded() -> None:
    """INV-W-4 (C): ``/FlateDecode`` + ``/Predictor`` returns de-predicted bytes.

    Builds a ``/FlateDecode`` stream with ``/DecodeParms << /Predictor 12
    /Columns N >>`` (PNG "Up" predictor) over real row data. The bounded read
    MUST equal ``stream.read_bytes()`` (pikepdf applies the inverse predictor),
    not the raw inflated-but-still-predictor-filtered bytes.

    Fixture soundness is asserted explicitly: ``read_bytes()`` equals the true
    un-predicted payload, and the predictor-filtered inflate (raw decode) is
    longer (it carries a per-row tag byte) so it differs from the target.

    RED reason (current buggy primitive): the impl inflates via the chunked
    ``zlib`` decode and returns the inflated bytes WITHOUT applying the inverse
    PNG predictor — so it returns the per-row-tagged predictor-filtered bytes
    (45 bytes here), garbage that is both wrong-length and wrong-content versus
    the 40-byte true payload.
    """
    pdf = pikepdf.Pdf.new()
    try:
        columns = 8
        rows = [bytes((r * 7 + c) % 256 for c in range(columns)) for r in range(5)]
        s, true_data = _png_up_predictor_stream(pdf, rows, columns)

        # Fixture soundness: read_bytes() is the de-predicted payload.
        assert s.read_bytes() == true_data, "fixture unsound: predictor decode != true_data"
        assert len(true_data) == columns * len(rows)

        out = read_stream_bounded(s, max_decoded=MAX_FONT_STREAM_BYTES, label="font")
        assert out == s.read_bytes(), (
            "INV-W-4 violated: a /FlateDecode + /Predictor stream must apply the "
            "inverse predictor (== read_bytes()), not return the un-de-predicted "
            "inflated bytes"
        )
    finally:
        pdf.close()


def test_inv_w_4_nonflate_paths_no_false_refusal_and_raw_over_cap() -> None:
    """INV-W-4: filter dispatch + raw-over-cap reject + NO false refusal.

    (a) An unfiltered (``/Filter`` None) stream whose raw bytes exceed the cap
        raises.
    (b) A Flate stream whose RAW (still-compressed) payload already exceeds
        the cap raises (the raw-over-cap arm — such a payload cannot inflate
        to <= cap and still be benign).
    (c) NO FALSE REFUSAL: a benign highly-compressible Flate stream that
        decodes to ~20 MiB (well under the 32 MiB cap) is ACCEPTED and returns
        byte-identical output — the cheap pre-gates never over-reject a
        legitimately compressible font.

    (The dead ``MAX_DECOMPRESSION_RATIO`` gate is GONE: the absolute cap plus
    the incremental bound is the complete defense; a legit highly-compressible
    font under the absolute cap is already memory-safe, so a ratio gate would
    only be a false-refusal liability.)
    """
    pdf = pikepdf.Pdf.new()
    try:
        cap = _SMALL_CAP

        # (a) unfiltered raw bytes over the cap -> raises.
        big_plain = pdf.make_stream(b"Z" * (cap + 1024))
        with pytest.raises(FontStreamTooLargeError):
            read_stream_bounded(big_plain, max_decoded=cap, label="x")

        # (b) raw (compressed) payload itself larger than the cap -> raises.
        #     Random-ish incompressible bytes keep the compressed size > cap.
        import os

        incompressible = os.urandom(cap + 4096)
        raw_big = pdf.make_stream(zlib.compress(incompressible))
        raw_big.Filter = pikepdf.Name("/FlateDecode")
        with pytest.raises(FontStreamTooLargeError):
            read_stream_bounded(raw_big, max_decoded=cap, label="x")

        # (c) NO FALSE REFUSAL: benign 20 MiB decoded (under 32 MiB cap),
        #     compresses tiny but decodes under cap -> accepted, byte-identical.
        benign = b"\x00" * (20 * 1024 * 1024)
        s = _flate_stream(pdf, benign)
        out = read_stream_bounded(s, max_decoded=MAX_FONT_STREAM_BYTES, label="font")
        assert out == benign, (
            "INV-W-4 violated: a benign highly-compressible stream decoding "
            "under the cap was over-rejected (false refusal)"
        )
    finally:
        pdf.close()


def test_inv_w_4_max_tounicode_bytes_value_lock() -> None:
    """INV-W-4: the documented decoded-size caps are value-locked.

    ``MAX_FONT_STREAM_BYTES`` is 32 MiB (a 30 MiB CJK ``/FontFile2`` passes)
    and ``MAX_TOUNICODE_BYTES`` is 8 MiB (a ToUnicode CMap is far smaller than
    a font binary, so a tighter cap is correct). Pins both so a future edit
    that silently widens/narrows a cap is caught.
    """
    assert MAX_FONT_STREAM_BYTES == 32 * 1024 * 1024
    assert MAX_TOUNICODE_BYTES == 8 * 1024 * 1024


# ──────────────────────────────────────────────────────────────────────────
# E2E / TARGETED probes: the font-extension read path bounds a bombed
# /FontFile2 and the public edit verb dual-emits.
# ──────────────────────────────────────────────────────────────────────────


def test_inv_w_4_extend_subset_refuses_bombed_fontfile2(tmp_path: Path) -> None:
    """INV-W-4: ``extend_subset`` over a bombed ``/FontFile2`` REFUSES honestly.

    Targeted form (deterministic, host-font-free). A minimal Type0 /
    Identity-H font dict whose ``/FontFile2`` is a Flate bomb is opened
    THROUGH ``_pathutil.open_pdf`` (INV-L-1: never ``pikepdf.open(BytesIO)``
    outside ``_pathutil``) and fed to the public ``fonts.extend_subset``. The
    extension read of the embedded binary (``_extract_font_bytes`` on the
    Identity-H CID path) must route through the bounded reader and raise
    ``FontStreamTooLargeError`` — which is IN ``_FONT_EXTEND_FAIL_EXCS`` (it
    subclasses ``FontNotFoundError``) so every edit verb surfaces it as
    ``font_extension_failed`` rather than leaking a 50 MiB materialisation or
    a raw exception.
    """
    bomb_pdf_path = tmp_path / "bomb.pdf"
    bomb_pdf_path.write_bytes(build_flate_bomb_fontfile2_pdf())

    pdf = open_pdf(str(bomb_pdf_path))
    try:
        page = pdf.pages[0]
        # 'Z' is absent from the embedded (bombed/unparseable) font, forcing
        # the extension read path that A1.3 bounds.
        with pytest.raises(FontStreamTooLargeError):
            extend_subset(pdf, page, "F1", "Z")
    finally:
        pdf.close()


def test_inv_w_4_replace_block_bombed_fontfile2_dual_emits(tmp_path: Path) -> None:
    """INV-W-4 (D): a PUBLIC edit verb over a bombed ``/FontFile2`` dual-emits.

    Verb-level end-to-end proof (the invariant-probe BLOCKER). The bombed PDF
    is written to ``tmp_path`` and edited through the PUBLIC
    ``structural.replace_block`` verb (routed through ``_pathutil.open_pdf``
    by the verb itself — INV-L-1). The bbox covers the single ``'A'`` glyph;
    the replacement ``'Z'`` is absent from the (bombed/unparseable) embedded
    font, forcing the font-extension read of the bomb.

    The edit must REFUSE HONESTLY and surface BOTH degradations:

    * ``result.success is False`` — the edit was refused before glyph surgery;
    * ``result.fidelity_report.font_preserved is False`` — driven by the
      ``font_extension_failed`` (in ``FONT_AFFECTING_KINDS``);
    * both ``"font_stream_too_large"`` (the specific-cause warning) AND
      ``"font_extension_failed"`` (the generic error) appear in the
      degradation kinds.

    This is a font-dependency-free path: the bomb is refused by the bounded
    reader before any system-font lookup, so no skipif is needed.
    """
    bomb_pdf_path = tmp_path / "bomb.pdf"
    bomb_pdf_path.write_bytes(build_flate_bomb_fontfile2_pdf())
    out_path = tmp_path / "out.pdf"

    # The 'A' glyph is shown at Tm (72, 720), size 24 (width 600/1000*24 = 14.4),
    # so this bbox covers it. Replacing with 'Z' forces the font-extension path.
    bbox = (60.0, 700.0, 200.0, 760.0)
    result = replace_block(str(bomb_pdf_path), 0, bbox, "Z", str(out_path))

    assert result.success is False, (
        "INV-W-4 violated: a bombed /FontFile2 edit must refuse (success=False)"
    )
    assert result.fidelity_report.font_preserved is False, (
        "INV-W-4 violated: font_preserved must be False (driven by font_extension_failed)"
    )
    kinds = [d.kind for d in result.fidelity_report.degradations]
    assert "font_stream_too_large" in kinds, (
        f"INV-W-4 violated: missing the specific-cause font_stream_too_large "
        f"degradation; got {kinds!r}"
    )
    assert "font_extension_failed" in kinds, (
        f"INV-W-4 violated: missing the font_extension_failed degradation; got {kinds!r}"
    )


def test_inv_w_4_bombed_fontfile2_is_in_fail_tuple() -> None:
    """INV-W-4: ``FontStreamTooLargeError`` sits in ``_FONT_EXTEND_FAIL_EXCS``.

    The dual-emit contract relies on the bomb error being caught by the verb-
    level ``except _FONT_EXTEND_FAIL_EXCS`` so the edit degrades to
    ``font_extension_failed`` (+ the new ``font_stream_too_large`` sibling)
    rather than crashing. Subclassing ``FontNotFoundError`` is what makes that
    automatic — pin it so a future refactor that moves the class out of the
    hierarchy is caught.
    """
    assert issubclass(FontStreamTooLargeError, FontNotFoundError)
    assert any(
        isinstance(exc, type) and issubclass(FontStreamTooLargeError, exc)
        for exc in _FONT_EXTEND_FAIL_EXCS
    ), (
        "INV-W-4 violated: FontStreamTooLargeError is not covered by "
        f"_FONT_EXTEND_FAIL_EXCS={_FONT_EXTEND_FAIL_EXCS!r}"
    )


def test_inv_w_4_make_flate_bomb_stream_decodes_to_bomb_size() -> None:
    """Fixture sanity: the builder's bomb stream really inflates to the bomb size.

    Guards the FIXTURE itself (not the engine): the compressed payload must be
    small while the declared decoded size is the 50 MiB bomb. A fixture
    regression (e.g. the payload silently shrinking) would make the e2e probe
    pass for the wrong reason once the fix lands.
    """
    pdf = pikepdf.Pdf.new()
    try:
        s = make_flate_bomb_stream(pdf, with_length1=True)
        assert str(s.Filter) == "/FlateDecode"
        assert int(s.Length1) == BOMB_DECODED_BYTES
        # The RAW compressed payload is tiny (a few KiB) for all-zeros.
        assert len(s.read_raw_bytes()) < 1 * 1024 * 1024, (
            "fixture regression: the compressed bomb payload is unexpectedly "
            "large (it should be a few KiB of compressed zeros)"
        )
    finally:
        pdf.close()
