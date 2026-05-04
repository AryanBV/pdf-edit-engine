"""Verify text equivalence between Identity-H baseline and algo A outputs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent.parent / "src"))

from pdf_edit_engine import get_text  # noqa: E402

ORIGINAL = ROOT.parent.parent / "tests" / "corpus" / "cidfont_synthetic.pdf"
BASELINE = ROOT / "out_identity_h_baseline.pdf"
ALGO_A = ROOT / "out_identity_h_algo_a.pdf"

orig_text = get_text(str(ORIGINAL))
baseline_text = get_text(str(BASELINE))
algo_a_text = get_text(str(ALGO_A))

print("=== Original ===")
print(orig_text[:300])
print("\n=== Baseline ===")
print(baseline_text[:300])
print("\n=== Algo A ===")
print(algo_a_text[:300])
print(f"\n\noriginal == baseline ? {orig_text == baseline_text}")
print(f"baseline == algo_a ? {baseline_text == algo_a_text}")
print("\nDifference baseline -> algo_a (first chars):")
for i, (a, b) in enumerate(zip(baseline_text, algo_a_text)):
    if a != b:
        print(f"  pos {i}: baseline={a!r} algo_a={b!r}")
        if i > 100:
            break
