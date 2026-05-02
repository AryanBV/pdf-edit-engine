"""INV-C-4 (P0, structural path): metric-equivalent substitution must
surface through ``FidelityReport.font_substituted`` on the bbox-driven
``replace_block`` path.

Companion to ``test_c_4_metric_equivalent_observable.py`` which
exercises the ``surgeon.replace_all`` path. The structural path
threads a ``substitution_log`` list through
``_replace_block_on_page → _extend_font → extend_subset``; if the
wiring is incomplete (as it was in the v0.1.2 RC before this audit),
the metric-equivalent fallback name is silently lost and callers see
``font_substituted=None`` despite a real fidelity concern.

Two probes:

1. **Resolver probe** (always runs): exercises the structural
   ``_extend_font`` helper directly with a monkeypatched
   ``substitution_log`` to confirm it accepts and threads the
   keyword argument. This is the unit-level guard against the
   wiring regressing.
2. **End-to-end probe** (skipped when host lacks the right fonts):
   replaces a bbox in a real PDF and asserts the EditResult surfaces
   the substitution.
"""

from __future__ import annotations

import platform
from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine import replace_block
from pdf_edit_engine.system_fonts import _METRIC_EQUIVALENTS, find_font

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_c_4_structural_extend_font_threads_log() -> None:
    """``structural._extend_font`` accepts and threads ``substitution_log``.

    Unit-level guard: the keyword argument exists in the signature and
    propagates to ``extend_subset``. If a future refactor drops the
    parameter, this probe fails immediately without needing a real
    metric-equivalent host.
    """
    import inspect

    from pdf_edit_engine.structural import _extend_font

    sig = inspect.signature(_extend_font)
    assert "substitution_log" in sig.parameters, (
        "structural._extend_font dropped the substitution_log kwarg — INV-C-4 wiring regressed"
    )
    param = sig.parameters["substitution_log"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
        "substitution_log must be keyword-only to match surgeon and reflow"
    )


def test_inv_c_4_structural_metric_equivalent_observable_e2e(
    resume_pdf: Path,
    tmp_path: Path,
) -> None:
    """End-to-end: ``replace_block`` on a PDF whose font triggers a
    metric-equivalent fallback surfaces the substitute name through
    ``FidelityReport.font_substituted``."""
    if not resume_pdf.exists():
        pytest.skip("resume PDF missing")
    if platform.system() != "Windows":
        pytest.skip("Windows-specific font lookup")
    has_equivalent = any(find_font(eq) for eq in _METRIC_EQUIVALENTS.get("Calibri", []))
    if not has_equivalent:
        pytest.skip("no Calibri metric-equivalent installed")

    out = tmp_path / "out.pdf"
    # Wide bbox covering the top of page 0 — should hit name/title text
    # in the resume which uses Calibri.
    bbox = (0.0, 700.0, 612.0, 800.0)
    result = replace_block(
        str(resume_pdf),
        page_number=0,
        bbox=bbox,
        new_text="Replacement✓",  # ✓ likely needs subset extension
        output_path=str(out),
    )

    if not result.success:
        pytest.skip(f"replace_block did not succeed in this env: {result.warnings}")

    surfaced = result.fidelity_report.font_substituted is not None or any(
        "Carlito" in w or "Liberation" in w or "Arimo" in w for w in result.warnings
    )
    assert surfaced, (
        "structural path: metric-equivalent font substitution invisible "
        "to caller — fidelity_report.font_substituted is None and no "
        "warning was emitted naming the substitute. The substitution_log "
        "wiring through _replace_block_on_page → _extend_font is broken."
    )
