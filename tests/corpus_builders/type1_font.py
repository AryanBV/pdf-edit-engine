"""Builder: embedded Type1 (/FontFile, PFA) simple-font PDF.

C.2's ``fonts._introspect_embedded_font`` has a Type1 (``/FontFile``) branch
that was DEAD on arrival: it called ``t1Lib.T1Font()`` with no arguments, but
fontTools' ``T1Font.__init__(self, path, ...)`` requires ``path`` positionally,
so the call raised ``TypeError`` before ``t1.data = font_bytes`` ran — the
bounded ``except`` swallowed it and the branch ALWAYS returned 0. A valid Type1
font therefore reported ``glyph_count == 0`` plus a FALSE
``font_subset_introspection_failed``.

This builder manufactures a deterministic, host-font-free Type1 font in-process
with ``fontTools.t1Lib`` (cleartext PFA program), embeds it as ``/FontFile``
inside a simple ``/Type1`` font dictionary (with the PFA-segment lengths in
``/Length1`` / ``/Length2`` / ``/Length3``), and shows the two non-notdef
glyphs in the content stream. The truthful CharStrings count is
``len(_GLYPH_CHARS) + 1`` (``.notdef`` + ``A`` + ``B`` == 3); the engine must
recover exactly that via ``t1Lib.T1Font(path).parse()`` on the embedded bytes.

Deterministic and host-font-free: the Type1 program is synthesised in-process,
so this builder never returns ``None``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from ._common import emit_or_write, save_pdf_deterministic

if TYPE_CHECKING:
    from pathlib import Path

# Non-.notdef glyphs embedded in the Type1 program. The TRUE CharStrings count
# is len(_GLYPH_CHARS) + 1 (incl .notdef) == 3.
_GLYPH_CHARS = ["A", "B"]
_TRUE_GLYPH_COUNT = len(_GLYPH_CHARS) + 1
_PS_NAME = "CorpusType1-Regular"


def _type1_program_bytes() -> bytes:
    """Synthesize a minimal Type1 font and return its cleartext PFA program.

    Hand-builds the Type1 ``font`` dict (``.notdef`` + the ``_GLYPH_CHARS``
    glyphs, each a bare ``hsbw .. endchar`` charstring) and serializes it via
    ``t1Lib.T1Font.createData`` — the cleartext PFA format a PDF ``/FontFile``
    carries. The Private dict carries the RD/ND/NP procedure definitions so the
    serialized stream round-trips through ``t1Lib``'s PostScript reader.

    Returns:
        The PFA program bytes (ASCII clear-text header + binary eexec section +
        trailing zeros), ready to embed as ``/FontFile``.
    """
    from fontTools import t1Lib  # type: ignore[import-untyped]
    from fontTools.misc.psCharStrings import T1CharString  # type: ignore[import-untyped]

    def _charstring(width: int) -> T1CharString:
        cs = T1CharString()
        # hsbw <left-sidebearing> <width> ; endchar — a zero-outline glyph that
        # still occupies a CharStrings slot (the count is what we introspect).
        cs.program = [0, width, "hsbw", "endchar"]
        return cs

    encoding = [".notdef"] * 256
    for char in _GLYPH_CHARS:
        encoding[ord(char)] = char

    charstrings: dict[str, T1CharString] = {".notdef": _charstring(500)}
    for char in _GLYPH_CHARS:
        charstrings[char] = _charstring(600)

    font_dict = {
        "FontName": _PS_NAME,
        "FontType": 1,
        "FontMatrix": [0.001, 0, 0, 0.001, 0, 0],
        "PaintType": 0,
        "FontBBox": [0, -200, 600, 800],
        "Encoding": encoding,
        "FontInfo": {
            "version": "001.000",
            "FullName": "CorpusType1 Regular",
            "FamilyName": "CorpusType1",
            "Weight": "Regular",
            "ItalicAngle": 0,
            "isFixedPitch": False,
            "UnderlinePosition": -100,
            "UnderlineThickness": 50,
        },
        "Private": {
            # RD/ND/NP procedure definitions — t1Lib.encode_eexec derives the
            # operator names by matching these value tuples, and t1Lib.parse
            # needs them defined to read the eexec section back.
            "RD": ("string", "currentfile", "exch", "readstring", "pop"),
            "ND": ("def",),
            "NP": ("put",),
            "lenIV": 4,
            "Subrs": [],
            "BlueValues": [],
            "password": 5839,
        },
        "CharStrings": charstrings,
    }
    # T1Font.__init__ insists on a path; construct the instance directly and
    # serialize via createData (no file I/O needed for the cleartext PFA).
    t1 = t1Lib.T1Font.__new__(t1Lib.T1Font)
    t1.font = font_dict
    t1.encoding = "ascii"
    return bytes(t1.createData())


def _pfa_segment_lengths(program: bytes) -> tuple[int, int, int]:
    """Split a cleartext PFA program into the /Length1 /Length2 /Length3 spans.

    A Type1 ``/FontFile`` records three byte spans: the ASCII clear-text header
    (Length1), the binary eexec-encrypted section (Length2), and the trailing
    fixed-content section of 512 zeros + ``cleartomark`` (Length3).

    Args:
        program: The PFA program bytes from :func:`_type1_program_bytes`.

    Returns:
        ``(length1, length2, length3)`` such that they sum to ``len(program)``.
    """
    eexec_marker = b"currentfile eexec\n"
    eexec_idx = program.find(eexec_marker)
    if eexec_idx == -1:
        # Fallback: treat the whole program as clear text. The engine's Type1
        # parser does not depend on the split being exact, only on the bytes.
        return len(program), 0, 0
    length1 = eexec_idx + len(eexec_marker)
    # The trailing section is the run of 512 '0' chars + cleartomark.
    tail_idx = program.find(b"0" * 64, length1)
    if tail_idx == -1:
        return length1, len(program) - length1, 0
    length2 = tail_idx - length1
    length3 = len(program) - length1 - length2
    return length1, length2, length3


def build_type1_font_pdf(out_path: Path | None = None) -> bytes:
    """Build a PDF whose embedded font is a Type1 program in ``/FontFile``.

    The font dictionary is a simple ``/Type1`` font (WinAnsi-encoded) whose
    ``/FontDescriptor`` embeds the synthesised Type1 program as ``/FontFile``
    with ``/Length1`` / ``/Length2`` / ``/Length3``. The TRUE CharStrings count
    is :data:`_TRUE_GLYPH_COUNT` (3); the engine must recover it via
    ``t1Lib`` rather than the dead-code 0.

    Args:
        out_path: Optional file to write the PDF to. The bytes are returned
            regardless.

    Returns:
        The PDF bytes. Synthesised in-process; never returns ``None``.
    """
    program = _type1_program_bytes()
    length1, length2, length3 = _pfa_segment_lengths(program)

    pdf = pikepdf.Pdf.new()
    try:
        font_stream = pikepdf.Stream(pdf, program)
        font_stream["/Length1"] = length1
        font_stream["/Length2"] = length2
        font_stream["/Length3"] = length3

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
                    "/CapHeight": 680,
                    "/StemV": 80,
                    "/FontFile": font_stream,
                }
            )
        )

        # Simple Type1 font dict. /Widths covers the two glyphs at codepoints
        # 'A'(65)..'B'(66); /FirstChar 65 /LastChar 66.
        type1 = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/" + _PS_NAME),
                "/FirstChar": 65,
                "/LastChar": 66,
                "/Widths": pikepdf.Array([600, 600]),
                "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
                "/FontDescriptor": font_descriptor,
            }
        )

        # Show 'AB' (bytes 0x41 0x42) under WinAnsi single-byte encoding.
        content = ("BT\n/F1 24 Tf\n1 0 0 1 72 720 Tm\n(AB) Tj\nET").encode("latin-1")

        page = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Page"),
                "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
                "/Resources": pikepdf.Dictionary(
                    {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(type1)})}
                ),
                "/Contents": pikepdf.Stream(pdf, content),
            }
        )
        pdf.pages.append(pikepdf.Page(page))
        return emit_or_write(save_pdf_deterministic(pdf), out_path)
    finally:
        pdf.close()
