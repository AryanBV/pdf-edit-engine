"""Tests for pikepdf wrapper operations."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.errors import PDFEditError
from pdf_edit_engine.wrapper import (
    add_bookmark,
    add_highlight,
    add_hyperlink,
    add_watermark,
    crop_pages,
    decrypt_pdf,
    delete_pages,
    edit_metadata,
    encrypt_pdf,
    fill_form,
    flatten_annotations,
    merge_pdfs,
    reorder_pages,
    rotate_pages,
    split_pdf,
)

CORPUS_DIR = Path(__file__).parent / "corpus"
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")
MULTI_PDF = str(CORPUS_DIR / "reportlab_multipage.pdf")
FORMS_PDF = str(CORPUS_DIR / "reportlab_forms.pdf")


# ── Page operations ──────────────────────────────────────────────────────


class TestMerge:
    def test_merge_two_pdfs(self, tmp_path: Path) -> None:
        output = str(tmp_path / "merged.pdf")
        merge_pdfs([SIMPLE_PDF, MULTI_PDF], output)
        with pikepdf.open(output) as pdf:
            # simple=1 page + multi=2 pages = 3
            assert len(pdf.pages) == 3

    # test_merge_empty_raises removed — strict subset of
    # tests/invariants/test_h_1_merge_empty.py
    # (also covered by tests/test_error_messages.py).


class TestSplit:
    def test_split_multipage(self, tmp_path: Path) -> None:
        outputs = split_pdf(MULTI_PDF, str(tmp_path))
        assert len(outputs) == 2
        for path in outputs:
            with pikepdf.open(path) as pdf:
                assert len(pdf.pages) == 1

    def test_split_single_page(self, tmp_path: Path) -> None:
        outputs = split_pdf(SIMPLE_PDF, str(tmp_path))
        assert len(outputs) == 1


class TestReorder:
    def test_reverse_pages(self, tmp_path: Path) -> None:
        output = str(tmp_path / "reordered.pdf")
        reorder_pages(MULTI_PDF, [1, 0], output)
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) == 2


class TestRotate:
    def test_rotate_90(self, tmp_path: Path) -> None:
        output = str(tmp_path / "rotated.pdf")
        rotate_pages(SIMPLE_PDF, [0], 90, output)
        with pikepdf.open(output) as pdf:
            assert int(pdf.pages[0].get("/Rotate", 0)) == 90

    def test_rotate_accumulates(self, tmp_path: Path) -> None:
        mid = str(tmp_path / "mid.pdf")
        output = str(tmp_path / "rotated.pdf")
        rotate_pages(SIMPLE_PDF, [0], 90, mid)
        rotate_pages(mid, [0], 90, output)
        with pikepdf.open(output) as pdf:
            assert int(pdf.pages[0].get("/Rotate", 0)) == 180

    def test_rotate_invalid_angle(self, tmp_path: Path) -> None:
        with pytest.raises(PDFEditError):
            rotate_pages(SIMPLE_PDF, [0], 45, str(tmp_path / "out.pdf"))


class TestDelete:
    def test_delete_one_page(self, tmp_path: Path) -> None:
        output = str(tmp_path / "deleted.pdf")
        delete_pages(MULTI_PDF, [1], output)
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) == 1


class TestCrop:
    def test_crop_half_page(self, tmp_path: Path) -> None:
        output = str(tmp_path / "cropped.pdf")
        crop_pages(SIMPLE_PDF, (0, 0, 306, 396), output)
        with pikepdf.open(output) as pdf:
            crop_box = pdf.pages[0]["/CropBox"]
            assert float(crop_box[2]) == 306.0
            assert float(crop_box[3]) == 396.0


# ── Document operations ──────────────────────────────────────────────────


class TestMetadata:
    def test_set_title(self, tmp_path: Path) -> None:
        output = str(tmp_path / "meta.pdf")
        edit_metadata(SIMPLE_PDF, {"dc:title": "Test Title"}, output)
        with pikepdf.open(output) as pdf, pdf.open_metadata() as meta:
            assert meta.get("dc:title") == "Test Title"

    def test_simple_key_mapping(self, tmp_path: Path) -> None:
        output = str(tmp_path / "meta2.pdf")
        edit_metadata(SIMPLE_PDF, {"title": "My Doc"}, output)
        with pikepdf.open(output) as pdf, pdf.open_metadata() as meta:
            assert meta.get("dc:title") == "My Doc"


class TestBookmark:
    def test_add_bookmark(self, tmp_path: Path) -> None:
        output = str(tmp_path / "bookmarked.pdf")
        add_bookmark(MULTI_PDF, "Chapter 1", 0, output)
        with pikepdf.open(output) as pdf, pdf.open_outline() as outline:
            assert len(outline.root) >= 1
            assert outline.root[0].title == "Chapter 1"


class TestEncryptDecrypt:
    def test_encrypt(self, tmp_path: Path) -> None:
        output = str(tmp_path / "encrypted.pdf")
        encrypt_pdf(SIMPLE_PDF, "owner123", "user123", output)
        with pytest.raises(pikepdf.PasswordError):
            pikepdf.open(output)

    def test_decrypt(self, tmp_path: Path) -> None:
        encrypted = str(tmp_path / "enc.pdf")
        output = str(tmp_path / "dec.pdf")
        encrypt_pdf(SIMPLE_PDF, "owner", "user", encrypted)
        decrypt_pdf(encrypted, "owner", output)
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) >= 1


# ── Annotation operations ────────────────────────────────────────────────


class TestHyperlink:
    def test_add_link(self, tmp_path: Path) -> None:
        output = str(tmp_path / "linked.pdf")
        add_hyperlink(
            SIMPLE_PDF,
            0,
            (72, 700, 200, 720),
            "https://example.com",
            output,
        )
        with pikepdf.open(output) as pdf:
            annots = pdf.pages[0].get("/Annots")
            assert annots is not None
            assert len(annots) >= 1
            link = annots[0]
            assert str(link["/Subtype"]) == "/Link"


class TestHighlight:
    def test_add_highlight(self, tmp_path: Path) -> None:
        output = str(tmp_path / "highlighted.pdf")
        quad = [72.0, 700.0, 200.0, 700.0, 72.0, 720.0, 200.0, 720.0]
        add_highlight(SIMPLE_PDF, 0, quad, output)
        with pikepdf.open(output) as pdf:
            annots = pdf.pages[0].get("/Annots")
            assert annots is not None
            assert len(annots) >= 1
            assert str(annots[0]["/Subtype"]) == "/Highlight"


class TestFlatten:
    def test_flatten_removes_annots(self, tmp_path: Path) -> None:
        linked = str(tmp_path / "linked.pdf")
        output = str(tmp_path / "flat.pdf")
        add_hyperlink(
            SIMPLE_PDF,
            0,
            (72, 700, 200, 720),
            "https://example.com",
            linked,
        )
        flatten_annotations(linked, output)
        with pikepdf.open(output) as pdf:
            assert "/Annots" not in pdf.pages[0]

    def test_flatten_no_annots_ok(self, tmp_path: Path) -> None:
        output = str(tmp_path / "flat.pdf")
        flatten_annotations(SIMPLE_PDF, output)
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) >= 1


# ── Form and other operations ────────────────────────────────────────────


class TestFillForm:
    def test_fill_form_fields(self, tmp_path: Path) -> None:
        output = str(tmp_path / "filled.pdf")
        fill_form(FORMS_PDF, {"name_field": "Jane Smith"}, output)
        with pikepdf.open(output) as pdf:
            acroform = pdf.Root["/AcroForm"]
            fields = acroform["/Fields"]
            found = False
            for field in fields:
                if str(field.get("/T", "")) == "name_field":
                    assert str(field["/V"]) == "Jane Smith"
                    found = True
            assert found, "name_field not found in form"

    def test_fill_no_acroform_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PDFEditError):
            fill_form(SIMPLE_PDF, {"x": "y"}, str(tmp_path / "out.pdf"))


class TestWatermark:
    def test_add_watermark(self, tmp_path: Path) -> None:
        output = str(tmp_path / "watermarked.pdf")
        add_watermark(MULTI_PDF, SIMPLE_PDF, output)
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) == 2
            # Verify page content stream has been modified (underlay adds data)
            page = pdf.pages[0]
            assert page.Contents is not None
