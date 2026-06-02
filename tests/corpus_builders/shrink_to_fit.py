"""Deterministic corpus builder for the E.8 shrink-to-fit font-size probes.

Net-new test tooling (no ``src/`` changes). Supports the INV-F-10 opt-in
shrink-to-fit invariant probes (``test_f_10_*``).

E.8 adds an opt-in ``fit`` policy to ``structural.replace_block`` that
binary-searches the font size DOWN until the replacement fits a FIXED-height
bbox region, emitting a typed ``font_size_reduced`` Degradation. The probes
need a document with a known body font/size at a known position so a probe
can replace a *short* fixed-height region with text that OVERFLOWS that region
at the original size — forcing the shrink path. The default ``fit`` value must
reproduce today's no-shrink behaviour byte-for-byte.

Like ``reflow_quality``, the geometry is encoding-agnostic: it only needs a
single body paragraph at a known position the engine can re-wrap. So this
builder emits a WinAnsi document drawn with the standard-14 ``Helvetica``
font. Helvetica needs no embedding and its AFM glyph widths are fixed, which
makes both the PDF bytes AND the engine's width measurements deterministic on
every host (no ``find_truetype_font`` skip path required).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write, save_pdf_deterministic

if TYPE_CHECKING:
    from pathlib import Path

# Fixed page + paragraph geometry. A single body line at x=72, baseline
# y=700, 11pt — a small fixture so a probe can size the replacement region
# precisely. The body line gives the engine a font/size to auto-detect.
PAGE_WIDTH: float = 612.0
PAGE_HEIGHT: float = 792.0
BODY_LEFT: float = 72.0
BODY_TOP_BASELINE: float = 700.0
BODY_FONT_SIZE: float = 11.0
BODY_LINE: str = "Original body line within the fixed-height region."

# The fixed-height region a probe targets. It is intentionally SHORT
# (a ~30pt band — about two 11pt lines of vertical space) so a multi-line
# replacement overflows it at the original 11pt size and must shrink to fit.
# Calibration (against the engine's own break_into_lines width oracle and the
# natural ``size * 1.2`` leading):
#   - ``build_overflow_text(40)`` overflows at 11/10/9/8.5pt but FITS at 8pt
#     (3 lines × 9.6 = 28.8 ≤ 30) — so the shrink search lands at ~8pt,
#     STRICTLY between the 5.5pt floor and the 11pt original (probes a/b).
#   - ``build_overflow_text(400)`` overflows even at the 5.5pt floor
#     (23 lines × 6.6 = 151.8 ≫ 30) — so the search clamps AT the floor and
#     still reports overflow (probe c).
# x spans the page text column.
FIT_BBOX: tuple[float, float, float, float] = (72.0, 674.0, 540.0, 704.0)


def build_shrink_to_fit_pdf(out_path: Path | None = None) -> bytes:
    """Build the deterministic single-line shrink-to-fit fixture.

    Args:
        out_path: Optional destination; when given the bytes are written
            there before being returned.

    Returns:
        The PDF file bytes. Never ``None`` — Helvetica is a standard-14 font
        requiring no host-font discovery, so this builder always succeeds.
    """
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(PAGE_WIDTH, PAGE_HEIGHT))

    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))

    escaped = BODY_LINE.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = [
        "BT",
        f"/F1 {BODY_FONT_SIZE:g} Tf",
        f"1 0 0 1 {BODY_LEFT:g} {BODY_TOP_BASELINE:g} Tm",
        f"({escaped}) Tj",
        "ET",
    ]
    page.Contents = pdf.make_stream(("\n".join(ops)).encode("latin-1"))

    return emit_or_write(save_pdf_deterministic(pdf), out_path)


def build_overflow_text(word_count: int = 40) -> str:
    """Build replacement text that overflows :data:`FIT_BBOX` at the original size.

    The region holds roughly one 11pt line; a multi-word run wraps into many
    lines, so its rendered height far exceeds the region. Used by the E.8
    probes to force the shrink-to-fit binary search.

    Args:
        word_count: Number of distinct short tokens (default 40 — well past
            what one short band can hold even after a 50% shrink).

    Returns:
        A space-joined run of distinct short tokens.
    """
    return " ".join(f"word{i:02d}" for i in range(max(word_count, 1)))
