"""INV-F-6 (P1): sequential ``batch_replace_block`` tolerates a failing
first section without mis-positioning subsequent successes.

The failure branches inside ``_replace_block_on_page`` return
``last_y = bbox[3]`` (top of the failed bbox) as a placeholder. Pre-
audit, the sequential-mode loop unconditionally updated
``prev_last_line_y`` from each iteration's last_y, propagating the
placeholder into the next iteration's ``first_line_y_override`` and
mis-positioning subsequent successful sections by an arbitrary amount.

The fix gates the update on ``result.success``; failed sections leave
the running cursor unchanged so the next section uses its own bbox-
derived default position.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine import batch_replace_block, get_text_layout

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_f_6_sequential_failure_does_not_misposition_next(
    reportlab_simple: Path,
    tmp_path: Path,
) -> None:
    """Sequential mode: a failed first section must not propagate its
    bbox-top placeholder into the next section's positioning cursor."""
    if not reportlab_simple.exists():
        pytest.skip("reportlab_simple missing")

    out = tmp_path / "out.pdf"
    # First bbox in empty whitespace at the bottom of the page —
    # forces the "No content found in specified bounding box" failure
    # path. last_y = empty_bbox[3] = 30.0 placeholder is what we're
    # guarding against.
    empty_bbox = (10.0, 10.0, 30.0, 30.0)
    # Second bbox with real text near the top of the page.
    real_bbox = (72.0, 700.0, 540.0, 720.0)

    results = batch_replace_block(
        str(reportlab_simple),
        page_number=0,
        replacements=[(empty_bbox, "X"), (real_bbox, "RegressionGuardF6")],
        output_path=str(out),
        section_gap=12.0,
        line_height=14.0,
    )

    assert len(results) == 2
    # First section must fail cleanly with a reason.
    assert results[0].success is False, "expected empty bbox to produce success=False"
    assert any("No content" in w for w in results[0].warnings), (
        f"failure reason missing from warnings: {results[0].warnings}"
    )
    # Second section must succeed despite the first section's failure.
    # Pre-fix: prev_last_line_y was set to empty_bbox[3] = 30.0 after
    # the failure; the next iteration's ffly = 30.0 - 12.0 = 18.0,
    # mis-positioning the replacement near the page bottom.
    assert results[1].success, f"second section should succeed; got: {results[1].warnings}"

    # Verify the replacement landed in the page area where real_bbox
    # lives (y near 700), not near the failed bbox (y near 18).
    blocks = get_text_layout(str(out))
    matched = [b for b in blocks if "RegressionGuardF6" in b.text]
    if not matched:
        # Text may have been split across blocks (per-glyph emission);
        # accept any block whose text starts with the marker prefix.
        matched = [b for b in blocks if b.text.startswith("Regression")]

    assert matched, "RegressionGuardF6 marker not found in output text layout"
    # All matched blocks must be in the upper half of the letter page
    # (y > 100). Pre-fix mis-position would put them near y=18.
    for block in matched:
        assert block.y > 100, (
            f"replacement text at y={block.y:.1f} suggests sequential-mode "
            f"misposition: should be near real_bbox[3]={real_bbox[3]:.1f}, "
            f"NOT near failed empty_bbox[3]={empty_bbox[3]:.1f}"
        )
