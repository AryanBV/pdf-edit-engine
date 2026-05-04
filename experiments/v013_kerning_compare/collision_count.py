"""Quantitative metric: count glyph-bbox-overlaps in rendered output.

For each output PDF, use pdfminer.six's character-bbox extraction to find
adjacent characters whose horizontal bounding boxes overlap. Overlap
indicates kerning compression has packed glyphs into each other.

Reports the count of overlapping adjacent-character pairs in the
"Project lead: ..." line for each output.
"""

from __future__ import annotations

from pathlib import Path

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTChar, LTTextContainer, LTTextLine

ROOT = Path(__file__).parent


def _iter_chars_in_band(pdf_path: Path, y_top: float, y_bottom: float):
    """Yield LTChar objects whose y center falls within (y_bottom, y_top)."""
    laparams = LAParams(char_margin=0.5, word_margin=0.1, line_margin=0.5)
    for page_layout in extract_pages(str(pdf_path), laparams=laparams):
        for element in page_layout:
            if not isinstance(element, LTTextContainer):
                continue
            for line in element:
                if not isinstance(line, LTTextLine):
                    continue
                for ch in line:
                    if not isinstance(ch, LTChar):
                        continue
                    cy = (ch.y0 + ch.y1) / 2
                    if y_bottom < cy < y_top:
                        yield ch


def count_overlaps(pdf_path: Path, y_top: float = 245, y_bottom: float = 200) -> int:
    """Count adjacent-char overlap pairs in the band where Sarah Chen sits."""
    chars = sorted(_iter_chars_in_band(pdf_path, y_top, y_bottom), key=lambda c: c.x0)
    overlaps = 0
    for a, b in zip(chars, chars[1:]):
        # Overlap if next-char x0 is left of prev-char x1 (and they're on
        # the same line, which the band filter approximates)
        if b.x0 < a.x1 - 0.5:  # 0.5pt slack for sub-pixel
            overlaps += 1
    return overlaps


def main() -> None:
    deltas = ["d15", "d25", "d40"]
    print(f"{'case':<6} {'algo':<6} {'overlaps':<10} replacement_rendered")
    for d in deltas:
        for algo in ("a", "b"):
            pdf = ROOT / f"out_{algo}_{d}.pdf"
            if not pdf.exists():
                continue
            n = count_overlaps(pdf)
            print(f"{d:<6} {algo.upper():<6} {n:<10}")
    print()
    # M10 cases
    m10_specs = [
        ("out_engine_baseline_m10.pdf", "baseline"),
        ("out_a_m10_with_extend.pdf", "algo A"),
        ("out_b_m10_with_extend.pdf", "algo B"),
    ]
    for fname, label in m10_specs:
        pdf = ROOT / fname
        if not pdf.exists():
            continue
        n = count_overlaps(pdf)
        print(f"M10    {label:<10} overlaps={n}")


if __name__ == "__main__":
    main()
