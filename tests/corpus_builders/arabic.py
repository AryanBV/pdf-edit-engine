"""Builder: Arabic-script Identity-H PDF.

Exercises the engine against a non-Latin, right-to-left script. The string
is rendered from an embedded TrueType subset of a host font that covers the
Arabic block (Arial / Tahoma / Segoe UI on Windows, DejaVu Sans on Linux).

Scope note: this builder emits the Arabic codepoints in *logical* order via
the Identity-H CID map; it does not run a shaping engine (no contextual
joining or bidi reordering). That is intentional — the fixture's job is to
present genuine Arabic Unicode + a covering CIDFont to the engine's
extraction / location / encoding paths, not to typeset publication-quality
Arabic. ``ToUnicode`` maps each CID back to its Arabic codepoint, so text
extraction round-trips.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write, find_arabic_font, save_pdf_deterministic
from ._truetype_assembler import embed_identity_h_font

if TYPE_CHECKING:
    from pathlib import Path

# "Hello" and "World" in Arabic, plus a Latin label so the fixture also has
# a mixed-script line. Marhaban = مرحبا, al-Alam = العالم.
_ARABIC_GREETING = "مرحبا"  # مرحبا
_ARABIC_WORLD = "العالم"  # العالم
_LATIN_LABEL = "Arabic sample:"
_CORPUS = _ARABIC_GREETING + _ARABIC_WORLD + _LATIN_LABEL + " "


def build_arabic_pdf(out_path: Path | None = None) -> bytes | None:
    """Build an Identity-H PDF containing an Arabic string.

    Args:
        out_path: Optional file to write the PDF to. The bytes are returned
            regardless.

    Returns:
        The PDF bytes, or ``None`` if no installed font covers the Arabic
        characters.
    """
    ttf_path = find_arabic_font(_ARABIC_GREETING + _ARABIC_WORLD)
    if ttf_path is None:
        return None

    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf_path, _CORPUS)

        arabic_line = f"{_ARABIC_GREETING} {_ARABIC_WORLD}"
        ops = [
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 700 Tm",
            f"<{font.encode(_LATIN_LABEL)}> Tj",
            "/F1 20 Tf",
            "1 0 0 1 72 660 Tm",
            f"<{font.encode(arabic_line)}> Tj",
            "ET",
        ]
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
