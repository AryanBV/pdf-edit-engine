"""INV-E-5 (P0): cross-font replace_all does not contaminate CIDs.

Regression guard for ARY-276/278 (Mode 2 cross-font CID pollution).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine import find, get_text, replace_all

if TYPE_CHECKING:
    from pathlib import Path


def _has_multiple_identity_h_fonts(pdf_path: Path) -> bool:
    import pikepdf

    with pikepdf.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            type0_fonts = 0
            fonts = page.get("/Resources", {}).get("/Font", {}) or {}
            for _, font_obj in fonts.items():
                try:
                    if str(pikepdf.Dictionary(font_obj).get("/Subtype")) == "/Type0":  # type: ignore[arg-type]
                        type0_fonts += 1
                except Exception:  # noqa: BLE001
                    continue
            if type0_fonts >= 2:
                return True
    return False


def test_inv_e_5_cross_font_replace_all_no_pollution(
    resume_pdf: Path,
    chrome_webpage: Path,
    tmp_path: Path,
) -> None:
    """On a multi-font Identity-H page, replace_all on a string from
    one font must not corrupt unrelated text from a different font."""
    pdf_path: Path | None = None
    for candidate in (resume_pdf, chrome_webpage):
        if candidate.exists() and _has_multiple_identity_h_fonts(candidate):
            pdf_path = candidate
            break
    if pdf_path is None:
        pytest.skip("no multi-Identity-H corpus PDF available")

    # Pick a query and a sentinel string from a likely different font.
    matches = find(str(pdf_path), "and")
    if not matches:
        pytest.skip("'and' not found")

    fonts_used = {m.font_info.name for m in matches[:3]}
    # Capture some non-target text on the same page that should be
    # unchanged by the replacement.
    pre_text = get_text(str(pdf_path))
    untouched_samples = [
        line.strip() for line in pre_text.split("\n") if line.strip() and "and" not in line
    ][:5]

    out = tmp_path / "out.pdf"
    replace_all(str(pdf_path), "and", "but", str(out))
    post_text = get_text(str(out))

    # Each untouched line must still be present, intact.
    missing = [
        s
        for s in untouched_samples
        if s not in post_text and s.replace("and", "but") not in post_text
    ]
    # Allow whitespace shifts but no garbling.
    assert not missing, (
        f"cross-font corruption suspected: {len(missing)} untouched lines "
        f"became unrecognizable after replace_all. Sample: {missing[:2]} "
        f"(fonts in matches: {fonts_used})"
    )
