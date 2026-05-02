"""INV-K-1: every name in pdf_edit_engine.__all__ resolves to a real attribute."""

from __future__ import annotations

import pdf_edit_engine


def test_inv_k_1_all_names_resolve() -> None:
    """Every name listed in pdf_edit_engine.__all__ is a real attribute of the package."""
    missing = [name for name in pdf_edit_engine.__all__ if not hasattr(pdf_edit_engine, name)]
    assert not missing, f"__all__ lists names that do not resolve: {missing}"
