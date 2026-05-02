"""INV-D-2: TextMatch.matched_text == join of unicode_chars from .characters."""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_edit_engine import find


def _check(pdf_path: Path, query: str) -> None:
    matches = find(str(pdf_path), query)
    assert matches, f"no matches for {query!r} in {pdf_path.name}"
    for m in matches:
        joined = "".join(c.unicode_char for c in m.characters)
        assert joined == m.matched_text, (
            f"{pdf_path.name} q={query!r}: matched_text={m.matched_text!r} "
            f"vs joined-chars={joined!r}"
        )


def test_inv_d_2_reportlab(reportlab_simple: Path) -> None:
    """For every match m, ''.join(c.unicode_char for c in m.characters) == m.matched_text."""
    assert reportlab_simple.exists(), f"missing fixture {reportlab_simple}"
    # 'Section' is a single contiguous word, no inferred separators.
    _check(reportlab_simple, "Section")


@pytest.mark.skipif(
    not (Path(__file__).parent.parent / "corpus" / "Aryan_BV_Resume_2026.pdf").exists(),
    reason="resume_pdf not in corpus",
)
def test_inv_d_2_resume(resume_pdf: Path) -> None:
    """For every match m, ''.join(c.unicode_char for c in m.characters) == m.matched_text."""
    # 'Bangalore' is a single contiguous word in the resume.
    _check(resume_pdf, "Bangalore")
