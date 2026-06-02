"""Deterministic corpus builder for colored-run reflow color-fidelity probes.

Net-new test tooling (no ``src/`` changes). Supports the Block F CORE color
slice invariant probes (INV-F-8 Separation color-space preservation through
reflow, INV-F-9 ``color_space_approximated`` honest degradation).

Like :mod:`reflow_quality`, the geometry these probes exercise is
encoding-agnostic — they only need body paragraphs at known positions whose
text the engine can re-wrap. So this builder emits WinAnsi documents drawn
with the standard-14 ``Helvetica`` font: no embedding, fixed AFM widths, and
therefore byte-stable PDF output AND deterministic engine width measurement on
every host (no ``find_truetype_font`` skip path required).

Two builders:

- :func:`build_separation_run_pdf` — a single reflowable body paragraph whose
  fill color is set via a **Separation** color space
  (``/CS0 cs 0.8 scn``). The pre-Block-F reflow path collapses this to device
  gray (``0.8 g``), losing the spot-color identity; INV-F-8 pins that the
  Separation color-setting operator subsequence survives the rewrite.
- :func:`build_devicergb_run_pdf` — the regression control: the same paragraph
  shape with a device-RGB fill (``1 0 0 rg``). The pre-Block-F length-guess
  already round-trips a 3-float RGB fill; INV-F-8's control pins that Block F
  does not regress it.

Both place the paragraph on three baselines at a fixed font size and leading
so a probe can reason about ``paragraph_width`` without parsing first; the
probes self-calibrate the wider replacement text against the engine's own
width measurement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write, save_pdf_deterministic

if TYPE_CHECKING:
    from pathlib import Path

# Fixed page + paragraph geometry — mirrors reflow_quality.py so the probes
# share the same calibration story (x=72, 11pt body, 15pt leading).
PAGE_WIDTH: float = 612.0
PAGE_HEIGHT: float = 792.0
BODY_LEFT: float = 72.0
BODY_FONT_SIZE: float = 11.0
BODY_LEADING: float = 15.0
BODY_TOP_BASELINE: float = 700.0
BODY_LINES: tuple[str, ...] = (
    "This is a colored body paragraph used to exercise reflow color",
    "preservation across the operator rewrite that re-wraps the text",
    "when a wider replacement no longer fits the original line breaks.",
)
# Anchor on the LAST body line so the wider replacement becomes the
# paragraph's trailing content and forces a re-wrap (same rationale as
# reflow_quality.BODY_FIND_ANCHOR).
BODY_FIND_ANCHOR: str = BODY_LINES[-1]

# The Separation color-space resource name and tint used by the builder.
# Probes assert these tokens survive the rewrite verbatim.
SEPARATION_CS_NAME: str = "CS0"
SEPARATION_TINT: float = 0.8


def _add_helvetica(pdf: pikepdf.Pdf) -> pikepdf.Object:
    """Create an indirect standard-14 Helvetica/WinAnsi font object."""
    return pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )


def _body_text_ops(color_setup: list[str]) -> bytes:
    """Build the page content stream: BT, color setup, then the three lines."""
    ops = ["BT", f"/F1 {BODY_FONT_SIZE:g} Tf", *color_setup]
    for i, line in enumerate(BODY_LINES):
        y = BODY_TOP_BASELINE - i * BODY_LEADING
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"1 0 0 1 {BODY_LEFT:g} {y:g} Tm")
        ops.append(f"({escaped}) Tj")
    ops.append("ET")
    return ("\n".join(ops)).encode("latin-1")


def _body_text_ops_per_line(per_line_color: list[list[str]]) -> bytes:
    """Build the content stream re-setting fill color BEFORE each body line.

    Each ``per_line_color[i]`` is the color-setting operator subsequence (raw
    content-stream tokens) emitted immediately before line ``i``'s ``Tj``. This
    lets a probe place a *different* (or precision-varied) fill color on each
    line of a single paragraph so each captured ``ContentElement`` carries its
    own ``fill_color_ops`` snapshot.
    """
    assert len(per_line_color) == len(BODY_LINES)
    ops = ["BT", f"/F1 {BODY_FONT_SIZE:g} Tf"]
    for i, line in enumerate(BODY_LINES):
        y = BODY_TOP_BASELINE - i * BODY_LEADING
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.extend(per_line_color[i])
        ops.append(f"1 0 0 1 {BODY_LEFT:g} {y:g} Tm")
        ops.append(f"({escaped}) Tj")
    ops.append("ET")
    return ("\n".join(ops)).encode("latin-1")


def build_separation_run_pdf(out_path: Path | None = None) -> bytes:
    """Build a single reflowable paragraph filled via a Separation color space.

    The content stream sets fill color with ``/CS0 cs`` followed by
    ``0.8 scn`` — a spot color whose identity the pre-Block-F reflow path
    silently collapses to ``0.8 g`` (device gray) on re-wrap.

    Args:
        out_path: Optional destination; when given the bytes are written
            there before being returned.

    Returns:
        The PDF file bytes. Never ``None`` — Helvetica is standard-14 and the
        Separation color space is self-contained, so this builder always
        succeeds with no host-font discovery.
    """
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(PAGE_WIDTH, PAGE_HEIGHT))
    font = _add_helvetica(pdf)

    # Separation color space: [/Separation /Sep1 /DeviceGray <tint transform>].
    # The tint transform is a trivial FunctionType 4 (PostScript calculator)
    # that passes the single tint component through — enough to make the color
    # space well-formed and pikepdf-parseable; its exact math is irrelevant to
    # the color-OPERATOR-preservation contract the probe pins.
    tint_fn = pdf.make_indirect(
        pikepdf.Stream(
            pdf,
            b"{ }",
            Domain=[0.0, 1.0],
            Range=[0.0, 1.0],
            FunctionType=4,
        )
    )
    sep_cs = pdf.make_indirect(
        pikepdf.Array(
            [pikepdf.Name.Separation, pikepdf.Name.Sep1, pikepdf.Name.DeviceGray, tint_fn]
        )
    )
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=font),
        ColorSpace=pikepdf.Dictionary(**{SEPARATION_CS_NAME: sep_cs}),
    )

    color_setup = [f"/{SEPARATION_CS_NAME} cs", f"{SEPARATION_TINT:g} scn"]
    page.Contents = pdf.make_stream(_body_text_ops(color_setup))

    return emit_or_write(save_pdf_deterministic(pdf), out_path)


def build_devicergb_run_pdf(out_path: Path | None = None) -> bytes:
    """Build a single reflowable paragraph filled via device RGB (regression control).

    The content stream sets fill color with ``1 0 0 rg`` (pure red). The
    pre-Block-F length-guess already round-trips a 3-float RGB fill; this is
    INV-F-8's no-regression control.

    Args:
        out_path: Optional destination; when given the bytes are written
            there before being returned.

    Returns:
        The PDF file bytes. Never ``None``.
    """
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(PAGE_WIDTH, PAGE_HEIGHT))
    font = _add_helvetica(pdf)
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))

    color_setup = ["1 0 0 rg"]
    page.Contents = pdf.make_stream(_body_text_ops(color_setup))

    return emit_or_write(save_pdf_deterministic(pdf), out_path)


def build_mixed_precision_run_pdf(out_path: Path | None = None) -> bytes:
    """Build a SINGLE-color paragraph whose lines vary the literal precision.

    All three body lines are pure red, but each writes the fill via a
    different numeric-literal precision: ``1 0 0 rg`` / ``1.0 0.0 0.0 rg`` /
    ``1.00 0.00 0.00 rg``. The rendered color is identical on every line, so
    reflow must NOT treat this as a multi-color paragraph. A keyer that
    compares operands by raw ``str()`` sees three distinct keys and falsely
    emits ``color_space_approximated(multi_color_run)`` — the false-emission
    bug this fixture pins.

    Args:
        out_path: Optional destination; when given the bytes are written
            there before being returned.

    Returns:
        The PDF file bytes. Never ``None``.
    """
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(PAGE_WIDTH, PAGE_HEIGHT))
    font = _add_helvetica(pdf)
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))

    per_line_color = [
        ["1 0 0 rg"],
        ["1.0 0.0 0.0 rg"],
        ["1.00 0.00 0.00 rg"],
    ]
    page.Contents = pdf.make_stream(_body_text_ops_per_line(per_line_color))

    return emit_or_write(save_pdf_deterministic(pdf), out_path)


def build_genuine_multicolor_run_pdf(out_path: Path | None = None) -> bytes:
    """Build a GENUINELY multi-color paragraph (red, then blue, then green).

    Each body line uses a different device-RGB fill, so the paragraph really
    does carry more than one distinct color. Reflow collapses to element[0]'s
    color on re-wrap and must surface ``color_space_approximated`` honestly —
    this fixture pins that the false-emission fix does NOT over-suppress the
    genuine case.

    Args:
        out_path: Optional destination; when given the bytes are written
            there before being returned.

    Returns:
        The PDF file bytes. Never ``None``.
    """
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(PAGE_WIDTH, PAGE_HEIGHT))
    font = _add_helvetica(pdf)
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))

    per_line_color = [
        ["1 0 0 rg"],
        ["0 0 1 rg"],
        ["0 1 0 rg"],
    ]
    page.Contents = pdf.make_stream(_body_text_ops_per_line(per_line_color))

    return emit_or_write(save_pdf_deterministic(pdf), out_path)
