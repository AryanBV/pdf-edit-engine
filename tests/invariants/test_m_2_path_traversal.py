"""INV-M-2: validate_output_path rejects path traversal before any I/O.

v0.1.2 audit closed the gap, then a follow-up security review caught
that the first fix was logically dead (Path.resolve() followed
symlinks before the parent-walk symlink check ever ran, and on Windows
Path.is_symlink() does not detect directory junctions). This probe
verifies the corrected implementation by:

1. Always: empty path, existing-dir, missing-parent rejection.
2. POSIX: building a real symlink in tmp_path and asserting refusal.
3. Windows: building a directory junction (``mklink /J``, no admin)
   and asserting refusal — junctions defeat ``Path.is_symlink()``
   so this exercises the realpath-vs-abspath check that replaced
   the broken parent-walk.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine._pathutil import validate_output_path
from pdf_edit_engine.errors import PDFEditError

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_m_2_empty_path_raises() -> None:
    """validate_output_path('') raises a PDFEditError subclass."""
    with pytest.raises(PDFEditError):
        validate_output_path("")


def test_inv_m_2_existing_dir_raises(tmp_path: Path) -> None:
    """An output path pointing at an existing directory is refused."""
    with pytest.raises(PDFEditError, match="existing directory"):
        validate_output_path(str(tmp_path))


def test_inv_m_2_missing_parent_raises(tmp_path: Path) -> None:
    """An output path whose parent directory does not exist is refused."""
    deep = tmp_path / "does_not_exist" / "out.pdf"
    with pytest.raises(PDFEditError, match="Parent directory does not exist"):
        validate_output_path(str(deep))


def _make_link_or_junction(real_target: Path, link_path: Path) -> bool:
    """Create a directory link at *link_path* pointing to *real_target*.

    Returns True on success, False if the host doesn't support either
    a symlink (POSIX) or a junction (Windows). Junctions on Windows
    do not require Developer Mode or admin, so this nearly always
    succeeds on Windows — an important property for CI coverage.
    """
    if sys.platform == "win32":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(real_target)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    try:
        os.symlink(real_target, link_path, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


def test_inv_m_2_link_traversal_raises(tmp_path: Path) -> None:
    """An output path that traverses a symlink/junction is refused.

    Constructs a real link in tmp_path (POSIX symlink or Windows
    junction) and asks the validator to accept a path that goes
    through it. Must reject — not by leaf-only is_symlink (which
    misses junctions on Windows), but by the realpath-vs-abspath
    comparison which catches both link types on both platforms.
    """
    real_target = tmp_path / "real_dir"
    real_target.mkdir()
    link = tmp_path / "link_to_real"
    if not _make_link_or_junction(real_target, link):
        pytest.skip("link/junction creation not supported on this host")

    output_through_link = link / "out.pdf"
    with pytest.raises(PDFEditError, match="symlink"):
        validate_output_path(str(output_through_link))


def test_inv_m_2_dotdot_traversal_into_existing_dir(tmp_path: Path) -> None:
    """A ``..``-traversal that reaches an existing dir is normalized
    away by abspath/realpath alike, so it is not a "symlink" finding.

    This documents the design choice: the engine refuses **link**
    traversal (the silent attack vector); ``..`` is benign because
    it shows up in both abspath and realpath consistently. A caller
    that wants to forbid all parent-relative output paths should
    layer that check on top.
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    # path through .. — both abspath and realpath produce tmp_path/out.pdf
    benign = str(sub / ".." / "out.pdf")
    # Should NOT raise the symlink-traversal error.
    # (May still raise for other reasons in some envs; we only assert
    # the specific message is absent.)
    try:
        validate_output_path(benign)
    except PDFEditError as e:
        assert "symlink" not in str(e), f"unexpected symlink rejection on benign ..: {e}"
