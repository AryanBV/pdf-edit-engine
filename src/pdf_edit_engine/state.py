"""Graphics state tracker for PDF content stream processing."""

from __future__ import annotations


class GraphicsStateTracker:
    """Tracks the PDF graphics state while processing content stream operators.

    Maintains the current transformation matrix (CTM), font, colors, and text
    state as operators are processed sequentially.
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def process_operator(self, operator: str, operands: list[object]) -> None:
        """Update state based on a content stream operator.

        Args:
            operator: The PDF operator name (e.g., 'Tm', 'Tf', 'cm').
            operands: The operands for the operator.
        """
        raise NotImplementedError

    def save(self) -> None:
        """Push current state onto the graphics state stack (q operator)."""
        raise NotImplementedError

    def restore(self) -> None:
        """Pop state from the graphics state stack (Q operator)."""
        raise NotImplementedError
