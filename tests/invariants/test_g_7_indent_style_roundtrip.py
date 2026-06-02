"""INV-G-7: paragraph reflow preserves the source paragraph's indent style.

Roadmap item E.2 (paragraph indent preservation through reflow). When
``reflow.reflow_paragraph`` re-wraps a paragraph, the per-line *indent style*
of the source — a FIRST-LINE indent (line 0 starts further right than the
flush continuations) or a HANGING indent (continuations start further right
than line 0) — must survive the round-trip. The pre-E.2 engine collapses
every per-line x-start to one mode ``left_margin`` (``reflow._compute_x_mode``)
and re-emits line 0 at that margin with continuations on a relative
``Td [0, -line_height]``, so both first-line and hanging indents are silently
DISCARDED.

E.2 classifies the indent from the source paragraph's per-line ``x_starts``
and re-emits it. The classifier is DEFAULT-FLUSH-BIASED: on ANY ambiguity
(a single-line paragraph with ``len(x_starts) < 2``, an x-delta below the
``MIN_INDENT = font_size * 0.6`` noise floor, or non-monotone x-starts) it
treats the paragraph as FLUSH — the existing byte-identical relative-``Td``
continuation path — AND surfaces a typed
``Degradation(kind="indent_flattened", severity="info")``. ``indent_flattened``
is NOT in ``FONT_AFFECTING_KINDS`` (glyph identity is untouched), so
``font_preserved`` stays True. A confident first-line / hanging classification
re-emits the indent and emits NO degradation.

The FLUSH regression control pins the geometry-safety guarantee: a genuinely
flush paragraph reflowed must NOT emit ``indent_flattened`` and must produce
the byte-identical relative-``Td`` continuation stream the pre-E.2 engine
already emits. A missed indent must be a no-op (flush == current behaviour),
NEVER a new positioning artifact.

Regression guard — fails on the pre-E.2 behaviour where the first-line and
hanging indents are flattened to the mode margin with no signal, and the
single-line ambiguous case reflows with no ``indent_flattened`` event.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pikepdf

from pdf_edit_engine import find, replace
from pdf_edit_engine.encoding import FontResolverCache
from pdf_edit_engine.locator import _build_index
from pdf_edit_engine.models import FONT_AFFECTING_KINDS
from pdf_edit_engine.reflow import (
    _detect_paragraphs_from_index,
    _group_elements_into_lines,
    _load_widths_from_ref,
    _measure_word,
    find_paragraph_for_match,
)

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders.indent_styles import (  # noqa: E402
    FIRST_LINE_ANCHOR,
    FLUSH_ANCHOR,
    HANGING_ANCHOR,
    INDENT,
    LEFT_MARGIN,
    NONMONO_ANCHOR,
    SINGLE_ANCHOR,
    build_indent_styles_pdf,
    build_wrapping_replacement,
)

_INDENT_KIND = "indent_flattened"
# Tolerance for floating-point x-position comparisons in page space.
_X_TOL = 0.6


# ── Geometry helpers ──────────────────────────────────────────────────


def _measure_for_anchor(pdf_path: Path, anchor: str) -> tuple[float, Any]:
    """Return ``(paragraph_width, measure_word)`` for the anchored paragraph.

    The measure callable binds the engine's own ``_measure_word`` so the
    replacement self-calibrates against the host's Helvetica metrics.
    """
    matches = find(str(pdf_path), anchor)
    assert matches, f"fixture missing find anchor {anchor!r}"
    match = matches[0]
    with pikepdf.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        elements = _build_index(page, match.page_number)
        para = find_paragraph_for_match(_detect_paragraphs_from_index(elements), match)
        assert para is not None, f"paragraph for {anchor!r} not detected"
        cache = FontResolverCache()
        resolver = cache.get_resolver(page, para.font_name.lstrip("/"))
        font_key = para.font_name if para.font_name.startswith("/") else f"/{para.font_name}"
        widths = _load_widths_from_ref(page["/Resources"]["/Font"][font_key])

        def measure(word: str) -> float:
            return _measure_word(word, resolver, widths, para.font_size, 1.0, 0.0)

        return para.paragraph_width, measure


def _source_x_starts(pdf_path: Path, anchor: str) -> list[float]:
    """Return the per-line source x-starts for the anchored paragraph."""
    matches = find(str(pdf_path), anchor)
    assert matches
    match = matches[0]
    with pikepdf.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        elements = _build_index(page, match.page_number)
        para = find_paragraph_for_match(_detect_paragraphs_from_index(elements), match)
        assert para is not None
        lines = _group_elements_into_lines(para.elements, para.font_size)
        return [ln[0].characters[0].page_x for ln in lines]


def _source_right_margin(pdf_path: Path, anchor: str) -> float:
    """Return the source paragraph's ``right_margin`` (rightmost element extent)."""
    matches = find(str(pdf_path), anchor)
    assert matches
    match = matches[0]
    with pikepdf.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        elements = _build_index(page, match.page_number)
        para = find_paragraph_for_match(_detect_paragraphs_from_index(elements), match)
        assert para is not None
        return para.right_margin


