"""Builder: TrueType / Identity-H baseline PDF.

The "happy path" reference document — a single page of Identity-H CIDFont
text rendered from an embedded TrueType subset. This is the control case
against which the adversarial builders (CFF, XObject, Arabic, tagged) are
compared: anything the engine can do on a real PDF it must do here first.

Deterministic: no network, fixed font timestamps, reproducible /ID.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write, find_truetype_font, save_pdf_deterministic
from ._truetype_assembler import TextLine, embed_identity_h_font

if TYPE_CHECKING:
    from pathlib import Path

_LINES: tuple[TextLine, ...] = (
    TextLine("Acme Corporation Annual Report", x=72.0, y=730.0, font_size=18.0),
    TextLine("Prepared by the Finance Department", x=72.0, y=700.0, font_size=12.0),
    TextLine("Revenue grew 24% year over year.", x=72.0, y=676.0, font_size=12.0),
    TextLine("Net income reached $4,250,000 in fiscal 2026.", x=72.0, y=652.0, font_size=12.0),
)


def build_truetype_baseline_pdf(out_path: Path | None = None) -> bytes | None:
    """Build a baseline TrueType / Identity-H PDF.

    Args:
        out_path: Optional file to write the PDF to. The bytes are returned
            regardless.

    Returns:
        The PDF bytes, or ``None`` if no host TrueType font is available.
    """
    ttf_path = find_truetype_font()
    if ttf_path is None:
        return None

    corpus = "".join(line.text for line in _LINES)
    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf_path, corpus)

        ops: list[str] = ["BT"]
        for line in _LINES:
            ops.append(f"{line.font_resource} {line.font_size} Tf")
            ops.append(f"1 0 0 1 {line.x} {line.y} Tm")
            ops.append(f"<{font.encode(line.text)}> Tj")
        ops.append("ET")
        content = "\n".join(ops).encode("latin-1")

        page = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Page"),
                "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
                "/Resources": pikepdf.Dictionary(
                    {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(font.type0)})}
                ),
                "/Contents": pikepdf.Stream(pdf, content),
            }
        )
        pdf.pages.append(pikepdf.Page(page))
        return emit_or_write(save_pdf_deterministic(pdf), out_path)
    finally:
        pdf.close()
