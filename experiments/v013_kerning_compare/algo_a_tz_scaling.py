"""Algorithm A — Tz horizontal text-space scaling.

Replace `target` with `replacement` in the page's content stream. To make
the replacement fit the original target's rendered width, emit a `Tz X`
operator before the replacement TJ and `Tz 100` after, where
X = 100 * original_width / replacement_width.

Operates only on Tj or TJ operators whose bytes (WinAnsi) decode to
contain the entire target string in one operator. The SOW PDF satisfies
this for "Sarah Chen" — verified by inspect_sow.py.
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


def apply_algo_a(
    pdf_path: Path,
    target: str,
    replacement: str,
    out_path: Path,
) -> dict[str, float]:
    """Apply algo A. Returns metrics dict (original_width, replacement_width,
    tz_factor, etc.).
    """
    with pikepdf.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        match = find_text_in_content_stream(page, target)
        if match is None:
            raise RuntimeError(f"target {target!r} not found in page-0 content stream")
        op_idx, _n_ops, font_name, font_size, fw = match

        ops = list(pikepdf.parse_content_stream(page))
        operands, operator = ops[op_idx]
        op_str = str(operator)
        old_bytes = get_op_bytes(operands, op_str)
        decoded = winansi_decode(old_bytes)
        # Locate target inside decoded
        idx = decoded.index(target)
        target_bytes = winansi_encode(target)
        replacement_bytes = winansi_encode(replacement)

        # Original rendered width of target (in page units)
        original_width = measure_text_width(target_bytes, fw, font_size)
        # Replacement width WITH default Tz 100
        replacement_width = measure_text_width(replacement_bytes, fw, font_size)

        if replacement_width <= 0:
            raise RuntimeError("replacement width is zero")

        tz_factor = (original_width / replacement_width) * 100.0

        # Build the new bytes: prefix + replacement + suffix
        prefix_bytes = old_bytes[:idx]
        suffix_bytes = old_bytes[idx + len(target_bytes) :]

        # We re-emit the operator as a SINGLE Tj of the new full string with
        # surrounding Tz operators. Insert: Tz X / Tj <new_full> / Tz 100,
        # replacing the original op_idx.
        # Because Tz scales horizontal advance for SUBSEQUENT operators too,
        # we wrap only the matched-operator's run and restore Tz=100 after.

        # Approach: Tz X must apply only to the replacement. We emit:
        #   Tj <prefix>          # at Tz=100 (still in effect)
        #   Tz X
        #   Tj <replacement>
        #   Tz 100
        #   Tj <suffix>
        # If prefix or suffix is empty, omit those Tjs.

        new_ops: list[tuple] = list(ops[:op_idx])
        if prefix_bytes:
            new_ops.append(([pikepdf.String(prefix_bytes)], pikepdf.Operator("Tj")))
        new_ops.append(([tz_factor], pikepdf.Operator("Tz")))
        if replacement_bytes:
            new_ops.append(([pikepdf.String(replacement_bytes)], pikepdf.Operator("Tj")))
        new_ops.append(([100], pikepdf.Operator("Tz")))
        if suffix_bytes:
            new_ops.append(([pikepdf.String(suffix_bytes)], pikepdf.Operator("Tj")))
        new_ops.extend(ops[op_idx + 1 :])

        new_stream = pikepdf.unparse_content_stream(new_ops)
        page.Contents = pdf.make_stream(new_stream)
        pdf.save(str(out_path))

        return {
            "original_width": original_width,
            "replacement_width": replacement_width,
            "tz_factor": tz_factor,
            "delta_pct": (replacement_width - original_width) / original_width * 100.0,
        }


def main() -> None:
    if len(sys.argv) != 5:
        print("usage: algo_a_tz_scaling.py <input.pdf> <target> <replacement> <output.pdf>")
        sys.exit(2)
    inp, tgt, repl, out = sys.argv[1:5]
    metrics = apply_algo_a(Path(inp), tgt, repl, Path(out))
    print(f"algo_a: tz={metrics['tz_factor']:.2f} delta={metrics['delta_pct']:+.1f}% -> {out}")


if __name__ == "__main__":
    main()
