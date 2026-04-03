"""Graphics state tracker for PDF content stream processing."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from pdf_edit_engine.models import GraphicsStateSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_IDENTITY: tuple[float, float, float, float, float, float] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


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
        # Graphics state (saved/restored by q/Q)
        self._ctm: tuple[float, float, float, float, float, float] = _IDENTITY
        self._fill_color: tuple[float, ...] | None = None
        self._stroke_color: tuple[float, ...] | None = None
        self._font_name: str | None = None
        self._font_size: float = 0.0
        self._char_spacing: float = 0.0  # Tc
        self._word_spacing: float = 0.0  # Tw
        self._horiz_scaling: float = 1.0  # Th (Tz/100)
        self._leading: float = 0.0  # TL
        self._text_render_mode: int = 0  # Tr
        self._text_rise: float = 0.0  # Ts

        # Graphics state stack for q/Q
        self._state_stack: list[dict[str, object]] = []

        # Text object state (NOT saved by q/Q, reset by BT)
        self._text_matrix: tuple[float, float, float, float, float, float] = _IDENTITY
        self._text_line_matrix: tuple[float, float, float, float, float, float] = _IDENTITY

        # Operator dispatch table
        self._handlers: dict[str, Callable[[list[object]], None]] = {
            "q": lambda ops: self.save(),
            "Q": lambda ops: self.restore(),
            "cm": self._handle_cm,
            "BT": self._handle_bt,
            "ET": self._handle_et,
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
            "Ts": self._handle_ts,
            "g": self._handle_g,
            "G": self._handle_g_upper,
            "rg": self._handle_rg,
            "RG": self._handle_rg_upper,
            "k": self._handle_k,
            "K": self._handle_k_upper,
            "cs": self._handle_cs,
            "CS": self._handle_cs_upper,
            "sc": self._handle_sc,
            "SC": self._handle_sc_upper,
            "scn": self._handle_sc,
            "SCN": self._handle_sc_upper,
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
        """Push current state onto the graphics state stack (q operator)."""
        self._state_stack.append({
            "ctm": self._ctm,
            "fill_color": self._fill_color,
            "stroke_color": self._stroke_color,
            "font_name": self._font_name,
            "font_size": self._font_size,
            "char_spacing": self._char_spacing,
            "word_spacing": self._word_spacing,
            "horiz_scaling": self._horiz_scaling,
            "leading": self._leading,
            "text_render_mode": self._text_render_mode,
            "text_rise": self._text_rise,
        })

    def restore(self) -> None:
        """Pop state from the graphics state stack (Q operator)."""
        if not self._state_stack:
            logger.warning("Unbalanced Q operator: state stack is empty")
            return
        state = self._state_stack.pop()
        self._ctm = state["ctm"]  # type: ignore[assignment]
        self._fill_color = state["fill_color"]  # type: ignore[assignment]
        self._stroke_color = state["stroke_color"]  # type: ignore[assignment]
        self._font_name = state["font_name"]  # type: ignore[assignment]
        self._font_size = state["font_size"]  # type: ignore[assignment]
        self._char_spacing = state["char_spacing"]  # type: ignore[assignment]
        self._word_spacing = state["word_spacing"]  # type: ignore[assignment]
        self._horiz_scaling = state["horiz_scaling"]  # type: ignore[assignment]
        self._leading = state["leading"]  # type: ignore[assignment]
        self._text_render_mode = state["text_render_mode"]  # type: ignore[assignment]
        self._text_rise = state["text_rise"]  # type: ignore[assignment]

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

    def advance_by_glyph(
        self, glyph_width: float, char_code: int, tj_adjustment: float = 0,
    ) -> None:
        """Advance the text position after rendering a glyph.

        Uses ISO 32000 section 9.4.4 displacement formula:
        tx = ((w0 - Tj/1000) * Tfs + Tc + Tw_if_space) * Th

        Args:
            glyph_width: Glyph width in text space (font units / 1000).
            char_code: The character code (word spacing applied if 0x0020).
            tj_adjustment: TJ array positioning value (thousandths of text space unit).
        """
        tw = self._word_spacing if char_code == 0x0020 else 0.0
        tx = (
            (glyph_width - tj_adjustment / 1000.0) * self._font_size
            + self._char_spacing
            + tw
        ) * self._horiz_scaling

        a, b, c, d, e, f = self._text_matrix
        self._text_matrix = (a, b, c, d, tx * a + e, tx * b + f)

    def snapshot(self) -> GraphicsStateSnapshot:
        """Capture the current graphics state as an immutable snapshot.

        Returns:
            A GraphicsStateSnapshot with all current state values.
        """
        return GraphicsStateSnapshot(
            ctm=self._ctm,
            fill_color=self._fill_color,
            stroke_color=self._stroke_color,
            font_name=self._font_name,
            font_size=self._font_size if self._font_name is not None else None,
            text_matrix=self._text_matrix,
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
    def stroke_color(self) -> tuple[float, ...] | None:
        """Current stroke color, or None if not set."""
        return self._stroke_color

    @property
    def text_rendering_mode(self) -> int:
        """Current text rendering mode (0-7)."""
        return self._text_render_mode

    # ── Operator handlers ───────────────────────────────────────────────

    def _handle_cm(self, operands: list[object]) -> None:
        m = (_f(operands[0]), _f(operands[1]), _f(operands[2]),
             _f(operands[3]), _f(operands[4]), _f(operands[5]))
        self._ctm = _mat_mult(m, self._ctm)

    def _handle_bt(self, operands: list[object]) -> None:
        self._text_matrix = _IDENTITY
        self._text_line_matrix = _IDENTITY

    def _handle_et(self, operands: list[object]) -> None:
        pass

    def _handle_tm(self, operands: list[object]) -> None:
        m = (_f(operands[0]), _f(operands[1]), _f(operands[2]),
             _f(operands[3]), _f(operands[4]), _f(operands[5]))
        self._text_matrix = m
        self._text_line_matrix = m

    def _handle_td(self, operands: list[object]) -> None:
        tx, ty = _f(operands[0]), _f(operands[1])
        translation: tuple[float, float, float, float, float, float] = (
            1.0, 0.0, 0.0, 1.0, tx, ty,
        )
        new_matrix = _mat_mult(translation, self._text_line_matrix)
        self._text_matrix = new_matrix
        self._text_line_matrix = new_matrix

    def _handle_td_upper(self, operands: list[object]) -> None:
        self._leading = -_f(operands[1])
        self._handle_td(operands)

    def _handle_tstar(self, operands: list[object]) -> None:
        self._handle_td([0.0, -self._leading])

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

    def _handle_tr(self, operands: list[object]) -> None:
        self._text_render_mode = int(_f(operands[0]))

    def _handle_ts(self, operands: list[object]) -> None:
        self._text_rise = _f(operands[0])

    def _handle_g(self, operands: list[object]) -> None:
        self._fill_color = (_f(operands[0]),)

    def _handle_g_upper(self, operands: list[object]) -> None:
        self._stroke_color = (_f(operands[0]),)

    def _handle_rg(self, operands: list[object]) -> None:
        self._fill_color = (_f(operands[0]), _f(operands[1]), _f(operands[2]))

    def _handle_rg_upper(self, operands: list[object]) -> None:
        self._stroke_color = (_f(operands[0]), _f(operands[1]), _f(operands[2]))

    def _handle_k(self, operands: list[object]) -> None:
        self._fill_color = (
            _f(operands[0]), _f(operands[1]), _f(operands[2]), _f(operands[3]),
        )

    def _handle_k_upper(self, operands: list[object]) -> None:
        self._stroke_color = (
            _f(operands[0]), _f(operands[1]), _f(operands[2]), _f(operands[3]),
        )

    def _handle_cs(self, operands: list[object]) -> None:
        # cs/CS set color space name — defer resolution to v2
        pass

    def _handle_cs_upper(self, operands: list[object]) -> None:
        pass

    def _handle_sc(self, operands: list[object]) -> None:
        values = self._safe_floats(operands)
        if values:
            self._fill_color = values

    def _handle_sc_upper(self, operands: list[object]) -> None:
        values = self._safe_floats(operands)
        if values:
            self._stroke_color = values

    @staticmethod
    def _safe_floats(operands: list[object]) -> tuple[float, ...]:
        """Convert numeric operands to floats, skipping non-numeric ones."""
        values: list[float] = []
        for o in operands:
            with contextlib.suppress(TypeError, ValueError):
                values.append(float(o))  # type: ignore[arg-type]
        return tuple(values)
