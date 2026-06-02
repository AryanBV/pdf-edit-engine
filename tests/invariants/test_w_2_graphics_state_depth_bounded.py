"""INV-W-2 — q/Q graphics-state stack depth is bounded (DoS guard).

``GraphicsStateTracker.save()`` pushes a snapshot onto an in-memory stack on
every ``q`` operator. A malformed or adversarial PDF whose content stream
emits deeply-nested ``q`` operators with no matching ``Q`` grows the stack
without bound — unbounded memory growth / denial-of-service. Pre-fix there
was NO cap: ``save()`` appended forever (verified: 500 calls -> depth 500).

Root fix (NOT a patch): a ``MAX_GRAPHICS_STATE_DEPTH = 128`` constant in
``state.py`` + an explicit depth check at the TOP of ``save()``, BEFORE the
append. When the stack is already at the cap, ``save()`` raises
``OperatorError`` — which IS a ``PDFEditError`` subclass and the EXISTING
contract for a malformed content stream (INV-B-5 / INV-M-3 / INV-L-1
precedent). The locator content-stream interpreter calls ``save()`` on every
``q`` (``locator._dispatch`` -> ``process_operator('q', [])`` -> ``save()``);
``locator._build_index`` translates only NON-PDFEditError exceptions into
``OperatorError`` and an ``OperatorError`` raised directly by ``save()`` sails
through unchanged, so it surfaces honestly from ``find`` / ``get_text`` /
``get_text_layout`` on the read path and is caught + translated into a
degraded/failed ``EditResult`` on the surgeon edit verbs (which already
``except OperatorError``). NO new DegradationKind is needed.

This mirrors the existing ``MAX_COMPOSITE_DEPTH = 64`` depth-cap pattern
(INV-W0-8): a load-bearing constant that bounds unbounded growth in an
internal structure by raising the existing typed error, rather than letting
a malformed input exhaust memory or blow the Python recursion limit.

INV-W-2 minted as the next collision-free slot of the ``W`` robustness layer
(INV-W-1 = width-cache objgen hygiene; W-2 is the next free id — confirmed by
grep that no INV-W-2 / test_w_2_* existed before this probe). A robustness/
DoS guard belongs in the ``W`` layer rather than the content-stream-format
``B`` layer or the input-rejection ``M`` layer, alongside its sibling
``W0`` depth-cap (INV-W0-8).

RED EXPECTATION (this phase, no src changes): the ``from ... import
MAX_GRAPHICS_STATE_DEPTH`` below raises ``ImportError`` at collection time
because the constant does not exist yet — every test in this module errors
RED. After the fix lands, all tests pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf
import pytest

from pdf_edit_engine import get_text
from pdf_edit_engine.errors import OperatorError
from pdf_edit_engine.state import MAX_GRAPHICS_STATE_DEPTH, GraphicsStateTracker

if TYPE_CHECKING:
    from pathlib import Path


# ──────────────────────────────────────────────────────────────────────────
# UNIT probes: GraphicsStateTracker.save() enforces the cap before append.
# ──────────────────────────────────────────────────────────────────────────


def test_inv_w_2_max_graphics_state_depth_constant_is_locked() -> None:
    """INV-W-2: ``MAX_GRAPHICS_STATE_DEPTH`` is locked at 128.

    Legitimate PDFs nest ``q`` only a handful deep (each ``q`` is matched by
    a ``Q``; nesting beyond a few levels is rare even in complex vector
    artwork). 128 is generously above any real document while firmly
    bounding an adversarial unbounded-``q`` stream. Bumping the cap is a
    behavior change requiring signoff — guard it as a load-bearing constant.
    """
    assert MAX_GRAPHICS_STATE_DEPTH == 128


def test_inv_w_2_save_beyond_cap_raises_operator_error() -> None:
    """INV-W-2: the (cap+1)-th ``save()`` MUST raise ``OperatorError``.

    ``save()`` is called ``MAX_GRAPHICS_STATE_DEPTH`` times to fill the stack
    exactly to the cap (those must all succeed), then once more — that call
    must raise ``OperatorError`` BEFORE appending, so the stack never grows
    past the cap. Pre-fix this loop ran to completion with a stack of
    cap+1 (no cap) — genuinely RED today.
    """
    tracker = GraphicsStateTracker()
    # Fill exactly to the cap — these must all succeed (inclusive cap).
    for _ in range(MAX_GRAPHICS_STATE_DEPTH):
        tracker.save()

    with pytest.raises(OperatorError) as excinfo:
        tracker.save()

    # The message must reference the cap so a debugger can identify the exact
    # contract violation, not some unrelated OperatorError.
    assert str(MAX_GRAPHICS_STATE_DEPTH) in str(excinfo.value)
    # The raise must fire BEFORE the append: depth stays at the cap.
    assert len(tracker._state_stack) == MAX_GRAPHICS_STATE_DEPTH


def test_inv_w_2_save_up_to_cap_does_not_raise() -> None:
    """INV-W-2: filling the stack to exactly the cap MUST NOT raise.

    The cap is inclusive (the check fires only when the stack is ALREADY at
    the cap, i.e. a (cap+1)-th push would exceed it), so a legitimate stream
    that nests exactly to the cap and unwinds renders cleanly. Guards an
    off-by-one regression in the depth check.
    """
    tracker = GraphicsStateTracker()
    for _ in range(MAX_GRAPHICS_STATE_DEPTH):
        tracker.save()  # must not raise
    assert len(tracker._state_stack) == MAX_GRAPHICS_STATE_DEPTH


def test_inv_w_2_restore_reenables_save_headroom() -> None:
    """INV-W-2: a ``restore()`` (Q) frees a slot so ``save()`` works again.

    Pins that the cap tracks LIVE depth, not a monotonic counter: a balanced
    q/Q stream of any total length is never refused. Fill to the cap, pop
    once, and the next push succeeds.
    """
    tracker = GraphicsStateTracker()
    for _ in range(MAX_GRAPHICS_STATE_DEPTH):
        tracker.save()
    tracker.restore()  # frees one slot
    tracker.save()  # must succeed now (back at the cap)
    assert len(tracker._state_stack) == MAX_GRAPHICS_STATE_DEPTH


# ──────────────────────────────────────────────────────────────────────────
# INTEGRATION probe: a public API path over a deeply-nested-q page must
# surface an HONEST translated PDFEditError, not a raw blowup.
# ──────────────────────────────────────────────────────────────────────────


def _make_deeply_nested_q_pdf(path: Path, n_q: int) -> None:
    """Build a one-page PDF whose content stream emits ``n_q`` nested ``q``.

    The stream draws one text run, then opens ``n_q`` graphics-state scopes
    (``q``) with NO matching ``Q`` — the adversarial unbounded-nesting shape.
    Built directly via pikepdf so the malformed stream is byte-exact.
    """
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    page = pdf.pages[0]

    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    page["/Resources"] = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

    body = b"BT /F1 12 Tf 10 100 Td (hi) Tj ET\n" + (b"q\n" * n_q)
    page.Contents = pdf.make_stream(body)
    pdf.save(str(path))
    pdf.close()


def test_inv_w_2_deeply_nested_q_public_api_surfaces_honest_error(tmp_path: Path) -> None:
    """INV-W-2: ``get_text`` on a page with > cap nested ``q`` surfaces an
    HONEST translated ``PDFEditError`` — never a raw, non-PDFEditError blowup.

    Builds a content stream with ``MAX_GRAPHICS_STATE_DEPTH + 50`` nested
    ``q`` operators. The interpreter calls ``save()`` on each ``q``; once the
    cap is hit, ``save()`` raises ``OperatorError`` mid-walk, which propagates
    out of ``interpret()`` and through ``_build_index`` unchanged (it is
    already a ``PDFEditError`` subclass, so the narrow translate-tuple does
    not re-wrap it) and surfaces from ``get_text`` as a ``PDFEditError``.

    Pre-fix (no cap) ``get_text`` returns normally with an unboundedly-grown
    in-memory stack and never signals the malformed input — so this probe
    asserts ``get_text`` UNCONDITIONALLY raises ``OperatorError``. Reverting
    the cap makes ``get_text`` return cleanly, which fails the
    ``pytest.raises`` below — a genuine regression guard, not a tautology.
    ``OperatorError`` is the specific malformed-content-stream contract and a
    ``PDFEditError`` subclass, so an honest surfacing can never be a raw
    ``RecursionError`` / ``MemoryError`` / bare ``Exception``.
    """
    bad = tmp_path / "nested_q.pdf"
    _make_deeply_nested_q_pdf(bad, n_q=MAX_GRAPHICS_STATE_DEPTH + 50)

    with pytest.raises(OperatorError):
        get_text(str(bad))
