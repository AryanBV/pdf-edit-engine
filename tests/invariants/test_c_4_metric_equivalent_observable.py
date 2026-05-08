"""INV-C-4 (P0): metric-equivalent substitution must be observable to caller.

Closed in v0.1.2: ``extend_subset`` gained an optional
``substitution_log`` list keyword argument; surgeon, reflow and
structural's ``_extend_font`` capture metric-equivalent substitution
events into it; surgeon and reflow then propagate the first event
through ``FidelityReport.font_substituted``.

Two probes:

1. **Resolver probe** (always runs): exercises
   ``system_fonts._find_font_with_origin`` with a monkeypatched
   ``_FONT_CACHE`` and ``_METRIC_EQUIVALENTS`` so the contract is
   verified regardless of which system fonts the host actually has.
2. **End-to-end probe** (skipped when host lacks the right fonts):
   replaces text in a real PDF and asserts the EditResult surfaces
   the substitution.
"""

from __future__ import annotations

import platform
from typing import TYPE_CHECKING

import pytest

import pdf_edit_engine.system_fonts as sf
from pdf_edit_engine import replace_all
from pdf_edit_engine.system_fonts import _METRIC_EQUIVALENTS, find_font

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_c_4_resolver_reports_substitute(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_find_font_with_origin`` returns the substitute name when a
    metric equivalent is used; ``None`` substitute when the exact
    font was found.

    Cache entries are now ``(path, origin)`` tuples (F-D-CC9 v0.1.3);
    the resolver returns ``(path, origin, substituted_name)``.
    """
    monkeypatch.setattr(sf, "_FONT_CACHE", {"Carlito-Regular": ("/fake/carlito.ttf", "system")})
    monkeypatch.setitem(_METRIC_EQUIVALENTS, "ZzNonExistent", ["Carlito-Regular"])

    found = sf._find_font_with_origin("ZzNonExistent")
    assert found is not None, "metric-equivalent fallback should resolve"
    path, origin, substituted = found
    assert path == "/fake/carlito.ttf"
    assert origin == "metric_equivalent"
    assert substituted == "Carlito-Regular"

    # Conversely: an exact-name hit reports None substitute and origin from cache.
    monkeypatch.setattr(sf, "_FONT_CACHE", {"ExactFont": ("/fake/exact.ttf", "system")})
    found2 = sf._find_font_with_origin("ExactFont")
    assert found2 == ("/fake/exact.ttf", "system", None)


def test_inv_c_4_metric_equivalent_observable_e2e(resume_pdf: Path, tmp_path: Path) -> None:
    """End-to-end: replace_all on a PDF whose font triggers a
    metric-equivalent fallback surfaces the substitute name through
    ``FidelityReport.font_substituted``."""
    if not resume_pdf.exists():
        pytest.skip("resume PDF missing")
    if platform.system() != "Windows":
        pytest.skip("Windows-specific font lookup")
    has_equivalent = any(find_font(eq) for eq in _METRIC_EQUIVALENTS["Calibri"])
    if not has_equivalent:
        pytest.skip("no Calibri metric-equivalent installed")

    out = tmp_path / "out.pdf"
    results = replace_all(str(resume_pdf), "Bangalore", "Bangalore✓", str(out))
    if not results:
        pytest.skip("'Bangalore' not in resume")

    surfaced = any(
        r.fidelity_report.font_substituted is not None
        or any("Carlito" in w or "Liberation" in w or "Arimo" in w for w in r.warnings)
        for r in results
    )
    assert surfaced, (
        "metric-equivalent font substitution invisible to caller — "
        "fidelity_report.font_substituted is None and no warning was "
        "emitted naming the substitute"
    )
