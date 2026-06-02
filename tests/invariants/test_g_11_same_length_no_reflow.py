"""INV-G-11: a same-length non-ligature edit does NOT route through reflow.

Roadmap item **same-length-no-reflow** (the byte-stable splice path is used for a
same-character-count replacement, so a width change alone can never trigger an
unnecessary re-wrap that visually collides with neighbouring text).

``surgeon.replace`` decides whether to reflow purely from a WIDTH gate
(``surgeon.py`` ~:2051): ::

    old_width = sum(ch.width for ch in match.characters)
    new_width = _calculate_new_width(new_text, ...)
    needs_reflow = new_width > old_width + 1.0

That gate is blind to whether the replacement is the SAME CHARACTER COUNT as the
original. A same-length swap of NARROW glyphs for WIDE glyphs (``"iii"`` ->
``"WWW"`` — both 3 chars, but ``WWW`` is ~26pt wider than ``iii`` at 12pt in a
general-purpose TrueType font) trips ``new_width > old_width + 1.0`` and routes
the edit through ``reflow_paragraph``, which re-wraps the whole paragraph and
re-emits a fresh line layout — even though the byte-stable, in-place splice path
could write the replacement directly (a same-length edit needs no operand byte
shift). An unnecessary re-wrap can change line breaks / baselines and visually
collide with trailing text that the same-length splice would have left untouched.

THE GREEN CONTRACT (INV-G-11): a same-length non-ligature replacement is routed
through the byte-stable splice path, NOT reflow — ``reflow_applied`` is False,
``overflow_detected`` is False, no ``overflow_shift_suppressed`` degradation —
while the edit still applies (the replacement renders and the neighbouring text
on the line is intact). A genuine length-INCREASING edit (the control) still
reflows, so the fix is a length-equality refinement of the width gate, NOT a
blanket reflow suppression.

THE CORE PROBE (a) is RED TODAY for the right reason: the width gate
``new_width > old_width + 1.0`` trips on the wider same-length glyphs, so
``replace`` takes the reflow branch and the result reports
``reflow_applied=True`` (and may flag ``overflow_detected`` / an
``overflow_shift_suppressed`` degradation). The length-INCREASING CONTROL (b)
PASSES today and after the fix — it pins that the length-equality guard does NOT
over-suppress a real reflow.

All PDF opens for inspection route through ``pdf_edit_engine._pathutil.open_pdf``
(INV-L-1) via the public ``find`` / ``replace`` / ``get_text`` surface. Fixtures
are built in-test with the deterministic corpus builders (test tooling, outside
``src/``) and skip cleanly when no host TrueType font is available.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine import find, get_text, replace

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders._common import (  # noqa: E402
    emit_or_write,
    find_truetype_font,
    save_pdf_deterministic,
)
from corpus_builders._truetype_assembler import embed_identity_h_font  # noqa: E402

# ---------------------------------------------------------------------------
# Skip gate
# ---------------------------------------------------------------------------

_FONT_OK = find_truetype_font() is not None
_no_font = pytest.mark.skipif(
    not _FONT_OK, reason="no host TrueType font for Identity-H construction"
)

_OVERFLOW_SHIFT_KIND = "overflow_shift_suppressed"

# A NARROW search token and a SAME-CHARACTER-COUNT WIDE replacement. ``iii`` and
# ``WWW`` are both 3 chars, but ``WWW`` is ~26pt wider than ``iii`` at 12pt in a
# general-purpose TrueType font, so the replacement trips the width gate
# (new_width > old_width + 1.0) even though no operand byte shift is needed.
_NARROW_TOKEN = "iii"
_WIDE_SAME_LEN = "WWW"
_FONT_SIZE = 12.0


# ---------------------------------------------------------------------------
# Synthetic fixture builder (in-test; deterministic corpus-builder pattern)
# ---------------------------------------------------------------------------


def _attach_page(pdf: pikepdf.Pdf, font: object, content: bytes) -> None:
    """Attach a single 612x792 page carrying ``content`` and the font resource."""
    page = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
            "/Resources": pikepdf.Dictionary(
                {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(font.type0)})}  # type: ignore[attr-defined]
            ),
            "/Contents": pikepdf.Stream(pdf, content),
        }
    )
    pdf.pages.append(pikepdf.Page(page))


def _build_narrow_token_pdf(out: Path, phrase: str) -> object:
    """One page: ``phrase`` rendered by ONE Tj operator near the left margin.

    The phrase carries a narrow ``iii`` token followed by trailing text on the
    same line, all in a single ``<...> Tj`` operand (so a same-length splice
    edits in place without touching the trailing run). The full corpus (search +
    every replacement glyph) is passed to the assembler so every glyph is in the
    subset and no font extension is needed.

    Returns the :class:`EmbeddedFont` so a probe can measure the width delta
    that drives the reflow gate.
    """
    ttf = find_truetype_font()
    assert ttf is not None
    pdf = pikepdf.Pdf.new()
    try:
        # Cover every search + replacement glyph used by the probes below.
        font = embed_identity_h_font(pdf, ttf, phrase + " WWWXYZ")
        ops = [
            "BT",
            f"/F1 {_FONT_SIZE:g} Tf",
            "1 0 0 1 72 700 Tm",
            f"<{font.encode(phrase)}> Tj",
            "ET",
        ]
        _attach_page(pdf, font, "\n".join(ops).encode("latin-1"))
        emit_or_write(save_pdf_deterministic(pdf), out)
        return font
    finally:
        pdf.close()


# ===========================================================================
# (a) CORE — a same-length WIDE replacement must NOT route through reflow
#     (RED today: the width gate trips on the wider same-length glyphs, so
#      replace takes the reflow branch -> reflow_applied=True)
# ===========================================================================


@_no_font
def test_inv_g_11_same_length_wide_replacement_no_reflow(tmp_path: Path) -> None:
    """``iii`` -> ``WWW`` (same length, much wider): the byte-stable splice is used.

    THE CORE RED. ``"iii middle tail"`` renders in ONE Tj operator. Replacing the
    narrow ``iii`` with the same-character-count but much-wider ``WWW`` is a
    SAME-LENGTH edit — the in-place splice writes the replacement CID bytes at the
    matched ``byte_position`` with ZERO operand byte shift, so the trailing
    ``middle tail`` run is untouched and there is nothing to re-wrap. But the
    width gate (``new_width > old_width + 1.0``) sees ~26pt of extra width and
    routes the edit through ``reflow_paragraph``, which re-wraps the paragraph and
    re-emits a fresh line layout.

    GREEN contract (INV-G-11): the same-length edit uses the byte-stable splice
    path, so ``reflow_applied`` is False, ``overflow_detected`` is False, and no
    ``overflow_shift_suppressed`` degradation is emitted; the edit still applies
    (``WWW`` renders, the trailing text is intact).

    RED today for the right reason: the width gate trips, so ``replace`` takes the
    reflow branch and the result reports ``reflow_applied=True`` (and may flag
    overflow), instead of the byte-stable splice.
    """
    phrase = f"{_NARROW_TOKEN} middle tail"
    src = tmp_path / "same_len_wide.pdf"
    font = _build_narrow_token_pdf(src, phrase)

    # Precondition: the same-length swap is genuinely WIDER by > 1pt, so it DOES
    # trip the width gate today — otherwise the probe would pass for the wrong
    # reason (the gate never fired).
    old_w = font.advance(_NARROW_TOKEN, _FONT_SIZE)  # type: ignore[attr-defined]
    new_w = font.advance(_WIDE_SAME_LEN, _FONT_SIZE)  # type: ignore[attr-defined]
    assert len(_NARROW_TOKEN) == len(_WIDE_SAME_LEN), "tokens must be the same length"
    assert new_w > old_w + 1.0, (
        "precondition: the same-length replacement must be > 1pt wider so it trips "
        f"the reflow width gate today; old={old_w:.3f}pt new={new_w:.3f}pt"
    )

    matches = find(str(src), _NARROW_TOKEN)
    assert matches, f"fixture missing search token {_NARROW_TOKEN!r}"

    out = tmp_path / "same_len_wide_out.pdf"
    result = replace(str(src), matches[0], _WIDE_SAME_LEN, str(out), reflow=True)
    assert result.success, f"same-length edit must succeed: {result!r}"

    fr = result.fidelity_report

    # THE CORE GUARD: a same-length edit must take the byte-stable splice path,
    # NOT reflow. Today the width gate trips and reflow_applied is True.
    assert fr.reflow_applied is False, (
        "a same-length non-ligature replacement must NOT route through reflow "
        "(the byte-stable splice path edits it in place); the width gate "
        f"(new_width > old_width + 1.0) tripped on the wider same-length glyphs. "
        f"reflow_applied={fr.reflow_applied}, old={old_w:.3f}pt new={new_w:.3f}pt"
    )
    assert fr.overflow_detected is False, (
        "a same-length splice cannot overflow the original run's box; "
        f"overflow_detected={fr.overflow_detected}"
    )
    kinds = {d.kind for d in fr.degradations}
    assert _OVERFLOW_SHIFT_KIND not in kinds, (
        "a same-length splice must not emit an overflow-shift degradation; "
        f"got kinds={sorted(kinds)}"
    )

    # CORRECTNESS GUARD: the edit applied and the neighbours are intact.
    after = get_text(str(out), page=0)
    assert _WIDE_SAME_LEN in after, f"replacement {_WIDE_SAME_LEN!r} must render; text={after!r}"
    assert _NARROW_TOKEN not in after, (
        f"the narrow token {_NARROW_TOKEN!r} must be gone; text={after!r}"
    )
    assert "middle" in after and "tail" in after, (
        f"trailing same-line text must be intact after the splice; text={after!r}"
    )


# ===========================================================================
# (b) CONTROL — a genuine length-INCREASING edit STILL reflows (no over-suppress)
#     (passes today and after the fix)
# ===========================================================================


@_no_font
def test_inv_g_11_length_increase_still_reflows(tmp_path: Path) -> None:
    """``iii`` -> ``iiiXXXX`` (genuine length increase): reflow STILL applies.

    The over-suppression control (CRITICAL — prevents the length-equality guard
    from blanket-suppressing real reflow). A length-INCREASING replacement adds
    characters that shift the operand bytes and grow the run beyond its original
    width, which is exactly the case reflow exists for. The guard must refine the
    width gate by length-equality ONLY, so a true length change still routes
    through ``reflow_paragraph`` and reports ``reflow_applied=True``.

    Passes today (the width gate trips AND the lengths differ) and must keep
    passing after the INV-G-11 fix.
    """
    phrase = f"{_NARROW_TOKEN} middle tail"
    src = tmp_path / "len_increase.pdf"
    font = _build_narrow_token_pdf(src, phrase)

    new_text = _NARROW_TOKEN + "XXXX"  # genuine length increase (3 -> 7 chars)
    assert len(new_text) > len(_NARROW_TOKEN), "control must be a real length increase"
    old_w = font.advance(_NARROW_TOKEN, _FONT_SIZE)  # type: ignore[attr-defined]
    new_w = font.advance(new_text, _FONT_SIZE)  # type: ignore[attr-defined]
    assert new_w > old_w + 1.0, (
        f"precondition: the length-increasing replacement must be wider; "
        f"old={old_w:.3f}pt new={new_w:.3f}pt"
    )

    matches = find(str(src), _NARROW_TOKEN)
    assert matches, f"fixture missing search token {_NARROW_TOKEN!r}"

    out = tmp_path / "len_increase_out.pdf"
    result = replace(str(src), matches[0], new_text, str(out), reflow=True)
    assert result.success, f"length-increasing edit must succeed: {result!r}"

    assert result.fidelity_report.reflow_applied is True, (
        "a genuine length-INCREASING edit must STILL route through reflow (the "
        "INV-G-11 length-equality guard must refine the width gate by "
        "length-equality only, not blanket-suppress real reflow); "
        f"reflow_applied={result.fidelity_report.reflow_applied}"
    )
