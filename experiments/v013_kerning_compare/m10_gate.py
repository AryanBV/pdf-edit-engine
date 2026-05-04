"""M10 verification gate (a) — Sarah Chen → Søren Müller.

Both algos require font extension (the embedded Calibri subset lacks
glyphs for ø, ü). We use the v0.1.2 engine's `replace()` (which performs
Tier 1 / Tier 1.5 font extension automatically) and monkey-patch the
engine's `_encode_with_kerning` to test each algorithm.

Outputs:
    out_engine_baseline_m10.pdf — engine as-is (v0.1.2 with > 0.5× cap)
    out_a_m10_with_extend.pdf — algo A (Tz scaling) post-extension
    out_b_m10_with_extend.pdf — algo B (uncapped) post-extension

Engine import is read-only — we do not modify any production source.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent
ENGINE_SRC = ROOT.parent.parent / "src"
sys.path.insert(0, str(ENGINE_SRC))
sys.path.insert(0, str(ROOT))

# After sys.path setup, import engine + experiment helpers.
import pikepdf  # noqa: E402

from pdf_edit_engine import find, replace  # noqa: E402
from pdf_edit_engine import surgeon as engine_surgeon  # noqa: E402

INPUT = ROOT / "input.pdf"
TARGET = "Sarah Chen"
REPLACEMENT = "Søren Müller"


def _baseline() -> Path:
    """Run the engine as-is (v0.1.2 kerning math, > 0.5× cap)."""
    out = ROOT / "out_engine_baseline_m10.pdf"
    matches = find(str(INPUT), TARGET)
    if not matches:
        raise RuntimeError(f"target {TARGET!r} not found")
    result = replace(str(INPUT), matches[0], REPLACEMENT, str(out), reflow=False)
    print(
        f"baseline: success={result.success} font_action={result.font_action} "
        f"warnings={result.warnings}"
    )
    return out


def _algo_a() -> Path:
    """Monkey-patch `_encode_with_kerning` to a flat-string emit and post-process
    the output to wrap the replacement TJ with `Tz X` / `Tz 100`.

    Approach: patch the kerning function to return flat (no kerning), let the
    engine handle font extension and content-stream surgery, then post-process
    the saved PDF to insert Tz operators around the replacement Tj/TJ.
    """
    # Step 1: run engine with flat-kerning (proportional cap effectively
    # forced by returning flat at any delta).
    intermediate = ROOT / "_a_m10_intermediate.pdf"

    original_fn = engine_surgeon._encode_with_kerning  # type: ignore[attr-defined]

    def flat_kerning(text, original_width_page, font_size, resolver, width_cache, page, font_name):
        # Flat string at default Tz; the post-processor adjusts width.
        bw = resolver.byte_width
        glyph_items = []
        full_encoded = resolver.encode(text)
        for i in range(0, len(full_encoded), bw):
            glyph_items.append(full_encoded[i : i + bw])
        return [pikepdf.String(b"".join(glyph_items))] if glyph_items else []

    engine_surgeon._encode_with_kerning = flat_kerning  # type: ignore[attr-defined]
    try:
        matches = find(str(INPUT), TARGET)
        if not matches:
            raise RuntimeError(f"target {TARGET!r} not found")
        result = replace(str(INPUT), matches[0], REPLACEMENT, str(intermediate), reflow=False)
        print(f"algo A intermediate: success={result.success} font_action={result.font_action}")
    finally:
        engine_surgeon._encode_with_kerning = original_fn  # type: ignore[attr-defined]

    # Step 2: post-process — wrap the Søren Müller Tj/TJ with Tz X / Tz 100.
    # Compute Tz factor from the original Sarah Chen width vs the new width
    # using the same WinAnsi /Widths that were embedded BEFORE font extension.
    from _common import (
        find_text_in_content_stream,
        load_page_fonts,
        measure_text_width,
        winansi_encode,
    )

    # Read the post-extension PDF, find the Søren Müller TJ.
    out = ROOT / "out_a_m10_with_extend.pdf"
    with pikepdf.open(str(intermediate)) as pdf:
        page = pdf.pages[0]
        page_fonts = load_page_fonts(page)
        # Find the Søren Müller op (post-extension, the bytes are the
        # WinAnsi-encoded ø/ü which now have glyphs).
        match = find_text_in_content_stream(page, REPLACEMENT)
        if match is None:
            # Try just the ASCII portion in case decoding differs
            match = find_text_in_content_stream(page, "ren M")
        if match is None:
            print("  WARNING: post-extension TJ not found by find; falling back")
            pdf.save(str(out))
            return out

        op_idx, _, font_name, font_size, fw = match

        # Measure original Sarah Chen width using the font's pre-extension widths
        # (since the engine extends but doesn't change pre-existing widths).
        sarah_bytes = winansi_encode(TARGET)
        soren_bytes = winansi_encode(REPLACEMENT)
        original_width = measure_text_width(sarah_bytes, fw, font_size)
        new_width = measure_text_width(soren_bytes, fw, font_size)
        if new_width <= 0:
            print("  WARNING: new_width <= 0; saving intermediate")
            pdf.save(str(out))
            return out

        tz_factor = (original_width / new_width) * 100.0

        ops = list(pikepdf.parse_content_stream(page))

        # Insert Tz X before the matched op, Tz 100 after.
        new_ops = (
            ops[:op_idx]
            + [([tz_factor], pikepdf.Operator("Tz"))]
            + [ops[op_idx]]
            + [([100], pikepdf.Operator("Tz"))]
            + ops[op_idx + 1 :]
        )

        new_stream = pikepdf.unparse_content_stream(new_ops)
        page.Contents = pdf.make_stream(new_stream)
        pdf.save(str(out))
        print(f"algo A: tz={tz_factor:.1f}% original_w={original_width:.1f} new_w={new_width:.1f}")

    return out


def _algo_b() -> Path:
    """Use engine as-is BUT monkey-patch to remove the > 0.5× cap (algo B)."""
    out = ROOT / "out_b_m10_with_extend.pdf"

    original_fn = engine_surgeon._encode_with_kerning  # type: ignore[attr-defined]

    def uncapped_kerning(
        text, original_width_page, font_size, resolver, width_cache, page, font_name
    ):
        """Replicates v0.1.2 logic minus the 0.5x cap — algo B."""
        if not text:
            return []
        bw = resolver.byte_width
        glyph_items: list = []
        full_encoded = resolver.encode(text)
        for i in range(0, len(full_encoded), bw):
            gb = full_encoded[i : i + bw]
            char_code = (gb[0] << 8) | gb[1] if bw == 2 else gb[0]
            w = width_cache.get_width(page, font_name, char_code)
            glyph_items.append((gb, w))
        if len(glyph_items) <= 1:
            return [pikepdf.String(glyph_items[0][0])] if glyph_items else []
        if original_width_page <= 0 or font_size <= 0:
            flat = b"".join(enc for enc, _ in glyph_items)
            return [pikepdf.String(flat)]
        original_fu = original_width_page * 1000.0 / font_size
        replacement_fu = sum(w for _, w in glyph_items)
        total_kern = replacement_fu - original_fu
        num_gaps = len(glyph_items) - 1

        # ── ALGO B: NO cap. ──
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
        result: list = []
        for i, (encoded, _) in enumerate(glyph_items):
            result.append(pikepdf.String(encoded))
            if i < num_gaps and kern_values[i] != 0:
                result.append(kern_values[i])
        return result

    engine_surgeon._encode_with_kerning = uncapped_kerning  # type: ignore[attr-defined]
    try:
        matches = find(str(INPUT), TARGET)
        if not matches:
            raise RuntimeError(f"target {TARGET!r} not found")
        result = replace(str(INPUT), matches[0], REPLACEMENT, str(out), reflow=False)
        print(f"algo B: success={result.success} font_action={result.font_action}")
    finally:
        engine_surgeon._encode_with_kerning = original_fn  # type: ignore[attr-defined]

    return out


def main() -> None:
    print("=== M10 verification gate (a) ===\n")
    _baseline()
    _algo_a()
    _algo_b()


if __name__ == "__main__":
    main()
