"""INV-A-2: encode("") returns b"" for resolvers of every encoding type."""

from __future__ import annotations

import pikepdf
import pytest

from pdf_edit_engine.encoding import FontResolver


def _make_winansi_resolver() -> FontResolver:
    fd = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/Helvetica"),
            "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
        }
    )
    return FontResolver(fd, "F1")


def _make_macroman_resolver() -> FontResolver:
    fd = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/Helvetica"),
            "/Encoding": pikepdf.Name("/MacRomanEncoding"),
        }
    )
    return FontResolver(fd, "F2")


def _make_custom_resolver() -> FontResolver:
    fd = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/Helvetica"),
            "/Encoding": pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Encoding"),
                    "/BaseEncoding": pikepdf.Name("/WinAnsiEncoding"),
                    "/Differences": pikepdf.Array([65, pikepdf.Name("/A")]),
                }
            ),
        }
    )
    return FontResolver(fd, "F3")


@pytest.mark.parametrize(
    "factory,expected_type",
    [
        (_make_winansi_resolver, "WinAnsi"),
        (_make_macroman_resolver, "MacRoman"),
        (_make_custom_resolver, "Custom"),
    ],
)
def test_inv_a_2_empty_encode(factory, expected_type: str) -> None:
    """resolver.encode('') returns b'' for every encoding type."""
    resolver = factory()
    assert resolver._encoding_type == expected_type
    assert resolver.encode("") == b""
