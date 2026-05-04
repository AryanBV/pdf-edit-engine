"""Step 0 — fixture existence check and table-structure detection.

For each fixture in tests/corpus/, determine:
  - Does its content stream contain rectangle drawing operators?
  - Does its text-element x-cluster modes exceed 1? (multi-column layout)
  - Does the existing _detect_paragraphs_from_index group elements that
    look like table cells (heuristic: cluster has multiple "rows" with
    each "row" containing < 30% of cluster width)?

Output: a CSV-like table to stdout + saved to fixture_survey.txt.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent
ENGINE_SRC = ROOT.parent.parent / "src"
sys.path.insert(0, str(ENGINE_SRC))

import pikepdf  # noqa: E402

from pdf_edit_engine import detect_paragraphs  # noqa: E402

CORPUS = ROOT.parent.parent / "tests" / "corpus"


def has_rect_drawing(pdf_path: Path) -> tuple[bool, int]:
    """Return (has_rect_ops, count_rect_ops)."""
    try:
        with pikepdf.open(str(pdf_path)) as pdf:
            count = 0
            for page in pdf.pages:
                ops = pikepdf.parse_content_stream(page)
                for _operands, op in ops:
                    if str(op) in ("re", "S", "s"):
                        count += 1
            return count > 0, count
    except Exception:  # noqa: BLE001
        return False, 0


def x_cluster_count(pdf_path: Path, page_idx: int = 0) -> int:
    """Approximate column count by clustering element x-starts."""
    try:
        paragraphs = detect_paragraphs(str(pdf_path), page=page_idx)
        if not paragraphs:
            return 0
        x_starts = [int(p.left_margin / 5.0) * 5 for p in paragraphs]
        return len(set(x_starts))
    except Exception:  # noqa: BLE001
        return 0


def main() -> None:
    fixtures = sorted(CORPUS.glob("*.pdf"))
    print(f"{'fixture':<45} {'rects':<7} {'paragraphs':<10} {'x_cols':<7}")
    print("-" * 75)
    out_lines = []
    out_lines.append(f"{'fixture':<45} {'rects':<7} {'paragraphs':<10} {'x_cols':<7}")
    out_lines.append("-" * 75)
    for f in fixtures:
        has_rect, n_rect = has_rect_drawing(f)
        try:
            paragraphs = detect_paragraphs(str(f), page=0)
            n_para = len(paragraphs)
        except Exception:  # noqa: BLE001
            n_para = -1
        n_cols = x_cluster_count(f)
        line = f"{f.name:<45} {n_rect:<7} {n_para:<10} {n_cols:<7}"
        print(line)
        out_lines.append(line)
    (ROOT / "fixture_survey.txt").write_text("\n".join(out_lines), encoding="utf-8")


if __name__ == "__main__":
    main()
