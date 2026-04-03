"""pikepdf wrapper operations — thin wrappers around pikepdf's API."""

from __future__ import annotations

# --- Page operations ---


def merge_pdfs(pdf_paths: list[str], output_path: str) -> str:
    """Merge multiple PDFs into a single document.

    Args:
        pdf_paths: List of paths to PDF files to merge, in order.
        output_path: Path for the merged output PDF.

    Returns:
        Path to the output file.
    """
    raise NotImplementedError


def split_pdf(pdf_path: str, output_dir: str) -> list[str]:
    """Split a PDF into individual pages.

    Args:
        pdf_path: Path to the PDF file to split.
        output_dir: Directory to write individual page PDFs.

    Returns:
        List of paths to the output page PDFs.
    """
    raise NotImplementedError


def reorder_pages(pdf_path: str, page_order: list[int], output_path: str) -> str:
    """Reorder pages in a PDF.

    Args:
        pdf_path: Path to the input PDF.
        page_order: List of 0-indexed page numbers in desired order.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    raise NotImplementedError


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
    raise NotImplementedError


def delete_pages(pdf_path: str, pages: list[int], output_path: str) -> str:
    """Delete specified pages from a PDF.

    Args:
        pdf_path: Path to the input PDF.
        pages: List of 0-indexed page numbers to delete.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    raise NotImplementedError


def crop_pages(
    pdf_path: str, box: tuple[float, float, float, float], output_path: str
) -> str:
    """Crop all pages to the specified bounding box.

    Args:
        pdf_path: Path to the input PDF.
        box: Crop box as (x1, y1, x2, y2) in PDF points.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    raise NotImplementedError


# --- Document operations ---


def edit_metadata(pdf_path: str, metadata: dict[str, str], output_path: str) -> str:
    """Edit PDF document metadata (title, author, subject, etc.).

    Args:
        pdf_path: Path to the input PDF.
        metadata: Dictionary of metadata keys and values to set.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    raise NotImplementedError


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
    raise NotImplementedError


def encrypt_pdf(
    pdf_path: str, owner_pass: str, user_pass: str, output_path: str
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
    raise NotImplementedError


def decrypt_pdf(pdf_path: str, password: str, output_path: str) -> str:
    """Decrypt a password-protected PDF.

    Args:
        pdf_path: Path to the input PDF.
        password: Password to decrypt the PDF.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    raise NotImplementedError


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
    raise NotImplementedError


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
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    raise NotImplementedError


def flatten_annotations(pdf_path: str, output_path: str) -> str:
    """Flatten all annotations into the page content.

    Args:
        pdf_path: Path to the input PDF.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    raise NotImplementedError


# --- Form and other operations ---


def fill_form(pdf_path: str, field_values: dict[str, str], output_path: str) -> str:
    """Fill form fields in a PDF.

    Args:
        pdf_path: Path to the input PDF with form fields.
        field_values: Dictionary mapping field names to values.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    raise NotImplementedError


def add_watermark(pdf_path: str, watermark_path: str, output_path: str) -> str:
    """Add a watermark from another PDF to all pages.

    Args:
        pdf_path: Path to the input PDF.
        watermark_path: Path to the PDF containing the watermark.
        output_path: Path for the output PDF.

    Returns:
        Path to the output file.
    """
    raise NotImplementedError
