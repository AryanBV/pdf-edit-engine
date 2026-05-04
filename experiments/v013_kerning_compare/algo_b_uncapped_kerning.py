"""Algorithm B — uncapped proportional per-glyph kerning.

Identical to v0.1.2 `surgeon._encode_with_kerning` except the
`abs(total_kern) > 0.5 * original_fu` cap (line 271) is removed. The
proportional-kerning loop runs at any width delta — no flat-fallback
short-circuit.

Single-Tj operator path only (matches the SOW PDF's content stream
shape for "Sarah Chen"). Replaces the matched bytes inside the Tj
operand with a TJ array carrying the per-glyph kerning offsets.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf

from _common import (
    find_text_in_content_stream,
    get_op_bytes,
    measure_text_width,
    winansi_decode,
    winansi_encode,
)


def _build_kerned_tj_items(
    replacement_bytes: bytes,
    original_width_page: float,
    font_widths,
    font_size: float,
) -> list[object]:
    """Build TJ items list with per-glyph kerning compressing the replacement
    into the original target width. Algo B: NO 0.5x cap."""
    if not replacement_bytes:
        return []
    if len(replacement_bytes) == 1:
        return [pikepdf.String(replacement_bytes)]

    # Glyph items: per-byte (encoded_byte, width_in_font_units)
    glyph_items: list[tuple[bytes, float]] = [
        (bytes([b]), font_widths.width(b)) for b in replacement_bytes
    ]

    if original_width_page <= 0 or font_size <= 0:
        flat = b"".join(enc for enc, _ in glyph_items)
        return [pikepdf.String(flat)]

    # Convert to font units (1/1000 em)
    original_fu = original_width_page * 1000.0 / font_size
    replacement_fu = sum(w for _, w in glyph_items)

    total_kern = replacement_fu - original_fu
    num_gaps = len(glyph_items) - 1

    # ── ALGO B: NO `> 0.5 * original_fu` cap. Proportional kerning runs at
    # ── any delta.

    kern_values: list[int] = []
    if replacement_fu > 0 and abs(total_kern) > 0.5:
        accumulated = 0.0
        for i in range(num_gaps):
            w_i = glyph_items[i][1]
            ideal = total_kern * (w_i / replacement_fu)
            accumulated += ideal
            kern_int = round(accumulated) - sum(kern_values)
            kern_values.append(kern_int)
    else:
        kern_values = [0] * num_gaps

    if all(k == 0 for k in kern_values):
        flat = b"".join(enc for enc, _ in glyph_items)
        return [pikepdf.String(flat)]

    # TJ kern convention: positive = move LEFT (tighten), negative = move RIGHT (widen).
    # We emit `-kern` because the formula above was written with kern>0 = tighten.
    # Cross-check: surgeon.py emits `kern_values[i]` as-is. Their convention is the
    # same — we keep it identical.
    result: list[object] = []
    for i, (encoded, _) in enumerate(glyph_items):
        result.append(pikepdf.String(encoded))
        if i < num_gaps and kern_values[i] != 0:
            result.append(kern_values[i])
    return result


def apply_algo_b(
    pdf_path: Path,
    target: str,
    replacement: str,
    out_path: Path,
) -> dict[str, float]:
    """Apply algo B. Returns metrics dict."""
    with pikepdf.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        match = find_text_in_content_stream(page, target)
        if match is None:
            raise RuntimeError(f"target {target!r} not found in page-0 content stream")
        op_idx, _n_ops, _font_name, font_size, fw = match

        ops = list(pikepdf.parse_content_stream(page))
        operands, operator = ops[op_idx]
        op_str = str(operator)
        old_bytes = get_op_bytes(operands, op_str)
        decoded = winansi_decode(old_bytes)
        idx = decoded.index(target)
        target_bytes = winansi_encode(target)
        replacement_bytes = winansi_encode(replacement)

        original_width = measure_text_width(target_bytes, fw, font_size)
        replacement_width = measure_text_width(replacement_bytes, fw, font_size)
        delta_pct = (replacement_width - original_width) / original_width * 100.0

        prefix_bytes = old_bytes[:idx]
        suffix_bytes = old_bytes[idx + len(target_bytes) :]

        # Build the kerned TJ items for the replacement
        kerned_items = _build_kerned_tj_items(replacement_bytes, original_width, fw, font_size)

        # Construct new TJ array: [prefix_string, *kerned_items, suffix_string]
        new_array: list[object] = []
        if prefix_bytes:
            new_array.append(pikepdf.String(prefix_bytes))
        new_array.extend(kerned_items)
        if suffix_bytes:
            new_array.append(pikepdf.String(suffix_bytes))

        new_op = ([pikepdf.Array(new_array)], pikepdf.Operator("TJ"))

        new_ops = ops[:op_idx] + [new_op] + ops[op_idx + 1 :]
        new_stream = pikepdf.unparse_content_stream(new_ops)
        page.Contents = pdf.make_stream(new_stream)
        pdf.save(str(out_path))

        return {
            "original_width": original_width,
            "replacement_width": replacement_width,
            "delta_pct": delta_pct,
            "num_glyphs": len(replacement_bytes),
        }


def main() -> None:
    if len(sys.argv) != 5:
        print("usage: algo_b_uncapped_kerning.py <input.pdf> <target> <replacement> <output.pdf>")
        sys.exit(2)
    inp, tgt, repl, out = sys.argv[1:5]
    metrics = apply_algo_b(Path(inp), tgt, repl, Path(out))
    print(f"algo_b: delta={metrics['delta_pct']:+.1f}% -> {out}")


if __name__ == "__main__":
    main()
