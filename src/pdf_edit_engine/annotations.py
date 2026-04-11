"""Annotations module — read and modify PDF annotations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pikepdf

from pdf_edit_engine._pathutil import validate_output_path
from pdf_edit_engine.errors import PDFEditError


def _open_pdf(path: str, **kwargs: object) -> pikepdf.Pdf:
    """Open a PDF file, translating pikepdf errors to PDFEditError."""
    try:
        return pikepdf.Pdf.open(path, **kwargs)  # type: ignore[arg-type]
    except pikepdf.PasswordError:
        raise PDFEditError("PDF is password-protected") from None
    except pikepdf.PdfError as exc:
        raise PDFEditError(f"Cannot open PDF: {exc}") from None
    except FileNotFoundError:
        raise PDFEditError(f"PDF file not found: {Path(path).name}") from None
    except IsADirectoryError:
        raise PDFEditError("Expected a file path, got a directory") from None
    except PermissionError:
        raise PDFEditError(f"Permission denied: {Path(path).name}") from None


@dataclass(frozen=True)
class Annotation:
    """A PDF annotation with its position and content."""

    index: int
    page: int
    subtype: str
    rect: tuple[float, float, float, float]
    uri: str | None
    text: str | None


def get_annotations(
    pdf_path: str,
    page: int | None = None,
) -> list[Annotation]:
    """List all annotations in the PDF.

    Args:
        pdf_path: Path to the PDF file.
        page: Optional 0-indexed page number. If None, returns all pages.

    Returns:
        List of Annotation objects.
    """
    path = str(Path(pdf_path).resolve())
    annotations: list[Annotation] = []
    with _open_pdf(path) as pdf:
        for page_num, page_obj in enumerate(pdf.pages):
            if page is not None and page_num != page:
                continue
            if "/Annots" not in page_obj:
                continue
            annots_arr: Any = page_obj["/Annots"]
            for idx in range(len(annots_arr)):
                annot_ref: Any = annots_arr[idx]
                annot: Any = annot_ref.resolve() if hasattr(annot_ref, "resolve") else annot_ref
                subtype = str(annot.get("/Subtype", "")).lstrip("/")
                rect_obj: Any = annot.get("/Rect")
                if rect_obj is None or len(rect_obj) < 4:
                    continue
                rect = (
                    float(rect_obj[0]),
                    float(rect_obj[1]),
                    float(rect_obj[2]),
                    float(rect_obj[3]),
                )
                uri: str | None = None
                if "/A" in annot:
                    action: Any = annot["/A"]
                    if "/URI" in action:
                        uri = str(action["/URI"])
                text_content: str | None = None
                if "/Contents" in annot:
                    text_content = str(annot["/Contents"])
                annotations.append(
                    Annotation(
                        index=idx,
                        page=page_num,
                        subtype=subtype,
                        rect=rect,
                        uri=uri,
                        text=text_content,
                    )
                )
    return annotations


def update_annotation_uri(
    pdf_path: str,
    annot: Annotation,
    new_uri: str,
    output_path: str,
) -> None:
    """Change the URI of a Link annotation.

    Args:
        pdf_path: Path to the input PDF file.
        annot: The Annotation to modify (from get_annotations).
        new_uri: New URI string.
        output_path: Path for the output PDF.
    """
    validate_output_path(output_path)
    path = str(Path(pdf_path).resolve())
    out = str(Path(output_path).resolve())
    allow_overwrite = path == out
    with _open_pdf(path, allow_overwriting_input=allow_overwrite) as pdf:
        page_obj = pdf.pages[annot.page]
        annots_arr: Any = page_obj["/Annots"]
        if not (0 <= annot.index < len(annots_arr)):
            raise PDFEditError(
                f"Annotation index {annot.index} out of bounds "
                f"(page has {len(annots_arr)} annotations)"
            )
        target: Any = annots_arr[annot.index]
        if hasattr(target, "resolve"):
            target = target.resolve()
        if "/A" not in target:
            target["/A"] = pikepdf.Dictionary({"/S": pikepdf.Name("/URI")})
        target["/A"]["/URI"] = pikepdf.String(new_uri)
        pdf.save(out)


def delete_annotation(
    pdf_path: str,
    annot: Annotation,
    output_path: str,
) -> None:
    """Remove an annotation from the PDF.

    Args:
        pdf_path: Path to the input PDF file.
        annot: The Annotation to delete (from get_annotations).
        output_path: Path for the output PDF.
    """
    validate_output_path(output_path)
    path = str(Path(pdf_path).resolve())
    out = str(Path(output_path).resolve())
    allow_overwrite = path == out
    with _open_pdf(path, allow_overwriting_input=allow_overwrite) as pdf:
        page_obj = pdf.pages[annot.page]
        if "/Annots" not in page_obj:
            pdf.save(out)
            return
        annots_list: list[Any] = list(page_obj["/Annots"])  # type: ignore[call-overload]
        if 0 <= annot.index < len(annots_list):
            annots_list.pop(annot.index)
        if annots_list:
            page_obj["/Annots"] = pdf.make_indirect(pikepdf.Array(annots_list))
        else:
            del page_obj["/Annots"]  # type: ignore[operator]
        pdf.save(out)


def move_annotation(
    pdf_path: str,
    annot: Annotation,
    new_rect: tuple[float, float, float, float],
    output_path: str,
) -> None:
    """Move an annotation to a new position.

    Args:
        pdf_path: Path to the input PDF file.
        annot: The Annotation to move (from get_annotations).
        new_rect: New (x0, y0, x1, y1) rectangle in PDF points.
        output_path: Path for the output PDF.
    """
    validate_output_path(output_path)
    path = str(Path(pdf_path).resolve())
    out = str(Path(output_path).resolve())
    allow_overwrite = path == out
    with _open_pdf(path, allow_overwriting_input=allow_overwrite) as pdf:
        page_obj = pdf.pages[annot.page]
        annots_arr: Any = page_obj["/Annots"]
        if not (0 <= annot.index < len(annots_arr)):
            raise PDFEditError(
                f"Annotation index {annot.index} out of bounds "
                f"(page has {len(annots_arr)} annotations)"
            )
        target: Any = annots_arr[annot.index]
        if hasattr(target, "resolve"):
            target = target.resolve()
        target["/Rect"] = pikepdf.Array([float(v) for v in new_rect])
        pdf.save(out)


def add_annotation(
    pdf_path: str,
    page: int,
    rect: tuple[float, float, float, float],
    uri: str,
    output_path: str,
    border_style: str = "none",
) -> None:
    """Add a Link annotation to the PDF at the specified position.

    Args:
        pdf_path: Path to the input PDF file.
        page: 0-indexed page number.
        rect: (x0, y0, x1, y1) rectangle in PDF user space units.
        uri: The URL the link points to.
        output_path: Path for the output PDF (can be same as pdf_path).
        border_style: "none" for invisible border, "underline" for blue underline.
    """
    validate_output_path(output_path)
    path = str(Path(pdf_path).resolve())
    out = str(Path(output_path).resolve())
    allow_overwrite = path == out
    with _open_pdf(path, allow_overwriting_input=allow_overwrite) as pdf:
        page_obj = pdf.pages[page]
        annot = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Link"),
                "/Rect": pikepdf.Array([float(v) for v in rect]),
                "/A": pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Action"),
                        "/S": pikepdf.Name("/URI"),
                        "/URI": pikepdf.String(uri),
                    }
                ),
                "/Border": pikepdf.Array([0, 0, 0]),
                "/F": 4,
            }
        )
        if border_style == "underline":
            annot["/Border"] = pikepdf.Array([0, 0, 1])
            annot["/C"] = pikepdf.Array([0.0, 0.0, 1.0])
        if "/Annots" not in page_obj:
            page_obj["/Annots"] = pikepdf.Array([])
        page_obj["/Annots"].append(pdf.make_indirect(annot))
        pdf.save(out)
