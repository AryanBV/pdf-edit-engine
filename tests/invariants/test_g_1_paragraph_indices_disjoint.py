"""INV-G-1: detect_paragraphs returns paragraphs with pairwise-disjoint operator_indices."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdf_edit_engine import detect_paragraphs

if TYPE_CHECKING:
    from pathlib import Path


def test_g_1_paragraph_indices_disjoint(gdocs_document: Path) -> None:
    """detect_paragraphs(pdf, 0) returns paragraphs whose operator_indices are pairwise disjoint."""
    paragraphs = detect_paragraphs(str(gdocs_document), 0)
    assert len(paragraphs) >= 1, "fixture has no paragraphs?"
    total_individual = sum(len(p.operator_indices) for p in paragraphs)
    union: set[int] = set()
    for p in paragraphs:
        union.update(p.operator_indices)
    assert len(union) == total_individual, (
        f"paragraphs share operator indices: union={len(union)} vs "
        f"sum={total_individual} (overlap={total_individual - len(union)})"
    )
