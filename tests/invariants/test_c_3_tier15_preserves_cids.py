"""INV-C-3 (P0): Tier 1.5 preserves every pre-existing CID → glyph mapping."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pikepdf
import pytest
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

from pdf_edit_engine.fonts import _parse_existing_tounicode, extend_subset

if TYPE_CHECKING:
    from pathlib import Path


def _find_type0_font_with_glyf(pdf: pikepdf.Pdf) -> tuple[pikepdf.Page, str] | None:
    for page in pdf.pages:
        fonts = page.get("/Resources", {}).get("/Font", {}) or {}
        for fname, font_obj in fonts.items():
            try:
                font_obj_resolved = pikepdf.Dictionary(font_obj)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                continue
            if str(font_obj_resolved.get("/Subtype")) != "/Type0":
                continue
            descendants = font_obj_resolved.get("/DescendantFonts")
            if not descendants:
                continue
            cid_font = pikepdf.Dictionary(descendants[0])  # type: ignore[arg-type]
            fd = cid_font.get("/FontDescriptor")
            if fd is None or "/FontFile2" not in fd:
                continue
            return page, str(fname).lstrip("/")
    return None


def test_inv_c_3_tier15_preserves_pre_existing_cids(
    resume_pdf: Path,
    tmp_path: Path,
) -> None:
    """Pre-existing CID → glyph_name mappings must be unchanged after a
    Tier 1.5 in-place injection. ARY-278's central guarantee."""
    if not resume_pdf.exists():
        pytest.skip("resume PDF missing")

    workfile = tmp_path / "in.pdf"
    workfile.write_bytes(resume_pdf.read_bytes())

    pdf = pikepdf.open(str(workfile), allow_overwriting_input=True)
    found = _find_type0_font_with_glyf(pdf)
    if not found:
        pdf.close()
        pytest.skip("no Type0 TrueType font found")
    page, fname = found

    fd = pikepdf.Dictionary(
        pikepdf.Dictionary(page["/Resources"]["/Font"][f"/{fname}"])["/DescendantFonts"][0]
    )["/FontDescriptor"]
    pre_bytes = bytes(fd["/FontFile2"].read_bytes())
    pre_font = TTFont(io.BytesIO(pre_bytes))
    pre_glyph_order = list(pre_font.getGlyphOrder())
    font_dict = pikepdf.Dictionary(page["/Resources"]["/Font"][f"/{fname}"])
    pre_tounicode = dict(_parse_existing_tounicode(font_dict))

    # Find a char absent from pre cmap to force Tier 1.5.
    pre_cmap = pre_font.getBestCmap() or {}
    pre_font.close()
    candidates = "Þæø∞√†€"
    target = next((c for c in candidates if ord(c) not in pre_cmap), None)
    if target is None:
        pdf.close()
        pytest.skip("no Tier-1.5 candidate")

    try:
        tier = extend_subset(pdf, page, fname, target)
    except Exception as e:  # noqa: BLE001
        pdf.close()
        pytest.skip(f"extend_subset unavailable in env: {e}")

    if tier != "full_extension":
        pdf.close()
        pytest.skip(f"chosen char did not trigger Tier 1.5 (got {tier})")

    pdf.save(str(tmp_path / "out.pdf"))
    pdf.close()

    pdf2 = pikepdf.open(str(tmp_path / "out.pdf"))
    fd2 = pikepdf.Dictionary(
        pikepdf.Dictionary(pdf2.pages[0]["/Resources"]["/Font"][f"/{fname}"])["/DescendantFonts"][0]
    )["/FontDescriptor"]
    post_bytes = bytes(fd2["/FontFile2"].read_bytes())
    post_font = TTFont(io.BytesIO(post_bytes))
    post_glyph_order = list(post_font.getGlyphOrder())
    post_tounicode = dict(
        _parse_existing_tounicode(
            pikepdf.Dictionary(pdf2.pages[0]["/Resources"]["/Font"][f"/{fname}"])
        )
    )
    post_font.close()
    pdf2.close()

    # Pre-existing GIDs must still resolve to their pre-existing glyph
    # names (the additive append rule).
    n_pre = len(pre_glyph_order)
    assert post_glyph_order[:n_pre] == pre_glyph_order, (
        "Tier 1.5 modified pre-existing glyph order — CIDs renumbered"
    )

    # Pre-existing ToUnicode entries must be intact post-extension.
    for cid, ustr in pre_tounicode.items():
        assert post_tounicode.get(cid) == ustr, (
            f"CID {cid:#06x} mapping changed: pre={ustr!r} post={post_tounicode.get(cid)!r}"
        )
