"""INV-F-10: opt-in shrink-to-fit font-size policy on ``replace_block``.

Roadmap item E.8 (``font_size_reduced`` shrink-to-fit). ``replace_block``
edits text within a FIXED-height bbox region. When a replacement overflows the
region height, the engine today reflows / compresses line height but keeps the
font size unchanged. E.8 adds an OPT-IN ``fit`` keyword: in the shrink mode it
BINARY-SEARCHES the font size DOWN — against the existing width/height oracle
(``break_into_lines`` wrapped-line-count times line-height vs region height) —
until the text fits, with a ``min_pt`` FLOOR, emitting a NEW typed
``Degradation(kind="font_size_reduced", severity="info")`` whenever it shrinks.

The contract has three load-bearing halves this file pins:

(a) **Shrink surfaces.** An overflowing ``replace_block`` with ``fit="shrink"``
    renders at a Tf font size STRICTLY below the original, the rendered text
    fits the region, and ``font_size_reduced`` is surfaced.

(b) **Default is byte-identical.** The SAME edit with the DEFAULT ``fit`` value
    produces byte-for-byte the pre-E.8 ``replace_block`` output (the legacy
    no-``fit`` call). Existing callers are unaffected; the default is the only
    blast-radius control. No ``font_size_reduced`` event.

(c) **Floor honesty.** Text that cannot fit even at the ``min_pt`` floor stops
    AT the floor (font size == floor), STILL emits ``font_size_reduced``, and
    STILL reports overflow honestly (``overflow_detected`` True) — the shrink
    is a best-effort fit, never a silent lie.

``font_size_reduced`` is NOT in ``FONT_AFFECTING_KINDS``: a size change does
not change glyph identity, so ``font_preserved`` stays True (pinned in
``test_j_8`` and asserted here).

Regression guard — fails on the pre-E.8 engine where ``replace_block`` has no
``fit`` keyword (TypeError) and never shrinks the font.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf

from pdf_edit_engine import replace_block

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders.shrink_to_fit import (  # noqa: E402
    BODY_FONT_SIZE,
    FIT_BBOX,
    build_overflow_text,
    build_shrink_to_fit_pdf,
)

_REDUCED_KIND = "font_size_reduced"

# Engineering default floor (see E.8 design): min_pt = max(4.0, original*0.5).
# For the 11pt fixture body that is max(4.0, 5.5) == 5.5pt.
_EXPECTED_FLOOR = max(4.0, BODY_FONT_SIZE * 0.5)


def _tf_sizes(pdf_path: Path) -> list[float]:
    """Return every ``Tf`` font-size operand emitted in the page-0 stream."""
    sizes: list[float] = []
    with pikepdf.open(str(pdf_path)) as pdf:
        for operands, operator in pikepdf.parse_content_stream(pdf.pages[0]):
            if str(operator) == "Tf":
                sizes.append(float(operands[1]))
    return sizes


def _region_fits(pdf_path: Path, region_height: float) -> bool:
    """Heuristic geometry check: rendered text band stays within the region.

    Measures the vertical span of the replacement's text baselines from the
    emitted ``Tm``/``Td`` positions and confirms it does not exceed the
    region height. A coarse oracle on purpose — the probe's job is to prove
    the shrink happened and the output is no taller than the region, not to
    re-derive the engine's exact wrap.
    """
    ys: list[float] = []
    with pikepdf.open(str(pdf_path)) as pdf:
        cur_y = 0.0
        for operands, operator in pikepdf.parse_content_stream(pdf.pages[0]):
            op = str(operator)
            if op == "Tm" and len(operands) == 6:
                cur_y = float(operands[5])
                ys.append(cur_y)
            elif op == "Td" and len(operands) == 2:
                cur_y += float(operands[1])
                ys.append(cur_y)
    if len(ys) < 2:
        return True
    return (max(ys) - min(ys)) <= region_height


# ── Probe (a): RED — fit='shrink' shrinks the font and surfaces the event ──
def test_inv_f_10_shrink_reduces_font_and_surfaces(tmp_path: Path) -> None:
    """Overflowing fit='shrink' renders below the original size + surfaces it.

    Regression guard — fails on the pre-E.8 engine (no ``fit`` keyword →
    TypeError; and the font would never shrink even if the call were made).
    """
    src = tmp_path / "shrink.pdf"
    build_shrink_to_fit_pdf(src)
    out = tmp_path / "shrink_out.pdf"

    overflow = build_overflow_text(40)
    result = replace_block(str(src), 0, FIT_BBOX, overflow, str(out), fit="shrink")

    assert result.success, f"shrink replace_block must succeed: {result!r}"

    sizes = _tf_sizes(out)
    assert sizes, "output emitted no Tf operator"
    assert max(sizes) < BODY_FONT_SIZE, (
        "fit='shrink' must render at a font size strictly below the original "
        f"{BODY_FONT_SIZE}pt; got Tf sizes={sizes}"
    )
    assert max(sizes) >= _EXPECTED_FLOOR, (
        f"shrunk font size must not drop below the floor {_EXPECTED_FLOOR}pt; got {sizes}"
    )

    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _REDUCED_KIND in kinds, (
        f"a shrink-to-fit edit must surface a typed {_REDUCED_KIND} Degradation; got kinds={kinds}"
    )
    deg = next(d for d in result.fidelity_report.degradations if d.kind == _REDUCED_KIND)
    assert deg.severity == "info", f"{_REDUCED_KIND} must be severity='info'; got {deg.severity!r}"

    region_height = FIT_BBOX[3] - FIT_BBOX[1]
    assert _region_fits(out, region_height), (
        "after shrinking, the rendered text band must fit the fixed-height region"
    )


def test_inv_f_10_shrink_is_not_font_affecting(tmp_path: Path) -> None:
    """The shrink event is non font-affecting: font_preserved stays True."""
    from pdf_edit_engine.models import FONT_AFFECTING_KINDS

    assert _REDUCED_KIND not in FONT_AFFECTING_KINDS, (
        f"{_REDUCED_KIND} is a size signal and must NOT be in FONT_AFFECTING_KINDS"
    )

    src = tmp_path / "shrink2.pdf"
    build_shrink_to_fit_pdf(src)
    out = tmp_path / "shrink_out2.pdf"
    overflow = build_overflow_text(40)
    result = replace_block(str(src), 0, FIT_BBOX, overflow, str(out), fit="shrink")

    assert result.success
    assert result.fidelity_report.font_preserved, (
        "a font-size-reduction signal must not flip font_preserved — glyph "
        f"identity is untouched; report={result.fidelity_report!r}"
    )


# ── Probe (b): RED — default fit is byte-identical to the legacy call ───────
def test_inv_f_10_default_fit_byte_identical(tmp_path: Path) -> None:
    """The DEFAULT ``fit`` value reproduces the pre-E.8 output byte-for-byte.

    The legacy no-``fit`` call is the regression reference. Calling with the
    explicit DEFAULT ``fit`` value must produce byte-identical bytes and emit
    no ``font_size_reduced`` event. This is the entire blast-radius contract:
    existing callers (who never pass ``fit``) are unaffected.

    Regression guard — fails on the pre-E.8 engine (no ``fit`` keyword →
    TypeError on the explicit-default call).
    """
    src = tmp_path / "shrink_b.pdf"
    build_shrink_to_fit_pdf(src)
    overflow = build_overflow_text(40)

    out_legacy = tmp_path / "legacy.pdf"
    res_legacy = replace_block(str(src), 0, FIT_BBOX, overflow, str(out_legacy))
    assert res_legacy.success, f"legacy replace_block must succeed: {res_legacy!r}"

    out_default = tmp_path / "default.pdf"
    # The default value must be the no-op fit. ``"none"`` is the E.8 default
    # literal that reproduces today's behaviour.
    res_default = replace_block(str(src), 0, FIT_BBOX, overflow, str(out_default), fit="none")
    assert res_default.success

    legacy_bytes = out_legacy.read_bytes()
    default_bytes = out_default.read_bytes()
    assert default_bytes == legacy_bytes, (
        "fit='none' (the default) must be byte-identical to the legacy no-fit "
        f"call; sizes legacy={len(legacy_bytes)} default={len(default_bytes)}"
    )

    kinds = {d.kind for d in res_default.fidelity_report.degradations}
    assert _REDUCED_KIND not in kinds, (
        f"the default (no-op) fit must NOT surface {_REDUCED_KIND}; got {kinds}"
    )


# ── Probe (c): RED — floor honesty (stop at floor, still surface + overflow) ─
def test_inv_f_10_floor_stops_and_stays_honest(tmp_path: Path) -> None:
    """Text that cannot fit even at the floor stops at the floor, honestly.

    The replacement is so large it overflows the region even at ``min_pt``.
    The engine must clamp at the floor (Tf size == floor), STILL emit
    ``font_size_reduced`` (it did shrink), and STILL report overflow honestly
    (``overflow_detected`` True) — never a silent claim the text fit.

    Regression guard — fails on the pre-E.8 engine (no ``fit`` keyword).
    """
    src = tmp_path / "shrink_c.pdf"
    build_shrink_to_fit_pdf(src)
    out = tmp_path / "floor_out.pdf"

    # A very large run that cannot fit the ~30pt region even at the 5.5pt floor.
    huge = build_overflow_text(400)
    result = replace_block(str(src), 0, FIT_BBOX, huge, str(out), fit="shrink")

    assert result.success, f"floor-case replace_block must succeed: {result!r}"

    sizes = _tf_sizes(out)
    assert sizes, "output emitted no Tf operator"
    assert abs(max(sizes) - _EXPECTED_FLOOR) < 1e-6, (
        f"font size must clamp AT the floor {_EXPECTED_FLOOR}pt when even the "
        f"floor cannot fit; got Tf sizes={sizes}"
    )

    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _REDUCED_KIND in kinds, (
        f"shrinking to the floor still counts as a reduction; {_REDUCED_KIND} "
        f"must be surfaced; got kinds={kinds}"
    )
    assert result.fidelity_report.overflow_detected, (
        "when even the floor cannot fit, overflow must STILL be reported "
        "honestly — the shrink is best-effort, not a silent lie"
    )
