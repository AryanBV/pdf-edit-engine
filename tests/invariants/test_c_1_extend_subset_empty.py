"""INV-C-1: extend_subset with empty additional_chars is a no-op."""

from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_edit_engine import extend_subset

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def test_inv_c_1_extend_subset_empty_is_noop() -> None:
    """extend_subset(pdf, page, font_name, additional_chars="") returns "cmap_only" without
    modifying the embedded /FontFile2 stream bytes.
    """
    pdf_path = CORPUS_DIR / "cidfont_synthetic.pdf"
    assert pdf_path.exists(), f"missing fixture {pdf_path}"

    pdf = pikepdf.open(str(pdf_path))
    try:
        page = pdf.pages[0]
        fonts_dict = page["/Resources"]["/Font"]
        # Pick the first Type0 font we can find
        target = None
        for fname in list(fonts_dict.keys()):
            f = fonts_dict[fname]
            if str(f.get("/Subtype")) == "/Type0":
                target = str(fname).lstrip("/")
                break
        assert target is not None, "no Type0 font found in corpus PDF"

        # Capture the embedded font bytes BEFORE the call
        cid_font = fonts_dict[f"/{target}"]["/DescendantFonts"][0]
        fd = cid_font["/FontDescriptor"]
        before_bytes = bytes(fd["/FontFile2"].read_bytes())

        result = extend_subset(pdf, page, target, "")
        assert result == "cmap_only", f"empty additional_chars should be a no-op, got {result!r}"

        after_bytes = bytes(fd["/FontFile2"].read_bytes())
        assert before_bytes == after_bytes, "FontFile2 bytes mutated for empty additional_chars"
    finally:
        pdf.close()