def _reflow_line_x_starts(out_pdf: Path, first_line_text_prefix: str) -> list[float]:
    """Reconstruct the absolute x-start of every line in the reflowed block.

    Walks the single rebuilt BT/ET block that contains ``first_line_text_prefix``
    and resolves each ``Tj`` line's x-origin from the positioning operators that
    precede it:

    - ``Tm [1 0 0 1 x y]`` sets an absolute x (and resets the text-line origin).
    - ``Td [x, y]`` immediately after BT/Tf sets the first absolute x; a later
      relative ``Td [dx, dy]`` shifts x by ``dx`` from the current text-line x.

    The pre-E.2 engine emits exactly one absolute first-line ``Td``/``Tm`` and
    relative ``Td [0, -line_height]`` continuations, so a flattened indent
    yields the same x on every line; a preserved first-line/hanging indent
    yields distinct line-0 vs continuation x-values.
    """
    with pikepdf.open(str(out_pdf)) as pdf:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))

    # Find the BT...ET block whose first Tj line starts with the prefix.
    blocks: list[list[tuple[list[Any], str]]] = []
    current: list[tuple[list[Any], str]] | None = None
    for operands, op in ops:
        name = str(op)
        if name == "BT":
            current = []
        if current is not None:
            current.append(([o for o in operands], name))
        if name == "ET" and current is not None:
            blocks.append(current)
            current = None

    target: list[tuple[list[Any], str]] | None = None
    for block in blocks:
        for operands, name in block:
            if name == "Tj" and operands and str(operands[0]).startswith(first_line_text_prefix):
                target = block
                break
        if target is not None:
            break
    assert target is not None, (
        f"no reflowed BT/ET block whose first Tj starts with {first_line_text_prefix!r}"
    )

    x_starts: list[float] = []
    text_line_x = 0.0  # x set by the most recent absolute Td/Tm (line origin)
    cur_x = 0.0
    for operands, name in target:
        if name == "Tm" and len(operands) == 6:
            text_line_x = float(operands[4])
            cur_x = text_line_x
        elif name == "Td" and len(operands) == 2:
            dx = float(operands[0])
            # Heuristic: an absolute first-line Td (the engine emits exactly one,
            # right after Tf) has a large x; relative continuations carry dx that
            # offsets the running line origin. We track both: cur_x is the
            # running line x.
            if abs(dx) > 1.0 and not x_starts:
                text_line_x = dx
                cur_x = dx
            else:
                text_line_x = text_line_x + dx
                cur_x = text_line_x
        elif name == "Tj":
            x_starts.append(cur_x)
    return x_starts


