"""INV-B-4: _find_bt_et_blocks sees every text-showing op inside BT/ET."""

from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_edit_engine.reflow import _find_bt_et_blocks

CORPUS_DIR = Path(__file__).parent.parent / "corpus"
TEXT_OPS = {"Tj", "TJ", "'", '"'}


def test_inv_b_4_bt_et_no_missed_text() -> None:
    """All text-showing operators inside BT/ET are seen by _find_bt_et_blocks from reflow.py.

    Parse content stream, count Tj/TJ/'/" ops between BT and ET. Then call _find_bt_et_blocks
    and sum the count of text ops it returns. Counts must match.
    """
    path = CORPUS_DIR / "reportlab_simple.pdf"
    assert path.exists(), f"missing fixture {path}"

    expected_total = 0
    found_total = 0
    with pikepdf.open(str(path)) as pdf:
        for page in pdf.pages:
            ops = list(pikepdf.parse_content_stream(page))
            in_block = False
            for inst in ops:
                name = str(inst.operator if hasattr(inst, "operator") else inst[1])
                if name == "BT":
                    in_block = True
                elif name == "ET":
                    in_block = False
                elif in_block and name in TEXT_OPS:
                    expected_total += 1

            blocks = _find_bt_et_blocks(ops)
            for _bt, _et, text_indices in blocks:
                found_total += len(text_indices)

    assert expected_total > 0, "no text ops found in fixture"
    assert found_total == expected_total, (
        f"_find_bt_et_blocks missed text ops: expected {expected_total}, got {found_total}"
    )
