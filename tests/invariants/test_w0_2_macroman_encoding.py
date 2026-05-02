"""INV-W0-2: _init_macRoman maps bytes that differ from WinAnsi correctly."""

from __future__ import annotations

import pikepdf

from pdf_edit_engine.encoding import FontResolver


def test_inv_w0_2_macroman_byte_0x80_is_a_diaeresis() -> None:
    """MacRoman 0x80 is U+00C4 ('Ä'); WinAnsi 0x80 is '€' (U+20AC).

    Constructs a synthetic font with /Encoding=/MacRomanEncoding and asserts
    the byte->unicode table reflects MacRoman, not WinAnsi.
    """
    font_dict = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/Helvetica"),
            "/Encoding": pikepdf.Name("/MacRomanEncoding"),
        }
    )
    resolver = FontResolver(font_dict, "F1")
    assert resolver._encoding_type == "MacRoman"
    assert resolver._is_cid is False
    assert resolver._byte_width == 1
    assert resolver._byte_to_unicode[0x80] == "Ä", (
        f"expected MacRoman 0x80 -> 'Ä', got {resolver._byte_to_unicode.get(0x80)!r}"
    )
    # Sanity: NOT the WinAnsi value
    assert resolver._byte_to_unicode[0x80] != "€"