def _reflow_line_x_and_text(out_pdf: Path, first_line_text_prefix: str) -> list[tuple[float, str]]:
    """Reconstruct ``(x_start, line_text)`` for every line in the reflowed block.

    Shares the positioning-resolution logic of :func:`_reflow_line_x_starts`
    but also pulls the decoded ``Tj`` string for each line so a probe can
    measure each emitted line's rendered width and reconstruct its right edge.

    The indent fixture uses standard-14 Helvetica (a simple WinAnsi font), so
    every reflowed line is a flat ``Tj`` with a single ``pikepdf.String``
    operand whose ``str()`` is the line text.
    """
    with pikepdf.open(str(out_pdf)) as pdf:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))

    blocks: list[list[tuple[list[Any], str]]] = []
    current: list[tuple[list[Any], str]] | None = None
    for operands, op in ops:
        name = str(op)
        if name == "BT":
            current = []
        if current is not None:
            current.append(([o for o in operands], name))
        if name == "ET" and current is not None:
            blocks.append(current)
            current = None

    target: list[tuple[list[Any], str]] | None = None
    for block in blocks:
        for operands, name in block:
            if name == "Tj" and operands and str(operands[0]).startswith(first_line_text_prefix):
                target = block
                break
        if target is not None:
            break
    assert target is not None, (
        f"no reflowed BT/ET block whose first Tj starts with {first_line_text_prefix!r}"
    )

    out: list[tuple[float, str]] = []
    text_line_x = 0.0
    cur_x = 0.0
    for operands, name in target:
        if name == "Tm" and len(operands) == 6:
            text_line_x = float(operands[4])
            cur_x = text_line_x
        elif name == "Td" and len(operands) == 2:
            dx = float(operands[0])
            if abs(dx) > 1.0 and not out:
                text_line_x = dx
                cur_x = dx
            else:
                text_line_x = text_line_x + dx
                cur_x = text_line_x
        elif name == "Tj" and operands:
            out.append((cur_x, str(operands[0])))
    return out


def _reflow_block_bytes(out_pdf: Path, first_line_text_prefix: str) -> bytes:
    """Return the raw content-stream bytes of the page (for byte-equivalence)."""
    with pikepdf.open(str(out_pdf)) as pdf:
        return bytes(pdf.pages[0]["/Contents"].read_bytes())


# ── Probes ────────────────────────────────────────────────────────────


def test_inv_g_7_first_line_indent_preserved(tmp_path: Path) -> None:
    """A first-line-indented paragraph keeps its first-line indent on reflow.

    Source ``x_starts == [margin+indent, margin, margin]``. After reflow the
    first wrapped line must start at ``margin+indent`` and every continuation
    must return to ``margin``.

    Regression guard — RED on the pre-E.2 engine, which flattens line 0 to the
    mode ``margin`` so every line starts at ``margin``.
    """
    src = tmp_path / "indent_first.pdf"
    build_indent_styles_pdf(src)
    assert _source_x_starts(src, FIRST_LINE_ANCHOR) == [
        LEFT_MARGIN + INDENT,
        LEFT_MARGIN,
        LEFT_MARGIN,
    ]

    width, measure = _measure_for_anchor(src, FIRST_LINE_ANCHOR)
    repl = build_wrapping_replacement(FIRST_LINE_ANCHOR, measure, width)
    matches = find(str(src), FIRST_LINE_ANCHOR)
    out = tmp_path / "first_out.pdf"
    result = replace(str(src), matches[0], repl, str(out), reflow=True)
    assert result.success, f"first-line reflow must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied

    xs = _reflow_line_x_starts(out, "Firstline indented opening sentence")
    assert len(xs) >= 2, f"replacement must wrap onto >= 2 lines; got {xs}"
    assert abs(xs[0] - (LEFT_MARGIN + INDENT)) <= _X_TOL, (
        f"first-line indent DISCARDED: line 0 x={xs[0]} expected {LEFT_MARGIN + INDENT}"
    )
    for i, x in enumerate(xs[1:], start=1):
        assert abs(x - LEFT_MARGIN) <= _X_TOL, (
            f"continuation {i} should return to margin {LEFT_MARGIN}; got {x}"
        )
    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _INDENT_KIND not in kinds, (
        f"a confident first-line classification must NOT emit {_INDENT_KIND}; got {kinds}"
    )


