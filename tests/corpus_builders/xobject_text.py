"""Builder: text drawn through a Form XObject invoked with ``Do``.

Most of the engine's locator/surgeon machinery walks the *page* content
stream. When the text-drawing operators live inside a Form XObject and the
page stream only contains ``/Fm1 Do``, naive page-only parsing misses the
text entirely. This builder produces that adversarial layout: the page
references a Form XObject whose own content stream holds the BT/ET text.

The XObject embeds a real TrueType / Identity-H subset so the text is
genuine CIDFont content, not a synthetic placeholder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write, find_truetype_font, save_pdf_deterministic
from ._truetype_assembler import embed_identity_h_font

if TYPE_CHECKING:
    from pathlib import Path

_TEXT = "Text inside a Form XObject"


def build_xobject_text_pdf(out_path: Path | None = None) -> bytes | None:
    """Build a PDF whose only page text lives in a Form XObject.

    Args:
        out_path: Optional file to write the PDF to. The bytes are returned
            regardless.

    Returns:
        The PDF bytes, or ``None`` if no host TrueType font is available.
    """
    ttf_path = find_truetype_font()
    if ttf_path is None:
        return None

    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf_path, _TEXT)

        # The Form XObject's content stream holds the actual text operators.
        xobject_ops = (
            "BT\n/F1 16 Tf\n1 0 0 1 10 60 Tm\n" + f"<{font.encode(_TEXT)}> Tj\nET"
        ).encode("latin-1")
        form_xobject = pikepdf.Stream(pdf, xobject_ops)
        form_xobject["/Type"] = pikepdf.Name("/XObject")
        form_xobject["/Subtype"] = pikepdf.Name("/Form")
        form_xobject["/FormType"] = 1
        form_xobject["/BBox"] = pikepdf.Array([0, 0, 400, 100])
        form_xobject["/Matrix"] = pikepdf.Array([1, 0, 0, 1, 0, 0])
        form_xobject["/Resources"] = pikepdf.Dictionary(
            {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(font.type0)})}
        )

        # The page stream only translates into place and invokes the form.
        page_ops = b"q\n1 0 0 1 72 660 cm\n/Fm1 Do\nQ\n"

        page = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Page"),
                "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
                "/Resources": pikepdf.Dictionary(
                    {"/XObject": pikepdf.Dictionary({"/Fm1": pdf.make_indirect(form_xobject)})}
                ),
                "/Contents": pikepdf.Stream(pdf, page_ops),
            }
        )
        pdf.pages.append(pikepdf.Page(page))
        return emit_or_write(save_pdf_deterministic(pdf), out_path)
    finally:
        pdf.close()
