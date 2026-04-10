"""Generate complex test corpus PDFs for pdf-edit-engine.

Creates synthetic PDFs of increasing complexity to stress-test the engine
beyond "well-behaved" reportlab output.  Five levels: multi-font, transformed
text, dense contract, PyMuPDF-edited, and CIDFont (Identity-H).

Usage::

    python tests/generate_complex_corpus.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pikepdf
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

CORPUS_DIR = Path(__file__).parent / "corpus"

W, H = letter  # 612 x 792

# ── System font discovery ──────────────────────────────────────────────

_TTF_CANDIDATES = [
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/calibri.ttf"),
]


def _find_ttf() -> Path | None:
    """Return the first available .ttf path, or None."""
    for p in _TTF_CANDIDATES:
        if p.exists():
            return p
    return None


# ── Level 1: Multi-font ────────────────────────────────────────────────


def make_multi_font_pdf(path: Path) -> None:
    """PDF with 3+ fonts on the same page, including mixed fonts on one line."""
    c = canvas.Canvas(str(path), pagesize=letter)

    # Title in Helvetica-Bold 18pt
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 740, "Multi-Font Stress Test")

    # Body paragraph in Times-Roman 11pt
    c.setFont("Times-Roman", 11)
    c.drawString(72, 710, "This paragraph is set in Times-Roman at 11 points.")
    c.drawString(72, 695, "It contains normal body text for the document.")

    # Code snippet in Courier 10pt
    c.setFont("Courier", 10)
    c.drawString(72, 665, "def hello(): print('Hello, world!')")

    # Mixed fonts on the SAME visual line:
    # "Contact us at " in Times-Roman, "support@example.com" in Courier, " for help" in Times
    y = 635
    c.setFont("Times-Roman", 11)
    c.drawString(72, y, "Contact us at ")
    x_after_contact = 72 + c.stringWidth("Contact us at ", "Times-Roman", 11)

    c.setFont("Courier", 11)
    c.drawString(x_after_contact, y, "support@example.com")
    x_after_email = x_after_contact + c.stringWidth("support@example.com", "Courier", 11)

    c.setFont("Times-Roman", 11)
    c.drawString(x_after_email, y, " for help")

    # Dollar amount line: "Amount: " bold + "$1,234.56" regular
    y2 = 605
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y2, "Amount: ")
    x_after_amount = 72 + c.stringWidth("Amount: ", "Helvetica-Bold", 11)
    c.setFont("Times-Roman", 11)
    c.drawString(x_after_amount, y2, "$1,234.56")

    c.save()


# ── Level 2: Transformed text ──────────────────────────────────────────


def make_transformed_text_pdf(path: Path) -> None:
    """PDF with text under various CTM transformations."""
    c = canvas.Canvas(str(path), pagesize=letter)

    # Normal text (baseline reference)
    c.setFont("Helvetica", 12)
    c.drawString(72, 740, "Normal text here")

    # Horizontally scaled text (150% width)
    c.saveState()
    c.translate(72, 700)
    c.transform(1.5, 0, 0, 1, 0, 0)
    c.setFont("Helvetica", 12)
    c.drawString(0, 0, "Stretched text")
    c.restoreState()

    # Rotated text (5 degrees)
    c.saveState()
    c.translate(72, 650)
    angle = 5
    cos_a = math.cos(math.radians(angle))
    sin_a = math.sin(math.radians(angle))
    c.transform(cos_a, sin_a, -sin_a, cos_a, 0, 0)
    c.setFont("Helvetica", 12)
    c.drawString(0, 0, "Rotated five degrees")
    c.restoreState()

    # Very small text (6pt)
    c.setFont("Helvetica", 6)
    c.drawString(72, 610, "Fine print disclaimer: terms and conditions apply")

    # Text with extra character spacing (via TextObject)
    t = c.beginText(72, 580)
    t.setFont("Helvetica", 12)
    t.setCharSpace(3)
    t.textLine("Spaced Out")
    c.drawText(t)

    # Text with negative character spacing (compressed)
    t2 = c.beginText(72, 550)
    t2.setFont("Helvetica", 12)
    t2.setCharSpace(-0.5)
    t2.textLine("Compressed text tightly packed together")
    c.drawText(t2)

    c.save()


# ── Level 3: Dense contract ────────────────────────────────────────────


def _page_footer(canvas_obj: canvas.Canvas, _doc: object) -> None:
    """Draw page footer with page number."""
    canvas_obj.saveState()
    canvas_obj.setFont("Helvetica", 8)
    page_num = canvas_obj.getPageNumber()
    canvas_obj.drawCentredString(W / 2, 30, f"Page {page_num} of 3")
    canvas_obj.restoreState()


def make_contract_pdf(path: Path) -> None:
    """Realistic 3-page contract with headings, clauses, table, signatures."""
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        topMargin=72,
        bottomMargin=60,
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ContractTitle",
        parent=styles["Title"],
        fontSize=16,
        spaceAfter=6,
        alignment=1,
    )
    heading_style = ParagraphStyle(
        "ContractHeading",
        parent=styles["Heading2"],
        fontSize=13,
        spaceBefore=16,
        spaceAfter=8,
        fontName="Helvetica-Bold",
    )
    body_style = ParagraphStyle(
        "ContractBody",
        parent=styles["Normal"],
        fontSize=10,
        spaceAfter=6,
        leading=14,
    )
    right_style = ParagraphStyle(
        "RightAlign",
        parent=body_style,
        alignment=2,
    )
    clause_style = ParagraphStyle(
        "Clause",
        parent=body_style,
        leftIndent=20,
        spaceAfter=4,
    )

    elements: list[object] = []

    # ── Page 1 ──
    elements.append(Paragraph("SERVICE AGREEMENT", title_style))
    elements.append(Paragraph("Agreement No: SA-2026-001", right_style))
    elements.append(Paragraph("Date: April 4, 2026", right_style))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("PARTIES", heading_style))
    elements.append(
        Paragraph(
            "<b>Provider:</b> Acme Technology Solutions Inc., "
            "123 Innovation Drive, San Francisco, CA 94105, "
            "represented by Jane Smith, Chief Executive Officer.",
            body_style,
        )
    )
    elements.append(
        Paragraph(
            "<b>Client:</b> GlobalTech Enterprises LLC, "
            "456 Commerce Boulevard, New York, NY 10001, "
            "represented by John Roberts, VP of Operations.",
            body_style,
        )
    )
    elements.append(Spacer(1, 8))

    # Horizontal rule via a thin table
    hr = Table([[""]], colWidths=[6.5 * inch])
    hr.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 1, colors.black),
            ]
        )
    )
    elements.append(hr)
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("SCOPE OF SERVICES", heading_style))
    elements.append(
        Paragraph(
            "The Provider agrees to deliver software development, consulting, "
            "and technical support services as described in Exhibit A attached hereto.",
            body_style,
        )
    )

    # ── Page 2 ──
    elements.append(PageBreak())
    elements.append(Paragraph("TERMS AND CONDITIONS", heading_style))

    clauses = [
        (
            "1. COMPENSATION",
            "The Client shall pay the Provider a total fee of $50,000.00 "
            "(fifty thousand dollars) for services rendered under this Agreement.",
        ),
        (
            "1.1 Payment Schedule",
            "Payments shall be made in three installments: $20,000.00 upon signing, "
            "$15,000.00 at midpoint delivery, and $15,000.00 upon final acceptance.",
        ),
        ("1.2 Late Payment", "Late payments shall accrue interest at 1.5% per month."),
        (
            "2. TERM",
            "This Agreement shall be effective from April 4, 2026 through "
            "October 4, 2026, unless terminated earlier per Section 4.",
        ),
        (
            "3. CONFIDENTIALITY",
            "Both parties agree to maintain strict confidentiality regarding "
            "proprietary information exchanged during the term of this Agreement.",
        ),
        (
            "4. TERMINATION",
            "Either party may terminate this Agreement with 30 days written notice. "
            "Upon termination, Client shall pay for all services rendered to date.",
        ),
        (
            "5. GOVERNING LAW",
            "This Agreement shall be governed by the laws of the State of California.",
        ),
    ]
    for title, text in clauses:
        elements.append(Paragraph(f"<b>{title}</b>", body_style))
        elements.append(Paragraph(text, clause_style))

    # Service rate table
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("<b>Schedule of Rates:</b>", body_style))
    table_data = [
        ["Service", "Rate (per hour)", "Duration"],
        ["Software Development", "$200.00", "120 hours"],
        ["Technical Consulting", "$250.00", "40 hours"],
        ["Support & Maintenance", "$150.00", "60 hours"],
        ["Project Management", "$175.00", "30 hours"],
    ]
    t = Table(table_data, colWidths=[2.2 * inch, 1.5 * inch, 1.5 * inch])
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
            ]
        )
    )
    elements.append(t)

    # ── Page 3 ──
    elements.append(PageBreak())
    elements.append(Paragraph("SIGNATURES", heading_style))
    elements.append(Spacer(1, 20))
    elements.append(
        Paragraph(
            "IN WITNESS WHEREOF, the parties have executed this Agreement "
            "as of the date first written above.",
            body_style,
        )
    )
    elements.append(Spacer(1, 30))

    sig_data = [
        ["Provider:", "Client:"],
        ["", ""],
        ["Name: ___________________________", "Name: ___________________________"],
        ["Title: ___________________________", "Title: ___________________________"],
        ["Date: ___________________________", "Date: ___________________________"],
    ]
    sig_table = Table(sig_data, colWidths=[3 * inch, 3 * inch])
    sig_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    elements.append(sig_table)

    doc.build(elements, onFirstPage=_page_footer, onLaterPages=_page_footer)


# ── Level 4: Previously-edited by PyMuPDF ──────────────────────────────


def make_previously_edited_pdf(path: Path) -> bool:
    """Create a PDF, edit it with PyMuPDF, save the result.

    Returns:
        True if the PDF was generated, False if fitz is not available.
    """
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError:
        return False

    # Step 1: Create base PDF with reportlab
    base = path.with_name("_pymupdf_base.pdf")
    c = canvas.Canvas(str(base), pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 740, "Original Document Title")
    c.setFont("Times-Roman", 11)
    c.drawString(72, 710, "This is the original body text before PyMuPDF editing.")
    c.drawString(72, 690, "Author: Original Author Name")
    c.drawString(72, 670, "Date: January 1, 2026")
    c.save()

    # Step 2: Edit with PyMuPDF redact+re-insert
    doc = fitz.open(str(base))
    page = doc[0]

    areas = page.search_for("Original Author Name")
    for area in areas:
        page.add_redact_annot(area, text="PyMuPDF Edited Author", fontsize=11)
    page.apply_redactions()

    doc.save(str(path))
    doc.close()
    base.unlink(missing_ok=True)
    return True


# ── Level 5: Synthetic CIDFont (Identity-H) ───────────────────────────


def make_cidfont_pdf(path: Path) -> bool:
    """Create a PDF with embedded CIDFont using Identity-H encoding.

    Constructs the Type0/CIDFont structure directly with pikepdf and
    fonttools, since reportlab's TTFont produces simple TrueType (not
    CIDFont) for ASCII-only text.

    Returns:
        True if generated AND verified as Identity-H, False otherwise.
    """
    ttf_path = _find_ttf()
    if ttf_path is None:
        print("  Level 5: No TTF font found — skipping CIDFont generation")
        return False

    from fontTools import ttLib
    from fontTools.subset import Subsetter

    font = ttLib.TTFont(str(ttf_path))
    cmap_table = font["cmap"]
    cmap: dict[int, str] = {}
    for table in cmap_table.tables:
        if table.platformID == 3 and table.platEncID == 1:
            cmap = table.cmap
            break

    glyph_order = font.getGlyphOrder()
    name_to_gid: dict[str, int] = {n: i for i, n in enumerate(glyph_order)}
    hmtx = font["hmtx"]
    units_per_em = font["head"].unitsPerEm

    # All text lines for the document
    lines: list[tuple[str, float, float, float]] = [
        ("Acme Corporation \u2014 Employment Contract", 72, 730, 18),
        ("Role: Software Engineer", 72, 690, 12),
        ("Department: Engineering", 72, 670, 12),
        ("Location: San Francisco, CA", 72, 650, 12),
        ("Start Date: January 15, 2026", 72, 630, 12),
        ("Annual Salary: $120,000.00", 72, 600, 12),
        ("This document is confidential and proprietary.", 72, 570, 12),
    ]

    # Collect all unique codepoints
    all_chars: set[int] = set()
    for text, *_ in lines:
        all_chars.update(ord(c) for c in text)

    # Build CID (==GID) mappings
    cp_to_gid: dict[int, int] = {}
    used_gids: set[int] = set()
    for cp in sorted(all_chars):
        glyph_name = cmap.get(cp)
        if glyph_name and glyph_name in name_to_gid:
            gid = name_to_gid[glyph_name]
            cp_to_gid[cp] = gid
            used_gids.add(gid)

    # Subset the font with --retain-gids
    subsetter = Subsetter()
    glyph_names = [glyph_order[gid] for gid in sorted(used_gids) if gid < len(glyph_order)]
    subsetter.populate(glyphs=glyph_names)
    sub_font = ttLib.TTFont(str(ttf_path))
    subsetter.subset(sub_font)

    import io

    buf = io.BytesIO()
    sub_font.save(buf)
    font_bytes = buf.getvalue()

    # Build /W array: [gid [width]] for each used GID
    w_entries: list[object] = []
    for gid in sorted(used_gids):
        glyph_name = glyph_order[gid] if gid < len(glyph_order) else ".notdef"
        try:
            advance = hmtx[glyph_name][0]
        except (KeyError, IndexError):
            advance = 500
        w_1000 = round(advance * 1000 / units_per_em)
        w_entries.append(gid)
        w_entries.append([w_1000])

    # Build ToUnicode CMap
    bfchar_lines: list[str] = []
    for cp in sorted(cp_to_gid):
        gid = cp_to_gid[cp]
        bfchar_lines.append(f"<{gid:04X}> <{cp:04X}>")

    tounicode_str = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\n"
        "begincmap\n"
        "/CIDSystemInfo\n"
        "<< /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n"
        "/CMapType 2 def\n"
        "1 begincodespacerange\n"
        "<0000> <FFFF>\n"
        "endcodespacerange\n"
    )
    # Split into chunks of 100 for bfchar
    for i in range(0, len(bfchar_lines), 100):
        chunk = bfchar_lines[i : i + 100]
        tounicode_str += f"{len(chunk)} beginbfchar\n"
        tounicode_str += "\n".join(chunk) + "\n"
        tounicode_str += "endbfchar\n"
    tounicode_str += "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"

    # Encode text lines as 2-byte hex strings
    def _encode_line(text: str) -> bytes:
        parts: list[int] = []
        for ch in text:
            gid = cp_to_gid.get(ord(ch), 0)
            parts.append(gid)
        return bytes(b for gid in parts for b in [(gid >> 8) & 0xFF, gid & 0xFF])

    # Build content stream using Tm (absolute text matrix) not Td (relative)
    content_ops: list[str] = []
    content_ops.append("BT")
    for text, x, y, size in lines:
        encoded = _encode_line(text)
        hex_str = encoded.hex().upper()
        content_ops.append(f"/F1 {size} Tf")
        content_ops.append(f"1 0 0 1 {x} {y} Tm")
        content_ops.append(f"<{hex_str}> Tj")
    content_ops.append("ET")
    content_stream = "\n".join(content_ops).encode("latin-1")

    # Build PDF with pikepdf
    raw_ps = font.get("name").getDebugName(6) or "ArialMT"
    ps_name = "/" + str(raw_ps) if not str(raw_ps).startswith("/") else str(raw_ps)
    ascent = font["OS/2"].sTypoAscender
    descent = font["OS/2"].sTypoDescender
    bbox = [font["head"].xMin, font["head"].yMin, font["head"].xMax, font["head"].yMax]
    bbox_1000 = [round(v * 1000 / units_per_em) for v in bbox]
    ascent_1000 = round(ascent * 1000 / units_per_em)
    descent_1000 = round(descent * 1000 / units_per_em)

    pdf = pikepdf.Pdf.new()

    # Font file stream
    font_stream = pikepdf.Stream(pdf, font_bytes)
    font_stream["/Length1"] = len(font_bytes)

    # FontDescriptor
    font_descriptor = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name(ps_name),
            "/Flags": 4,
            "/FontBBox": pikepdf.Array(bbox_1000),
            "/ItalicAngle": 0,
            "/Ascent": ascent_1000,
            "/Descent": descent_1000,
            "/CapHeight": round(font["OS/2"].sCapHeight * 1000 / units_per_em),
            "/StemV": 80,
            "/FontFile2": font_stream,
        }
    )

    # CIDFont (descendant)
    cid_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/BaseFont": pikepdf.Name(ps_name),
            "/CIDSystemInfo": pikepdf.Dictionary(
                {
                    "/Registry": pikepdf.String("Adobe"),
                    "/Ordering": pikepdf.String("Identity"),
                    "/Supplement": 0,
                }
            ),
            "/FontDescriptor": pdf.make_indirect(font_descriptor),
            "/DW": 1000,
            "/W": pikepdf.Array(_build_w_array(w_entries)),
            "/CIDToGIDMap": pikepdf.Name("/Identity"),
        }
    )

    # ToUnicode stream
    tounicode_stream = pikepdf.Stream(pdf, tounicode_str.encode("latin-1"))

    # Type0 font (top-level)
    type0_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/BaseFont": pikepdf.Name(ps_name),
            "/Encoding": pikepdf.Name("/Identity-H"),
            "/DescendantFonts": pikepdf.Array([pdf.make_indirect(cid_font)]),
            "/ToUnicode": tounicode_stream,
        }
    )

    # Page
    page_dict = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, W, H]),
            "/Resources": pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": pdf.make_indirect(type0_font),
                        }
                    ),
                }
            ),
            "/Contents": pikepdf.Stream(pdf, content_stream),
        }
    )
    pdf.pages.append(pikepdf.Page(page_dict))
    pdf.save(str(path))
    pdf.close()

    # Verify
    with pikepdf.Pdf.open(str(path)) as verify_pdf:
        p = verify_pdf.pages[0]
        fd = p["/Resources"]["/Font"]
        for key in fd:
            f_obj = fd[key]
            if str(f_obj.get("/Subtype", "")) == "/Type0":
                enc = str(f_obj.get("/Encoding", ""))
                if "Identity-H" in enc:
                    print(f"  Level 5: Verified Identity-H encoding ({ttf_path.name})")
                    return True

    print("  Level 5: WARNING — construction did not produce Identity-H")
    path.unlink(missing_ok=True)
    return False


def _build_w_array(entries: list[object]) -> list[object]:
    """Convert flat [gid, [width], gid, [width], ...] to pikepdf objects."""
    result: list[object] = []
    i = 0
    while i < len(entries):
        gid = entries[i]
        widths = entries[i + 1]
        result.append(gid)
        if isinstance(widths, list):
            result.append(pikepdf.Array(widths))
        i += 2
    return result


# ── Manifest management ────────────────────────────────────────────────

COMPLEX_MANIFEST_ENTRIES: list[dict[str, object]] = [
    {
        "filename": "complex_multifont.pdf",
        "generator": "reportlab",
        "encoding": "WinAnsi",
        "features": ["3 fonts", "mixed fonts same line", "dollar amounts"],
        "expected_text": "Contact us at",
    },
    {
        "filename": "complex_transformed.pdf",
        "generator": "reportlab",
        "encoding": "WinAnsi",
        "features": ["horizontal scaling", "rotation", "small text", "char spacing"],
        "expected_text": "Normal text here",
    },
    {
        "filename": "complex_contract.pdf",
        "generator": "reportlab",
        "encoding": "WinAnsi",
        "features": ["3 pages", "headings", "numbered clauses", "table", "signatures"],
        "expected_text": "SERVICE AGREEMENT",
    },
    {
        "filename": "complex_pymupdf_edited.pdf",
        "generator": "reportlab+PyMuPDF",
        "encoding": "WinAnsi",
        "features": ["previously edited", "mixed font origins", "redact+replace"],
        "expected_text": "PyMuPDF Edited Author",
    },
    {
        "filename": "cidfont_synthetic.pdf",
        "generator": "reportlab+TTFont",
        "encoding": "Identity-H",
        "features": ["CIDFont", "Type0", "TrueType subset", "synthetic"],
        "expected_text": "Software Engineer",
    },
]


def update_manifest(corpus_dir: Path, generated: dict[str, bool]) -> None:
    """Merge new entries into the existing manifest.json."""
    manifest_path = corpus_dir / "manifest.json"
    existing: list[dict[str, object]] = []
    if manifest_path.exists():
        with open(manifest_path, encoding="utf-8") as f:
            existing = json.load(f)

    existing_names = {e["filename"] for e in existing}

    for entry in COMPLEX_MANIFEST_ENTRIES:
        fname = str(entry["filename"])
        if fname not in existing_names and generated.get(fname, False):
            existing.append(entry)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ── Main entry point ───────────────────────────────────────────────────


def generate_all_complex(corpus_dir: Path | None = None) -> dict[str, bool]:
    """Generate all complex corpus PDFs.

    Returns:
        Dict mapping filename to whether it was successfully generated.
    """
    target = corpus_dir or CORPUS_DIR
    target.mkdir(parents=True, exist_ok=True)

    generated: dict[str, bool] = {}

    # Level 1: Multi-font
    out1 = target / "complex_multifont.pdf"
    make_multi_font_pdf(out1)
    generated["complex_multifont.pdf"] = True
    print(f"  Level 1: {out1}")

    # Level 2: Transformed text
    out2 = target / "complex_transformed.pdf"
    make_transformed_text_pdf(out2)
    generated["complex_transformed.pdf"] = True
    print(f"  Level 2: {out2}")

    # Level 3: Contract
    out3 = target / "complex_contract.pdf"
    make_contract_pdf(out3)
    generated["complex_contract.pdf"] = True
    print(f"  Level 3: {out3}")

    # Level 4: PyMuPDF-edited (conditional)
    out4 = target / "complex_pymupdf_edited.pdf"
    ok4 = make_previously_edited_pdf(out4)
    generated["complex_pymupdf_edited.pdf"] = ok4
    if ok4:
        print(f"  Level 4: {out4}")
    else:
        print("  Level 4: Skipped (PyMuPDF not installed)")

    # Level 5: CIDFont (conditional)
    out5 = target / "cidfont_synthetic.pdf"
    ok5 = make_cidfont_pdf(out5)
    generated["cidfont_synthetic.pdf"] = ok5
    if not ok5:
        print("  Level 5: Not generated or not Identity-H")

    # Update manifest
    update_manifest(target, generated)
    print(f"  Manifest updated: {target / 'manifest.json'}")

    return generated


if __name__ == "__main__":
    print("Generating complex corpus PDFs...")
    results = generate_all_complex()
    total = sum(1 for v in results.values() if v)
    print(f"\nGenerated {total}/{len(results)} complex PDFs")
    sys.exit(0 if total >= 3 else 1)
