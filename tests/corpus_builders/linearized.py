"""Deterministic corpus builders for the linearization-preservation probes.

Net-new test tooling (no ``src/`` changes). Supports the A2.2
linearization detect-and-preserve invariant probes
(``test_w_3_linearization_preserved_on_save``).

A *linearized* PDF (a.k.a. "Fast Web View") places a linearization
parameter dictionary at the front of the file so a viewer can render the
first page before the whole file has downloaded. pikepdf can both emit a
linearized layout (``pdf.save(..., linearize=True)``) and detect one on an
opened document (``pdf.is_linearized``). The A2.2 invariant is that the
engine, which today *always* saves non-linearized, must DETECT a linearized
input and PRESERVE that property on save.

Both builders emit the SAME logical document — a single WinAnsi standard-14
Helvetica paragraph the engine can locate and replace — and differ ONLY in
whether the bytes are linearized. Helvetica is a standard-14 font requiring
no host-font discovery, so these builders always succeed (never ``None``)
and the bytes are deterministic on every host.

The two builders share :func:`_build_base_pdf` so the *content* is
byte-identical; only the final ``save`` flag differs. That keeps the
linearized / non-linearized pair a clean A/B control: any output-state
difference a probe observes is attributable to the save-time
linearization flag alone, not to a content divergence.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write

if TYPE_CHECKING:
    from pathlib import Path

PAGE_WIDTH: float = 612.0
PAGE_HEIGHT: float = 792.0
BODY_LEFT: float = 72.0
BODY_FONT_SIZE: float = 12.0
BODY_BASELINE: float = 700.0
# The logical text the probe passes to find() to locate the editable run.
BODY_TEXT: str = "Fast Web View linearized body text here"
# A locatable substring anchoring the replace; short so a same-or-shorter
# replacement keeps the simple (non-reflow) splice path that preserves the
# rest of the document untouched.
FIND_ANCHOR: str = "linearized"
REPLACEMENT: str = "preserved"


def _build_base_pdf() -> pikepdf.Pdf:
    """Construct the shared single-paragraph Helvetica document (unsaved).

    Returns:
        An open ``pikepdf.Pdf`` whose single page carries one WinAnsi
        Helvetica text run. The caller chooses the save-time linearization
        flag; the in-memory object is identical for both variants.
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

    escaped = BODY_TEXT.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = (
        "BT",
        f"/F1 {BODY_FONT_SIZE:g} Tf",
        f"1 0 0 1 {BODY_LEFT:g} {BODY_BASELINE:g} Tm",
        f"({escaped}) Tj",
        "ET",
    )
    page.Contents = pdf.make_stream(("\n".join(ops)).encode("latin-1"))
    return pdf


def build_linearized_pdf(out_path: Path | None = None) -> bytes:
    """Build the deterministic LINEARIZED (Fast Web View) fixture.

    Saved with ``linearize=True`` so a re-opened copy reports
    ``is_linearized is True``. This is the A2.2 subject: editing it through
    the engine must keep the output linearized.

    Args:
        out_path: Optional destination; when given the bytes are written
            there before being returned.

    Returns:
        The linearized PDF bytes. Never ``None``.
    """
    pdf = _build_base_pdf()
    buf = io.BytesIO()
    # ``static_id`` keeps the /ID reproducible; ``linearize=True`` is the
    # only flag that distinguishes this from the control builder. We do NOT
    # use ``save_pdf_deterministic`` here because that helper hard-codes a
    # non-linearized save (compress_streams=False, no linearize).
    pdf.save(buf, static_id=True, linearize=True)
    return emit_or_write(buf.getvalue(), out_path)


def build_nonlinearized_pdf(out_path: Path | None = None) -> bytes:
    """Build the deterministic NON-linearized control fixture.

    Byte-content-identical to :func:`build_linearized_pdf` except saved with
    the default (non-linearized) layout, so a re-opened copy reports
    ``is_linearized is False``. This is the A2.2 control: editing it must
    leave the output non-linearized and emit NO ``linearization_dropped``.

    Args:
        out_path: Optional destination; when given the bytes are written
            there before being returned.

    Returns:
        The non-linearized PDF bytes. Never ``None``.
    """
    pdf = _build_base_pdf()
    buf = io.BytesIO()
    pdf.save(buf, static_id=True)
    return emit_or_write(buf.getvalue(), out_path)
