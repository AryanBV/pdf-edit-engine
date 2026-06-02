"""Builder: rotated / non-axis-aligned Identity-H text PDF.

POS-GATE fixture. A single page whose text is drawn under a 90-degree
rotated text matrix (``Tm = [0 -1 1 0 x y]``) — a NON-axis-aligned matrix
(``a~0, b~-1, c~1, d~0``, i.e. NOT ``a~1 AND b~0 AND c~0 AND d~1``). Two
text runs share the same rotated baseline so that an edit to the first run
leaves a *trailing* run for ``surgeon._adjust_subsequent_positioning`` to
(mis-)shift: the current engine subtracts ``width_delta`` from the trailing
run's horizontal operand, which is wrong when the baseline is rotated.

A sibling ``build_axis_aligned_two_run_pdf`` emits the SAME two runs under a
plain ``Tm = [1 0 0 1 x y]`` identity-rotation matrix; it is the INV-POS-1
regression control (axis-aligned compensation must remain byte-identical).

Deterministic: no network, fixed font timestamps, reproducible /ID. Returns
``None`` when no host TrueType font is installed (cidfont_synthetic skipif
precedent).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write, find_truetype_font, save_pdf_deterministic
from ._truetype_assembler import embed_identity_h_font

if TYPE_CHECKING:
    from pathlib import Path

# Two runs on one baseline. The first run is the edit target; the second is
# the trailing run whose positioning the engine adjusts. Keeping both glyph
# sets in the subset means a same-subset edit needs no font extension.
_RUN_A_TEXT = "Section"
_RUN_B_TEXT = "Heading"
_CORPUS = _RUN_A_TEXT + _RUN_B_TEXT

# Rotated 90deg counter-clockwise text matrix: a=0, b=-1, c=1, d=0.
_ROT_TM = (0.0, -1.0, 1.0, 0.0)
# Axis-aligned identity-rotation control: a=1, b=0, c=0, d=1.
_AXIS_TM = (1.0, 0.0, 0.0, 1.0)

_FONT_SIZE = 14.0
_ORIGIN_X = 300.0
_ORIGIN_Y = 700.0


def _build_two_run_pdf(
    out_path: Path | None,
    tm_abcd: tuple[float, float, float, float],
) -> bytes | None:
    """Emit a one-page PDF: two Identity-H runs sharing one ``Tm`` baseline.

    The first run is positioned by an absolute ``Tm`` whose linear part is
    ``tm_abcd``; the second run follows on the same baseline via a relative
    ``Td`` advance so it is a same-line trailing run from the engine's
    perspective.

    Args:
        out_path: Optional file to write the PDF to. Bytes are returned
            regardless.
        tm_abcd: The ``(a, b, c, d)`` linear part of the text matrix for the
            first run. ``(1, 0, 0, 1)`` is axis-aligned; ``(0, -1, 1, 0)`` is
            a 90-degree rotation.

    Returns:
        The PDF bytes, or ``None`` if no host TrueType font is available.
    """
    ttf_path = find_truetype_font()
    if ttf_path is None:
        return None

    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf_path, _CORPUS)
        a, b, c, d = tm_abcd

        # Advance of run A along the run's own (rotated) baseline, in text
        # space. The trailing run is moved by this advance plus a small gap
        # via a relative Td so it lands just after run A on the same line.
        adv_a = font.advance(_RUN_A_TEXT, _FONT_SIZE)
        gap = font.advance(" ", _FONT_SIZE) if " " in font.cp_to_gid else _FONT_SIZE * 0.3

        ops: list[str] = [
            "BT",
            f"/F1 {_FONT_SIZE} Tf",
            f"{a} {b} {c} {d} {_ORIGIN_X} {_ORIGIN_Y} Tm",
            f"<{font.encode(_RUN_A_TEXT)}> Tj",
            # Trailing run on the SAME baseline: a same-line relative move.
            f"{adv_a + gap} 0 Td",
            f"<{font.encode(_RUN_B_TEXT)}> Tj",
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


def build_rotated_text_pdf(out_path: Path | None = None) -> bytes | None:
    """Build a 90deg-rotated two-run Identity-H PDF (POS-GATE adversarial case).

    Args:
        out_path: Optional file to write the PDF to.

    Returns:
        The PDF bytes, or ``None`` if no host TrueType font is available.
    """
    return _build_two_run_pdf(out_path, _ROT_TM)


def build_axis_aligned_two_run_pdf(out_path: Path | None = None) -> bytes | None:
    """Build the axis-aligned two-run control PDF (INV-POS-1 regression base).

    Identical content to :func:`build_rotated_text_pdf` except the first run's
    text matrix is the axis-aligned identity rotation ``[1 0 0 1 x y]``.

    Args:
        out_path: Optional file to write the PDF to.

    Returns:
        The PDF bytes, or ``None`` if no host TrueType font is available.
    """
    return _build_two_run_pdf(out_path, _AXIS_TM)
