"""PDF generators for ultimate stress tests.

Each function takes a tmp_path and returns the path to a generated PDF.
Uses only reportlab + pikepdf (existing dev dependencies).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

if TYPE_CHECKING:
    from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# ── Helpers ───────────────────────────────────────────────────────────


def _make_with_font(
    tmp_path: Path,
    stream: bytes,
    name: str,
    *,
    font_name: str = "Helvetica",
    font_subtype: str = "Type1",
) -> str:
    """Create a PDF with a single Type1 font and custom content stream."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name(f"/{font_subtype}"),
        BaseFont=pikepdf.Name(f"/{font_name}"),
    )
    page.Contents = pdf.make_stream(stream)
    page["/Resources"] = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=font),
    )
    out = str(tmp_path / name)
    pdf.save(out)
    pdf.close()
    return out


def _make_multi_font(tmp_path: Path, stream: bytes, name: str, fonts: dict[str, str]) -> str:
    """Create a PDF with multiple named fonts and custom content stream."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    font_dict = {}
    for key, base_name in fonts.items():
        font_dict[f"/{key}"] = pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name(f"/{base_name}"),
        )
    page.Contents = pdf.make_stream(stream)
    page["/Resources"] = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(font_dict),
    )
    out = str(tmp_path / name)
    pdf.save(out)
    pdf.close()
    return out


# ── Large-scale generators ────────────────────────────────────────────


def gen_1000_page_pdf(tmp_path: Path) -> str:
    """1000 pages, each with 3 predictable lines of text."""
    out = str(tmp_path / "stress_1000.pdf")
    c = canvas.Canvas(out, pagesize=letter)
    for page_num in range(1000):
        c.setFont("Helvetica", 12)
        line0 = f"Page {page_num} Line 0: The quick brown fox jumped over the lazy dog"
        line1 = f"Page {page_num} Line 1: ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789"
        line2 = f"Page {page_num} Line 2: Special chars: $100.00 email@test.com"
        c.drawString(72, 700, line0)
        c.drawString(72, 680, line1)
        c.drawString(72, 660, line2)
        c.showPage()
    c.save()
    return out


def gen_multipage_pdf(tmp_path: Path, pages: int = 100) -> str:
    """Variable-page PDF for scaling tests."""
    out = str(tmp_path / f"stress_{pages}p.pdf")
    c = canvas.Canvas(out, pagesize=letter)
    for page_num in range(pages):
        c.setFont("Helvetica", 12)
        line = f"Page {page_num} Line 0: The quick brown fox jumped over the lazy dog"
        c.drawString(72, 700, line)
        c.showPage()
    c.save()
    return out


def gen_batch_500_pdf(tmp_path: Path) -> str:
    """10 pages with 500 unique tokens for batch replacement."""
    out = str(tmp_path / "stress_batch500.pdf")
    c = canvas.Canvas(out, pagesize=letter)
    token_idx = 0
    for _page_num in range(10):
        c.setFont("Helvetica", 10)
        y = 750
        for _ in range(50):
            c.drawString(72, y, f"stressword_{token_idx:03d}")
            y -= 14
            token_idx += 1
        c.showPage()
    c.save()
    return out


# ── Font stress generators ────────────────────────────────────────────


def gen_100_fonts_pdf(tmp_path: Path) -> str:
    """Page with 100+ distinct font dictionaries."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]

    fonts: dict[str, pikepdf.Object] = {}
    stream_parts: list[bytes] = []
    base_fonts = ["Helvetica", "Courier", "Times-Roman", "Symbol", "ZapfDingbats"]

    for i in range(100):
        fname = f"/FX{i}"
        base = base_fonts[i % len(base_fonts)]
        fonts[fname] = pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name(f"/{base}"),
        )
        y = 750 - (i % 50) * 14
        stream_parts.append(f"BT {fname} 8 Tf 72 {y} Td (Font{i:03d} text) Tj ET\n".encode())

    page.Contents = pdf.make_stream(b"".join(stream_parts))
    page["/Resources"] = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(fonts),
    )
    out = str(tmp_path / "stress_100fonts.pdf")
    pdf.save(out)
    pdf.close()
    return out


# ── Encryption generators ─────────────────────────────────────────────


