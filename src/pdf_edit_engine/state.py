"""Graphics state tracker for PDF content stream processing."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from pdf_edit_engine.errors import OperatorError
from pdf_edit_engine.models import GraphicsStateSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_IDENTITY: tuple[float, float, float, float, float, float] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# Legitimate PDFs nest q a handful deep; the cap bounds an adversarial
# unbounded-q stream; raising OperatorError reuses the existing
# malformed-content-stream contract -- root fix, not a patch. Mirrors the
# MAX_COMPOSITE_DEPTH=64 precedent in fonts.py.
MAX_GRAPHICS_STATE_DEPTH = 128


def _mat_mult(
    m1: tuple[float, float, float, float, float, float],
    m2: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    """Multiply two 3x3 affine matrices represented as 6-element tuples.

    Matrix layout: [a b 0; c d 0; e f 1].

    Args:
        m1: First matrix (left operand).
        m2: Second matrix (right operand).

    Returns:
        The product m1 x m2 as a 6-element tuple.
    """
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _f(value: object) -> float:
    """Coerce a pikepdf operand to float."""
    return float(value)  # type: ignore[arg-type]


class GraphicsStateTracker:
    """Tracks the PDF graphics state while processing content stream operators.

    Maintains the current transformation matrix (CTM), font, colors, and text
    state as operators are processed sequentially.
    """

    def __init__(self) -> None:
        # Graphics state (saved/restored by q/Q). Only fill_color is
        # tracked: every consumer in the engine (locator, reflow,
        # surgeon, structural) reads .fill_color but never .stroke_color.
        # The stroke-state and text_rise tracking from prior versions
        # was never read in production; removed in v0.1.2 cleanup.
        self._ctm: tuple[float, float, float, float, float, float] = _IDENTITY
        self._fill_color: tuple[float, ...] | None = None
        # Block F CORE (v0.2.0): verbatim fill color-setting operator
        # subsequence kept in lockstep with _fill_color. Each entry is
        # (operands, operator_name); operands are the ORIGINAL pikepdf objects
        # (pikepdf.Name for cs color-space names, numerics for sc/scn/g/rg/k)
        # — NOT routed through _safe_floats/_f, so non-device color-space
        # identity survives for verbatim replay in reflow.
        self._fill_color_ops: list[tuple[list[object], str]] | None = None
        self._font_name: str | None = None
        self._font_size: float = 0.0
        self._char_spacing: float = 0.0  # Tc
        self._word_spacing: float = 0.0  # Tw
        self._horiz_scaling: float = 1.0  # Th (Tz/100)
        self._leading: float = 0.0  # TL
        # E.3 (v0.2.0): declared-leading authority. ``_leading_active`` is the
        # AUTHORITATIVE signal snapshotted for reflow: True iff the CURRENT text
        # line was positioned by a freshly-declared leading — either an absolute
        # ``Tm`` that committed a TL/TD declared since the previous line, or a
        # leading-mechanism advance (``T*`` / ``'`` / ``"``). A stale ``TL``
        # value left in the graphics state by an EARLIER paragraph (no fresh
        # TL/TD before this line's ``Tm``) is NOT authoritative, so reflow keeps
        # such paragraphs byte-identical. ``_pending_tl`` remembers that a
        # TL/TD was declared but not yet committed by a ``Tm``/``T*``; the next
        # absolute ``Tm`` commits it into ``_leading_active``. Both are saved/
        # restored on the q/Q stack alongside ``_leading`` so an in-q ``TL``
        # cannot leak its authority to text drawn after the matching ``Q``.
        self._leading_active: bool = False
        self._pending_tl: bool = False
        self._text_render_mode: int = 0  # Tr

        # Graphics state stack for q/Q
        self._state_stack: list[dict[str, object]] = []

        # Text object state (NOT saved by q/Q, reset by BT)
        self._text_matrix: tuple[float, float, float, float, float, float] = _IDENTITY
        self._text_line_matrix: tuple[float, float, float, float, float, float] = _IDENTITY

        # Operator dispatch table. Stroke-state operators (G, RG, K,
        # SC, SCN) are intentionally absent — the engine does not
        # consume stroke color anywhere. The FILL color-space select
        # ``cs`` IS handled (Block F CORE) to capture the verbatim fill
        # color-setting subsequence; the STROKE color-space select ``CS``
        # is an explicit no-op (stroke is out of scope, but registering it
        # documents that it is consciously ignored for fill capture).
        self._handlers: dict[str, Callable[[list[object]], None]] = {
            "q": lambda ops: self.save(),
            "Q": lambda ops: self.restore(),
            "cm": self._handle_cm,
            "BT": self._handle_bt,
            "Tm": self._handle_tm,
            "Td": self._handle_td,
            "TD": self._handle_td_upper,
            "T*": self._handle_tstar,
            "Tf": self._handle_tf,
            "Tc": self._handle_tc,
            "Tw": self._handle_tw,
            "Tz": self._handle_tz,
            "TL": self._handle_tl,
            "Tr": self._handle_tr,
            "cs": self._handle_cs,
            "CS": self._handle_cs_noop,
            "g": self._handle_g,
            "rg": self._handle_rg,
            "k": self._handle_k,
            "sc": lambda ops: self._handle_sc(ops, "sc"),
            "scn": lambda ops: self._handle_sc(ops, "scn"),
        }

    # ── Public API ──────────────────────────────────────────────────────

    def process_operator(self, operator: str, operands: list[object]) -> None:
        """Update state based on a content stream operator.

        Args:
            operator: The PDF operator name (e.g., 'Tm', 'Tf', 'cm').
            operands: The operands for the operator.
        """
        handler = self._handlers.get(operator)
        if handler is not None:
            handler(operands)

    def save(self) -> None:
        """Push current state onto the graphics state stack (q operator).

        Raises:
            OperatorError: If the stack is already at ``MAX_GRAPHICS_STATE_DEPTH``
                (a malformed deeply-nested ``q`` stream). The cap is inclusive:
                filling exactly to the cap succeeds, the next push raises.
        """
        if len(self._state_stack) >= MAX_GRAPHICS_STATE_DEPTH:
            raise OperatorError(
                f"graphics-state stack depth exceeds {MAX_GRAPHICS_STATE_DEPTH} "
                f"(malformed deeply-nested q operator)"
            )
        self._state_stack.append(
            {
                "ctm": self._ctm,
                "fill_color": self._fill_color,
                "fill_color_ops": self._fill_color_ops,
                "font_name": self._font_name,
                "font_size": self._font_size,
                "char_spacing": self._char_spacing,
                "word_spacing": self._word_spacing,
                "horiz_scaling": self._horiz_scaling,
                "leading": self._leading,
                "leading_active": self._leading_active,
                "pending_tl": self._pending_tl,
                "text_render_mode": self._text_render_mode,
            }
        )

    def restore(self) -> None:
        """Pop state from the graphics state stack (Q operator)."""
        if not self._state_stack:
            logger.warning("Unbalanced Q operator: state stack is empty")
            return
        state = self._state_stack.pop()
        self._ctm = state["ctm"]  # type: ignore[assignment]
        self._fill_color = state["fill_color"]  # type: ignore[assignment]
        self._fill_color_ops = state["fill_color_ops"]  # type: ignore[assignment]
        self._font_name = state["font_name"]  # type: ignore[assignment]
        self._font_size = state["font_size"]  # type: ignore[assignment]
        self._char_spacing = state["char_spacing"]  # type: ignore[assignment]
        self._word_spacing = state["word_spacing"]  # type: ignore[assignment]
        self._horiz_scaling = state["horiz_scaling"]  # type: ignore[assignment]
        self._leading = state["leading"]  # type: ignore[assignment]
        self._leading_active = state["leading_active"]  # type: ignore[assignment]
        self._pending_tl = state["pending_tl"]  # type: ignore[assignment]
        self._text_render_mode = state["text_render_mode"]  # type: ignore[assignment]

    def get_text_position(self) -> tuple[float, float]:
        """Get the current text position in user space.

        Returns:
            Tuple of (x, y) coordinates from compositing Tm with CTM.
        """
        tm = self._text_matrix
        ctm = self._ctm
        x = tm[4] * ctm[0] + tm[5] * ctm[2] + ctm[4]
        y = tm[4] * ctm[1] + tm[5] * ctm[3] + ctm[5]
        return (x, y)

    def apply_tj_displacement(self, value: float) -> None:
        """Apply a TJ numeric adjustment as a pure text matrix translation.

        Separates TJ kerning from glyph advance so that character spacing (Tc)
        is not erroneously applied to kerning values.

        Args:
            value: TJ positioning value in thousandths of a text space unit.
                   Negative values move right (tighten), positive move left.
        """
        tx = -value / 1000.0 * self._font_size
        a, b, c, d, e, f = self._text_matrix
        self._text_matrix = (a, b, c, d, tx * a + e, tx * b + f)

    def advance_by_glyph(self, glyph_width: float, char_code: int) -> None:
        """Advance the text position after rendering a glyph.

        Uses ISO 32000 section 9.4.4 displacement formula (without TJ component):
        tx = (w0 * Tfs + Tc + Tw_if_space) * Th

        TJ adjustments should be applied separately via apply_tj_displacement()
        before calling this method, to avoid Tc being applied to kerning values.

        Args:
            glyph_width: Glyph width in text space (font units / 1000).
            char_code: The character code (word spacing applied if 0x0020).
        """
        tw = self._word_spacing if char_code == 0x0020 else 0.0
        tx = (glyph_width * self._font_size + self._char_spacing + tw) * self._horiz_scaling

        a, b, c, d, e, f = self._text_matrix
        self._text_matrix = (a, b, c, d, tx * a + e, tx * b + f)

    def snapshot(self) -> GraphicsStateSnapshot:
        """Capture the current graphics state as an immutable snapshot.

        Returns:
            A GraphicsStateSnapshot with all current state values.
        """
        # Block F CORE: snapshot the verbatim fill color-setting subsequence
        # as an immutable point-in-time record. A shallow copy of the outer
        # list plus per-entry (list-copy, name) is enough — the inner operand
        # objects are immutable pikepdf scalars / Names — so a later mutation
        # of self._fill_color_ops does not alias this snapshot.
        fill_color_ops: list[tuple[list[object], str]] | None
        if self._fill_color_ops is None:
            fill_color_ops = None
        else:
            fill_color_ops = [(list(operands), name) for operands, name in self._fill_color_ops]

        return GraphicsStateSnapshot(
            ctm=self._ctm,
            fill_color=self._fill_color,
            font_name=self._font_name,
            font_size=self._font_size if self._font_name is not None else None,
            text_matrix=self._text_matrix,
            fill_color_ops=fill_color_ops,
            leading=self._leading,
            leading_authoritative=self._leading_active,
        )

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def ctm(self) -> tuple[float, ...]:
        """Current transformation matrix as a 6-element tuple."""
        return self._ctm

    @property
    def font_name(self) -> str | None:
        """Current font name, or None if no font has been set."""
        return self._font_name

    @property
    def font_size(self) -> float:
        """Current font size."""
        return self._font_size

    @property
    def fill_color(self) -> tuple[float, ...] | None:
        """Current fill color, or None if not set."""
        return self._fill_color

    @property
    def text_rendering_mode(self) -> int:
        """Current text rendering mode (0-7)."""
        return self._text_render_mode

    # ── Operator handlers ───────────────────────────────────────────────

    def _handle_cm(self, operands: list[object]) -> None:
        m = (
            _f(operands[0]),
            _f(operands[1]),
            _f(operands[2]),
            _f(operands[3]),
            _f(operands[4]),
            _f(operands[5]),
        )
        self._ctm = _mat_mult(m, self._ctm)

    def _handle_bt(self, operands: list[object]) -> None:
        self._text_matrix = _IDENTITY
        self._text_line_matrix = _IDENTITY

    def _handle_tm(self, operands: list[object]) -> None:
        m = (
            _f(operands[0]),
            _f(operands[1]),
            _f(operands[2]),
            _f(operands[3]),
            _f(operands[4]),
            _f(operands[5]),
        )
        self._text_matrix = m
        self._text_line_matrix = m
        # E.3: an absolute Tm COMMITS the declared-leading authority. The new
        # line is authoritative iff a fresh TL/TD was declared since the last
        # line was committed (``_pending_tl``); a stale leading inherited from
        # an earlier paragraph (no fresh TL before this Tm) is NOT authoritative.
        self._leading_active = self._pending_tl
        self._pending_tl = False

    def _handle_td(self, operands: list[object]) -> None:
        tx, ty = _f(operands[0]), _f(operands[1])
        translation: tuple[float, float, float, float, float, float] = (
            1.0,
            0.0,
            0.0,
            1.0,
            tx,
            ty,
        )
        new_matrix = _mat_mult(translation, self._text_line_matrix)
        self._text_matrix = new_matrix
        self._text_line_matrix = new_matrix

    def _handle_td_upper(self, operands: list[object]) -> None:
        self._leading = -_f(operands[1])
        # E.3: TD declares a leading (via -ty) AND moves to the next line by
        # that leading in one operator, so the resulting line IS positioned by a
        # freshly-declared leading — authoritative immediately (no separate Tm
        # commit needed).
        self._pending_tl = False
        self._leading_active = True
        self._handle_td(operands)

    def _handle_tstar(self, operands: list[object]) -> None:
        # E.3: T* advances to the next line BY the current leading, so the new
        # line is positioned by the declared leading — authoritative.
        self._handle_td([0.0, -self._leading])
        self._pending_tl = False
        self._leading_active = True

    def _handle_tf(self, operands: list[object]) -> None:
        name = str(operands[0])
        if name.startswith("/"):
            name = name[1:]
        self._font_name = name
        self._font_size = _f(operands[1])

    def _handle_tc(self, operands: list[object]) -> None:
        self._char_spacing = _f(operands[0])

    def _handle_tw(self, operands: list[object]) -> None:
        self._word_spacing = _f(operands[0])

    def _handle_tz(self, operands: list[object]) -> None:
        self._horiz_scaling = _f(operands[0]) / 100.0

    def _handle_tl(self, operands: list[object]) -> None:
        self._leading = _f(operands[0])
        # E.3: an explicit TL DECLARES a leading but does not itself position a
        # line. Arm it as pending; the next absolute Tm commits it into
        # _leading_active (or a T* advance consumes it). This distinguishes a
        # fresh TL issued for THIS paragraph from a stale TL value inherited
        # from an earlier paragraph in the same content stream.
        self._pending_tl = True

    def _handle_tr(self, operands: list[object]) -> None:
        self._text_render_mode = int(_f(operands[0]))

    def _handle_cs(self, operands: list[object]) -> None:
        # Block F CORE: ``cs`` selects the FILL color space and STARTS a fresh
        # captured subsequence. It does not by itself change _fill_color (the
        # following sc/scn supplies the components); we only begin recording
        # the verbatim op list so a subsequent scn appends to the right cs.
        self._fill_color_ops = [(list(operands), "cs")]

    def _handle_cs_noop(self, operands: list[object]) -> None:
        # ``CS`` selects the STROKE color space — out of scope for fill
        # capture. Explicit no-op so the dispatch table documents that it is
        # consciously ignored rather than accidentally omitted.
        return

    def _handle_g(self, operands: list[object]) -> None:
        self._fill_color = (_f(operands[0]),)
        # Device fill resets any prior cs: REPLACE the captured subsequence.
        self._fill_color_ops = [(list(operands), "g")]

    def _handle_rg(self, operands: list[object]) -> None:
        self._fill_color = (_f(operands[0]), _f(operands[1]), _f(operands[2]))
        self._fill_color_ops = [(list(operands), "rg")]

    def _handle_k(self, operands: list[object]) -> None:
        self._fill_color = (
            _f(operands[0]),
            _f(operands[1]),
            _f(operands[2]),
            _f(operands[3]),
        )
        self._fill_color_ops = [(list(operands), "k")]

    def _handle_sc(self, operands: list[object], op_name: str) -> None:
        values = self._safe_floats(operands)
        if values:
            self._fill_color = values
        # Block F CORE: append the verbatim sc/scn op to the current
        # subsequence (the cs that preceded it), preserving the exact operator
        # name so a stream using ``sc`` replays ``sc`` and one using ``scn``
        # replays ``scn``. If no cs was seen the op is implicitly in the
        # current device space; start a one-entry list so the verbatim tint
        # still replays.
        if self._fill_color_ops is None:
            self._fill_color_ops = [(list(operands), op_name)]
        else:
            self._fill_color_ops = [*self._fill_color_ops, (list(operands), op_name)]

    @staticmethod
    def _safe_floats(operands: list[object]) -> tuple[float, ...]:
        """Convert numeric operands to floats, skipping non-numeric ones."""
        values: list[float] = []
        for o in operands:
            with contextlib.suppress(TypeError, ValueError):
                values.append(float(o))  # type: ignore[arg-type]
        return tuple(values)