def test_inv_g_7_hanging_indent_preserved(tmp_path: Path) -> None:
    """A hanging-indented paragraph keeps its hanging indent on reflow.

    Source ``x_starts == [margin, margin+indent, margin+indent]``. After
    reflow the first wrapped line must start at ``margin`` and every
    continuation must start at ``margin+indent``.

    Regression guard — RED on the pre-E.2 engine, which collapses to the mode
    margin (``margin+indent``, the 2-of-3 majority) and re-emits line 0 there.
    """
    src = tmp_path / "indent_hanging.pdf"
    build_indent_styles_pdf(src)
    assert _source_x_starts(src, HANGING_ANCHOR) == [
        LEFT_MARGIN,
        LEFT_MARGIN + INDENT,
        LEFT_MARGIN + INDENT,
    ]

    width, measure = _measure_for_anchor(src, HANGING_ANCHOR)
    repl = build_wrapping_replacement(HANGING_ANCHOR, measure, width)
    matches = find(str(src), HANGING_ANCHOR)
    out = tmp_path / "hanging_out.pdf"
    result = replace(str(src), matches[0], repl, str(out), reflow=True)
    assert result.success, f"hanging reflow must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied

    xs = _reflow_line_x_starts(out, "Hanging flush opening sentence")
    assert len(xs) >= 2, f"replacement must wrap onto >= 2 lines; got {xs}"
    assert abs(xs[0] - LEFT_MARGIN) <= _X_TOL, (
        f"hanging line 0 should be at margin {LEFT_MARGIN}; got {xs[0]}"
    )
    for i, x in enumerate(xs[1:], start=1):
        assert abs(x - (LEFT_MARGIN + INDENT)) <= _X_TOL, (
            f"hanging continuation {i} should be at margin+indent {LEFT_MARGIN + INDENT}; got {x}"
        )
    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _INDENT_KIND not in kinds, (
        f"a confident hanging classification must NOT emit {_INDENT_KIND}; got {kinds}"
    )


def test_inv_g_7_indent_no_right_margin_overflow(tmp_path: Path) -> None:
    """No reflowed line overruns the SOURCE right margin (first-line AND hanging).

    Geometry bug guard: ``_build_paragraph`` wraps every line to a single
    ``paragraph_width`` anchored at one margin, but indented lines RENDER at a
    different x (line 0 at ``margin+first_line_indent``; continuations at
    ``margin+hanging_indent``). A line whose width was computed against the
    flush margin but rendered at an indented x overruns the source right edge by
    up to the indent magnitude. The vertical-overflow logic never sees it.

    For BOTH a first-line-indented and a hanging-indented paragraph reflowed via
    the public API this reconstructs each emitted line's right edge
    (``line_start_x + measured_line_width`` against the engine's own Helvetica
    metrics) and asserts NONE exceeds the source paragraph's ``right_margin``.

    Regression guard — RED on the pre-fix engine (indented continuations /
    line 0 overrun the right margin by ~indent magnitude); GREEN once the wrap
    width is made indent-aware.
    """
    for anchor, first_line_prefix in (
        (FIRST_LINE_ANCHOR, "Firstline indented opening sentence"),
        (HANGING_ANCHOR, "Hanging flush opening sentence"),
    ):
        src = tmp_path / f"indent_overflow_{anchor.split()[0].lower()}.pdf"
        build_indent_styles_pdf(src)

        width, measure = _measure_for_anchor(src, anchor)
        right_margin = _source_right_margin(src, anchor)
        repl = build_wrapping_replacement(anchor, measure, width)
        matches = find(str(src), anchor)
        out = tmp_path / f"overflow_{anchor.split()[0].lower()}_out.pdf"
        result = replace(str(src), matches[0], repl, str(out), reflow=True)
        assert result.success, f"{anchor!r} reflow must succeed: {result!r}"

        lines = _reflow_line_x_and_text(out, first_line_prefix)
        assert len(lines) >= 2, f"{anchor!r} replacement must wrap onto >= 2 lines; got {lines}"

        overflows: list[str] = []
        for i, (x_start, text) in enumerate(lines):
            right_edge = x_start + measure(text)
            if right_edge > right_margin + 1.0:
                overflows.append(
                    f"line {i}: right_edge={right_edge:.1f} exceeds "
                    f"right_margin={right_margin:.1f} by {right_edge - right_margin:.1f}pt"
                )
        assert not overflows, (
            f"{anchor!r}: reflowed line(s) overrun the source right margin:\n"
            + "\n".join(overflows)
        )


