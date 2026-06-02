"""Differential-render proving harness for pdf-edit-engine.

Net-new test tooling (no ``src/`` dependency). Rasterizes PDF pages with
pypdfium2 (Apache/BSD) and compares before/after renders pixel-for-pixel so
visual-fidelity regressions surface as a measurable similarity score rather
than an eyeballed screenshot.

The harness is import-safe even when its optional rendering dependencies
(pypdfium2 / numpy) are absent: ``pytest --collect-only`` always succeeds.
Tests that actually render must be gated with the ``render`` marker and the
``requires_render`` skipif provided here.
"""

from __future__ import annotations

from tests.harness.diff_render import (
    HAVE_RENDER,
    assert_visual_equal_except,
    compare_pdfs,
    render_page,
    requires_render,
)

__all__ = [
    "HAVE_RENDER",
    "assert_visual_equal_except",
    "compare_pdfs",
    "render_page",
    "requires_render",
]