def gen_encrypted_pdf(tmp_path: Path) -> str:
    """Encrypted PDF with known passwords."""
    # First create a source PDF with text
    source = str(tmp_path / "encrypt_source.pdf")
    c = canvas.Canvas(source, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Secret Document Content")
    c.drawString(72, 680, "Confidential Data Here")
    c.save()

    out = str(tmp_path / "stress_encrypted.pdf")
    pdf = pikepdf.Pdf.open(source)
    pdf.save(out, encryption=pikepdf.Encryption(owner="owner123", user="user123"))
    pdf.close()
    return out


def gen_empty_password_pdf(tmp_path: Path) -> str:
    """Encrypted PDF with empty user password."""
    source = str(tmp_path / "empty_pw_source.pdf")
    c = canvas.Canvas(source, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Empty Password Document")
    c.save()

    out = str(tmp_path / "stress_empty_pw.pdf")
    pdf = pikepdf.Pdf.open(source)
    pdf.save(out, encryption=pikepdf.Encryption(owner="ownerpass", user=""))
    pdf.close()
    return out


# ── Merge/split generators ────────────────────────────────────────────


def gen_3_source_pdfs(tmp_path: Path) -> tuple[str, str, str]:
    """Create 3 separate PDFs with distinct text for merge testing."""
    paths = []
    texts = [
        ("Source One Alpha", "First document content here"),
        ("Source Two Bravo", "Second document content here"),
        ("Source Three Charlie", "Third document content here"),
    ]
    for i, (title, body) in enumerate(texts):
        out = str(tmp_path / f"merge_source_{i}.pdf")
        c = canvas.Canvas(out, pagesize=letter)
        c.setFont("Helvetica", 14)
        c.drawString(72, 700, title)
        c.setFont("Helvetica", 12)
        c.drawString(72, 680, body)
        c.showPage()
        c.save()
        paths.append(out)
    return paths[0], paths[1], paths[2]


def gen_sequential_edit_pdf(tmp_path: Path) -> str:
    """PDF with 5 distinct findable phrases for edit chain testing."""
    out = str(tmp_path / "stress_sequential.pdf")
    c = canvas.Canvas(out, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "ChainAlpha is the first phrase")
    c.drawString(72, 680, "ChainBravo is the second phrase")
    c.drawString(72, 660, "ChainCharlie is the third phrase")
    c.drawString(72, 640, "ChainDelta is the fourth phrase")
    c.drawString(72, 620, "ChainEcho is the fifth phrase")
    c.save()
    return out


# ── Form generators ──────────────────────────────────────────────────


def gen_form_all_types_pdf(tmp_path: Path) -> str:
    """PDF with text field + checkbox via reportlab acroForm."""
    out = str(tmp_path / "stress_forms.pdf")
    c = canvas.Canvas(out, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 750, "Form Test Document")

    c.acroForm.textfield(
        name="name_field",
        x=72,
        y=700,
        width=200,
        height=20,
        value="default",
    )
    c.acroForm.checkbox(
        name="agree_field",
        x=72,
        y=660,
        size=20,
    )
    c.acroForm.textfield(
        name="email_field",
        x=72,
        y=620,
        width=200,
        height=20,
        value="user@test.com",
    )
    c.save()
    return out


# ── Rotation generators ──────────────────────────────────────────────


def gen_rotated_pages_pdf(tmp_path: Path) -> str:
    """3-page PDF with different rotations applied."""
    source = str(tmp_path / "rotate_source.pdf")
    c = canvas.Canvas(source, pagesize=letter)
    for i in range(3):
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, f"RotatedPage{i} text content here")
        c.showPage()
    c.save()

    out = str(tmp_path / "stress_rotated.pdf")
    pdf = pikepdf.Pdf.open(source)
    pdf.pages[0]["/Rotate"] = 90
    pdf.pages[1]["/Rotate"] = 180
    pdf.pages[2]["/Rotate"] = 270
    pdf.save(out)
    pdf.close()
    return out


# ── Degenerate content stream generators ─────────────────────────────


def gen_degenerate_ctm_pdf(tmp_path: Path) -> str:
    """Content stream with zero-determinant CTM."""
    stream = b"q 0 0 0 0 0 0 cm BT /F1 12 Tf 72 700 Td (Zero CTM text) Tj ET Q"
    return _make_with_font(tmp_path, stream, "stress_degenerate_ctm.pdf")


def gen_inline_image_between_text_pdf(tmp_path: Path) -> str:
    """Text, then inline image, then more text."""
    # 2x2 grayscale image = 4 bytes
    img_data = b"\x00\xff\xff\x00"
    stream = (
        b"BT /F1 12 Tf 72 700 Td (BeforeImage) Tj ET\n"
        b"BI /W 2 /H 2 /CS /G /BPC 8 ID " + img_data + b" EI\n"
        b"BT /F1 12 Tf 72 680 Td (AfterImage) Tj ET\n"
    )
    return _make_with_font(tmp_path, stream, "stress_inline_image.pdf")


def gen_text_outside_mediabox_pdf(tmp_path: Path) -> str:
    """Text placed at coordinates far outside the visible page."""
    stream = b"BT /F1 12 Tf 2000 2000 Td (OutOfBoundsText) Tj ET"
    return _make_with_font(tmp_path, stream, "stress_outside_mediabox.pdf")


def gen_very_long_line_pdf(tmp_path: Path) -> str:
    """Single Tj with 10,000 'A' characters."""
    long_text = b"A" * 10000
    stream = b"BT /F1 10 Tf 72 700 Td (" + long_text + b") Tj ET"
    return _make_with_font(tmp_path, stream, "stress_long_line.pdf")


def gen_no_fonts_pdf(tmp_path: Path) -> str:
    """Text operators but no /Font in /Resources."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(b"BT /F1 12 Tf (NoFontDefined) Tj ET")
    page["/Resources"] = pikepdf.Dictionary()  # Empty — no Font dict
    out = str(tmp_path / "stress_no_fonts.pdf")
    pdf.save(out)
    pdf.close()
    return out


def gen_damaged_font_stream_pdf(tmp_path: Path) -> str:
    """Valid font dictionary structure but garbage font stream data."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]

    # Create a font dict that claims to be TrueType but has garbage data
    font_stream = pdf.make_stream(b"\x00\x01\x02\x03GARBAGE_FONT_DATA_NOT_REAL_TRUETYPE")
    font_descriptor = pikepdf.Dictionary(
        Type=pikepdf.Name.FontDescriptor,
        FontName=pikepdf.Name("/DamagedFont"),
        FontFile2=font_stream,
    )
    font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.TrueType,
        BaseFont=pikepdf.Name("/DamagedFont"),
        FontDescriptor=font_descriptor,
    )
    page.Contents = pdf.make_stream(b"BT /F1 12 Tf (DamagedFontText) Tj ET")
    page["/Resources"] = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=font),
    )
    out = str(tmp_path / "stress_damaged_font.pdf")
    pdf.save(out)
    pdf.close()
    return out


