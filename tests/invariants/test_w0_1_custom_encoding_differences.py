"""INV-W0-1: _init_custom correctly applies /Differences overrides."""

from __future__ import annotations

import pikepdf

from pdf_edit_engine.encoding import FontResolver


def test_inv_w0_1_differences_applied_to_byte_to_unicode() -> None:
    """A /Differences entry must reach resolver._byte_to_unicode at the right code.

    Builds a synthetic Type1 font dict whose /Encoding is a custom dict with
    /BaseEncoding=/WinAnsiEncoding and /Differences=[65 /A 66 /B 67 /Z].
    After construction, resolver._byte_to_unicode[67] should be 'Z' even though
    the WinAnsi default for 0x43 is 'C'.
    """
    font_dict = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/Helvetica"),
            "/Encoding": pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Encoding"),
                    "/BaseEncoding": pikepdf.Name("/WinAnsiEncoding"),
                    "/Differences": pikepdf.Array(
                        [
                            65,
                            pikepdf.Name("/A"),
                            pikepdf.Name("/B"),
                            pikepdf.Name("/Z"),
                        ]
                    ),
                }
            ),
        }
    )
    resolver = FontResolver(font_dict, "F1")
    assert resolver._encoding_type == "Custom"
    assert resolver._byte_to_unicode[65] == "A"
    assert resolver._byte_to_unicode[66] == "B"
    # The third name overrides code 67 with Z (which would otherwise be 'C').
    assert resolver._byte_to_unicode[67] == "Z", (
        f"expected /Z override at code 67, got {resolver._byte_to_unicode.get(67)!r}"
    )
    # Decoding bytes 65, 67 should yield 'AZ'.
    assert resolver.decode(b"\x41\x43") == "AZ"
