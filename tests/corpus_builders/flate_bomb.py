"""Builder: Identity-H PDF whose embedded ``/FontFile2`` is a Flate bomb.

Net-new test tooling (no ``src/`` changes). Supports the A1.3 / INV-W-4
stream-size-bound invariant probes (decompression-bomb guard).

A *Flate decompression bomb* is a small compressed payload that expands to
an enormous decoded size — here ``zlib.compress(b"\\x00" * 50 MiB)`` is a few
KiB compressed but balloons to 50 MiB when inflated. Embedding it as a
font's ``/FontFile2`` means any read path that does
``stream.read_bytes()`` (which transparently inflates) materialises 50 MiB
in memory, and any extension path that loads it through ``fontTools`` peaks
even higher. The A1.3 root fix replaces those reads with a bounded
``_pathutil.read_stream_bounded`` that refuses (raises
``FontStreamTooLargeError``) BEFORE the full decode and never peaks far
above the cap.

This builder produces a minimal, self-contained Type0 / Identity-H font
dictionary so the targeted ``extend_subset`` probe is **deterministic and
host-font-free** (it never skips): the embedded ``/FontFile2`` bytes are
just compressed zeros, and the bound rejects the stream before fontTools
ever tries to parse the (deliberately non-font) bytes. The font dict carries
a ``/ToUnicode`` CMap so the B.3 "no /ToUnicode" refusal does not fire first
and the extension path reaches the ``_extract_font_bytes`` read of the bomb.

The content stream shows CID ``0x0001`` (the editable run); a replacement
that introduces a glyph absent from the (unparseable) embedded font forces
the font-extension read path that A1.3 bounds.
"""

from __future__ import annotations

import zlib
from typing import TYPE_CHECKING

import pikepdf

from ._common import build_tounicode_cmap, emit_or_write

if TYPE_CHECKING:
    from pathlib import Path

# The decoded (inflated) size of the bomb payload. Far above the
# ``MAX_FONT_STREAM_BYTES = 32 MiB`` cap A1.3 introduces, so a bounded read
# must refuse it. The payload is all-zeros so it compresses to a few KiB.
BOMB_DECODED_BYTES: int = 50 * 1024 * 1024

# Logical anchor for the public find()/replace() e2e form. The fixture shows
# CID 0x0001, mapped to 'A' via ToUnicode.
FIND_ANCHOR: str = "A"
# A replacement that introduces a glyph absent from the embedded cmap, so the
# edit must consult / extend the embedded font binary (the bombed read path).
# 'Z' is not mapped by the fixture's ToUnicode, forcing the extension route.
REPLACEMENT: str = "Z"

_PS_NAME = "FlateBomb-Regular"


def make_flate_bomb_stream(pdf: pikepdf.Pdf, *, with_length1: bool = True) -> pikepdf.Object:
    """Create a ``/FlateDecode`` stream whose decoded size is a bomb.

    The raw stream bytes are ``zlib.compress(b"\\x00" * BOMB_DECODED_BYTES)``
    (a few KiB), but inflating them yields ``BOMB_DECODED_BYTES``. The stream
    is tagged ``/Filter /FlateDecode`` so ``read_bytes()`` would inflate it.

    Args:
        pdf: The open document the stream is attached to.
        with_length1: When True, sets ``/Length1`` to the declared decoded
            size (the shape a real ``/FontFile2`` carries). The A1.3
            ``/Length1`` pre-gate uses this for a cheap pre-decompression
            reject. Pass False to force the chunked-decode path (the memory
            proof needs the cheap pre-gate disabled).

    Returns:
        A ``pikepdf`` stream object carrying the compressed bomb payload.
    """
    raw = zlib.compress(b"\x00" * BOMB_DECODED_BYTES)
    stream = pdf.make_stream(raw)
    stream.Filter = pikepdf.Name("/FlateDecode")
    if with_length1:
        stream.Length1 = BOMB_DECODED_BYTES
    return stream


def build_flate_bomb_fontfile2_pdf(
    out_path: Path | None = None, *, with_length1: bool = True
) -> bytes:
    """Build an Identity-H PDF whose ``/FontFile2`` is a Flate bomb.

    The Type0 / Identity-H font dict is minimal and self-contained: the
    embedded ``/FontFile2`` is a decompression bomb (not a real font), and a
    ``/ToUnicode`` CMap maps CID ``0x0001`` to ``'A'`` so the editable run is
    locatable and the B.3 "no /ToUnicode" refusal does not pre-empt the
    extension read path.

    Args:
        out_path: Optional file to write the PDF to. The bytes are returned
            regardless.
        with_length1: Forwarded to :func:`make_flate_bomb_stream`. Default
            True (declares the oversize ``/Length1``).

    Returns:
        The PDF bytes. Synthesised in-process; never returns ``None``.
    """
    pdf = pikepdf.Pdf.new()
    try:
        font_stream = make_flate_bomb_stream(pdf, with_length1=with_length1)

        font_descriptor = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/FontDescriptor"),
                    "/FontName": pikepdf.Name("/" + _PS_NAME),
                    "/Flags": 4,
                    "/FontBBox": pikepdf.Array([0, -200, 600, 800]),
                    "/ItalicAngle": 0,
                    "/Ascent": 800,
                    "/Descent": -200,
                    "/CapHeight": 600,
                    "/StemV": 80,
                    "/FontFile2": font_stream,
                }
            )
        )

        cid_font = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/CIDFontType2"),
                    "/BaseFont": pikepdf.Name("/" + _PS_NAME),
                    "/CIDSystemInfo": pikepdf.Dictionary(
                        {
                            "/Registry": pikepdf.String("Adobe"),
                            "/Ordering": pikepdf.String("Identity"),
                            "/Supplement": 0,
                        }
                    ),
                    "/FontDescriptor": font_descriptor,
                    "/DW": 1000,
                    "/W": pikepdf.Array([1, pikepdf.Array([600])]),
                    "/CIDToGIDMap": pikepdf.Name("/Identity"),
                }
            )
        )

        tounicode = build_tounicode_cmap({1: ord("A")})
        type0 = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/" + _PS_NAME),
                "/Encoding": pikepdf.Name("/Identity-H"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
                "/ToUnicode": pikepdf.Stream(pdf, tounicode.encode("latin-1")),
            }
        )

        # Show CID 0x0001 == 'A'.
        content = ("BT\n/F1 24 Tf\n1 0 0 1 72 720 Tm\n<0001> Tj\nET").encode("latin-1")

        page = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Page"),
                "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
                "/Resources": pikepdf.Dictionary(
                    {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(type0)})}
                ),
                "/Contents": pikepdf.Stream(pdf, content),
            }
        )
        pdf.pages.append(pikepdf.Page(page))

        buf_bytes = _serialize(pdf)
        return emit_or_write(buf_bytes, out_path)
    finally:
        pdf.close()


def _serialize(pdf: pikepdf.Pdf) -> bytes:
    """Serialize the bomb PDF, keeping the ``/FontFile2`` Flate-compressed.

    The DEFAULT pikepdf save preserves the ``/FlateDecode`` filter and keeps
    the compressed bomb payload small (a few KiB of compressed zeros) — so the
    fixture file on disk stays tiny while ``/FontFile2`` still inflates to the
    50 MiB bomb. We must NOT pass ``compress_streams=False`` (that DECODES the
    stream, storing the full 50 MiB raw and dropping the filter, defeating the
    bomb). ``static_id`` keeps the ``/ID`` reproducible.
    """
    import io

    buf = io.BytesIO()
    pdf.save(buf, static_id=True)
    return buf.getvalue()
