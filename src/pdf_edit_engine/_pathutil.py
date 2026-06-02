"""Internal path validation utilities for output file/directory paths.

Also hosts ``open_pdf``, the single canonical entry point for opening a
PDF file. Routing every public-API entrypoint through this helper is
how we close INV-L-1 / INV-M-1 / INV-M-4 / INV-M-5: pikepdf and
filesystem exceptions are translated into ``PDFEditError`` subclasses
in exactly one place. New modules cannot accidentally re-introduce the
leak — calling ``pikepdf.Pdf.open`` directly inside this package is a
violation of architectural intent.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import pikepdf
from pikepdf.models import PdfParsingError

from pdf_edit_engine.errors import (
    EncodingError,
    FontStreamTooLargeError,
    OperatorError,
    PDFEditError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


# A1.3 / INV-W-4: decoded-size bounds for embedded font / CMap streams (Flate
# decompression-bomb guard). These cap the DECOMPRESSED size of a SINGLE
# embedded stream, not the whole document — a 30 MiB CJK ``/FontFile2`` passes,
# but a few-KiB payload that inflates to tens of MiB is refused before the full
# decode. The constants live here (beside the primitive that enforces them)
# rather than in ``fonts.py`` so ``encoding.py`` can import them without a
# ``encoding -> fonts`` cycle; ``fonts.py`` re-exports them.
MAX_FONT_STREAM_BYTES = 32 * 1024 * 1024
MAX_TOUNICODE_BYTES = 8 * 1024 * 1024

_FLATE = pikepdf.Name("/FlateDecode")


def _normalize_to_list(value: Any) -> list[Any]:
    """Normalise a PDF scalar-or-array into a Python list.

    A ``/Filter`` (or ``/DecodeParms``) entry may be either a single object
    (one filter / one parms dict) or an array of them. This collapses both
    shapes to a list so callers iterate uniformly. ``None`` becomes ``[]``.

    Args:
        value: A pikepdf object that may be a single value or an array.

    Returns:
        A list of the contained objects (empty if ``value`` is ``None``).
    """
    if value is None:
        return []
    if isinstance(value, (pikepdf.Array, list)):
        # Index-based (not ``list(value)`` / a comprehension): pikepdf's
        # ``Array.__iter__`` is typed as a union that confuses mypy, but
        # ``len()`` + ``[]`` is well-typed and works for both Array and list.
        return [value[i] for i in range(len(value))]
    return [value]


def _stream_filters(stream: pikepdf.Object) -> list[Any]:
    """Return the stream's ``/Filter`` chain as a normalised list.

    Args:
        stream: The pikepdf stream ``Object``.

    Returns:
        The list of filter ``Name`` objects (empty for an unfiltered stream).
    """
    return _normalize_to_list(stream.get("/Filter"))


def _stream_has_predictor(stream: pikepdf.Object) -> bool:
    """Return True if any ``/DecodeParms`` (or ``/DP``) declares a predictor.

    A ``/Predictor`` greater than 1 means the inflated bytes are PNG/TIFF
    predictor-filtered and must be run through the inverse predictor before
    they are the true payload — the chunked ``zlib``-only path cannot do that,
    so such a stream goes through pikepdf's complete decode instead.

    Args:
        stream: The pikepdf stream ``Object``.

    Returns:
        True when a predictor (> 1) is declared on any parms dict.
    """
    parms = stream.get("/DecodeParms")
    if parms is None:
        parms = stream.get("/DP")
    for p in _normalize_to_list(parms):
        if isinstance(p, (pikepdf.Dictionary, dict)):
            predictor = p.get("/Predictor")
            if predictor is None:
                continue
            try:
                if int(predictor) > 1:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def read_stream_bounded(
    stream: pikepdf.Object,
    *,
    max_decoded: int,
    label: str,
) -> bytes:
    """Read a pikepdf stream's decoded bytes with a hard decoded-size bound.

    Drop-in replacement for ``stream.read_bytes()`` that refuses a Flate
    *decompression bomb* — a small compressed payload that inflates to an
    enormous decoded size — before the full decode materialises in memory.
    On a benign stream the returned bytes are byte-identical to
    ``stream.read_bytes()``; the bound is invisible on legitimate input.

    DUAL-PATH design — correctness is never traded for the memory bound:

    * **Single ``/FlateDecode``, no predictor** (the documented Flate-bomb
      vector — the encoding of ~all real font / CMap streams): a CHUNKED
      incremental ``zlib`` decode runs that aborts the instant the running
      output crosses the cap, so the peak resident output stays far below the
      cap on a bomb (the single-shot ``d.decompress(raw, cap+1)`` alternative
      peaks ~2x the cap and is rejected by the memory-proof probe).
    * **Unfiltered** (no ``/Filter``): the raw bytes ARE the decoded bytes, so
      the raw length is bounded directly (no zlib).
    * **Everything else** — a non-Flate single filter (``/ASCIIHexDecode`` /
      ``/ASCII85Decode`` / ``/LZWDecode`` / ...), a multi-filter chain
      (``[/ASCIIHexDecode /FlateDecode]``), or ``/FlateDecode`` + a
      ``/Predictor`` — goes CORRECTNESS-FIRST through pikepdf's complete,
      filter-and-predictor-correct ``stream.read_bytes()`` decode, and then
      bounds the decoded length. These exotic encodings are NOT used by real
      font / CMap streams; an oversize one is still refused. RESIDUAL: for a
      bomb-capable exotic chain, ``read_bytes()`` may transiently expand the
      payload BEFORE the post-decode size check (re-implementing every PDF
      codec — and the inverse predictors — incrementally is out of scope and
      deferred hardening). The chunked path above is the hard memory bound for
      the one vector real fonts actually use.

    A ``/Length1`` declared-decompressed-size PRE-GATE runs first on every
    path: an oversize declared size refuses cheaply before any decode.

    The bound predicate is strict ``>``: a decoded size of exactly
    ``max_decoded`` PASSES; one byte over RAISES.

    The detail string is generic (F-C-03 / INV-W0-9: never echo attacker-
    controlled stream bytes into user-visible text) — a fixed caller label
    plus the cap value.

    Args:
        stream: The pikepdf stream ``Object`` to read.
        max_decoded: The inclusive decoded-size cap, in bytes.
        label: A fixed caller tag (e.g. ``"font"`` / ``"ToUnicode"`` /
            ``"CIDToGIDMap"``) used only in the generic refusal message.

    Returns:
        The decoded stream bytes (byte-identical to ``stream.read_bytes()`` on
        a benign stream).

    Raises:
        FontStreamTooLargeError: If the decoded size exceeds ``max_decoded``
            (or a cheap pre-gate proves it must). A ``FontNotFoundError``
            subclass, so it is automatically inside
            ``fonts._FONT_EXTEND_FAIL_EXCS``.
        EncodingError: If the stream is CORRUPT (not oversized) and the decode
            fails — a raw ``zlib.error`` on the chunked Flate arm, or a raw
            pikepdf decode error (``pikepdf.DataDecodingError`` /
            ``pikepdf.PdfError``) on the ``read_bytes()`` arm. A1.3 / INV-L-1:
            the decode failure is translated AT this single font/CMap read
            chokepoint into a typed ``PDFEditError`` (``EncodingError`` is also
            inside ``fonts._FONT_EXTEND_FAIL_EXCS``), with a generic message
            (no stream bytes / no raw exception text — F-C-03 / INV-W0-9).
    """
    refusal = f"{label} stream exceeds the {max_decoded}-byte decoded-size bound (refused)"

    # 1. /Length1 declared-decompressed-size PRE-GATE (FontFile2 carries it).
    #    Runs on every path: an oversize declared size refuses before any
    #    decode. A malformed (non-int) /Length1 falls through silently.
    length1 = stream.get("/Length1")
    if length1 is not None:
        try:
            if int(length1) > max_decoded:
                raise FontStreamTooLargeError(refusal)
        except (TypeError, ValueError):
            pass

    # 2. Normalise the filter chain and detect a predictor.
    filters = _stream_filters(stream)
    has_predictor = _stream_has_predictor(stream)

    # 3. RAW bytes (still-compressed for a filtered stream; the payload for an
    #    unfiltered one). Cheap — no expansion.
    raw = stream.read_raw_bytes()

    # 4. SINGLE /FlateDecode, NO predictor — the documented Flate-bomb vector,
    #    the encoding of ~all real font/CMap streams. CHUNKED incremental decode
    #    (the load-bearing memory guarantee): never materialise more than
    #    ~``max_decoded + 1 chunk``.
    if filters == [_FLATE] and not has_predictor:
        chunk = 1024 * 1024
        decompressor = zlib.decompressobj()
        out = bytearray()
        # A1.3 / INV-L-1: a CORRUPT (not oversized) zlib payload makes
        # ``decompress`` / ``flush`` raise a raw ``zlib.error``. This is the
        # single chokepoint for every font/CMap read and ``_pathutil`` is the
        # engine's exception-translation home, so translate the decode failure
        # HERE into a typed ``EncodingError`` (a ``PDFEditError`` in
        # ``fonts._FONT_EXTEND_FAIL_EXCS``). The ``FontStreamTooLargeError``
        # size-cap raises below are NOT ``zlib.error`` subclasses, so the
        # ``except zlib.error`` cannot swallow them. The generic message carries
        # no stream bytes / no raw exception text (F-C-03 / INV-W0-9); the
        # original goes to the traceback via ``from exc``.
        try:
            out += decompressor.decompress(raw, chunk)
            while True:
                if len(out) > max_decoded:
                    raise FontStreamTooLargeError(refusal)
                if decompressor.unconsumed_tail:
                    out += decompressor.decompress(decompressor.unconsumed_tail, chunk)
                else:
                    out += decompressor.flush()
                    break
        except zlib.error as exc:
            raise EncodingError(f"{label} stream decode failed (corrupt stream)") from exc
        if len(out) > max_decoded:
            raise FontStreamTooLargeError(refusal)
        return bytes(out)

    # 5. UNFILTERED: raw IS the decoded payload; bound directly, no zlib.
    if not filters:
        if len(raw) > max_decoded:
            raise FontStreamTooLargeError(refusal)
        return raw

    # 6. EVERYTHING ELSE (non-Flate single filter, multi-filter chain, or
    #    /FlateDecode + /Predictor) — CORRECTNESS FIRST. pikepdf's complete
    #    decode applies the full filter chain and the inverse predictor; we
    #    then bound the decoded length.
    #
    #    Residual (deferred hardening): an exotic BOMB-capable chain (e.g.
    #    [/ASCIIHexDecode /FlateDecode], /LZWDecode, /RunLengthDecode) is still
    #    REFUSED here on oversize, but ``read_bytes()`` may TRANSIENTLY expand
    #    it before this size check — real font / CMap streams do not use these
    #    encodings, and reimplementing every PDF codec (and its inverse
    #    predictors) incrementally is out of scope. The chunked Flate path
    #    above is the hard memory bound for the one vector real fonts use.
    #
    #    A1.3 / INV-L-1: a CORRUPT (not oversized) non-Flate / multi-filter /
    #    predictor stream makes ``read_bytes()`` raise a raw pikepdf decode
    #    error — empirically ``pikepdf.DataDecodingError`` (which, NB, is NOT a
    #    ``pikepdf.PdfError`` subclass, so it must be named explicitly);
    #    ``pikepdf.PdfError`` is also caught for the residual case where the
    #    decode failure surfaces through that base type. Translate at the
    #    chokepoint into a typed ``EncodingError`` (a ``PDFEditError`` in
    #    ``fonts._FONT_EXTEND_FAIL_EXCS``) with a generic message (F-C-03 /
    #    INV-W0-9). The ``FontStreamTooLargeError`` size-cap raise stays OUTSIDE
    #    this ``try`` so it can never be swallowed.
    try:
        decoded = stream.read_bytes()
    except (pikepdf.DataDecodingError, pikepdf.PdfError) as exc:
        raise EncodingError(f"{label} stream decode failed (corrupt stream)") from exc
    if len(decoded) > max_decoded:
        raise FontStreamTooLargeError(refusal)
    return decoded


# F-W21-MERGED: Windows reserved device names. Case-insensitive match
# against any path component, with or without an extension. Per the
# Win32 file-naming rules, a write to ``CON.pdf`` opens the console
# device rather than the file; ``LPT1`` opens the parallel port; etc.
# A caller-controlled output path that lands on one of these silently
# redirects engine writes off-disk, which is a covert-channel /
# write-redirection vector.
_WIN_RESERVED_NAMES = re.compile(
    r"^(CON|AUX|NUL|PRN|COM[1-9]|LPT[1-9])(\.[^.]*)?$",
    re.IGNORECASE,
)


def _validate_windows_path(path: str, *, allow_unc: bool) -> None:
    """Windows-only: refuse reserved names, ADS, extended-path prefix, UNC.

    No-op on non-Windows platforms. Called as a final gate from
    ``validate_output_path`` and ``validate_output_dir`` after the
    realpath/abspath link-traversal check has already passed.

    The four classes refused (F-W21-MERGED):

    1. **Extended-path prefix** ``\\\\?\\...``. Bypasses Win32 path
       normalization and can target raw NT object paths
       (``\\\\?\\GLOBALROOT\\...``). Refused unconditionally.
    2. **UNC paths** ``\\\\server\\share\\...``. Traverse the SMB
       stack; may bypass local filesystem ACLs and reach attacker-
       controlled hosts. Refused unless ``allow_unc=True``.
    3. **Alternate Data Streams** — any ``:`` after the drive-letter
       colon (e.g. ``C:\\out.pdf:hidden``). NTFS silently writes the
       payload to a side-stream invisible to most tools.
    4. **Reserved device names** (``CON``, ``AUX``, ``NUL``, ``PRN``,
       ``COM1``-``COM9``, ``LPT1``-``LPT9``) in any path component,
       case-insensitive, with or without extension.

    Args:
        path: The output path string (already non-empty, link-safe).
        allow_unc: When True, permits UNC paths (``\\\\server\\share\\...``).
            Default False; explicit opt-in required because UNC writes
            traverse the SMB stack and may bypass local filesystem ACLs.

    Raises:
        PDFEditError: On any Windows-specific validation failure.
    """
    if sys.platform != "win32":
        return
    # 1. Extended-path prefix — refuse before UNC because ``\\?\UNC\...``
    #    matches both prefixes and the extended-path semantics dominate.
    if path.startswith("\\\\?\\") or path.startswith("//?/"):
        raise PDFEditError(f"Output path uses Windows extended-path prefix (refused): {path}")
    # 2. UNC paths. Refuse unless allow_unc=True. Both ``\\\\`` and ``//``
    #    forms must be checked because ``Path.resolve()`` may have
    #    normalized one to the other depending on cwd at call time.
    if (path.startswith("\\\\") or path.startswith("//")) and not allow_unc:
        raise PDFEditError(f"Output path is UNC; pass allow_unc=True to permit: {path}")
    # 3. Alt Data Streams: any ``:`` AFTER the drive-letter colon.
    #    e.g. ``C:\\foo\\bar.pdf``    → drive-letter colon at index 1, OK
    #    e.g. ``C:foo:bar.pdf``       → second colon at index 5, REFUSE
    #    e.g. ``out.pdf:hidden``      → no drive letter, colon present, REFUSE
    rest = path
    if len(path) >= 2 and path[1] == ":":
        rest = path[2:]
    if ":" in rest:
        raise PDFEditError(f"Output path contains Alt Data Stream marker (refused): {path}")
    # 4. Reserved device names. Check every path component (split on
    #    both ``\\`` and ``/`` to catch mixed-separator inputs).
    parts = re.split(r"[\\/]+", path)
    for part in parts:
        if _WIN_RESERVED_NAMES.match(part):
            raise PDFEditError(
                f"Output path contains Windows reserved device name (refused): {part!r} in {path}"
            )


def _path_traverses_link(path: str) -> bool:
    """Return True if *path* contains any symlink or directory junction.

    Uses ``os.path.realpath`` (follows symlinks AND Windows junctions)
    vs ``os.path.abspath`` (does not follow either). When they differ
    after case normalization, the path crossed a link of some kind.

    The previous implementation used ``Path(path).resolve()`` then
    walked parents calling ``Path.is_symlink()`` — that is dead code,
    because:
      1. ``resolve()`` follows every symlink in its argument by
         contract, so the resolved path has no symlink components left
         for a parent walk to find.
      2. Even on the raw path, ``Path.is_symlink()`` returns ``False``
         for Windows directory junctions (they carry a different
         reparse-point tag than NTFS symlinks).

    The realpath-vs-abspath comparison catches both, on both POSIX and
    Windows, without requiring a leaf or parent to exist.
    """
    try:
        real = os.path.realpath(path)
        absolute = os.path.abspath(path)
    except OSError:
        # Defensive: if either call fails (transient FS error), treat
        # it as "traversal could not be ruled out" and refuse.
        return True
    return os.path.normcase(real) != os.path.normcase(absolute)


def validate_output_path(path: str, *, allow_unc: bool = False) -> None:
    """Validate that an output file path is safe to write to.

    Refuses empty paths, paths whose resolved target is an existing
    directory, paths whose parent directory does not exist, and paths
    that traverse a symlink (or, on Windows, a directory junction) at
    any point in the chain. The link-traversal check enforces the
    long-documented contract that ``../../etc/passwd``-style traversal
    cannot redirect engine writes to a location the caller did not
    intend.

    On Windows, additionally refuses (F-W21-MERGED): reserved device
    names (``CON``, ``AUX``, ``NUL``, ``PRN``, ``COM1``-``COM9``,
    ``LPT1``-``LPT9``); paths containing Alternate Data Stream
    markers (``:`` after the drive-letter colon); and the extended-
    path prefix ``\\\\?\\``. UNC paths (``\\\\server\\share\\...``)
    are refused unless the caller passes ``allow_unc=True``. These
    checks are no-ops on POSIX.

    Args:
        path: Output file path string.
        allow_unc: When True, permits UNC paths on Windows. Default
            False; explicit opt-in is required because UNC writes
            traverse the SMB stack and may bypass local filesystem
            ACLs. No effect on non-Windows platforms.

    Raises:
        PDFEditError: If any check fails.
    """
    if not path:
        raise PDFEditError("Output path must not be empty")
    # Windows-specific checks first: reserved device names (``CON``,
    # ``NUL``, ``LPT1``, ...) are string-level rejections that must
    # fire before ``_path_traverses_link``. ``os.path.realpath`` on a
    # bare reserved name resolves it to the device (``\\.\NUL``), which
    # then differs from ``abspath`` and triggers the link-traversal
    # branch with a misleading message. The Win helper short-circuits
    # that path with the correct diagnostic.
    _validate_windows_path(path, allow_unc=allow_unc)
    if _path_traverses_link(path):
        raise PDFEditError(
            f"Output path traverses a symlink or junction (refused for safety): {path}"
        )
    try:
        p = Path(path).resolve()
        # Bundle the existence checks under the same translator: on
        # UNC / network-mount paths, ``is_dir`` and ``parent.exists``
        # can raise ``OSError`` (WinError 64 "network name no longer
        # available", ENETDOWN, ETIMEDOUT). INV-L-1 requires those be
        # surfaced as ``PDFEditError`` rather than leaking the raw
        # platform exception to callers.
        is_dir = p.is_dir()
        parent_exists = p.parent.exists()
    except (OSError, ValueError) as exc:
        raise PDFEditError(f"Invalid output path: {type(exc).__name__}") from exc
    if is_dir:
        raise PDFEditError(f"Output path is an existing directory: {path}")
    if not parent_exists:
        raise PDFEditError(f"Parent directory does not exist: {p.parent}")


def validate_output_dir(path: str, *, allow_unc: bool = False) -> None:
    """Validate that an output directory path is safe to write to.

    On Windows, additionally refuses reserved device names, ADS
    markers, the extended-path prefix, and UNC paths (unless
    ``allow_unc=True``). See :func:`validate_output_path` for the
    full rationale.

    Args:
        path: Output directory path string.
        allow_unc: When True, permits UNC paths on Windows. Default
            False. No effect on non-Windows platforms.

    Raises:
        PDFEditError: If path is empty, points to an existing regular
            file, traverses a symlink/junction, or fails any of the
            Windows-specific checks.
    """
    if not path:
        raise PDFEditError("Output directory must not be empty")
    # See ``validate_output_path`` for ordering rationale: Windows
    # reserved-name / ADS / extended-prefix / UNC checks must precede
    # the realpath-vs-abspath comparison.
    _validate_windows_path(path, allow_unc=allow_unc)
    if _path_traverses_link(path):
        raise PDFEditError(
            f"Output directory traverses a symlink or junction (refused for safety): {path}"
        )
    try:
        p = Path(path).resolve()
        # Same INV-L-1 translation as ``validate_output_path``: UNC /
        # network-mount paths can raise ``OSError`` from ``is_file``.
        is_file = p.is_file()
    except (OSError, ValueError) as exc:
        raise PDFEditError(f"Invalid output directory: {type(exc).__name__}") from exc
    if is_file:
        raise PDFEditError(f"Output directory path is an existing file: {path}")


def open_pdf(
    path: str | Path,
    *,
    password: str | bytes | None = None,
    allow_overwriting_input: bool = False,
) -> pikepdf.Pdf:
    """Open a PDF, translating pikepdf and filesystem errors to ``PDFEditError``.

    This is the **single canonical entry point** for opening a PDF in
    this package. Every public-API entrypoint (``locator.get_text``,
    ``surgeon.replace``, ``structural.replace_block``, ``wrapper.merge_pdfs``,
    etc.) must call ``open_pdf`` rather than ``pikepdf.Pdf.open``. The
    translator below is the only place where library exceptions are
    caught; routing through it guarantees no raw ``pikepdf.PasswordError``
    or ``pikepdf.PdfError`` ever reaches the user (INV-L-1).

    The signature explicitly enumerates the two pikepdf kwargs we use,
    rather than ``**kwargs``-passthrough: future pikepdf versions may
    add side-effecting kwargs (e.g. callbacks) that we do not want
    callers of this package to invoke through us implicitly. This is
    the security-hardening change applied with the v0.1.2 audit.

    Args:
        path: Path to a PDF file on disk.
        password: Decryption password, if the PDF is encrypted. Never
            logged or persisted by this helper.
        allow_overwriting_input: When ``True``, permits saving over the
            input file. pikepdf-specific; defaults to ``False`` for
            safety.

    Returns:
        An open ``pikepdf.Pdf``. The caller is responsible for closing
        it (via ``with`` or ``pdf.close()``).

    Raises:
        PDFEditError: For any open-time failure — encrypted, malformed,
            zero-byte, missing-file, permission-denied, or directory-as-file.
    """
    try:
        return pikepdf.Pdf.open(
            str(path),
            password=password if password is not None else "",
            allow_overwriting_input=allow_overwriting_input,
        )
    except pikepdf.PasswordError:
        raise PDFEditError("PDF is password-protected") from None
    except pikepdf.PdfError as exc:
        # F-C-03 / INV-W0-9: forensic detail to logs only; user-visible
        # text is the exception type name (no attacker-controlled bytes).
        logger.error("pikepdf.Pdf.open: pikepdf.PdfError", exc_info=True)
        raise PDFEditError(f"Cannot open PDF: {type(exc).__name__}") from None
    except FileNotFoundError:
        raise PDFEditError(f"PDF file not found: {Path(path).name}") from None
    except IsADirectoryError:
        raise PDFEditError("Expected a file path, got a directory") from None
    except PermissionError:
        raise PDFEditError(f"Permission denied: {Path(path).name}") from None
    except OSError as exc:
        # Catches network-FS, EBADF, ENOSPC, EIO, sharing-violations, etc.
        # INV-L-1 says no raw OSError reaches a caller; the three subclasses
        # above are the common cases — this is the residual.
        # F-C-03 / INV-W0-9: forensic detail to logs only.
        logger.error("pikepdf.Pdf.open: OSError", exc_info=True)
        raise PDFEditError(f"I/O error opening PDF: {type(exc).__name__}") from None


# A2.2 / INV-W-3: marker appended to a caller-supplied ``linearization_log``
# (and emitted as a generic detail string) when a linearized input had to be
# saved non-linearized. Kept generic on purpose — F-C-03 / INV-W0-9 forbid
# echoing attacker-controlled exception bytes into user-visible text; the
# pikepdf exception type goes to logs only.
_LINEARIZATION_DROPPED_DETAIL = "re-linearization failed; saved non-linearized"


# A2.3 / INV-W-5: marker appended to a caller-supplied ``encryption_log`` (and
# emitted as a generic detail string) when an encrypted input could NOT be
# re-encrypted at all (pikepdf raised on the encryption= save). Generic on
# purpose — F-C-03 / INV-W0-9 forbid echoing attacker-controlled exception
# bytes into user-visible text; the pikepdf exception type goes to logs only.
_ENCRYPTION_DROPPED_DETAIL = "re-encryption failed; saved unencrypted"


def _save_pdf(
    pdf: pikepdf.Pdf,
    output_path: str | Path,
    *,
    linearization_log: list[str] | None = None,
    reencrypt_password: str | bytes | None = None,
    encryption_log: list[str] | None = None,
    **save_kwargs: Any,
) -> None:
    """Save a Pdf, translating pikepdf and filesystem errors to ``PDFEditError``.

    This is the **single canonical save entry point** for this package.
    Every internal site that calls ``pdf.save(...)`` must route through
    this helper; raw ``pdf.save`` outside ``_pathutil`` is an
    architectural violation that re-introduces F-C-01 (post-validate /
    pre-save TOCTOU exposing raw ``PermissionError``).

    The signature mirrors ``open_pdf``'s narrow surface for the common
    case (positional ``pdf`` and ``output_path``) and forwards any
    additional keyword arguments to ``pikepdf.Pdf.save`` so callers
    that genuinely need ``encryption=``, ``linearize=``, etc. retain
    centralized exception translation.

    **A2.2 / INV-W-3 — linearization preservation.** A *linearized*
    ("Fast Web View") input must not be silently down-converted on save.
    This helper reads ``pdf.is_linearized`` BEFORE serializing; when True
    (and the caller did not already pass an explicit ``linearize``), it
    saves with ``linearize=True`` so the property round-trips. A
    NON-linearized input never sets the flag, so its save call is
    byte-identical to the pre-A2.2 behaviour (zero blast radius). If
    pikepdf raises ``pikepdf.PdfError`` on the ``linearize=True`` attempt,
    the helper retries ONCE with a normal (non-linearized) save so the
    edit still succeeds, and records the loss: it appends
    ``_LINEARIZATION_DROPPED_DETAIL`` to ``linearization_log`` when one was
    supplied (so an edit verb can surface a typed ``linearization_dropped``
    Degradation), else logs the drop at INFO. A genuine save failure on the
    normal-save retry still propagates through the existing PDFEditError
    translation (INV-L-1 preserved).

    **A2.3 / INV-W-5 — encryption preservation.** An *encrypted* input must
    not be silently down-converted to a plaintext output on save. This helper
    reads ``pdf.is_encrypted`` ONCE BEFORE serializing; when True (and the
    caller did not already pin an explicit ``encryption`` in ``save_kwargs``,
    so ``wrapper.encrypt_pdf``'s explicit ``encryption=`` still wins and
    ``wrapper.decrypt_pdf``'s explicit ``encryption=False`` opt-out is
    honoured), it re-encrypts the output with a ``pikepdf.Encryption`` built
    from the input's ``R`` and ``allow`` (permission bitmask) and the
    caller-supplied ``reencrypt_password`` for BOTH owner and user. A
    NON-encrypted input never sets ``encryption``, so its save call is
    byte-identical to the pre-A2.3 behaviour (zero blast radius). DOCUMENTED
    BOUNDARIES (NOT per-edit degradations): the owner password is NOT
    recoverable from an opened pikepdf document, so a distinct owner!=user pair
    collapses to the single caller password; ``/P`` is preserved STRUCTURALLY
    (mirrored via ``allow=``) but is advisory because pikepdf does not enforce
    permissions. If pikepdf raises ``pikepdf.PdfError`` on the encrypted save,
    the helper retries ONCE WITHOUT ``encryption`` (so the edit still lands)
    and appends ``_ENCRYPTION_DROPPED_DETAIL`` to ``encryption_log`` when one
    was supplied (so an edit verb can surface a typed ``encryption_dropped``
    Degradation), else logs the drop at INFO. An ``OSError`` from the encrypted
    save is NOT caught here — it falls through to the outer translator
    identically to W-3 (INV-L-1 / F-C-03: no raw OSError, no path leak).

    Args:
        pdf: An open ``pikepdf.Pdf`` to serialize.
        output_path: Filesystem path where the PDF will be written.
        linearization_log: Optional out-parameter (mirrors
            ``fonts.extend_subset``'s ``substitution_log``). When provided
            and re-linearization fell back to a normal save, a marker is
            appended so the calling edit verb can surface a
            ``linearization_dropped`` Degradation. When ``None`` (the
            default — wrapper/annotation verbs that carry no
            ``FidelityReport``), the fallback is logged only.
        reencrypt_password: The password the calling verb used to OPEN the
            (possibly encrypted) input. This is how the re-encryption password
            reaches the helper so ALL encryption logic stays inside
            ``_pathutil`` (INV-L-1 spirit). Bytes are decoded ``latin-1``
            (lossless for any byte sequence). When ``None``/``""`` on an
            encrypted input, ``pikepdf.Encryption(owner="", user="")`` is the
            honest best-effort (still structurally encrypted). Ignored for a
            non-encrypted input.
        encryption_log: Optional out-parameter mirroring
            ``linearization_log``. When provided and re-encryption fell back
            to an unencrypted save, ``_ENCRYPTION_DROPPED_DETAIL`` is appended
            so the calling edit verb can surface an ``encryption_dropped``
            Degradation. When ``None``, the fallback is logged only.
        **save_kwargs: Forwarded verbatim to ``pikepdf.Pdf.save``.
            Reserve for cases (encryption, linearize) where the
            underlying API requires them; the common path passes none.

    Raises:
        PDFEditError: For any save-time failure — permission denied,
            target is a directory, target's parent vanished mid-flight,
            disk full, sharing violation, pikepdf serialization failure.
    """
    # A2.3 / INV-W-5: detect the input's encryption once, before save, and
    # ask pikepdf to preserve it. Only when the caller has not already pinned
    # ``encryption`` explicitly — ``wrapper.encrypt_pdf`` pins its own
    # ``encryption=`` (which wins), and ``wrapper.decrypt_pdf`` pins
    # ``encryption=False`` to OPT OUT of this auto-preservation.
    was_encrypted = bool(pdf.is_encrypted)
    if was_encrypted and "encryption" not in save_kwargs:
        # The owner password is NOT recoverable from an opened pikepdf
        # document, and ``user_password`` is recoverable ONLY when the file was
        # opened with the USER password (it is ``b''`` when opened with the
        # owner password). The HONEST, deterministic choice is to re-encrypt
        # with the password the CALLER supplied, for owner AND user.
        # Consequence (documented boundary, NOT a per-edit degradation): a file
        # with a distinct owner!=user pair collapses to a single caller
        # password, and ``/P`` is preserved STRUCTURALLY (mirrored via
        # ``allow=``) but is advisory only because pikepdf does not enforce
        # permissions.
        # Pass the caller's password through WITH ITS ORIGINAL TYPE (str or
        # bytes). pikepdf.Encryption owner/user are typed ``str`` but accept
        # ``bytes`` at runtime. We must NOT decode bytes->str: pikepdf's R=6
        # (AES-256) key derivation hashes a ``bytes`` password from its RAW
        # bytes but a ``str`` password from its UTF-8 encoding, so decoding a
        # non-ASCII bytes password to str (even losslessly via latin-1) would
        # re-encrypt under a DIFFERENT key than the caller used to open the
        # file — silently locking them out of their own output (success=True,
        # no degradation). Passing the original type keeps open-time and
        # re-encrypt-time hashing identical.
        pw: str | bytes = reencrypt_password if reencrypt_password is not None else ""
        info = pdf.encryption
        # Mirror only ``R`` and ``allow``: ``bits``/``V`` are DERIVED from
        # ``R`` (R=4 -> V=4/128-bit, R=6 -> V=5/256-bit) and have no public
        # Encryption kwarg, so they round-trip from ``R`` alone.
        # ``info.R`` is an ``int`` per pikepdf's stub, but ``Encryption(R=...)``
        # narrows to ``Literal[2,3,4,5,6]``. R read from a real encrypted file is
        # always in that range, so the cast is honest (not a value override).
        save_kwargs["encryption"] = pikepdf.Encryption(
            owner=pw,  # type: ignore[arg-type]  # str|bytes ok at runtime; bytes must pass through (R=6)
            user=pw,  # type: ignore[arg-type]
            R=cast("Literal[2, 3, 4, 5, 6]", info.R),
            allow=pdf.allow,
        )

    # A2.2 / INV-W-3: detect the input's linearization once, before save, and
    # ask pikepdf to preserve it. Only when the caller has not already pinned
    # ``linearize`` explicitly (e.g. a deterministic corpus builder).
    was_linearized = bool(pdf.is_linearized)
    # True iff WE injected the ``encryption`` kwarg above (so the genuine-
    # encryption-failure fallback applies). A caller-pinned ``encryption`` (or
    # the ``encryption=False`` decrypt opt-out) is NOT a ``pikepdf.Encryption``
    # we built, so it does NOT enable the fallback.
    injected_encryption = was_encrypted and isinstance(
        save_kwargs.get("encryption"), pikepdf.Encryption
    )
    try:
        if was_linearized and "linearize" not in save_kwargs:
            try:
                pdf.save(str(output_path), linearize=True, **save_kwargs)
                return
            except pikepdf.PdfError:
                # Re-linearization failed (a can't-linearize failure, NOT an
                # IO failure): fall back to a NORMAL save below so the edit
                # still lands, and surface the loss honestly. Any ``OSError``
                # from this same attempt is NOT caught here — it falls through
                # to the outer translator below, identically to the
                # non-linearized save's IO-error path (INV-L-1 / F-C-03: no raw
                # OSError, no absolute-path leak), and emits NO
                # ``linearization_dropped`` (an IO failure is not a dropped
                # Fast Web View layout). The injected ``encryption`` is still
                # carried into the normal-save retry below, so a
                # linearize-failure alone does NOT drop encryption.
                logger.info(
                    "pdf.save(linearize=True) failed; retrying non-linearized (INV-W-3 fallback)",
                    exc_info=True,
                )
                if linearization_log is not None:
                    linearization_log.append(_LINEARIZATION_DROPPED_DETAIL)
        try:
            pdf.save(str(output_path), **save_kwargs)
        except pikepdf.PdfError:
            if not injected_encryption:
                raise
            # A2.3 / INV-W-5 honest fallback: re-encryption could not be
            # applied at all (a genuine can't-encrypt failure, NOT an IO
            # failure — an ``OSError`` is not caught here and falls to the
            # outer translator). Retry WITHOUT encryption so the edit still
            # succeeds, and record the loss so the verb surfaces a typed
            # ``encryption_dropped`` Degradation.
            logger.info(
                "pdf.save(encryption=...) failed; retrying unencrypted (INV-W-5 fallback)",
                exc_info=True,
            )
            if encryption_log is not None:
                encryption_log.append(_ENCRYPTION_DROPPED_DETAIL)
            save_kwargs.pop("encryption", None)
            pdf.save(str(output_path), **save_kwargs)
    except pikepdf.PdfError as exc:
        # F-C-03 / INV-W0-9: %s of an exception object renders str(exc),
        # which can leak attacker-controlled bytes. Use exc_info=True for
        # forensic detail in logs and the bare type name for everything
        # else.
        logger.error("pdf.save: pikepdf.PdfError", exc_info=True)
        raise PDFEditError(f"Cannot save PDF: {type(exc).__name__}") from None
    except IsADirectoryError:
        logger.error("pdf.save: IsADirectoryError on %r", str(output_path))
        raise PDFEditError("Save target is an existing directory") from None
    except PermissionError:
        logger.error("pdf.save: PermissionError on %r", str(output_path))
        raise PDFEditError(f"Permission denied saving PDF: {Path(output_path).name}") from None
    except OSError as exc:
        # F-C-03 / INV-W0-9: same rationale as the PdfError branch.
        logger.error("pdf.save: OSError", exc_info=True)
        raise PDFEditError(f"I/O error saving PDF: {type(exc).__name__}") from None


# Exception types that pikepdf's content-stream parse/unparse can raise.
# ``pikepdf.PdfError`` covers the residual ``raise e from e`` branch in
# ``parse_content_stream`` (e.g. "ignoring non-stream in an array of
# streams"). ``PdfParsingError`` — raised by ``unparse_content_stream``
# on malformed operand/operator items AND by some parse failures — is
# NOT a subclass of ``PdfError`` (it derives straight from ``Exception``),
# so it must be named explicitly or it escapes any ``except PdfError``.
# ``TypeError`` is what ``parse_content_stream`` raises when handed a
# non-stream/non-page object (its own guard clauses + the
# "supposed to be a stream or an array" remap). All three are translated
# to ``OperatorError`` per the documented contract in ``errors.py``.
_CONTENT_STREAM_PARSE_EXCS = (pikepdf.PdfError, PdfParsingError, TypeError)


@contextlib.contextmanager
def _with_content_stream_translation(context: str) -> Iterator[None]:
    """Translate pikepdf content-stream parse/unparse failures to ``OperatorError``.

    INV-B-5 / INV-L-1 family: ``pikepdf.parse_content_stream`` and
    ``pikepdf.unparse_content_stream`` can raise ``pikepdf.PdfError``,
    ``pikepdf.models.PdfParsingError`` (which is **not** a ``PdfError``
    subclass), or ``TypeError`` on a malformed or un-parseable content
    stream. Before this translator, those escaped raw to public callers
    from ``surgeon`` / ``structural`` / ``reflow`` (only ``open_pdf``
    translated *open*-time errors, and ``locator._build_index`` caught a
    narrower set that excluded both pikepdf parse types). This is the
    parse/unparse analogue of ``open_pdf``'s open-time translation and of
    ``fonts._with_fonttools_translation``'s fontTools-boundary translation.

    A forensic ``logger.error(..., exc_info=True)`` line preserves the
    original exception type and traceback even though ``{exc}`` is dropped
    from the user-visible ``OperatorError`` message (F-C-03 / INV-W0-9:
    parse failures can echo attacker-controlled content-stream bytes).

    Args:
        context: Short identifier of the call site (e.g.
            ``"surgeon.replace_all"``) — included in the forensic log
            line to localise failures.

    Raises:
        OperatorError: when any caught parse/unparse exception fires
            inside the ``with`` block. ``__cause__`` is set to the
            original exception so the chain is preserved.
    """
    try:
        yield
    except _CONTENT_STREAM_PARSE_EXCS as exc:
        logger.error("content-stream boundary [%s]", context, exc_info=True)
        raise OperatorError(f"Content stream parse/unparse failed: {type(exc).__name__}") from exc


def _parse_content_stream(target: pikepdf.Object | pikepdf.Page, *, context: str) -> list[Any]:
    """Parse a content stream, translating pikepdf failures to ``OperatorError``.

    Thin wrapper over ``pikepdf.parse_content_stream`` that routes the
    call through ``_with_content_stream_translation``. Returns the parsed
    instructions as a ``list`` (callers across the package consistently
    materialise the result, so doing it here keeps call sites uniform).

    Args:
        target: A ``pikepdf.Page`` or content-stream ``Object`` to parse.
        context: Call-site identifier for the forensic log line.

    Returns:
        List of parsed content-stream instructions.

    Raises:
        OperatorError: If pikepdf raises a parse failure.
    """
    with _with_content_stream_translation(context):
        return list(pikepdf.parse_content_stream(target))


def _unparse_content_stream(ops: list[Any], *, context: str) -> bytes:
    """Serialize content-stream ops, translating pikepdf failures to ``OperatorError``.

    Thin wrapper over ``pikepdf.unparse_content_stream`` that routes the
    call through ``_with_content_stream_translation``.

    Args:
        ops: The (operands, operator) instruction list to serialize.
        context: Call-site identifier for the forensic log line.

    Returns:
        The serialized content-stream bytes.

    Raises:
        OperatorError: If pikepdf raises an unparse failure.
    """
    with _with_content_stream_translation(context):
        return pikepdf.unparse_content_stream(ops)
