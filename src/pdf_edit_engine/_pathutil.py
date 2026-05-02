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

import os
from pathlib import Path

import pikepdf

from pdf_edit_engine.errors import PDFEditError


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


def validate_output_path(path: str) -> None:
    """Validate that an output file path is safe to write to.

    Refuses empty paths, paths whose resolved target is an existing
    directory, paths whose parent directory does not exist, and paths
    that traverse a symlink (or, on Windows, a directory junction) at
    any point in the chain. The link-traversal check enforces the
    long-documented contract that ``../../etc/passwd``-style traversal
    cannot redirect engine writes to a location the caller did not
    intend.

    Args:
        path: Output file path string.

    Raises:
        PDFEditError: If any check fails.
    """
    if not path:
        raise PDFEditError("Output path must not be empty")
    if _path_traverses_link(path):
        raise PDFEditError(
            f"Output path traverses a symlink or junction (refused for safety): {path}"
        )
    p = Path(path).resolve()
    if p.is_dir():
        raise PDFEditError(f"Output path is an existing directory: {path}")
    if not p.parent.exists():
        raise PDFEditError(f"Parent directory does not exist: {p.parent}")


def validate_output_dir(path: str) -> None:
    """Validate that an output directory path is safe to write to.

    Args:
        path: Output directory path string.

    Raises:
        PDFEditError: If path is empty, points to an existing regular
            file, or traverses a symlink/junction.
    """
    if not path:
        raise PDFEditError("Output directory must not be empty")
    if _path_traverses_link(path):
        raise PDFEditError(
            f"Output directory traverses a symlink or junction (refused for safety): {path}"
        )
    p = Path(path).resolve()
    if p.is_file():
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
        raise PDFEditError(f"Cannot open PDF: {exc}") from None
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
        raise PDFEditError(f"I/O error opening PDF: {exc}") from None
