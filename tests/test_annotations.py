"""Tests for annotations module."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine import (
    Annotation,
    delete_annotation,
    get_annotations,
    move_annotation,
    update_annotation_uri,
)

if TYPE_CHECKING:
    from pathlib import Path


RESUME = "tests/corpus/resume_aryan.pdf"


@pytest.fixture()
def tmp_pdf(tmp_path: Path) -> str:
    """Copy resume to a temp file for mutation tests."""
    dest = str(tmp_path / "resume_copy.pdf")
    shutil.copy2(RESUME, dest)
    return dest


class TestGetAnnotations:
    """Tests for get_annotations()."""

    def test_resume_has_link_annotations(self) -> None:
        annots = get_annotations(RESUME)
        assert len(annots) >= 3
        assert all(isinstance(a, Annotation) for a in annots)

    def test_link_annotations_have_uris(self) -> None:
        annots = get_annotations(RESUME)
        links = [a for a in annots if a.subtype == "Link"]
        assert len(links) >= 3
        for link in links:
            assert link.uri is not None
            assert link.uri.startswith("http")

    def test_page_filter(self) -> None:
        all_annots = get_annotations(RESUME)
        page0 = get_annotations(RESUME, page=0)
        assert len(page0) == len(all_annots)  # resume is single page
        assert all(a.page == 0 for a in page0)

    def test_annotation_rect_is_valid(self) -> None:
        annots = get_annotations(RESUME)
        for a in annots:
            x0, y0, x1, y1 = a.rect
            assert x1 > x0
            assert y1 > y0


class TestUpdateAnnotationUri:
    """Tests for update_annotation_uri()."""

    def test_change_uri(self, tmp_pdf: str, tmp_path: Path) -> None:
        annots = get_annotations(tmp_pdf)
        link = next(a for a in annots if a.subtype == "Link")
        out = str(tmp_path / "updated.pdf")
        update_annotation_uri(tmp_pdf, link, "https://example.com/new", out)

        # Verify
        updated = get_annotations(out)
        assert updated[link.index].uri == "https://example.com/new"


class TestDeleteAnnotation:
    """Tests for delete_annotation()."""

    def test_delete_link(self, tmp_pdf: str, tmp_path: Path) -> None:
        before = get_annotations(tmp_pdf)
        link = before[0]
        out = str(tmp_path / "deleted.pdf")
        delete_annotation(tmp_pdf, link, out)

        after = get_annotations(out)
        assert len(after) == len(before) - 1

    def test_delete_all_links(self, tmp_pdf: str, tmp_path: Path) -> None:
        """Delete annotations one at a time (re-reading between each)."""
        out = str(tmp_path / "no_links.pdf")
        shutil.copy2(tmp_pdf, out)
        while True:
            annots = get_annotations(out)
            links = [a for a in annots if a.subtype == "Link"]
            if not links:
                break
            delete_annotation(out, links[0], out)
        final = get_annotations(out)
        assert all(a.subtype != "Link" for a in final)


class TestMoveAnnotation:
    """Tests for move_annotation()."""

    def test_move_changes_rect(self, tmp_pdf: str, tmp_path: Path) -> None:
        annots = get_annotations(tmp_pdf)
        link = annots[0]
        new_rect = (100.0, 100.0, 200.0, 120.0)
        out = str(tmp_path / "moved.pdf")
        move_annotation(tmp_pdf, link, new_rect, out)

        moved = get_annotations(out)
        assert moved[link.index].rect == pytest.approx(new_rect, abs=0.1)
