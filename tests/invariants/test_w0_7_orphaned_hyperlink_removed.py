"""INV-W0-7 (P1): orphaned hyperlinks (no URI keyword overlap with new text) are removed."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine import add_hyperlink, get_annotations, replace_block

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_w0_7_orphaned_hyperlink_removed(
    reportlab_simple: Path,
    tmp_path: Path,
) -> None:
    """When replace_block replaces a region containing a hyperlinked
    word with new text whose keywords don't appear in the URI, the
    annotation must be deleted (not left dangling pointing at deleted
    glyphs)."""
    if not reportlab_simple.exists():
        pytest.skip("reportlab_simple missing")

    annotated = tmp_path / "annotated.pdf"
    out = tmp_path / "out.pdf"

    # Add a hyperlink whose URI references "github" — a token that
    # will NOT appear in our replacement text.
    bbox = (72.0, 700.0, 540.0, 730.0)
    add_hyperlink(
        str(reportlab_simple),
        page=0,
        bbox=bbox,
        uri="https://github.com/example/should-be-orphaned",
        output_path=str(annotated),
    )
    pre_count = sum(
        1 for a in get_annotations(str(annotated), page=0) if "github.com" in (a.uri or "")
    )
    if pre_count == 0:
        pytest.skip("hyperlink didn't persist")

    # Replace the bbox with text that has no keyword overlap with the URI.
    replace_block(
        str(annotated),
        page_number=0,
        bbox=bbox,
        new_text="Replacement copy without any link keywords",
        output_path=str(out),
    )

    post_count = sum(1 for a in get_annotations(str(out), page=0) if "github.com" in (a.uri or ""))
    assert post_count == 0, (
        f"orphaned hyperlink not removed: {post_count} github.com link(s) "
        f"remain after replace_block dropped the linked text"
    )