def test_inv_g_7_flush_paragraph_no_degradation_and_byte_equivalent(
    tmp_path: Path,
) -> None:
    """A genuinely flush paragraph reflows with NO indent_flattened event.

    Geometry-safety control: the flush path must stay the existing
    byte-identical relative-``Td`` continuation stream. The probe replaces the
    same flush paragraph with the same text twice (E.2 must be a pure
    function of the input) and asserts the reflowed content stream is identical
    AND no ``indent_flattened`` degradation fired.

    Regression guard — fails if E.2 emits ``indent_flattened`` on a genuinely
    flush paragraph (false positive) or perturbs the flush byte stream.
    """
    src = tmp_path / "indent_flush.pdf"
    build_indent_styles_pdf(src)
    assert _source_x_starts(src, FLUSH_ANCHOR) == [LEFT_MARGIN, LEFT_MARGIN, LEFT_MARGIN]

    width, measure = _measure_for_anchor(src, FLUSH_ANCHOR)
    repl = build_wrapping_replacement(FLUSH_ANCHOR, measure, width)

    matches = find(str(src), FLUSH_ANCHOR)
    out_a = tmp_path / "flush_out_a.pdf"
    result = replace(str(src), matches[0], repl, str(out_a), reflow=True)
    assert result.success, f"flush reflow must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied

    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _INDENT_KIND not in kinds, (
        f"a genuinely flush paragraph must NOT emit {_INDENT_KIND}; got {kinds}"
    )

    # Continuations must remain relative Td [0, -line_height] (flush
    # behaviour): every reconstructed line x equals the margin.
    xs = _reflow_line_x_starts(out_a, "Flush block opening sentence")
    assert len(xs) >= 2, f"flush replacement must wrap onto >= 2 lines; got {xs}"
    for i, x in enumerate(xs):
        assert abs(x - LEFT_MARGIN) <= _X_TOL, (
            f"flush line {i} must stay at margin {LEFT_MARGIN}; got {x}"
        )

    # Byte-equivalence of the reflowed flush stream across a re-run: E.2 must
    # be deterministic and must not perturb the flush path's bytes.
    src2 = tmp_path / "indent_flush2.pdf"
    build_indent_styles_pdf(src2)
    matches2 = find(str(src2), FLUSH_ANCHOR)
    out_b = tmp_path / "flush_out_b.pdf"
    replace(str(src2), matches2[0], repl, str(out_b), reflow=True)
    assert _reflow_block_bytes(out_a, "Flush block opening sentence") == _reflow_block_bytes(
        out_b, "Flush block opening sentence"
    ), "flush reflow output is not byte-stable"


