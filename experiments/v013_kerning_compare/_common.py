"""Shared helpers for the kerning experiment.

WinAnsi-encoded Calibri (the SOW PDF) only — keeps the experiment
focused. All the engine's CIDFont / Identity-H decoding is bypassed; we
read the simple-font /Widths array directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pikepdf

# Adobe WinAnsi map: maps Unicode → byte code.
# We hand-roll the subset we need; pikepdf's encoding helpers are stricter
# and refuse non-WinAnsi-mappable chars (e.g. Ø, ø, ü) silently.
WINANSI_UNICODE_TO_BYTE: dict[str, int] = {}

# Build from the standard Adobe WinAnsi table (subset relevant here):
#   A-Z, a-z, 0-9, basic punctuation map identity (ASCII).
for c in range(0x20, 0x7F):
    WINANSI_UNICODE_TO_BYTE[chr(c)] = c

# Latin-1-ish high bytes
for unicode_char, byte in {
    "Ø": 0xD8,
    "ø": 0xF8,
    "Ü": 0xDC,
    "ü": 0xFC,
    "ö": 0xF6,
    "Ö": 0xD6,
    "ä": 0xE4,
    "Ä": 0xC4,
    "ß": 0xDF,
    "é": 0xE9,
    "è": 0xE8,
    "ñ": 0xF1,
    "Ñ": 0xD1,
    "—": 0x97,  # em dash
    "–": 0x96,  # en dash
    "'": 0x92,  # right single quote
    "'": 0x91,  # left single quote
    """: 0x93,
    """: 0x94,
}.items():
    WINANSI_UNICODE_TO_BYTE[unicode_char] = byte

# Reverse map for decoding observed bytes → Unicode (display only).
WINANSI_BYTE_TO_UNICODE: dict[int, str] = {b: u for u, b in WINANSI_UNICODE_TO_BYTE.items()}


def winansi_encode(text: str) -> bytes:
    """Encode text to WinAnsi bytes. Raises if any char is unmappable."""
    out = bytearray()
    for ch in text:
        b = WINANSI_UNICODE_TO_BYTE.get(ch)
        if b is None:
            raise ValueError(f"WinAnsi cannot encode {ch!r} (U+{ord(ch):04X})")
        out.append(b)
    return bytes(out)


def winansi_decode(data: bytes) -> str:
    """Decode WinAnsi bytes → Unicode (best-effort)."""
    return "".join(WINANSI_BYTE_TO_UNICODE.get(b, chr(b)) for b in data)


@dataclass
class FontWidths:
    """Per-byte width table for a simple TrueType font with /Widths array.

    Width units are 1000ths of an em. PDF advances character N by
    width[N] * font_size / 1000 page units.
    """

    name: str  # PDF name like "/F2"
    base_font: str  # "/BCDFEE+Calibri"
    first_char: int
    last_char: int
    widths: list[float]
    default_width: float = 500.0

    def width(self, byte: int) -> float:
        """Width in 1000ths of an em."""
        if self.first_char <= byte <= self.last_char:
            idx = byte - self.first_char
            if 0 <= idx < len(self.widths):
                return self.widths[idx]
        return self.default_width


def load_page_fonts(page: Any) -> dict[str, FontWidths]:
    """Read the page's /Font dictionary and parse each simple-font's /Widths."""
    out: dict[str, FontWidths] = {}
    fonts = page.get("/Resources", {}).get("/Font", {}) or {}
    for name, font_obj in fonts.items():
        d = pikepdf.Dictionary(font_obj)
        if str(d.get("/Subtype", "")) != "/TrueType":
            # Not a simple TrueType — skip (the experiment is WinAnsi-Calibri only).
            continue
        first = int(d.get("/FirstChar", 0))
        last = int(d.get("/LastChar", 255))
        widths_arr = d.get("/Widths")
        widths: list[float] = []
        if widths_arr is not None:
            widths = [float(w) for w in widths_arr]  # type: ignore[union-attr]
        base_font = str(d.get("/BaseFont", "?"))
        out[str(name)] = FontWidths(
            name=str(name),
            base_font=base_font,
            first_char=first,
            last_char=last,
            widths=widths,
        )
    return out


def measure_text_width(text_bytes: bytes, fw: FontWidths, font_size: float) -> float:
    """Sum advance widths of the bytes at the given font size, in page units."""
    fu = sum(fw.width(b) for b in text_bytes)
    return fu * font_size / 1000.0


def find_text_in_content_stream(
    page: Any,
    target: str,
) -> tuple[int, int, str, float, FontWidths] | None:
    """Find a Tj or TJ operator whose decoded WinAnsi bytes contain `target`.

    Returns (operator_index, op_count, current_font_name, current_font_size,
    font_widths) or None.

    Walks BT/ET state to track the current /Font name and size. Returns the
    FIRST match.
    """
    page_fonts = load_page_fonts(page)
    ops = list(pikepdf.parse_content_stream(page))

    cur_font_name: str | None = None
    cur_font_size: float = 0.0

    for i, (operands, operator) in enumerate(ops):
        op_str = str(operator)
        if op_str == "Tf":
            cur_font_name = str(operands[0])
            cur_font_size = float(operands[1])
            continue
        if op_str == "Tj":
            data = bytes(operands[0])
            decoded = winansi_decode(data)
            if target in decoded:
                fw = page_fonts.get(cur_font_name or "")
                if fw is None:
                    continue
                return i, len(ops), cur_font_name or "", cur_font_size, fw
        if op_str == "TJ":
            arr = operands[0]
            joined = b""
            for item in arr:  # type: ignore[union-attr]
                if isinstance(item, pikepdf.String):
                    joined += bytes(item)
            decoded = winansi_decode(joined)
            if target in decoded:
                fw = page_fonts.get(cur_font_name or "")
                if fw is None:
                    continue
                return i, len(ops), cur_font_name or "", cur_font_size, fw
    return None


def get_op_bytes(operands: Any, operator: str) -> bytes:
    """Pull the visible bytes out of a Tj or TJ operand set."""
    if operator == "Tj":
        return bytes(operands[0])
    if operator == "TJ":
        out = b""
        for item in operands[0]:
            if isinstance(item, pikepdf.String):
                out += bytes(item)
        return out
    return b""
