"""INV-W0-4: _inject_glyph_in_place raises FontNotFoundError on unitsPerEm mismatch."""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
import pytest
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

from pdf_edit_engine.errors import FontNotFoundError
from pdf_edit_engine.fonts import _extract_font_bytes, _inject_glyph_in_place

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def _load_embedded_truetype() -> TTFont:
    """Load the first TrueType embedded subset we can find from the corpus."""
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


def test_inv_w0_4_inject_upm_mismatch_raises() -> None:
    """_inject_glyph_in_place(embedded, system, ch) raises FontNotFoundError when
    embedded.unitsPerEm != system.unitsPerEm.
    """
    embedded = _load_embedded_truetype()
    # Reload a separate copy as 'system'
    system_bytes = io.BytesIO()
    embedded.save(system_bytes)
    system_bytes.seek(0)
    system = TTFont(system_bytes)

    try:
        # Mutate the system font's unitsPerEm so it differs from embedded
        original_upem = int(system["head"].unitsPerEm)
        system["head"].unitsPerEm = original_upem + 1024
        assert system["head"].unitsPerEm != embedded["head"].unitsPerEm

        with pytest.raises(FontNotFoundError, match="unitsPerEm"):
            _inject_glyph_in_place(embedded, system, "A")
    finally:
        embedded.close()
        system.close()
