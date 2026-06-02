"""INV-F-9: reflow surfaces color loss honestly, with no false positives.

Block F CORE color slice — the honest-degradation half of the contract split
out of ``test_f_8_reflow_color_space_preserved`` (probe-schema S2: one
invariant per file). INV-F-8 (verbatim preservation) stays in test_f_8; this
file owns INV-F-9.

INV-F-9 — honest degradation when verbatim replay is impossible, AND no false
signal when it is possible:
  (a) A reflow that cannot replay the original non-device color identity must
      NOT silently reinterpret it as device gray. It surfaces a typed
      ``Degradation(kind="color_space_approximated", severity="warning")``.
      The kind is NON font-affecting (glyph identity is untouched), so
      ``FidelityReport.font_preserved`` stays True.
  (b) A genuinely multi-color paragraph (distinct fills per line) still emits
      ``color_space_approximated`` on re-wrap — the fix must not over-suppress.
  (c) A SINGLE-color paragraph whose lines express the SAME color in different
      numeric-literal precisions (``1 0 0 rg`` vs ``1.0 0.0 0.0 rg``) must NOT
      emit ``color_space_approximated`` — the rendered color is identical, so a
      multi-color signal would be a FALSE honesty signal. The keyer must
      compare operands numerically, not by raw ``str()``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf

from pdf_edit_engine import find, replace
from pdf_edit_engine.models import FONT_AFFECTING_KINDS

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders.colored_runs import (  # noqa: E402
    BODY_FIND_ANCHOR,
    SEPARATION_CS_NAME,
    build_genuine_multicolor_run_pdf,
    build_mixed_precision_run_pdf,
    build_separation_run_pdf,
)

_APPROX_KIND = "color_space_approximated"

# A replacement meaningfully wider than the anchored last line so the engine
# routes through paragraph reflow (surgeon: new_width > old_width + 1.0) and
# re-wraps, exercising _build_replacement_ops' color rebuild.
_WIDER = (
    "when a wider replacement no longer fits the original line breaks and must "
    "be re-wrapped onto considerably more lines than before this edit."
)


def _page_stream(pdf_path: str) -> str:
    """Return page 0's content stream as a latin-1 string for token scanning."""
    with pikepdf.open(pdf_path) as pdf:
        return pdf.pages[0]["/Contents"].read_bytes().decode("latin-1", "replace")


def test_inv_f_9_unpreservable_color_surfaces_typed_degradation(tmp_path: Path) -> None:
    """A reflow that cannot replay a non-device color verbatim surfaces it honestly.

    Honest-degradation contract: when the original Separation color identity is
    NOT replayed verbatim, the engine must NOT silently substitute device gray.
    Either the Separation op is preserved (the Block F happy path) OR a typed
    ``color_space_approximated`` (severity 'warning', NON font-affecting)
    Degradation is surfaced.
    """
    src = tmp_path / "separation_run_f9.pdf"
    build_separation_run_pdf(src)
    out = tmp_path / "separation_out_f9.pdf"

    matches = find(str(src), BODY_FIND_ANCHOR)
    assert matches, f"anchor {BODY_FIND_ANCHOR!r} not found in fixture"

    result = replace(str(src), matches[0], _WIDER, str(out), reflow=True)
    assert result.success, f"reflow replace must succeed: {result!r}"

    stream = _page_stream(str(out))
    preserved = f"/{SEPARATION_CS_NAME} cs" in stream and "scn" in stream
    kinds = {d.kind for d in result.fidelity_report.degradations}
    approximated = _APPROX_KIND in kinds

    assert preserved or approximated, (
        "a Separation reflow must either preserve the color-setting operators "
        f"verbatim OR surface a typed {_APPROX_KIND} degradation; silent "
        "device-gray collapse is forbidden. "
        f"degradation kinds={kinds}; output stream:\n{stream}"
    )

    # When approximated, pin the typed contract: severity warning, NON
    # font-affecting, and the kind is a registered DegradationKind.
    if approximated:
        deg = next(d for d in result.fidelity_report.degradations if d.kind == _APPROX_KIND)
        assert deg.severity == "warning", (
            f"{_APPROX_KIND} must be severity='warning'; got {deg.severity!r}"
        )
        assert _APPROX_KIND not in FONT_AFFECTING_KINDS, (
            f"{_APPROX_KIND} is a color (layout) signal and must NOT be in FONT_AFFECTING_KINDS"
        )
        assert result.fidelity_report.font_preserved, (
            "a color-space approximation must not flip font_preserved — glyph "
            f"identity is untouched; report={result.fidelity_report!r}"
        )


def test_inv_f_9_genuine_multicolor_run_surfaces_degradation(tmp_path: Path) -> None:
    """A genuinely multi-color paragraph still surfaces color_space_approximated.

    Regression guard against over-suppression: when the paragraph's lines carry
    truly distinct fills (red / blue / green), reflow collapses to element[0]'s
    color on re-wrap and MUST surface the loss via a typed
    ``color_space_approximated(multi_color_run)`` degradation.
    """
    src = tmp_path / "multicolor_run.pdf"
    build_genuine_multicolor_run_pdf(src)
    out = tmp_path / "multicolor_out.pdf"

    matches = find(str(src), BODY_FIND_ANCHOR)
    assert matches, f"anchor {BODY_FIND_ANCHOR!r} not found in fixture"

    result = replace(str(src), matches[0], _WIDER, str(out), reflow=True)
    assert result.success, f"reflow replace must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied, "edit must route through reflow"

    degradations = result.fidelity_report.degradations
    approx = [d for d in degradations if d.kind == _APPROX_KIND]
    assert approx, (
        "a genuinely multi-color paragraph (red/blue/green per line) must "
        f"surface {_APPROX_KIND} on re-wrap; the fix must not over-suppress. "
        f"degradations={degradations!r}"
    )
    assert any("multi_color_run" in (d.detail or "") for d in approx), (
        f"{_APPROX_KIND} detail must record the multi_color_run cause; "
        f"got {[d.detail for d in approx]!r}"
    )
    assert approx[0].severity == "warning"
    assert result.fidelity_report.font_preserved, (
        "color approximation must not flip font_preserved — glyph identity "
        f"is untouched; report={result.fidelity_report!r}"
    )


def test_inv_f_9_mixed_precision_same_color_no_false_degradation(tmp_path: Path) -> None:
    """A single color written at mixed literal precision is NOT a multi-color run.

    False-emission guard: all three lines are pure red, but each writes the
    fill at a different numeric precision (``1 0 0 rg`` / ``1.0 0.0 0.0 rg`` /
    ``1.00 0.00 0.00 rg``). The rendered color is identical, so reflow must NOT
    emit ``color_space_approximated`` — that would be a FALSE honesty signal.
    The keyer must compare operands numerically (1 == 1.0 == 1.00), not by raw
    ``str()``.
    """
    src = tmp_path / "mixed_precision_run.pdf"
    build_mixed_precision_run_pdf(src)
    out = tmp_path / "mixed_precision_out.pdf"

    matches = find(str(src), BODY_FIND_ANCHOR)
    assert matches, f"anchor {BODY_FIND_ANCHOR!r} not found in fixture"

    result = replace(str(src), matches[0], _WIDER, str(out), reflow=True)
    assert result.success, f"reflow replace must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied, "edit must route through reflow"

    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _APPROX_KIND not in kinds, (
        "a single color expressed at mixed literal precision must NOT emit "
        f"{_APPROX_KIND} — the rendered color is identical on every line, so a "
        "multi_color_run signal is a false honesty signal. "
        f"degradations={result.fidelity_report.degradations!r}"
    )
