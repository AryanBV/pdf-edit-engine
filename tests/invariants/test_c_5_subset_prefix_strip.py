"""INV-C-5: find_font strips the 6-letter subset prefix before lookup."""

from __future__ import annotations

import pytest

from pdf_edit_engine.fonts import _strip_subset_prefix
from pdf_edit_engine.system_fonts import find_font


def test_inv_c_5_strip_subset_prefix_pure() -> None:
    """_strip_subset_prefix('ABCDEF+Calibri') == 'Calibri' and is the identity for non-prefixed."""
    assert _strip_subset_prefix("ABCDEF+Calibri") == "Calibri"
    assert _strip_subset_prefix("ABCDEF+Calibri-Bold") == "Calibri-Bold"
    # Lowercase or non-alpha prefix → not a subset prefix, returned as-is
    assert _strip_subset_prefix("abcdef+Calibri") == "abcdef+Calibri"
    assert _strip_subset_prefix("Calibri") == "Calibri"


def test_inv_c_5_find_font_strips_prefix_consistent() -> None:
    """find_font('ABCDEF+Calibri') returns the same path as find_font('Calibri').

    Either both resolve to the same path, or both are None. Asymmetric behaviour
    would mean a subset-prefixed lookup is broken or extra-permissive.
    """
    bare = find_font("Calibri")
    prefixed = find_font("ABCDEF+Calibri")
    if bare is None and prefixed is None:
        # Acceptable: both unavailable. Try a font likely present cross-platform.
        bare2 = find_font("Arial")
        prefixed2 = find_font("ABCDEF+Arial")
        if bare2 is None and prefixed2 is None:
            pytest.skip("Neither Calibri nor Arial installed; cannot verify prefix strip")
        assert bare2 == prefixed2, (
            f"prefix-strip asymmetry: 'Arial' -> {bare2!r}, 'ABCDEF+Arial' -> {prefixed2!r}"
        )
        return
    assert bare == prefixed, (
        f"prefix-strip asymmetry: 'Calibri' -> {bare!r}, 'ABCDEF+Calibri' -> {prefixed!r}"
    )
