"""INV-G-8: paragraph reflow re-emits the document's DECLARED line leading.

Roadmap item E.3 (declared leading capture + re-emit on reflow). A PDF can
declare its line leading with the ``TL`` operator and advance lines with ``T*``
(or the ``'`` / ``"`` show-and-advance operators). ``state.py`` already tracks
``self._leading`` on ``TL``, but ``GraphicsStateSnapshot`` does not carry it,
and ``reflow`` re-synthesizes its OWN line advance — measured from the source
line y-gaps when the source has >= 2 lines, else a ``font_size * 1.2`` proxy.
So when a paragraph carries an AUTHORITATIVE declared leading (the document
genuinely advanced lines with ``TL``/``T*``) but the reflow cannot measure a
gap (a single-line source re-wrapped onto several lines), the declared leading
is silently discarded and replaced by the ``font_size * 1.2`` proxy.

E.3 captures the declared leading additively onto ``GraphicsStateSnapshot``
together with an AUTHORITATIVE signal (set only when ``T*`` / ``'`` / ``"``
actually fired as the line-advance mechanism, distinguishing a real declared
leading from a stray ``TL`` that was never used to advance). When the captured
leading is authoritative it WINS at the top of the reflow line-height
precedence ladder; otherwise the existing measured-gap / proxy behaviour is
byte-identical.

Three regression guards:

(a) ``test_tl_single_line_reemits_declared_leading`` — RED on the pre-E.3
    engine. A single-line paragraph laid out with ``18 TL`` (distinct from the
    ``11 * 1.2 == 13.2`` single-line proxy) re-wrapped onto several lines must
    advance lines by the DECLARED ``18``, not the re-synthesized ``13.2``.

(b) ``test_control_non_tl_is_byte_identical`` — a paragraph laid out with a
    per-line absolute ``Tm`` and NO ``TL``/``T*`` carries no authoritative
    declared leading, so E.3 must leave its reflow output byte-identical to the
    pre-E.3 engine (the measured y-gap line advance, ``14.0`` here).

(c) ``test_qq_scope_does_not_leak_leading`` — the LOAD-BEARING correctness
    guard. A ``TL`` issued INSIDE a ``q ... Q`` block must be restored at ``Q``
    and must NOT leak its value (or its authoritative flag) to a later
    paragraph drawn OUTSIDE the block. The outer paragraph uses its own
    per-line ``Tm`` and never issues ``TL``/``T*``, so it must reflow with the
    measured ``14.0`` advance, never the in-q ``30.0``. If the new
    leading-active tracker is not saved/restored on the q/Q graphics-state
    stack, this paragraph would inherit the in-q leading and reflow with the
    wrong line spacing.

A coherence control (``test_tl_multi_line_advance_matches_declared``) pins the
already-correct multi-line case: a ``TL``/``T*`` paragraph with two source
lines reflows with ``18`` both before E.3 (via the measured 18pt gap) and after
(via the authoritative-leading path), so the two paths must agree.

Regression guard — guard (a) fails on the pre-E.3 behaviour where the declared
leading is dropped in favour of the ``font_size * 1.2`` proxy; guards (b)/(c)
pin the byte-identical control and the q/Q leak isolation that the additive
capture must not disturb.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf

from pdf_edit_engine import find, replace

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders.declared_leading import (  # noqa: E402
    CONTROL_ANCHOR,
    CONTROL_GAP,
    LEADING_DECL,
    LEAK_ANCHOR,
    LEAK_GAP,
    PROXY_LINE_HEIGHT,
    TL_MULTI_ANCHOR,
    TL_SINGLE_ANCHOR,
    build_declared_leading_pdf,
)

# Tolerance for floating-point line-advance comparisons in text space.
_DY_TOL = 1e-3


# ── Helpers ───────────────────────────────────────────────────────────


def _reflowed_line_advances(tmp_path: Path, anchor: str, *, filler_words: int = 60) -> list[float]:
    """Reflow the anchored paragraph wide and return its distinct -dy advances.

    Replaces the anchored paragraph's first line with a long string so the
    greedy wrap spills onto several continuation lines, then collects the
    distinct vertical magnitudes of every pure-vertical relative ``Td``
    (``Td [0, -dy]``) the reflow emitted. The vertical advance is the value
    E.3 governs; the per-line ``-dy`` set is what the probes assert on.

    Args:
        tmp_path: pytest tmp dir for the source/output PDFs.
        anchor: first-line find-anchor of the paragraph to reflow.
        filler_words: number of filler words appended to force a wide re-wrap.

    Returns:
        Sorted list of distinct ``dy`` magnitudes (positive numbers) emitted by
        the continuation ``Td [0, -dy]`` operators.
    """
    src = tmp_path / "declared_leading.pdf"
    build_declared_leading_pdf(out_path=src)

    matches = find(str(src), anchor)
    assert matches, f"fixture missing find anchor {anchor!r}"
    match = matches[0]

    out = tmp_path / f"{anchor.split()[0]}.out.pdf"
    replacement = anchor + " " + " ".join(["word"] * filler_words)
    result = replace(str(src), match, replacement, str(out))
    assert result.success, f"reflow replace failed for {anchor!r}"
    assert result.fidelity_report.reflow_applied, f"{anchor!r} did not reflow"

    advances: set[float] = set()
    with pikepdf.open(str(out)) as pdf:
        for operands, operator in pikepdf.parse_content_stream(pdf.pages[0]):
            if str(operator) == "Td" and float(operands[0]) == 0.0:
                advances.add(-float(operands[1]))
    # Drop any non-positive entries (a zero-dy first-continuation indent return).
    return sorted(dy for dy in advances if dy > _DY_TOL)


# ── Probe (a): RED — declared leading must be re-emitted ───────────────


def test_tl_single_line_reemits_declared_leading(tmp_path: Path) -> None:
    """A single-line TL paragraph reflows with the DECLARED leading (INV-G-8).

    RED on the pre-E.3 engine: the source has one line, so the reflow has no
    measured y-gap and falls back to the ``font_size * 1.2`` proxy
    (``11 * 1.2 == 13.2``), discarding the document's authoritative ``18 TL``
    leading. E.3 must re-emit the declared ``18``.
    """
    advances = _reflowed_line_advances(tmp_path, TL_SINGLE_ANCHOR)
    assert advances, "single-line paragraph did not re-wrap onto continuations"

    # Guard the fixture's own premise: the declared leading and the proxy the
    # pre-E.3 engine emits are genuinely distinct, so this is not a tautology.
    assert abs(LEADING_DECL - PROXY_LINE_HEIGHT) > _DY_TOL

    for dy in advances:
        assert abs(dy - LEADING_DECL) < _DY_TOL, (
            f"reflow emitted line advance {dy} but the document declared "
            f"{LEADING_DECL} via TL; pre-E.3 emitted the {PROXY_LINE_HEIGHT} "
            f"font_size*1.2 proxy and discarded the declared leading (INV-G-8)"
        )


# ── Probe (b): control — non-TL paragraph stays byte-identical ─────────


def test_control_non_tl_is_byte_identical(tmp_path: Path) -> None:
    """A non-TL paragraph keeps the measured-gap advance unchanged (INV-G-8).

    The control paragraph uses a per-line absolute ``Tm`` and never issues
    ``TL``/``T*``, so it carries no authoritative declared leading. E.3 must
    leave the reflow output exactly as the pre-E.3 engine produced it — the
    measured ``CONTROL_GAP`` (14.0) y-gap advance — never overriding it with
    any leading.
    """
    advances = _reflowed_line_advances(tmp_path, CONTROL_ANCHOR)
    assert advances, "control paragraph did not re-wrap onto continuations"

    for dy in advances:
        assert abs(dy - CONTROL_GAP) < _DY_TOL, (
            f"non-TL control reflow emitted {dy}; it must keep the measured "
            f"y-gap advance {CONTROL_GAP} byte-identical to the pre-E.3 engine "
            f"(no authoritative declared leading to re-emit) (INV-G-8)"
        )


# ── Probe (c): LOAD-BEARING — q/Q must not leak the in-q leading ───────


def test_qq_scope_does_not_leak_leading(tmp_path: Path) -> None:
    """A TL set inside q...Q must not leak to a later paragraph (INV-G-8).

    The fixture issues ``30 TL`` inside a ``q ... Q`` block, then draws the
    outer paragraph with its own per-line ``Tm`` and no ``TL``/``T*``. The
    outer paragraph must reflow with the measured ``LEAK_GAP`` (14.0) advance.
    If the new leading-active tracker is not saved/restored on the q/Q
    graphics-state stack, the in-q ``30`` would leak past ``Q`` and the outer
    paragraph would be mis-treated as carrying an authoritative declared
    leading of 30 — corrupting unrelated line spacing.
    """
    advances = _reflowed_line_advances(tmp_path, LEAK_ANCHOR)
    assert advances, "leak-guard paragraph did not re-wrap onto continuations"

    for dy in advances:
        assert abs(dy - 30.0) > _DY_TOL, (
            f"reflow emitted {dy}: the leading set inside the q...Q block "
            f"(30) LEAKED past Q to the outer paragraph — the leading-active "
            f"field is not saved/restored on the q/Q stack (INV-G-8)"
        )
        assert abs(dy - LEAK_GAP) < _DY_TOL, (
            f"outer paragraph reflow emitted {dy}; with no authoritative "
            f"leading in scope it must keep the measured y-gap advance "
            f"{LEAK_GAP} byte-identical (INV-G-8)"
        )


# ── Coherence control: multi-line TL agrees before and after E.3 ───────


def test_tl_multi_line_advance_matches_declared(tmp_path: Path) -> None:
    """A multi-line TL paragraph advances by the declared leading (INV-G-8).

    Already correct on the pre-E.3 engine via the measured 18pt y-gap; E.3 must
    keep it correct via the authoritative-leading path. Pins that the two paths
    agree (the authoritative leading equals the measured gap here), so E.3 does
    not regress the multi-line case.
    """
    advances = _reflowed_line_advances(tmp_path, TL_MULTI_ANCHOR)
    assert advances, "multi-line paragraph did not re-wrap onto continuations"

    for dy in advances:
        assert abs(dy - LEADING_DECL) < _DY_TOL, (
            f"multi-line TL reflow emitted {dy}; the declared/measured leading "
            f"is {LEADING_DECL} and the two line-height paths must agree "
            f"(INV-G-8)"
        )
