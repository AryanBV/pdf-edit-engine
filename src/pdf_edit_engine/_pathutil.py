"""Internal path validation utilities for output file/directory paths."""

from __future__ import annotations

from pathlib import Path

from pdf_edit_engine.errors import PDFEditError


def validate_output_path(path: str) -> None:
    """Validate that an output file path is safe to write to.

    Args:
        path: Output file path string.

    Raises:
        PDFEditError: If path is empty, points to an existing directory,
            has a parent directory that does not exist, or resolves to
            a symlink target outside the parent directory.
    """
    if not path:
        raise PDFEditError("Output path must not be empty")
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
        PDFEditError: If path is empty or points to an existing regular file.
    """
    if not path:
        raise PDFEditError("Output directory must not be empty")
    p = Path(path).resolve()
    if p.is_file():
        raise PDFEditError(f"Output directory path is an existing file: {path}")
