"""For each paragraph in each fixture, compute candidate detector signals
and output a markdown table. Manual labels go in labels.md afterwards.

Signals:
  S1 = paragraph_width / page_width  (C1: width-based, threshold candidate 0.6)
  S2 = avg row stub-coverage = avg over lines of (sum of element-line-widths
       / paragraph_width)  — low value (<50%) means lines are stubs (table)
  S3 = number of distinct x-clusters of element x-starts within paragraph
       (C3: column-count signal)
  S4 = combined (S1 > 0.6 AND S2 < 0.5)

Each row is one paragraph from one fixture.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
ENGINE_SRC = ROOT.parent.parent / "src"
sys.path.insert(0, str(ENGINE_SRC))

import pikepdf  # noqa: E402

from pdf_edit_engine import detect_paragraphs  # noqa: E402

FIXTURES = [
    ("SOW", ROOT.parent / "v013_kerning_compare" / "input.pdf"),
    ("reportlab", ROOT.parent.parent / "tests" / "corpus" / "reportlab_table.pdf"),
    ("chrome", ROOT.parent.parent / "tests" / "corpus" / "chrome_webpage.pdf"),
    ("resume", ROOT.parent.parent / "tests" / "corpus" / "Aryan_BV_Resume_2026.pdf"),
    ("gdocs", ROOT.parent.parent / "tests" / "corpus" / "gdocs_document.pdf"),
]


def page_width(pdf_path: Path) -> float:
    with pikepdf.open(str(pdf_path)) as pdf:
        return float(pdf.pages[0].MediaBox[2])


def x_cluster_count(xs: list[float], tol: float = 8.0) -> int:
    """Count distinct clusters of x values, tol-pt apart."""
    if not xs:
        return 0
    s = sorted(xs)
    clusters = 1
    last = s[0]
    for x in s[1:]:
        if x - last > tol:
            clusters += 1
        last = x
    return clusters


def signals_for_paragraph(p, page_w: float) -> dict[str, float | int]:
    elems = p.elements
    s1 = p.paragraph_width / page_w if page_w > 0 else 0.0

    # Group elements by line (y bucket)
    lines: dict[int, list] = {}
    for e in elems:
        y_bucket = round(e.bbox[1] / 4) * 4  # 4-pt bucket for line clustering
        lines.setdefault(y_bucket, []).append(e)

    # S2: avg row stub-coverage = mean(sum(elem_width per line) / paragraph_width)
    coverages: list[float] = []
    for line_elems in lines.values():
        # Sum of element widths in this line (approximating "ink" not gaps)
        total_width = sum(e.bbox[2] - e.bbox[0] for e in line_elems)
        if p.paragraph_width > 0:
            coverages.append(total_width / p.paragraph_width)
    s2 = sum(coverages) / len(coverages) if coverages else 0.0

    # S3: x-cluster count over element x-starts
    xs = [e.bbox[0] for e in elems]
    s3 = x_cluster_count(xs)

    s4 = 1 if (s1 > 0.6 and s2 < 0.5) else 0
    return {
        "S1_w_ratio": s1,
        "S2_stub_cov": s2,
        "S3_x_clusters": s3,
        "S4_combined": s4,
        "n_lines": p.line_count,
        "n_elements": len(elems),
    }


def main() -> None:
    rows = []
    for fix_label, fix_path in FIXTURES:
        if not fix_path.exists():
            print(f"  skip {fix_label}: missing", file=sys.stderr)
            continue
        page_w = page_width(fix_path)
        try:
            paragraphs = detect_paragraphs(str(fix_path), page=0)
        except Exception as e:  # noqa: BLE001
            print(f"  skip {fix_label}: {e}", file=sys.stderr)
            continue
        for i, p in enumerate(paragraphs):
            sig = signals_for_paragraph(p, page_w)
            text = p.full_text.replace("\n", " ").replace("|", "\\|")[:60]
            rows.append(
                {
                    "fixture": fix_label,
                    "id": i,
                    **sig,
                    "n_chars": len(p.full_text),
                    "text": text,
                }
            )

    # Print markdown table
    out_lines = [
        "| fixture | id | S1_w | S2_cov | S3_x | S4 | lines | elems | chars | text |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        out_lines.append(
            f"| {r['fixture']} | {r['id']} | {r['S1_w_ratio']:.2f} | "
            f"{r['S2_stub_cov']:.2f} | {r['S3_x_clusters']} | {r['S4_combined']} | "
            f"{r['n_lines']} | {r['n_elements']} | {r['n_chars']} | {r['text']} |"
        )

    out = "\n".join(out_lines) + "\n"
    (ROOT / "signals_table.md").write_text(out, encoding="utf-8")
    print(out)
    print(f"\nWrote {ROOT / 'signals_table.md'}")
    print(f"Total rows: {len(rows)}")


if __name__ == "__main__":
    main()
