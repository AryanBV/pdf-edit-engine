"""Deterministic corpus builder for declared-leading reflow probes (E.3).

Net-new test tooling (no ``src/`` changes). Supports the E.3 declared-leading
capture + re-emit invariant probes (``test_g_8_declared_leading_reemit``).

Like :mod:`indent_styles` and :mod:`reflow_quality`, the geometry these probes
exercise is encoding-agnostic, so this builder emits a WinAnsi document drawn
with the standard-14 ``Helvetica`` font (no embedding, fixed AFM widths,
deterministic on every host).

The distinguishing feature of E.3 is the line-advance *mechanism*. The
:mod:`indent_styles` builder gives every line its own absolute ``Tm`` — the
declared leading is irrelevant there. Here the paragraphs advance lines via the
PDF ``TL`` (set text leading) + ``T*`` (next line) operators, so the document's
*declared leading* is the authoritative line-advance value the reflow must
preserve.

Three paragraphs are laid out, each separated by a large vertical gap so the
detector keeps them distinct:

- a **TL single-line** paragraph: one line drawn after ``LEADING_DECL TL`` with
  NO ``T*`` after it (single-line source). The declared leading
  (``LEADING_DECL`` == 18) is chosen DISTINCT from the engine's
  ``font_size * 1.2`` single-line proxy (11 * 1.2 == 13.2). When a wide
  replacement re-wraps this one line onto several lines, the pre-E.3 engine
  emits the 13.2 proxy advance and silently discards the declared 18.

- a **TL multi-line** paragraph: two lines advanced by ``T*`` under
  ``LEADING_DECL TL``. Authoritative leading is again 18.

- a **control non-TL** paragraph: every line gets its own absolute ``Tm`` (no
  ``TL`` / ``T*`` ever issued). This paragraph has NO authoritative declared
  leading, so E.3 must leave its reflow output byte-identical to the pre-E.3
  engine.

- a **q/Q leak** paragraph: a ``TL LEAK_LEADING`` is issued INSIDE a ``q ... Q``
  block (e.g. for an unrelated graphics object), then the block is closed and a
  separate paragraph is drawn with its OWN absolute ``Tm`` per line and NO
  ``TL``/``T*``. If the in-q leading (or its authoritative flag) leaks past the
  ``Q``, the outer paragraph would be mis-treated as carrying an authoritative
  leading of ``LEAK_LEADING`` and reflow with the wrong line advance. The leak
  guard requires the outer paragraph stay byte-identical (control behaviour).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write, save_pdf_deterministic

if TYPE_CHECKING:
    from pathlib import Path

# ── Shared page + font geometry ───────────────────────────────────────
PAGE_WIDTH: float = 612.0
PAGE_HEIGHT: float = 792.0
FONT_SIZE: float = 11.0
LEFT_MARGIN: float = 72.0

# Declared leading set via the ``TL`` operator. Chosen DISTINCT from the
# engine's single-line ``font_size * 1.2`` proxy (11 * 1.2 == 13.2) AND from a
# plausible measured y-gap so a probe can tell the declared value apart from a
# re-synthesized one.
LEADING_DECL: float = 18.0
# Single-line proxy the pre-E.3 engine emits when it has no measured y-gap.
PROXY_LINE_HEIGHT: float = FONT_SIZE * 1.2  # 13.2

# Leading set inside a q...Q block — must NOT leak to the outer paragraph.
LEAK_LEADING: float = 30.0

# ── TL single-line paragraph (authoritative leading, one source line) ──
TL_SINGLE_TOP: float = 720.0
TL_SINGLE_ANCHOR: str = "Singleline declared leading opening"

# ── TL multi-line paragraph (authoritative leading via T*) ────────────
TL_MULTI_TOP: float = 600.0
TL_MULTI_ANCHOR: str = "Multiline declared leading opening"
_TL_MULTI_LINES: tuple[str, ...] = (
    f"{TL_MULTI_ANCHOR} of this paragraph here",
    "continuation advanced by an explicit Tstar operator",
)

# ── Control non-TL paragraph (no declared leading; per-line Tm) ───────
CONTROL_TOP: float = 460.0
CONTROL_GAP: float = 14.0
CONTROL_ANCHOR: str = "Control absolute tm opening sentence"
_CONTROL_LINES: tuple[str, ...] = (
    f"{CONTROL_ANCHOR} of this paragraph here",
    "continuation placed by its own absolute text matrix",
)

# ── q/Q leak paragraph (outer paragraph must not inherit in-q leading) ─
LEAK_TOP: float = 320.0
LEAK_GAP: float = 14.0
LEAK_ANCHOR: str = "Leak guard outer paragraph opening"
_LEAK_LINES: tuple[str, ...] = (
    f"{LEAK_ANCHOR} of this paragraph here",
    "continuation outside the q block with its own tm",
)


def _escape(text: str) -> str:
    """Escape a literal string for a PDF ``(...)`` Tj operand."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _emit_tl_single(ops: list[str]) -> None:
    """Draw the single-line paragraph: declared leading, one line, no T*."""
    ops.append(f"{LEADING_DECL:g} TL")
    ops.append(f"1 0 0 1 {LEFT_MARGIN:g} {TL_SINGLE_TOP:g} Tm")
    ops.append(f"({_escape(TL_SINGLE_ANCHOR + ' alone on its own line here')}) Tj")


def _emit_tl_multi(ops: list[str]) -> None:
    """Draw the multi-line paragraph advancing lines with T* under TL."""
    ops.append(f"{LEADING_DECL:g} TL")
    ops.append(f"1 0 0 1 {LEFT_MARGIN:g} {TL_MULTI_TOP:g} Tm")
    for i, line in enumerate(_TL_MULTI_LINES):
        if i > 0:
            ops.append("T*")
        ops.append(f"({_escape(line)}) Tj")


def _emit_control(ops: list[str]) -> None:
    """Draw the control paragraph with a per-line absolute Tm and no TL/T*."""
    for i, line in enumerate(_CONTROL_LINES):
        y = CONTROL_TOP - i * CONTROL_GAP
        ops.append(f"1 0 0 1 {LEFT_MARGIN:g} {y:g} Tm")
        ops.append(f"({_escape(line)}) Tj")


def _emit_leak(ops: list[str]) -> None:
    """Set a leading inside a q...Q block, then draw the outer paragraph.

    The ``TL LEAK_LEADING`` is confined to the ``q ... Q`` scope (as if it were
    set for an unrelated graphics object). After ``Q`` the outer paragraph is
    drawn with its own absolute ``Tm`` per line and never issues ``TL``/``T*``,
    so it carries NO authoritative declared leading. The leak guard fails if the
    in-q leading (or its authoritative flag) survives past ``Q``.
    """
    ops.append("q")
    ops.append(f"{LEAK_LEADING:g} TL")
    ops.append("Q")
    for i, line in enumerate(_LEAK_LINES):
        y = LEAK_TOP - i * LEAK_GAP
        ops.append(f"1 0 0 1 {LEFT_MARGIN:g} {y:g} Tm")
        ops.append(f"({_escape(line)}) Tj")


def build_declared_leading_pdf(out_path: Path | None = None) -> bytes:
    """Build the deterministic declared-leading reflow fixture.

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

    ops: list[str] = ["BT", f"/F1 {FONT_SIZE:g} Tf"]
    _emit_tl_single(ops)
    _emit_tl_multi(ops)
    _emit_control(ops)
    _emit_leak(ops)
    ops.append("ET")
    page.Contents = pdf.make_stream(("\n".join(ops)).encode("latin-1"))

    return emit_or_write(save_pdf_deterministic(pdf), out_path)
