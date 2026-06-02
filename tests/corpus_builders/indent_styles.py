"""Deterministic corpus builder for paragraph-indent reflow probes (E.2).

Net-new test tooling (no ``src/`` changes). Supports the E.2 indent-style
round-trip invariant probes (``test_g_7_indent_style_roundtrip``).

Like :mod:`reflow_quality`, the geometry these probes exercise is encoding-
agnostic: they only need single body paragraphs at known positions whose text
the engine can re-wrap. So this builder emits a WinAnsi document drawn with
the standard-14 ``Helvetica`` font. Helvetica needs no embedding and its AFM
glyph widths are fixed, which makes both the PDF bytes AND the engine's width
measurements deterministic on every host (no ``find_truetype_font`` skip path
required).

The fixture lays out three independent body paragraphs, each separated by a
large vertical gap so the detector keeps them distinct:

- a **FIRST-LINE-indent** paragraph: line 0 starts further right than the
  flush continuations (``line0_x > continuation_x``);
- a **HANGING-indent** paragraph: line 0 starts at the left margin and every
  continuation is pushed right (``line0_x < continuation_x``);
- a **FLUSH** paragraph: every line starts at the same left x.

Each paragraph carries a unique find-anchor on its FIRST line so a probe can
locate it with :func:`pdf_edit_engine.find` and force a wider replacement that
re-wraps it. The continuation lines hold filler text only — the replacement
substitutes the first-line span and re-wraps the whole paragraph, so the
emitted positioning operators reflect the classified indent style, not the
original per-line text.
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
LEADING: float = 15.0
LEFT_MARGIN: float = 72.0
# Indent magnitude: well above the engine's MIN_INDENT noise floor
# (font_size * 0.6 == 6.6pt at 11pt) so the classifier is confident.
INDENT: float = 36.0

# ── FIRST-LINE-indent paragraph (line 0 pushed right) ─────────────────
FIRST_LINE_TOP: float = 720.0
FIRST_LINE_ANCHOR: str = "Firstline indented opening sentence"
_FIRST_LINE_PARA: tuple[tuple[str, float], ...] = (
    (f"{FIRST_LINE_ANCHOR} of this paragraph here", LEFT_MARGIN + INDENT),
    ("continuation alpha runs flush against the left margin", LEFT_MARGIN),
    ("continuation beta also sits flush at the left margin", LEFT_MARGIN),
)

# ── HANGING-indent paragraph (continuations pushed right) ─────────────
HANGING_TOP: float = 560.0
HANGING_ANCHOR: str = "Hanging flush opening sentence"
_HANGING_PARA: tuple[tuple[str, float], ...] = (
    (f"{HANGING_ANCHOR} of this paragraph here", LEFT_MARGIN),
    ("continuation gamma is indented under the hanging margin", LEFT_MARGIN + INDENT),
    ("continuation delta also indented under the hanging margin", LEFT_MARGIN + INDENT),
)

# ── FLUSH paragraph (every line at the same x) ────────────────────────
FLUSH_TOP: float = 400.0
FLUSH_ANCHOR: str = "Flush block opening sentence"
_FLUSH_PARA: tuple[tuple[str, float], ...] = (
    (f"{FLUSH_ANCHOR} of this paragraph here", LEFT_MARGIN),
    ("continuation epsilon stays flush against the left margin", LEFT_MARGIN),
    ("continuation zeta also stays flush at the left margin too", LEFT_MARGIN),
)

# ── Single-line / ambiguous paragraph (one line only) ─────────────────
SINGLE_TOP: float = 260.0
SINGLE_ANCHOR: str = "Single ambiguous standalone sentence"
_SINGLE_PARA: tuple[tuple[str, float], ...] = (
    (f"{SINGLE_ANCHOR} alone on its own line here", LEFT_MARGIN + INDENT),
)

# ── Non-monotone multi-line paragraph (continuations mutually inconsistent) ──
# A MULTI-LINE paragraph whose continuation x-starts disagree by more than the
# engine's MIN_INDENT noise floor (font_size * 0.6 == 6.6pt at 11pt). This is a
# real-but-unclassifiable indent signal: ``_detect_indent_style`` cannot resolve
# it to first-line or hanging, so it falls back to FLUSH and the caller surfaces
# ``indent_flattened`` (info). The two continuation deltas (NONMONO_DELTA_A,
# NONMONO_DELTA_B) are both above the noise floor AND differ from each other by
# well above it, while every line stays within the detector's 50pt x-jump
# grouping window so the three lines remain one paragraph.
NONMONO_TOP: float = 140.0
NONMONO_ANCHOR: str = "Nonmonotone inconsistent opening sentence"
NONMONO_DELTA_A: float = 12.0
NONMONO_DELTA_B: float = 40.0
_NONMONO_PARA: tuple[tuple[str, float], ...] = (
    (f"{NONMONO_ANCHOR} of this paragraph here", LEFT_MARGIN),
    ("continuation eta sits at a small inconsistent indent", LEFT_MARGIN + NONMONO_DELTA_A),
    ("continuation theta sits at a larger inconsistent indent", LEFT_MARGIN + NONMONO_DELTA_B),
)


def _escape(text: str) -> str:
    """Escape a literal string for a PDF ``(...)`` Tj operand."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _emit_paragraph(
    ops: list[str],
    para: tuple[tuple[str, float], ...],
    top_baseline: float,
) -> None:
    """Append the operators that draw one paragraph at ``top_baseline``.

    Each line gets its own absolute ``Tm`` so the per-line start x is exactly
    the value the builder specifies (the engine reads these back as the
    paragraph's ``x_starts``).
    """
    for i, (line, x) in enumerate(para):
        y = top_baseline - i * LEADING
        ops.append(f"1 0 0 1 {x:g} {y:g} Tm")
        ops.append(f"({_escape(line)}) Tj")