def test_inv_g_7_single_line_no_false_indent_flattened(
    tmp_path: Path,
) -> None:
    """A plain single-line paragraph reflows to FLUSH with NO indent_flattened.

    The common reflow case: a single source line widens through re-wrap. A
    single line has NO multi-line indent structure that could be flattened —
    there is no continuation block whose shape was lost — so emitting
    ``indent_flattened`` would be info-channel noise / false honesty. The
    paragraph is simply FLUSH and must surface NO ``indent_flattened`` event.

    Regression guard — RED on the over-surfacing behaviour where
    ``_is_degraded_flush`` returned True unconditionally for ``len(x_starts) < 2``
    so EVERY single-line reflow falsely emitted ``indent_flattened`` (INV-G-7).
    """
    assert _INDENT_KIND not in FONT_AFFECTING_KINDS, (
        f"{_INDENT_KIND} is a layout signal and must NOT be in FONT_AFFECTING_KINDS"
    )

    src = tmp_path / "indent_single.pdf"
    build_indent_styles_pdf(src)
    assert len(_source_x_starts(src, SINGLE_ANCHOR)) == 1, "single-line fixture must have 1 line"

    width, measure = _measure_for_anchor(src, SINGLE_ANCHOR)
    repl = build_wrapping_replacement(SINGLE_ANCHOR, measure, width)
    matches = find(str(src), SINGLE_ANCHOR)
    out = tmp_path / "single_out.pdf"
    result = replace(str(src), matches[0], repl, str(out), reflow=True)
    assert result.success, f"single-line reflow must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied

    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _INDENT_KIND not in kinds, (
        f"a plain single-line paragraph (no multi-line indent structure) must NOT "
        f"surface {_INDENT_KIND} — the common widening case must stay quiet; got {kinds}"
    )
    assert result.fidelity_report.font_preserved, (
        f"font_preserved must stay True; report={result.fidelity_report!r}"
    )


def test_inv_g_7_multiline_nonmonotone_surfaces_indent_flattened(
    tmp_path: Path,
) -> None:
    """A multi-line paragraph with non-monotone continuations surfaces indent_flattened.

    A GENUINE but un-classifiable indent: the continuation x-starts disagree
    with each other by more than the ``MIN_INDENT = font_size * 0.6`` noise
    floor, so ``_detect_indent_style`` cannot resolve it to first-line or
    hanging and falls back to FLUSH. Because a real multi-line indent signal
    WAS lost, the caller surfaces a typed
    ``Degradation(kind="indent_flattened", severity="info")``.
    ``indent_flattened`` is NON font-affecting, so ``font_preserved`` stays True.

    Regression guard — pins that the degraded-flush path still fires for a real
    un-classifiable multi-line indent (INV-G-7), distinct from the now-quiet
    plain single-line case.
    """
    assert _INDENT_KIND not in FONT_AFFECTING_KINDS, (
        f"{_INDENT_KIND} is a layout signal and must NOT be in FONT_AFFECTING_KINDS"
    )

    src = tmp_path / "indent_nonmono.pdf"
    build_indent_styles_pdf(src)
    xs = _source_x_starts(src, NONMONO_ANCHOR)
    assert len(xs) >= 3, f"non-monotone fixture must have >= 3 lines; got {xs}"
    # Continuations must be mutually inconsistent beyond the noise floor.
    rest = xs[1:]
    assert max(rest) - min(rest) > 11.0 * 0.6, (
        f"continuations must disagree beyond the MIN_INDENT noise floor; got {rest}"
    )

    width, measure = _measure_for_anchor(src, NONMONO_ANCHOR)
    repl = build_wrapping_replacement(NONMONO_ANCHOR, measure, width)
    matches = find(str(src), NONMONO_ANCHOR)
    out = tmp_path / "nonmono_out.pdf"
    result = replace(str(src), matches[0], repl, str(out), reflow=True)
    assert result.success, f"non-monotone reflow must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied

    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _INDENT_KIND in kinds, (
        f"a multi-line paragraph with non-monotone continuations must surface a "
        f"typed {_INDENT_KIND} Degradation; got kinds={kinds}"
    )
    deg = next(d for d in result.fidelity_report.degradations if d.kind == _INDENT_KIND)
    assert deg.severity == "info", f"{_INDENT_KIND} must be severity='info'; got {deg.severity!r}"
    assert result.fidelity_report.font_preserved, (
        f"{_INDENT_KIND} must not flip font_preserved; report={result.fidelity_report!r}"
    )
