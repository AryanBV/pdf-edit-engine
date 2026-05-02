"""INV-B-1: parse_content_stream(unparse_content_stream(ops)) is idempotent."""

from __future__ import annotations

from pathlib import Path

import pikepdf

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def test_inv_b_1_parse_unparse_idempotent() -> None:
    """parse_content_stream(unparse_content_stream(ops)) is idempotent (no modification).

    For every page of every existing corpus PDF: parse, unparse, parse again, assert the
    two parsed lists have the same length and the same operator names in the same order.
    """
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
                    ops1 = list(pikepdf.parse_content_stream(page))
                    raw = pikepdf.unparse_content_stream(ops1)
                    ops2 = list(pikepdf.parse_content_stream(pdf.make_stream(raw)))
                except Exception as exc:
                    failures.append(f"{path.name} p{page_num}: parse/unparse raised {exc!r}")
                    continue

                if len(ops1) != len(ops2):
                    failures.append(
                        f"{path.name} p{page_num}: len mismatch {len(ops1)} vs {len(ops2)}"
                    )
                    continue

                for i, (a, b) in enumerate(zip(ops1, ops2, strict=True)):
                    name_a = str(a.operator if hasattr(a, "operator") else a[1])
                    name_b = str(b.operator if hasattr(b, "operator") else b[1])
                    if name_a != name_b:
                        failures.append(
                            f"{path.name} p{page_num} op[{i}]: {name_a!r} != {name_b!r}"
                        )
                        break
                pages_checked += 1

    assert pages_checked > 0, "no pages parsed"
    assert not failures, "round-trip mismatches:\n  " + "\n  ".join(failures[:20])
