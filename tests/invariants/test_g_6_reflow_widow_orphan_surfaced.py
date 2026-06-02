"""INV-G-6: reflow that produces a widow/orphan surfaces a typed degradation.

Roadmap item E.4 (widow/orphan honesty surfacing). When
``reflow.reflow_paragraph`` re-wraps a paragraph and the resulting line set
leaves a *widow* — a final line holding a single short word — the engine must
surface that line-break-quality problem as a typed
``Degradation(kind="line_break_quality_degraded", severity="info")`` on
``EditResult.fidelity_report.degradations`` rather than emit the re-wrapped
paragraph with no signal.

E.4 is *detect + surface only*. The successful output geometry is unchanged:
the engine does NOT attempt the risky pull-down repair (pulling a word down
from the penultimate line, which can mis-join across the wrap boundary). It
re-wraps exactly as before and merely tells the caller the result has a
widow. ``line_break_quality_degraded`` is NOT in ``FONT_AFFECTING_KINDS``
(glyph identity is untouched), so ``font_preserved`` stays True.

The non-widow regression control pins the over-surfacing boundary: a reflow
that wraps into a balanced final line (multiple words) must NOT emit the
degradation — flagging healthy line breaks would be a false positive.

Regression guard — fails on the pre-E.4 behaviour where the widow re-wrap
returns success=True with no ``line_break_quality_degraded`` event.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf

from pdf_edit_engine import find, replace
from pdf_edit_engine.encoding import FontResolverCache
from pdf_edit_engine.locator import _build_index
from pdf_edit_engine.reflow import (
    _detect_paragraphs_from_index,
    _get_space_width,
    _load_widths_from_ref,
    _measure_word,
    find_paragraph_for_match,
)

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders.reflow_quality import (  # noqa: E402
    BODY_FIND_ANCHOR,
    build_reflow_quality_pdf,
    build_widow_replacement,
)

_WIDOW_KIND = "line_break_quality_degraded"


def _calibrate_widow_text(pdf_path: Path) -> str:
    """Build a replacement whose greedy re-wrap ends in a lone short word.

    Self-calibrates against the engine's own width measurement so the widow
    is produced regardless of fractional metric drift.
    """
    matches = find(str(pdf_path), BODY_FIND_ANCHOR)
    assert matches, f"fixture missing find anchor {BODY_FIND_ANCHOR!r}"
    match = matches[0]
    with pikepdf.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        elements = _build_index(page, match.page_number)
        para = find_paragraph_for_match(_detect_paragraphs_from_index(elements), match)
        assert para is not None, "fixture paragraph not detected"
        cache = FontResolverCache()
        resolver = cache.get_resolver(page, para.font_name.lstrip("/"))
        font_key = para.font_name if para.font_name.startswith("/") else f"/{para.font_name}"
        font_ref = page["/Resources"]["/Font"][font_key]
        widths = _load_widths_from_ref(font_ref)
        space_w = _get_space_width(resolver, widths, para.font_size, 1.0, 0.0)

        def measure(word: str) -> float:
            return _measure_word(word, resolver, widths, para.font_size, 1.0, 0.0)

        return build_widow_replacement(para.paragraph_width, measure, space_w)


def test_inv_g_6_reflow_widow_surfaces_degradation(tmp_path: Path) -> None:
    """A widow-producing reflow emits a typed line_break_quality_degraded event.

    Regression guard — fails on the pre-E.4 behaviour (success=True, no
    widow event surfaced).
    """
    src = tmp_path / "reflow_quality.pdf"
    build_reflow_quality_pdf(src)
    widow_text = _calibrate_widow_text(src)

    matches = find(str(src), BODY_FIND_ANCHOR)
    assert matches
    out = tmp_path / "widow_out.pdf"
    result = replace(str(src), matches[0], widow_text, str(out), reflow=True)

    assert result.success, f"widow reflow must still succeed: {result!r}"
    assert result.fidelity_report.reflow_applied, "edit must route through reflow"

    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _WIDOW_KIND in kinds, (
        "a reflow whose final line is a single short word (widow) must surface a "
        f"typed {_WIDOW_KIND} Degradation; got kinds={kinds}"
    )
    deg = next(d for d in result.fidelity_report.degradations if d.kind == _WIDOW_KIND)
    assert deg.severity == "info", f"{_WIDOW_KIND} must be severity='info'; got {deg.severity!r}"


def test_inv_g_6_widow_degradation_not_font_affecting(tmp_path: Path) -> None:
    """The widow event is non font-affecting: font_preserved stays True.

    Detect+surface does not touch glyph identity, so the widow signal must
    not flip ``font_preserved`` (it is keyed off FONT_AFFECTING_KINDS).
    """
    from pdf_edit_engine.models import FONT_AFFECTING_KINDS

    assert _WIDOW_KIND not in FONT_AFFECTING_KINDS, (
        f"{_WIDOW_KIND} is a layout-quality signal and must NOT be in FONT_AFFECTING_KINDS"
    )

    src = tmp_path / "reflow_quality2.pdf"
    build_reflow_quality_pdf(src)
    widow_text = _calibrate_widow_text(src)
    matches = find(str(src), BODY_FIND_ANCHOR)
    assert matches
    out = tmp_path / "widow_out2.pdf"
    result = replace(str(src), matches[0], widow_text, str(out), reflow=True)

    assert result.success
    assert result.fidelity_report.font_preserved, (
        "a widow signal must not flip font_preserved — glyph identity is "
        f"untouched; report={result.fidelity_report!r}"
    )


def test_inv_g_6_balanced_reflow_does_not_surface_widow(tmp_path: Path) -> None:
    """Over-surfacing control: a balanced final line must NOT flag a widow.

    A reflow that wraps into a multi-word final line is healthy output;
    emitting line_break_quality_degraded there would be a false positive.
    """
    src = tmp_path / "reflow_quality3.pdf"
    build_reflow_quality_pdf(src)

    matches = find(str(src), BODY_FIND_ANCHOR)
    assert matches
    out = tmp_path / "balanced_out.pdf"
    # A wider replacement whose greedy wrap leaves a multi-word final line
    # (verified to wrap into 3 lines with a 7-word last line at the fixture
    # geometry — no lone widow word).
    balanced = (
        "one two three four five six seven eight nine ten "
        "one two three four five six seven eight nine ten "
        "one two three four five six seven eight nine ten one two three"
    )
    result = replace(str(src), matches[0], balanced, str(out), reflow=True)

    assert result.success, f"balanced reflow must succeed: {result!r}"
    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _WIDOW_KIND not in kinds, (
        f"a balanced multi-word final line must NOT be flagged as a widow; got kinds={kinds}"
    )
