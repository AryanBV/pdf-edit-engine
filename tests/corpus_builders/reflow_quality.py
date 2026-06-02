"""Deterministic corpus builder for typographic-quality reflow probes.

Net-new test tooling (no ``src/`` changes). Supports the E.4 widow/orphan
and E.6 line-height-compression invariant probes
(``test_e_4_reflow_widow_orphan_surfaced`` and
``test_e_6_line_height_compressed_emitted``).

Unlike the Identity-H builders in this package, the geometry these probes
exercise is encoding-agnostic — they only need a single body paragraph at a
known position whose text the engine can re-wrap. So this builder emits a
WinAnsi document drawn with the standard-14 ``Helvetica`` font. Helvetica
needs no embedding and its AFM glyph widths are fixed, which makes both the
PDF bytes AND the engine's width measurements deterministic on every host
(no ``find_truetype_font`` skip path required).

The single body paragraph is placed on three baselines at a fixed font size
and leading so a probe can reason about ``paragraph_width`` and the natural
single-line ratio without parsing the document first. The probes self-
calibrate the exact widow / compression-forcing replacement text against the
engine's own width measurement, so they stay robust if Helvetica metrics
ever shift fractionally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write, save_pdf_deterministic

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# Fixed page + paragraph geometry. The body paragraph occupies three lines at
# x=72, baselines y=700/685/670 (15pt leading == natural single-line gap), at
# 11pt — matching the ``reportlab_simple`` body block the existing reflow
# probes already calibrate against, but emitted deterministically here so the
# E.* probes own their fixture.
PAGE_WIDTH: float = 612.0
PAGE_HEIGHT: float = 792.0
BODY_LEFT: float = 72.0
BODY_FONT_SIZE: float = 11.0
BODY_LEADING: float = 15.0
BODY_TOP_BASELINE: float = 700.0
BODY_LINES: tuple[str, ...] = (
    "This is a simple body paragraph used to exercise paragraph reflow.",
    "It carries enough words across several baselines that a wider",
    "replacement re-wraps it into additional lines.",
)
# The contiguous logical text a probe passes to find() to locate the block.
# Anchored on the LAST body line: ``reflow_paragraph`` substitutes the matched
# span inside the paragraph's full_text, so a widow word only lands on the
# final wrapped line when the replacement becomes the paragraph's *trailing*
# content. Anchoring on the first line would leave the original tail after the
# replacement, so the widow word could never reach the last line.
BODY_FIND_ANCHOR: str = BODY_LINES[-1]


def build_reflow_quality_pdf(out_path: Path | None = None) -> bytes:
    """Build the deterministic single-paragraph reflow-quality fixture.

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

    ops = ["BT", f"/F1 {BODY_FONT_SIZE:g} Tf"]
    for i, line in enumerate(BODY_LINES):
        y = BODY_TOP_BASELINE - i * BODY_LEADING
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"1 0 0 1 {BODY_LEFT:g} {y:g} Tm")
        ops.append(f"({escaped}) Tj")
    ops.append("ET")
    page.Contents = pdf.make_stream(("\n".join(ops)).encode("latin-1"))

    return emit_or_write(save_pdf_deterministic(pdf), out_path)


def build_widow_replacement(
    paragraph_width: float,
    measure_word: Callable[[str], float],
    space_width: float,
) -> str:
    """Build a replacement string that re-wraps into a single-word widow.

    The result is three near-full-width filler words followed by one short
    tail word (``"ok"``). With the filler sized just under the line width,
    the tail cannot share any filler's line and lands alone on the final
    line — the widow the E.4 detector must surface.

    Self-calibrating: the caller passes the engine's own ``_measure_word``
    (and space width) so the filler length is tuned to the measured Helvetica
    metrics rather than a hard-coded character count.

    Args:
        paragraph_width: Available wrap width in page-space points.
        measure_word: Callable ``(word) -> float`` returning a word's
            rendered width at the body font/size (the probe binds the
            engine's ``_measure_word`` with its fixed trailing args).
        space_width: Rendered width of a single inter-word space.

    Returns:
        The replacement text whose greedy wrap ends in a lone short word.
    """
    measure = measure_word
    tail = "ok"
    tail_w = measure(tail)
    # Largest filler that still leaves no room for " ok" on its line.
    floor = paragraph_width - space_width - tail_w
    n = 1
    while measure("m" * (n + 1)) <= paragraph_width:
        n += 1
        if measure("m" * n) > floor and measure("m" * n) <= paragraph_width:
            break
    filler = "m" * n
    return f"{filler} {filler} {filler} {tail}"


def build_compression_text(line_count: int) -> str:
    """Build replacement text that wraps into at least ``line_count`` lines.

    Used by the E.6 probe to overfill a short fixed-height bbox so the
    structural reflow compresses line height below the natural ratio.

    Args:
        line_count: Lower bound on the number of wrapped lines desired.

    Returns:
        A space-joined run of distinct short tokens.
    """
    # Each token is short enough that several fit per line; a generous token
    # count guarantees the wrap exceeds ``line_count`` for any sane bbox.
    return " ".join(f"word{i:02d}" for i in range(max(line_count, 1) * 10))
