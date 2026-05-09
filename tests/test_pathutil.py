"""Tests for output path validation utilities."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from pdf_edit_engine._pathutil import validate_output_dir, validate_output_path
from pdf_edit_engine.errors import PDFEditError


class TestValidateOutputPath:
    def test_empty_string_raises(self) -> None:
        with pytest.raises(PDFEditError, match="must not be empty"):
            validate_output_path("")

    def test_directory_as_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PDFEditError, match="existing directory"):
            validate_output_path(str(tmp_path))

    def test_missing_parent_raises(self, tmp_path: Path) -> None:
        bad_path = str(tmp_path / "nonexistent_dir" / "output.pdf")
        with pytest.raises(PDFEditError, match="Parent directory does not exist"):
            validate_output_path(bad_path)

    def test_valid_path_passes(self, tmp_path: Path) -> None:
        valid_path = str(tmp_path / "output.pdf")
        validate_output_path(valid_path)  # Should not raise

    def test_existing_file_passes(self, tmp_path: Path) -> None:
        existing = tmp_path / "existing.pdf"
        existing.write_bytes(b"")
        validate_output_path(str(existing))  # Overwrite is fine


class TestValidateOutputDir:
    def test_empty_string_raises(self) -> None:
        with pytest.raises(PDFEditError, match="must not be empty"):
            validate_output_dir("")

    def test_file_as_dir_raises(self, tmp_path: Path) -> None:
        existing = tmp_path / "a_file.txt"
        existing.write_bytes(b"")
        with pytest.raises(PDFEditError, match="existing file"):
            validate_output_dir(str(existing))

    def test_existing_dir_passes(self, tmp_path: Path) -> None:
        validate_output_dir(str(tmp_path))  # Should not raise

    def test_nonexistent_dir_passes(self, tmp_path: Path) -> None:
        new_dir = str(tmp_path / "new_subdir")
        validate_output_dir(new_dir)  # Should not raise — will be created


# ---------------------------------------------------------------------------
# F-W21-MERGED: Windows path validation probes.
#
# Skipped on non-Windows because the validator no-ops on POSIX. On
# Windows, every probe asserts a refusal (or, for ``allow_unc=True``,
# that the UNC class is bypassed without altering the other checks).
# ---------------------------------------------------------------------------


_WIN_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path validation")


@_WIN_ONLY
class TestWindowsReservedNames:
    def test_reject_reserved_name_con(self, tmp_path: Path) -> None:
        with pytest.raises(PDFEditError, match="reserved device name"):
            validate_output_path(str(tmp_path / "CON.pdf"))

    def test_reject_reserved_name_case_insensitive(self, tmp_path: Path) -> None:
        for name in ("con.pdf", "Con.pdf", "AUX.pdf", "Lpt1.txt", "nul", "PRN.out"):
            with pytest.raises(PDFEditError, match="reserved device name"):
                validate_output_path(str(tmp_path / name))

    def test_reject_reserved_name_in_intermediate_component(self, tmp_path: Path) -> None:
        # ``CON`` as a directory component, not just leaf — also refused.
        with pytest.raises(PDFEditError, match="reserved device name"):
            validate_output_path(str(tmp_path / "CON" / "ok.pdf"))

    def test_non_reserved_lookalike_passes(self, tmp_path: Path) -> None:
        # ``CONFIG.pdf`` is not reserved (regex anchors to whole component).
        validate_output_path(str(tmp_path / "CONFIG.pdf"))


@_WIN_ONLY
class TestWindowsAlternateDataStreams:
    def test_reject_alt_data_stream_after_drive(self, tmp_path: Path) -> None:
        # Drive-letter colon at index 1, second colon embedded in leaf.
        bad = str(tmp_path / "out.pdf") + ":hidden"
        with pytest.raises(PDFEditError, match="Alt Data Stream"):
            validate_output_path(bad)

    def test_reject_alt_data_stream_relative(self) -> None:
        # No drive letter; bare ``:`` in leaf is still an ADS marker.
        with pytest.raises(PDFEditError, match="Alt Data Stream"):
            validate_output_path("out.pdf:hidden")


@_WIN_ONLY
class TestWindowsExtendedPathPrefix:
    def test_reject_extended_path_prefix_backslash(self) -> None:
        with pytest.raises(PDFEditError, match="extended-path"):
            validate_output_path(r"\\?\C:\foo.pdf")

    def test_reject_extended_path_prefix_forward_slash(self) -> None:
        with pytest.raises(PDFEditError, match="extended-path"):
            validate_output_path("//?/C:/foo.pdf")


@_WIN_ONLY
class TestWindowsUNC:
    def test_reject_unc_by_default(self) -> None:
        with pytest.raises(PDFEditError, match="UNC"):
            validate_output_path(r"\\server\share\out.pdf")

    def test_allow_unc_opt_in_passes_unc_check(self) -> None:
        # The UNC check itself must be bypassed when allow_unc=True.
        # The path may still fail later checks (resolve / parent.exists)
        # because the UNC share doesn't exist — we only assert that the
        # UNC-refusal message is NOT what fires.
        try:
            validate_output_path(r"\\server\share\out.pdf", allow_unc=True)
        except PDFEditError as e:
            assert "UNC" not in str(e), f"UNC check should be bypassed: {e}"

    def test_validate_output_dir_unc_default_refused(self) -> None:
        with pytest.raises(PDFEditError, match="UNC"):
            validate_output_dir(r"\\server\share\subdir")
