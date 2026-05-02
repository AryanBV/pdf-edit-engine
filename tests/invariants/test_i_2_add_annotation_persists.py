"""INV-I-2: add_annotation persists the URI on the page."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import add_annotation, get_annotations

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_i_2_add_annotation_persists(reportlab_simple: Path, tmp_path: Path) -> None:
    """`add_annotation(p, page=0, uri="https://example.com/test", rect=(...), out)`
    then `get_annotations(out)` includes one with `uri == "https://example.com/test"`."""
    src = str(reportlab_simple)
    out = str(tmp_path / "i2_added.pdf")
    target_uri = "https://example.com/test"
    rect = (72.0, 600.0, 240.0, 620.0)

    add_annotation(src, page=0, rect=rect, uri=target_uri, output_path=out)
    annots = get_annotations(out)
    uris = [a.uri for a in annots]
    assert target_uri in uris, f"expected URI {target_uri!r} not in annotations: {uris!r}"
