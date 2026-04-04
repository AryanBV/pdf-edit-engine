"""pikepdf wrapper operations — thin wrappers around pikepdf's API."""

from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_edit_engine._pathutil import validate_output_dir, validate_output_path
from pdf_edit_engine.errors import PDFEditError

# --- Page operations ---


def merge_pdfs(pdf_paths: list[str], output_path: str) -> str:
    """Merge multiple PDFs into a single document.

    Args:
        pdf_paths: List of paths to PDF files to merge, in order.
        output_path: Path for the merged output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    if not pdf_paths:
        msg = "No PDF paths provided"
        raise PDFEditError(msg)
    pdf = pikepdf.Pdf.open(pdf_paths[0])
    for path in pdf_paths[1:]:
        other = pikepdf.Pdf.open(path)
        pdf.pages.extend(other.pages)
    pdf.save(output_path)
    return output_path


def split_pdf(pdf_path: str, output_dir: str) -> list[str]:
    """Split a PDF into individual pages.

    Args:
        pdf_path: Path to the PDF file to split.
        output_dir: Directory to write individual page PDFs.

    Returns:
        List of paths to the output page PDFs.
    """
    validate_output_dir(output_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pikepdf.Pdf.open(pdf_path)
    outputs: list[str] = []
    for i, page in enumerate(pdf.pages):
        new_pdf = pikepdf.Pdf.new()
        new_pdf.pages.append(page)
        out = str(out_dir / f"page_{i}.pdf")
        new_pdf.save(out)
        outputs.append(out)
    return outputs


def reorder_pages(pdf_path: str, page_order: list[int], output_path: str) -> str:
    """Reorder pages in a PDF.

    Args:
        pdf_path: Path to the input PDF.
        page_order: List of 0-indexed page numbers in desired order.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    new_pdf = pikepdf.Pdf.new()
    for i in page_order:
        new_pdf.pages.append(pdf.pages[i])
    new_pdf.save(output_path)
    return output_path


def rotate_pages(pdf_path: str, pages: list[int], angle: int, output_path: str) -> str:
    """Rotate specified pages in a PDF.

    Args:
        pdf_path: Path to the input PDF.
        pages: List of 0-indexed page numbers to rotate.
        angle: Rotation angle in degrees (90, 180, or 270).
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    if angle not in (90, 180, 270):
        msg = f"Angle must be 90, 180, or 270, got {angle}"
        raise PDFEditError(msg)
    pdf = pikepdf.Pdf.open(pdf_path)
    for i in pages:
        page = pdf.pages[i]
        rotate_val = page.get("/Rotate")
        existing = int(rotate_val) if rotate_val is not None else 0
        page["/Rotate"] = (existing + angle) % 360
    pdf.save(output_path)
    return output_path


def delete_pages(pdf_path: str, pages: list[int], output_path: str) -> str:
    """Delete specified pages from a PDF.

    Args:
        pdf_path: Path to the input PDF.
        pages: List of 0-indexed page numbers to delete.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    for i in sorted(pages, reverse=True):
        del pdf.pages[i]
    pdf.save(output_path)
    return output_path


def crop_pages(
    pdf_path: str,
    box: tuple[float, float, float, float],
    output_path: str,
) -> str:
    """Crop all pages to the specified bounding box.

    Args:
        pdf_path: Path to the input PDF.
        box: Crop box as (x1, y1, x2, y2) in PDF points.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    for page in pdf.pages:
        page["/CropBox"] = pikepdf.Array(
            [pikepdf.Object.parse(str(v).encode()) for v in box],
        )
    pdf.save(output_path)
    return output_path


# --- Document operations ---


def edit_metadata(pdf_path: str, metadata: dict[str, str], output_path: str) -> str:
    """Edit PDF document metadata (title, author, subject, etc.).

    Args:
        pdf_path: Path to the input PDF.
        metadata: Dictionary of metadata keys and values to set.
            Keys can use XMP namespaces directly (e.g. 'dc:title')
            or simple names ('title' -> 'dc:title', 'author' -> 'dc:creator').
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    _simple_to_xmp = {
        "title": "dc:title",
        "author": "dc:creator",
        "subject": "dc:description",
        "creator": "xmp:CreatorTool",
    }
    pdf = pikepdf.Pdf.open(pdf_path)
    with pdf.open_metadata() as meta:
        for key, value in metadata.items():
            xmp_key = _simple_to_xmp.get(key, key)
            meta[xmp_key] = value
    pdf.save(output_path)
    return output_path


def add_bookmark(pdf_path: str, title: str, page: int, output_path: str) -> str:
    """Add a bookmark (outline entry) to a PDF.

    Args:
        pdf_path: Path to the input PDF.
        title: Bookmark title text.
        page: 0-indexed page number the bookmark points to.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    with pdf.open_outline() as outline:
        outline.root.append(pikepdf.OutlineItem(title, page))
    pdf.save(output_path)
    return output_path


def encrypt_pdf(
    pdf_path: str,
    owner_pass: str,
    user_pass: str,
    output_path: str,
) -> str:
    """Encrypt a PDF with owner and user passwords.

    Args:
        pdf_path: Path to the input PDF.
        owner_pass: Owner password (full permissions).
        user_pass: User password (restricted permissions).
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    pdf.save(
        output_path,
        encryption=pikepdf.Encryption(owner=owner_pass, user=user_pass),
    )
    return output_path


