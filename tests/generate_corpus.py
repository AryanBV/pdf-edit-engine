"""Generate test corpus PDFs for pdf-edit-engine.

Creates synthetic PDFs using reportlab and pikepdf to test different
encodings, features, and edge cases.  Run directly or via the
``ensure_corpus`` pytest fixture in conftest.py.

Usage::

    python tests/generate_corpus.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pikepdf
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

CORPUS_DIR = Path(__file__).parent / "corpus"

MANIFEST: list[dict[str, object]] = [
    {
        "filename": "resume_aryan.pdf",
        "generator": "real-world",
        "encoding": "Identity-H",
        "features": ["CIDFont", "subsetted fonts", "ligatures"],
        "expected_text": "Aryan B V",
    },
    {
        "filename": "reportlab_simple.pdf",
        "generator": "reportlab",
        "encoding": "WinAnsi",
        "features": ["standard fonts", "non-ASCII", "horizontal rule"],
        "expected_text": "Test Document",
    },
    {
        "filename": "reportlab_table.pdf",
        "generator": "reportlab",
        "encoding": "WinAnsi",
        "features": ["table with grid", "paths"],
        "expected_text": "Quarterly Report",
    },
    {
        "filename": "reportlab_multipage.pdf",
        "generator": "reportlab",
        "encoding": "WinAnsi",
        "features": ["multi-page"],
        "expected_text": "Page One Content",
    },
    {
        "filename": "pikepdf_synthetic.pdf",
        "generator": "reportlab+pikepdf",
        "encoding": "WinAnsi",
        "features": ["invisible text", "Tr mode 3", "path rect"],
        "expected_text": "Visible text here",
    },
    {
        "filename": "reportlab_forms.pdf",
        "generator": "reportlab",
        "encoding": "WinAnsi",
        "features": ["AcroForm fields"],
        "expected_text": "Please fill out this form",
    },
]


def generate_reportlab_simple(output: Path) -> None:
    """PDF 1: Simple single-page with title, body, line, and non-ASCII text."""
    c = canvas.Canvas(str(output), pagesize=letter)

    # Title
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 750, "Test Document")

    # Body with non-ASCII characters (WinAnsi-encodable)
    c.setFont("Helvetica", 11)
    c.drawString(
        72,
        700,
        "This is a simple test document created by reportlab. It contains",
    )
    c.drawString(
        72,
        685,
        "standard WinAnsi encoded text with special characters like",
    )
    c.drawString(72, 670, "caf\u00e9 and na\u00efve.")

    # Horizontal line
    c.line(72, 655, 540, 655)

    # Second paragraph
    c.drawString(72, 635, "Section two has numbers: 12345 and symbols: @#$%")

    c.save()


def generate_reportlab_table(output: Path) -> None:
    """PDF 2: Table with grid borders and a paragraph below."""
    doc = SimpleDocTemplate(str(output), pagesize=letter)
    styles = getSampleStyleSheet()
    elements: list[object] = []

    # Title
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Title"],
        fontSize=18,
        spaceAfter=20,
    )
    elements.append(Paragraph("Quarterly Report", title_style))

    # Table data
    data = [
        ["Quarter", "Revenue", "Expenses"],
        ["Q1 2026", "$1,200,000", "$980,000"],
        ["Q2 2026", "$1,350,000", "$1,020,000"],
        ["Q3 2026", "$1,500,000", "$1,100,000"],
    ]

    table = Table(data, colWidths=[2 * inch, 2 * inch, 2 * inch])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 20))

    # Paragraph below table
    elements.append(Paragraph(
        "Total revenue exceeded expectations.",
        styles["Normal"],
    ))

    doc.build(elements)


def generate_reportlab_multipage(output: Path) -> None:
    """PDF 3: Two pages with different content."""
    c = canvas.Canvas(str(output), pagesize=letter)

    # Page 1
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 750, "Page One Content")
    c.setFont("Helvetica", 11)
    c.drawString(72, 720, "This is the first page of a multi-page document.")
    c.drawString(72, 705, "It contains introductory text and content.")

    c.showPage()

    # Page 2
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 750, "Page Two Content")
    c.setFont("Helvetica", 11)
    c.drawString(72, 720, "This is the second page with different text.")
    c.drawString(72, 705, "It demonstrates multi-page PDF handling.")

    c.save()


def generate_pikepdf_synthetic(output: Path) -> None:
    """PDF 4: Reportlab base + pikepdf post-processing for invisible text."""
    # Step 1: Generate base PDF with reportlab
    base_path = output.parent / "_pikepdf_base.pdf"
    c = canvas.Canvas(str(base_path), pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Visible text here")
    c.save()

    # Step 2: Post-process with pikepdf
    with pikepdf.open(base_path) as pdf:
        page = pdf.pages[0]

        # Find the font key reportlab used
        font_dict = page["/Resources"]["/Font"]
        font_key = list(font_dict.keys())[0]  # e.g., "/F1"

        # Read existing content stream
        existing_stream = page.Contents.read_bytes()

        # Append invisible text (Tr mode 3) and a rectangle path
        appended = (
            b"\nBT\n"
            + f"{font_key} 12 Tf\n".encode()
            + b"3 Tr\n"
            + b"100 500 Td\n"
            + b"(Invisible hidden text) Tj\n"
            + b"0 Tr\n"
            + b"ET\n"
            + b"100 100 200 50 re S\n"
        )

        new_stream = existing_stream + appended
        page.Contents = pdf.make_stream(new_stream)
        pdf.save(str(output))

    # Clean up base file
    base_path.unlink(missing_ok=True)


def generate_reportlab_forms(output: Path) -> None:
    """PDF 5: AcroForm fields with regular text content."""
    c = canvas.Canvas(str(output), pagesize=letter)

    # Body text
    c.setFont("Helvetica", 12)
    c.drawString(72, 750, "Please fill out this form")

    # Labels
    c.setFont("Helvetica", 10)
    c.drawString(72, 700, "Name:")
    c.drawString(72, 660, "Email:")

    # Form fields
    c.acroForm.textfield(
        name="name_field",
        x=130,
        y=690,
        width=200,
        height=20,
        fontSize=10,
        value="John Doe",
    )
    c.acroForm.textfield(
        name="email_field",
        x=130,
        y=650,
        width=200,
        height=20,
        fontSize=10,
    )

    c.save()


def write_manifest(corpus_dir: Path) -> None:
    """Write the corpus manifest.json."""
    manifest_path = corpus_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(MANIFEST, f, indent=2, ensure_ascii=False)
        f.write("\n")


def generate_all(corpus_dir: Path | None = None) -> None:
    """Generate all corpus PDFs and write the manifest."""
    target = corpus_dir or CORPUS_DIR
    target.mkdir(parents=True, exist_ok=True)

    generators = {
        "reportlab_simple.pdf": generate_reportlab_simple,
        "reportlab_table.pdf": generate_reportlab_table,
        "reportlab_multipage.pdf": generate_reportlab_multipage,
        "pikepdf_synthetic.pdf": generate_pikepdf_synthetic,
        "reportlab_forms.pdf": generate_reportlab_forms,
    }

    for filename, gen_func in generators.items():
        out = target / filename
        gen_func(out)
        print(f"Generated {out}")

    write_manifest(target)
    print(f"Wrote manifest to {target / 'manifest.json'}")


if __name__ == "__main__":
    generate_all()