def gen_overlapping_text_pdf(tmp_path: Path) -> str:
    """Two Tj operators at the exact same coordinates with different text."""
    stream = (
        b"BT /F1 12 Tf 72 700 Td (OverlapAlpha) Tj ET\n"
        b"BT /F1 12 Tf 72 700 Td (OverlapBravo) Tj ET\n"
    )
    return _make_with_font(tmp_path, stream, "stress_overlapping.pdf")


def gen_zero_font_size_pdf(tmp_path: Path) -> str:
    """Content stream with zero font size."""
    stream = b"BT /F1 0 Tf (ZeroSizeText) Tj ET"
    return _make_with_font(tmp_path, stream, "stress_zero_fontsize.pdf")


def gen_xref_stream_pdf(tmp_path: Path) -> str:
    """PDF saved with object streams (cross-reference streams)."""
    source = str(tmp_path / "xref_source.pdf")
    c = canvas.Canvas(source, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "XRefStream document content")
    c.drawString(72, 680, "More text for searching")
    c.save()

    out = str(tmp_path / "stress_xref_stream.pdf")
    pdf = pikepdf.Pdf.open(source)
    pdf.save(out, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    pdf.close()
    return out


def gen_cropped_pdf(tmp_path: Path) -> str:
    """PDF with CropBox applied to a small region."""
    source = str(tmp_path / "crop_source.pdf")
    c = canvas.Canvas(source, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "CropVisible text at top")
    c.drawString(72, 400, "CropHidden text in middle")
    c.drawString(72, 100, "CropBelow text at bottom")
    c.save()

    out = str(tmp_path / "stress_cropped.pdf")
    pdf = pikepdf.Pdf.open(source)
    # Crop to top half only (y from 396 to 792)
    for page in pdf.pages:
        page["/CropBox"] = pikepdf.Array([0, 396, 612, 792])
    pdf.save(out)
    pdf.close()
    return out


def gen_digital_signature_pdf(tmp_path: Path) -> str:
    """PDF with a /Sig field in AcroForm (simulated, not a real cryptographic sig)."""
    source = str(tmp_path / "sig_source.pdf")
    c = canvas.Canvas(source, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "SignedDocument content here")
    c.save()

    out = str(tmp_path / "stress_signed.pdf")
    pdf = pikepdf.Pdf.open(source)
    # Add a fake signature field to AcroForm
    sig_field = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Sig,
        T=pikepdf.String("Signature1"),
        Rect=pikepdf.Array([0, 0, 0, 0]),
    )
    pdf.Root["/AcroForm"] = pikepdf.Dictionary(
        Fields=pikepdf.Array([pdf.make_indirect(sig_field)]),
        SigFlags=3,
    )
    pdf.save(out)
    pdf.close()
    return out


def gen_combining_diacritics_pdf(tmp_path: Path) -> str:
    """PDF with combining diacritics in text (WinAnsi subset)."""
    # Use reportlab which handles encoding
    out = str(tmp_path / "stress_combining.pdf")
    c = canvas.Canvas(out, pagesize=letter)
    c.setFont("Helvetica", 12)
    # These are pre-composed forms that WinAnsi can handle
    c.drawString(72, 700, "Caf\u00e9 na\u00efve r\u00e9sum\u00e9")
    c.drawString(72, 680, "Normal text without diacritics")
    c.save()
    return out


def gen_resource_leak_pdf(tmp_path: Path) -> str:
    """Simple valid PDF for resource leak testing."""
    out = str(tmp_path / "stress_leak_test.pdf")
    c = canvas.Canvas(out, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "LeakTestContent for resource testing")
    c.save()
    return out


def gen_mixed_encoding_pdf(tmp_path: Path) -> str:
    """Page with both WinAnsi Type1 font and a second font (simulated mixed)."""
    stream = (
        b"BT /F1 12 Tf 72 700 Td (WinAnsiText hello) Tj ET\n"
        b"BT /F2 12 Tf 72 680 Td (CourierText world) Tj ET\n"
    )
    return _make_multi_font(
        tmp_path,
        stream,
        "stress_mixed_encoding.pdf",
        fonts={"F1": "Helvetica", "F2": "Courier"},
    )
