"""INV-W0-5: _inject_glyph_in_place raises FontNotFoundError when embedded lacks glyf."""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
import pytest
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

from pdf_edit_engine.errors import FontNotFoundError
from pdf_edit_engine.fonts import _extract_font_bytes, _inject_glyph_in_place

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def _load_truetype() -> TTFont:
    for pdf_path in CORPUS_DIR.glob("*.pdf"):
        try:
            pdf = pikepdf.open(str(pdf_path))
        except Exception:  # noqa: BLE001
            continue
        with pdf:
            for page in pdf.pages:
                resources = page.get("/Resources")
                if resources is None:
                    continue
                fonts = resources.get("/Font") or {}
                for fname in list(fonts.keys()):
                    f = fonts[fname]
                    if str(f.get("/Subtype")) != "/Type0":
                        continue
                    desc_fonts = f.get("/DescendantFonts")
                    if desc_fonts is None or len(list(desc_fonts)) == 0:
                        continue
                    fd = desc_fonts[0].get("/FontDescriptor")
                    if fd is None or "/FontFile2" not in fd:
                        continue
                    try:
                        raw, _ = _extract_font_bytes(fd)
                        return TTFont(io.BytesIO(raw))
                    except Exception:  # noqa: BLE001
                        continue
    pytest.skip("no TrueType embedded subset found in corpus")


def test_inv_w0_5_inject_no_glyf_raises() -> None:
    """_inject_glyph_in_place(embedded, system, ch) raises FontNotFoundError when the
    embedded font lacks the 'glyf' table (CFF / OpenType case).
    """
    system = _load_truetype()
    embedded = _load_truetype()

    try:
        # Simulate a CFF / non-TrueType embedded font by removing the glyf table.
        # The function's first guard checks 'glyf' in embedded — this is the W0-5 path.
        if "glyf" in embedded:
            del embedded["glyf"]

        with pytest.raises(FontNotFoundError, match="glyf|TrueType|CFF"):
            _inject_glyph_in_place(embedded, system, "A")
    finally:
        embedded.close()
        system.close()
