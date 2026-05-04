"""Step 1-3 — inspect table-cell content streams across multiple generators.

For each fixture: print operator distribution, marked-content tags, and
the first 80 paragraph candidates from _detect_paragraphs_from_index
along with their text + bbox. The output goes into the design doc.
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

# Fixtures to inspect (cross-generator)
FIXTURES = [
    ("SOW (Word, WinAnsi)", ROOT.parent / "v013_kerning_compare" / "input.pdf"),
    ("reportlab_table.pdf", ROOT.parent.parent / "tests" / "corpus" / "reportlab_table.pdf"),
    ("chrome_webpage.pdf", ROOT.parent.parent / "tests" / "corpus" / "chrome_webpage.pdf"),
    (
        "Aryan_BV_Resume_2026.pdf",
        ROOT.parent.parent / "tests" / "corpus" / "Aryan_BV_Resume_2026.pdf",
    ),
]


def inspect(label: str, pdf_path: Path) -> dict:
    print(f"\n=== {label}: {pdf_path.name} ===")
    if not pdf_path.exists():
        print("  MISSING")
        return {}
    with pikepdf.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        page_w = float(page.MediaBox[2])
        page_h = float(page.MediaBox[3])
        print(f"  MediaBox: {page_w:.0f} x {page_h:.0f}")
        ops = list(pikepdf.parse_content_stream(page))
        op_counter: Counter[str] = Counter()
        bmc_tags: list[str] = []
        rect_count = 0
        for operands, op in ops:
            os_ = str(op)
            op_counter[os_] += 1
            if os_ in ("BMC", "BDC"):
                if operands:
                    bmc_tags.append(str(operands[0]))
            if os_ == "re":
                rect_count += 1
        print(f"  ops: total={len(ops)}")
        print(
            f"    drawing: re={op_counter['re']} f={op_counter['f']} "
            f"S={op_counter['S']} s={op_counter['s']} B={op_counter['B']}"
        )
        print(f"    text: BT={op_counter['BT']} ET={op_counter['ET']}")
        print(
            f"    text-show: Tj={op_counter['Tj']} TJ={op_counter['TJ']} "
            f"Tf={op_counter['Tf']} Tm={op_counter['Tm']} Td={op_counter['Td']}"
        )
        print(f"    marked: BMC={op_counter['BMC']} BDC={op_counter['BDC']}")
        if bmc_tags:
            tag_counter = Counter(bmc_tags[:50])
            print(f"    BMC/BDC tags (first 50): {dict(tag_counter)}")

    paragraphs = detect_paragraphs(str(pdf_path), page=0)
    print(f"  paragraphs detected: {len(paragraphs)}")
    for i, p in enumerate(paragraphs[:30]):
        text_oneline = p.full_text.replace("\n", " ")[:80]
        print(
            f"    [{i:>2}] x={p.left_margin:>5.1f} y={p.first_line_y:>6.1f} "
            f"w={p.paragraph_width:>5.1f} lines={p.line_count} font={p.font_name[:20]:<20} "
            f"text={text_oneline!r}"
        )
    return {
        "ops": dict(op_counter),
        "rect_count": rect_count,
        "page_w": page_w,
        "paragraphs": paragraphs,
    }


def main() -> None:
    for label, fix in FIXTURES:
        inspect(label, fix)


if __name__ == "__main__":
    main()