def decrypt_pdf(pdf_path: str, password: str, output_path: str) -> str:
    """Decrypt a password-protected PDF.

    Args:
        pdf_path: Path to the input PDF.
        password: Password to decrypt the PDF.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path, password=password)
    pdf.save(output_path)
    return output_path


# --- Annotation operations ---


def add_hyperlink(
    pdf_path: str,
    page: int,
    bbox: tuple[float, float, float, float],
    uri: str,
    output_path: str,
) -> str:
    """Add a hyperlink annotation to a PDF page.

    Args:
        pdf_path: Path to the input PDF.
        page: 0-indexed page number.
        bbox: Link area as (x1, y1, x2, y2) in PDF points.
        uri: Target URI for the hyperlink.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    target_page = pdf.pages[page]
    annot = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name.Annot,
                "/Subtype": pikepdf.Name.Link,
                "/Rect": pikepdf.Array(
                    [pikepdf.Object.parse(str(v).encode()) for v in bbox],
                ),
                "/A": pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name.Action,
                        "/S": pikepdf.Name.URI,
                        "/URI": pikepdf.String(uri),
                    }
                ),
                "/Border": pikepdf.Array([0, 0, 0]),
            }
        )
    )
    if "/Annots" not in target_page:
        target_page["/Annots"] = pikepdf.Array()
    target_page["/Annots"].append(annot)
    pdf.save(output_path)
    return output_path


def add_highlight(
    pdf_path: str,
    page: int,
    quad_points: list[float],
    output_path: str,
) -> str:
    """Add a highlight annotation to a PDF page.

    Args:
        pdf_path: Path to the input PDF.
        page: 0-indexed page number.
        quad_points: List of coordinates defining the highlight quadrilateral.
            Eight values per quad: x1,y1, x2,y2, x3,y3, x4,y4.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    target_page = pdf.pages[page]
    # Derive bounding rect from quad points
    xs = quad_points[0::2]
    ys = quad_points[1::2]
    rect = [min(xs), min(ys), max(xs), max(ys)]
    annot = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name.Annot,
                "/Subtype": pikepdf.Name.Highlight,
                "/Rect": pikepdf.Array(
                    [pikepdf.Object.parse(str(v).encode()) for v in rect],
                ),
                "/QuadPoints": pikepdf.Array(
                    [pikepdf.Object.parse(str(v).encode()) for v in quad_points],
                ),
                "/C": pikepdf.Array([1, 1, 0]),  # Yellow
            }
        )
    )
    if "/Annots" not in target_page:
        target_page["/Annots"] = pikepdf.Array()
    target_page["/Annots"].append(annot)
    pdf.save(output_path)
    return output_path


def flatten_annotations(pdf_path: str, output_path: str) -> str:
    """Flatten all annotations into the page content.

    Uses the fallback approach: removes /Annots from all pages,
    stripping annotations without rendering them into the content stream.
    True annotation flattening (merging appearance streams) is non-trivial
    and deferred to a future version.

    Args:
        pdf_path: Path to the input PDF.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    for page in pdf.pages:
        page_obj = page.obj
        if "/Annots" in page_obj:
            del page_obj["/Annots"]
    pdf.save(output_path)
    return output_path


# --- Form and other operations ---


def fill_form(pdf_path: str, field_values: dict[str, str], output_path: str) -> str:
    """Fill form fields in a PDF.

    Walks the /AcroForm/Fields tree recursively and sets /V for matching
    field names. Sets /NeedAppearances to let PDF viewers regenerate
    field appearance streams.

    Args:
        pdf_path: Path to the input PDF with form fields.
        field_values: Dictionary mapping field names to values.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    if "/AcroForm" not in pdf.Root:
        msg = "PDF has no AcroForm"
        raise PDFEditError(msg)

    def _fill_fields(fields: pikepdf.Object) -> None:
        for field in list(fields):  # type: ignore[call-overload]
            name = str(field.get("/T", ""))
            if name in field_values:
                field["/V"] = pikepdf.String(field_values[name])
            if "/Kids" in field:
                _fill_fields(field["/Kids"])

    acroform = pdf.Root["/AcroForm"]
    if "/Fields" in acroform:
        _fill_fields(acroform["/Fields"])
    acroform["/NeedAppearances"] = True
    pdf.save(output_path)
    return output_path


def add_watermark(pdf_path: str, watermark_path: str, output_path: str) -> str:
    """Add a watermark from another PDF to all pages.

    Uses pikepdf's Page.add_underlay() to place the watermark PDF's first
    page behind the content of each page.

    Args:
        pdf_path: Path to the input PDF.
        watermark_path: Path to the PDF containing the watermark.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    validate_output_path(output_path)
    pdf = pikepdf.Pdf.open(pdf_path)
    watermark = pikepdf.Pdf.open(watermark_path)
    watermark_page = watermark.pages[0]
    for page in pdf.pages:
        page.add_underlay(watermark_page, None)
    pdf.save(output_path)
    return output_path
