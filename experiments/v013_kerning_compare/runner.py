"""Drive both kerning algorithms across three width deltas (15%, 25%, 40%)
plus the M10 verification (Sarah Chen → Søren Müller).

Output PDFs:
    out_a_d15.pdf, out_a_d25.pdf, out_a_d40.pdf
    out_b_d15.pdf, out_b_d25.pdf, out_b_d40.pdf
    out_a_m10.pdf, out_b_m10.pdf

The replacement strings were picked to land near the target deltas; the
runner prints the actual measured delta. Real names rather than "X" * N
because rendered shape (descenders, mixed caps) affects collision visibility.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent

# Make local helpers importable
sys.path.insert(0, str(ROOT))

from algo_a_tz_scaling import apply_algo_a  # noqa: E402
from algo_b_uncapped_kerning import apply_algo_b  # noqa: E402

INPUT = ROOT / "input.pdf"
TARGET = "Sarah Chen"

# Replacements ordered by approximate width delta — the runner prints actuals.
CASES = [
    ("d15", "Sarah Cohen"),  # +1 char, ~10-15% delta target
    ("d25", "Sarah Chenoa"),  # +2 chars, ~20-25%
    ("d40", "Sarah Chen Smith"),  # +6 chars, ~40-50%
    ("m10", "Søren Müller"),  # M10 demo case (gate a)
]


def main() -> None:
    print(f"Input: {INPUT}")
    print(f"Target: {TARGET!r}\n")
    print(f"{'case':<5} {'replacement':<25} {'algo':<2} {'delta':<8} {'tz':<8}")
    for label, replacement in CASES:
        for algo, fn in (("A", apply_algo_a), ("B", apply_algo_b)):
            out = ROOT / f"out_{algo.lower()}_{label}.pdf"
            try:
                m = fn(INPUT, TARGET, replacement, out)
                tz = f"{m.get('tz_factor', float('nan')):.1f}" if "tz_factor" in m else "—"
                print(f"{label:<5} {replacement:<25} {algo:<2} {m['delta_pct']:+.1f}%  {tz}")
            except Exception as e:  # noqa: BLE001
                print(f"{label:<5} {replacement:<25} {algo:<2} ERROR: {e}")


if __name__ == "__main__":
    main()
