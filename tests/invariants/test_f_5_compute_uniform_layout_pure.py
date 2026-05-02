"""INV-F-5: compute_uniform_layout is pure (deterministic, no side effects)."""

from __future__ import annotations

from pdf_edit_engine import compute_uniform_layout


def test_inv_f_5_compute_uniform_layout_pure() -> None:
    """`compute_uniform_layout` is pure: same inputs → same outputs."""
    # Signature: compute_uniform_layout(region_height, line_counts,
    #   font_size=10.0, original_gap=27.0)
    args1 = (300.0, [3, 2, 4], 10.0, 27.0)
    args2 = (120.0, [5, 5], 12.0, 18.0)

    r1a = compute_uniform_layout(*args1)
    r1b = compute_uniform_layout(*args1)
    assert r1a == r1b, f"non-deterministic for {args1}: {r1a} vs {r1b}"

    r2a = compute_uniform_layout(*args2)
    r2b = compute_uniform_layout(*args2)
    assert r2a == r2b, f"non-deterministic for {args2}: {r2a} vs {r2b}"

    # Sanity: outputs are tuples of two finite floats.
    for r in (r1a, r2a):
        assert isinstance(r, tuple) and len(r) == 2, f"unexpected return: {r!r}"
        assert all(isinstance(v, float) for v in r), f"non-float entries: {r!r}"
