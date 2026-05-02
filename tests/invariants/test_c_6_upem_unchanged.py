"""INV-C-6: After Tier 1.5 extension, the embedded font's unitsPerEm is unchanged."""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
import pytest
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

from pdf_edit_engine import extend_subset
from pdf_edit_engine.errors import FontNotFoundError

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def _read_upem_from_fontfile2(fd: pikepdf.Object) -> int:
    raw = bytes(fd["/FontFile2"].read_bytes())
    f = TTFont(io.BytesIO(raw))
    try:
        return int(f["head"].unitsPerEm)
    finally:
        f.close()


def test_inv_c_6_upem_unchanged_after_tier15() -> None:
    """After extend_subset triggers Tier 1.5 (in-place glyph injection), the embedded
    font's head.unitsPerEm is unchanged.
    """
    pdf_path = CORPUS_DIR / "Aryan_BV_Resume_2026.pdf"
    if not pdf_path.exists():
        pytest.skip(f"missing fixture {pdf_path}")

    pdf = pikepdf.open(str(pdf_path))
    try:
        page = pdf.pages[0]
        fonts_dict = page["/Resources"]["/Font"]
        # Find the first Type0 font with a non-Latin gap we can fill via Tier 1.5
        for fname in list(fonts_dict.keys()):
            f = fonts_dict[fname]
            if str(f.get("/Subtype")) != "/Type0":
                continue
            target = str(fname).lstrip("/")
            cid_font = f["/DescendantFonts"][0]
            fd = cid_font["/FontDescriptor"]
            if "/FontFile2" not in fd:
                continue
            upem_before = _read_upem_from_fontfile2(fd)

            # Try a non-Latin char Tier 1.5 needs to inject. Cycle a few candidates.
            for ch in ("Ω", "←", "★", "中", "é"):
                try:
                    raw_before = bytes(fd["/FontFile2"].read_bytes())
                    extend_subset(pdf, page, target, ch)
                    raw_after = bytes(fd["/FontFile2"].read_bytes())
                    if raw_before == raw_after:
                        # Tier-1 path (already in cmap) — try the next candidate
                        continue
                    upem_after = _read_upem_from_fontfile2(fd)
                    assert upem_after == upem_before, (
                        f"Tier 1.5 changed unitsPerEm: before={upem_before} after={upem_after}"
                    )
                    return  # success: we exercised Tier 1.5 on this font
                except FontNotFoundError:
                    # System font for this PostScript name not available — keep trying
                    continue
        pytest.skip("Could not trigger Tier 1.5 on any embedded font (system fonts unavailable)")
    finally:
        pdf.close()
