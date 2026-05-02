"""INV-G-5 (P0): narrow single-line paragraph with inline-continuation
overlap MUST either reflow correctly or emit an explicit warning.

Holds in v0.1.2: the same EditResult.__post_init__ guard that closes
INV-J-3 also surfaces overflow on this scenario (overflow_detected
implies an "overflow" warning entry).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as rl_canvas

from pdf_edit_engine import replace_all

if TYPE_CHECKING:
    from pathlib import Path


def _make_narrow_paragraph_pdf(path: Path) -> None:
    """Build a PDF with two stacked single-line paragraphs.

    Paragraph 1 starts narrow at top (so a longer replacement spills
    horizontally past page-width). Paragraph 2 sits just below.
    """
    page_w, page_h = letter
    c = rl_canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 12)
    # P1 starts near right margin so any width-increase pushes off-page
    c.drawString(page_w - 80, page_h - 50, "TARGET")
    c.drawString(72, page_h - 80, "untouched continuation line below")
    c.save()


def test_inv_g_5_inline_continuation_overlap(tmp_path: Path) -> None:
    """Narrow paragraph + longer replacement: must warn or reflow."""
    src = tmp_path / "narrow.pdf"
    out = tmp_path / "out.pdf"
    _make_narrow_paragraph_pdf(src)

    results = replace_all(str(src), "TARGET", "X" * 80, str(out))
    assert results, "expected at least one match"
    r = results[0]

    # Acceptance: success=True implies SOME overflow signal must reach
    # the caller. The caller cannot otherwise tell that text now extends
    # past page-width.
    overflow_signaled = (
        r.fidelity_report.overflow_detected
        or r.fidelity_report.reflow_applied
        or any("overflow" in w.lower() or "page width" in w.lower() for w in r.warnings)
    )
    assert overflow_signaled, (
        "narrow-paragraph horizontal overflow not signaled to caller "
        "(no overflow_detected, no reflow_applied, no warning)"
    )
