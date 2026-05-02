"""INV-W0-3: _glyph_name_to_unicode handles uniXXXX and known glyph names."""

from __future__ import annotations

from pdf_edit_engine.encoding import _glyph_name_to_unicode


def test_inv_w0_3_uniXXXX_form() -> None:
    """uniXXXX form returns chr(int(XXXX, 16))."""
    assert _glyph_name_to_unicode("uni0041") == "A"
    assert _glyph_name_to_unicode("uni0020") == " "
    assert _glyph_name_to_unicode("uni00C4") == "Ä"


def test_inv_w0_3_known_glyph_names() -> None:
    """Common Adobe glyph names resolve via pdfminer's glyphname2unicode."""
    assert _glyph_name_to_unicode("space") == " "
    assert _glyph_name_to_unicode("A") == "A"


def test_inv_w0_3_unknown_returns_none() -> None:
    """Unknown glyph names return None."""
    assert _glyph_name_to_unicode("xxnotreal_glyph_xx") is None
