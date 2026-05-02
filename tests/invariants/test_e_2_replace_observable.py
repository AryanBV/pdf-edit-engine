"""INV-E-2: replace_all output contains ≥ as many replacements as inputs had originals."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import get_text, replace_all

if TYPE_CHECKING:
    from pathlib import Path


def test_e_2_replace_observable(reportlab_simple: Path, tmp_path: Path) -> None:
    """get_text(out).count(new) >= get_text(p).count(old) after replace_all."""
    out = tmp_path / "out.pdf"
    original_text = get_text(str(reportlab_simple))
    # "and" appears 3 times in reportlab_simple; pick a same-length token to
    # avoid triggering reflow (we are testing observability, not reflow).
    old, new = "and", "but"
    original_count = original_text.count(old)
    assert original_count >= 1, "test fixture changed: 'and' no longer in reportlab_simple"
    results = replace_all(str(reportlab_simple), old, new, str(out))
    assert len(results) >= 1
    new_text = get_text(str(out))
    assert new_text.count(new) >= original_count, (
        f"observability violated: orig had {original_count} '{old}', "
        f"output has only {new_text.count(new)} '{new}'"
    )
