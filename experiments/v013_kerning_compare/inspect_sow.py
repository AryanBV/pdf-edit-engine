"""Inspect the SOW PDF: find Sarah Chen, list pages, fonts, and page-by-page text.

Run after `pip install pikepdf pdfminer.six` in the experiment venv.
Prints to stdout; no side effects.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
from pdfminer.high_level import extract_text

ROOT = Path(__file__).parent
INPUT = ROOT / "input.pdf"


def main() -> None:
    print(f"=== SOW: {INPUT} ({INPUT.stat().st_size} bytes) ===\n")

    with pikepdf.open(str(INPUT)) as pdf:
        print(f"Pages: {len(pdf.pages)}")
        for i, page in enumerate(pdf.pages):
            mb = page.MediaBox
            print(f"  page {i}: MediaBox={list(mb)}")
            fonts = page.get("/Resources", {}).get("/Font", {}) or {}
            for name, font in fonts.items():
                d = pikepdf.Dictionary(font)
                bf = str(d.get("/BaseFont", "?"))
                subtype = str(d.get("/Subtype", "?"))
                enc = str(d.get("/Encoding", "?"))
                print(f"    font {name}: {bf} ({subtype}, enc={enc[:60]})")

    print("\n=== Full text per page ===\n")
    full = extract_text(str(INPUT))
    for i, page_text in enumerate(full.split("\f")):
        if not page_text.strip():
            continue
        print(f"--- page {i} ---")
        print(page_text[:2000])
        print()

    print("\n=== 'Sarah Chen' search ===")
    if "Sarah Chen" in full:
        idx = full.index("Sarah Chen")
        ctx = full[max(0, idx - 80) : idx + 80]
        print(f"  FOUND. Context: ...{ctx}...")
    else:
        print("  NOT in extracted text — may be in a position pdfminer can't decode")
        print("  Trying case-insensitive substring matches:")
        lower = full.lower()
        for q in ("sarah", "chen", "sarah chen", "client signature", "consultant signature"):
            if q in lower:
                idx = lower.index(q)
                print(f"    {q!r} @ {idx}: ...{full[max(0, idx - 60) : idx + 60]}...")


if __name__ == "__main__":
    main()
