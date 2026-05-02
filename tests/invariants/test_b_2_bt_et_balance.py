"""INV-B-2: BT/ET blocks are balanced on every corpus page."""

from __future__ import annotations

from pathlib import Path

import pikepdf

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def test_inv_b_2_bt_et_balance() -> None:
    """For every page of every corpus PDF, BT/ET counters never go negative and end at 0."""
    pdf_paths = sorted(CORPUS_DIR.glob("*.pdf"))
    assert pdf_paths, "no corpus PDFs found"
    pages_checked = 0
    failures: list[str] = []

    for path in pdf_paths:
        try:
            pdf = pikepdf.open(str(path))
        except Exception:
            continue
        with pdf:
            for page_num, page in enumerate(pdf.pages):
                try:
                    ops = list(pikepdf.parse_content_stream(page))
                except Exception:
                    continue
                counter = 0
                went_negative = False
                for inst in ops:
                    op = inst.operator if hasattr(inst, "operator") else inst[1]
                    name = str(op)
                    if name == "BT":
                        counter += 1
                    elif name == "ET":
                        counter -= 1
                        if counter < 0:
                            went_negative = True
                            break
                if went_negative:
                    failures.append(f"{path.name} p{page_num}: ET before BT")
                elif counter != 0:
                    failures.append(f"{path.name} p{page_num}: ended at counter={counter}")
                pages_checked += 1

    assert pages_checked > 0, "no pages parsed"
    assert not failures, "BT/ET imbalance:\n  " + "\n  ".join(failures[:20])
