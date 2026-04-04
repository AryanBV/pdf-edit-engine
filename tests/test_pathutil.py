"""Tests for output path validation utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

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