def build_indent_styles_pdf(out_path: Path | None = None) -> bytes:
    """Build the deterministic multi-paragraph indent-style fixture.

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
    _emit_paragraph(ops, _FIRST_LINE_PARA, FIRST_LINE_TOP)
    _emit_paragraph(ops, _HANGING_PARA, HANGING_TOP)
    _emit_paragraph(ops, _FLUSH_PARA, FLUSH_TOP)
    _emit_paragraph(ops, _SINGLE_PARA, SINGLE_TOP)
    _emit_paragraph(ops, _NONMONO_PARA, NONMONO_TOP)
    ops.append("ET")
    page.Contents = pdf.make_stream(("\n".join(ops)).encode("latin-1"))

    return emit_or_write(save_pdf_deterministic(pdf), out_path)


def build_wrapping_replacement(
    anchor: str,
    measure_word: object,
    paragraph_width: float,
) -> str:
    """Build a replacement long enough to re-wrap the paragraph onto >= 2 lines.

    The replacement keeps the original first-line anchor as a prefix (so the
    paragraph's classified indent style is unchanged across the edit) and
    appends enough filler that a greedy wrap spills onto continuation lines,
    exposing the per-line positioning operators the probe inspects.

    Self-calibrating against the supplied word-measurement callable so the
    wrap is forced regardless of fractional Helvetica metric drift.

    Args:
        anchor: The first-line anchor text to keep as the replacement prefix.
        measure_word: Callable ``(word) -> float`` returning a word's rendered
            width at the body font/size (the probe binds the engine's
            ``_measure_word``). Typed ``object`` to avoid importing engine
            internals into this test-tooling module.
        paragraph_width: Available wrap width in page-space points.

    Returns:
        The replacement text whose greedy wrap spans at least two lines.
    """
    measure = measure_word  # type: ignore[assignment]
    filler = "word"
    filler_w = measure(filler + " ")  # type: ignore[operator]
    # Enough filler words to comfortably exceed two line widths.
    needed = int((paragraph_width * 2.2) / max(filler_w, 1.0)) + 2
    words = [anchor, *([filler] * needed)]
    return " ".join(words)
