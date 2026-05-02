"""INV-C-2 (P1): Tier 1 (CMap-only) extension does not modify /FontFile2 stream bytes."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pikepdf
import pytest

from pdf_edit_engine.fonts import extend_subset

if TYPE_CHECKING:
    from pathlib import Path


def _find_type0(pdf: pikepdf.Pdf) -> tuple[pikepdf.Page, str] | None:
    for page in pdf.pages:
        fonts = page.get("/Resources", {}).get("/Font", {}) or {}
        for fname, font_obj in fonts.items():
            try:
                fd_obj = pikepdf.Dictionary(font_obj)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                continue
            if str(fd_obj.get("/Subtype")) != "/Type0":
                continue
            descs = fd_obj.get("/DescendantFonts")
            if descs is None:
                continue
            cid_font = pikepdf.Dictionary(descs[0])  # type: ignore[arg-type]
            fd = cid_font.get("/FontDescriptor")
            if fd is None or "/FontFile2" not in fd:
                continue
            return page, str(fname).lstrip("/")
    return None


def test_inv_c_2_tier1_keeps_fontfile2_byte_identical(
    cidfont_synthetic: Path,
    tmp_path: Path,
) -> None:
    """Tier 1 (CMap-only) must add /ToUnicode + /W + /CIDToGIDMap entries
    only — the embedded /FontFile2 stream bytes must be byte-identical
    before and after."""
    if not cidfont_synthetic.exists():
        pytest.skip("cidfont_synthetic.pdf missing")

    workfile = tmp_path / "in.pdf"
    workfile.write_bytes(cidfont_synthetic.read_bytes())
    pdf = pikepdf.open(str(workfile), allow_overwriting_input=True)
    found = _find_type0(pdf)
    if not found:
        pdf.close()
        pytest.skip("no Type0 TrueType font available")
    page, fname = found

    fd = pikepdf.Dictionary(
        pikepdf.Dictionary(page["/Resources"]["/Font"][f"/{fname}"])["/DescendantFonts"][0]
    )["/FontDescriptor"]
    pre_hash = hashlib.sha256(fd["/FontFile2"].read_bytes()).hexdigest()

    # Pick a char that is in the embedded font cmap but not yet mapped
    # in /ToUnicode. Easiest: re-add an existing mapping (extend_subset
    # is idempotent on Tier 1; the /FontFile2 must remain untouched).
    import io as _io

    from fontTools.ttLib import TTFont

    f = TTFont(_io.BytesIO(bytes(fd["/FontFile2"].read_bytes())))
    cmap = f.getBestCmap() or {}
    f.close()
    candidates = [chr(cp) for cp in cmap if cp < 0x7F]
    if not candidates:
        pdf.close()
        pytest.skip("no Tier-1 candidate char")
    target = candidates[0]

    tier = extend_subset(pdf, page, fname, target)
    assert tier == "cmap_only", f"expected Tier 1, got {tier!r}"

    out = tmp_path / "out.pdf"
    pdf.save(str(out))
    pdf.close()

    pdf2 = pikepdf.open(str(out))
    fd2 = pikepdf.Dictionary(
        pikepdf.Dictionary(pdf2.pages[0]["/Resources"]["/Font"][f"/{fname}"])["/DescendantFonts"][0]
    )["/FontDescriptor"]
    post_hash = hashlib.sha256(fd2["/FontFile2"].read_bytes()).hexdigest()
    pdf2.close()
    assert pre_hash == post_hash, (
        f"Tier 1 modified /FontFile2 stream bytes — pre={pre_hash[:16]}... post={post_hash[:16]}..."
    )
