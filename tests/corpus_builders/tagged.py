"""Builder: tagged (accessible) PDF with marked content and /ActualText.

Produces a Tagged-PDF structure: a ``/StructTreeRoot`` in the catalog, a
``/P`` (paragraph) structure element pointing at a marked-content sequence,
and the page content wrapped in ``/P <</MCID 0>> BDC ... EMC``. The BDC
property dictionary carries an ``/ActualText`` entry whose Unicode string
differs from the glyphs actually drawn — the canonical case where the
"visible" CID text and the accessibility text diverge, and where an editor
must decide which one wins.

The drawn text uses a real TrueType / Identity-H subset; the ``/ActualText``
is a UTF-16BE string (the PDF spec's text-string encoding).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write, find_truetype_font, save_pdf_deterministic
from ._truetype_assembler import embed_identity_h_font

if TYPE_CHECKING:
    from pathlib import Path

# What the glyphs spell vs. what the accessibility layer reports.
_DRAWN_TEXT = "Q3 FY26"
_ACTUAL_TEXT = "Third Quarter Fiscal Year 2026"


def _utf16be_hex(text: str) -> str:
    """Encode ``text`` as a UTF-16BE PDF text-string hex literal body."""
    return text.encode("utf-16-be").hex().upper()


def build_tagged_pdf(out_path: Path | None = None) -> bytes | None:
    """Build a Tagged PDF with marked content and an /ActualText override.

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
        font = embed_identity_h_font(pdf, ttf_path, _DRAWN_TEXT)

        # /ActualText lives in the BDC property dictionary as a UTF-16BE
        # text string; the drawn glyphs are the short CID form.
        content = (
            f"/P <</MCID 0 /ActualText <{_utf16be_hex(_ACTUAL_TEXT)}>>> BDC\n"
            "BT\n/F1 20 Tf\n1 0 0 1 72 700 Tm\n"
            f"<{font.encode(_DRAWN_TEXT)}> Tj\nET\n"
            "EMC\n"
        ).encode("latin-1")

        content_stream = pikepdf.Stream(pdf, content)

        page = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Page"),
                    "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
                    "/Resources": pikepdf.Dictionary(
                        {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(font.type0)})}
                    ),
                    "/Contents": content_stream,
                }
            )
        )
        pdf.pages.append(pikepdf.Page(page))

        # Build the structure tree: StructTreeRoot -> P element -> MCID 0.
        struct_tree_root = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/StructTreeRoot"),
                }
            )
        )
        para_elem = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/StructElem"),
                    "/S": pikepdf.Name("/P"),
                    "/P": struct_tree_root,
                    "/Pg": page,
                    "/K": 0,  # marked-content id (MCID) on /Pg
                    "/ActualText": pikepdf.String(_ACTUAL_TEXT),
                }
            )
        )
        struct_tree_root["/K"] = para_elem

        # Mark the document as tagged so consumers honour the struct tree.
        pdf.Root["/StructTreeRoot"] = struct_tree_root
        pdf.Root["/MarkInfo"] = pikepdf.Dictionary({"/Marked": True})

        return emit_or_write(save_pdf_deterministic(pdf), out_path)
    finally:
        pdf.close()
