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

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import pikepdf

from pdf_edit_engine.errors import PDFEditError

logger = logging.getLogger(__name__)


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


def _save_pdf(pdf: pikepdf.Pdf, output_path: str | Path, **save_kwargs: Any) -> None:
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

    Args:
        pdf: An open ``pikepdf.Pdf`` to serialize.
        output_path: Filesystem path where the PDF will be written.
        **save_kwargs: Forwarded verbatim to ``pikepdf.Pdf.save``.
            Reserve for cases (encryption, linearize) where the
            underlying API requires them; the common path passes none.

    Raises:
        PDFEditError: For any save-time failure — permission denied,
            target is a directory, target's parent vanished mid-flight,
            disk full, sharing violation, pikepdf serialization failure.
    """
    try:
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
