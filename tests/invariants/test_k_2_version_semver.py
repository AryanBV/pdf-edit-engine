"""INV-K-2: pdf_edit_engine.__version__ matches semver."""

from __future__ import annotations

import re

import pdf_edit_engine

SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+].+)?")


def test_inv_k_2_version_is_semver() -> None:
    """pdf_edit_engine.__version__ matches the semver pattern \\d+\\.\\d+\\.\\d+(?:[-+].+)?."""
    v = pdf_edit_engine.__version__
    assert isinstance(v, str), f"__version__ is not a string: {type(v).__name__}"
    assert SEMVER.fullmatch(v), f"__version__ {v!r} is not a valid semver"
