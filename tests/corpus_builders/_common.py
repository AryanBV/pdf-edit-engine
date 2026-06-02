"""Shared deterministic helpers for the adversarial corpus builders.

Every builder in this package must be **deterministic**: given the same
inputs it produces byte-stable output with no network access and no
wall-clock timestamps. Two knobs make that hold:

- :data:`FIXED_FONT_EPOCH` is written into every synthesised font's
  ``head`` table (``created`` / ``modified``) so fontTools does not stamp
  the current time.
- :func:`save_pdf_deterministic` saves with pikepdf's ``deterministic_id``
  and ``static_id`` so the ``/ID`` trailer entry and document instance ID
  are reproducible.

The builders follow the repo's existing ``cidfont_synthetic`` precedent
(``tests/generate_complex_corpus.py`` and ``tests/_identity_h_fixture.py``):
a TrueType font is discovered on the host, a narrow subset is embedded as a
``/FontFile2`` inside a Type0 / Identity-H font, and a ToUnicode CMap maps
the CID(==GID) glyph indices back to Unicode.

No ``src/pdf_edit_engine`` import lives here — these are net-new test
tooling and must not couple to engine internals.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

# fontTools' head table stamps the build time by default; pin it so the
# emitted font bytes are reproducible. The value is the Mac font epoch
# offset for 2001-01-01T00:00:00Z (seconds since 1904-01-01), a stable,
# arbitrary, non-zero constant.
FIXED_FONT_EPOCH: int = 3061152000

# Candidate TrueType fonts in platform priority order. Mirrors the
# discovery list in tests/_identity_h_fixture.py so the builders run on the
# same hosts the existing synthetic fixtures already cover.
_TTF_CANDIDATES: tuple[Path, ...] = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/ARIAL.TTF"),
    Path("C:/Windows/Fonts/calibri.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
)

# Candidate fonts that cover the Arabic block (U+0600..U+06FF). Arial and
# Tahoma both carry Arabic on Windows; DejaVu Sans on Linux.
_ARABIC_TTF_CANDIDATES: tuple[Path, ...] = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/tahoma.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
)


def find_truetype_font() -> Path | None:
    """Return the first available general-purpose TrueType font, or None."""
    for candidate in _TTF_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _font_covers(font_path: Path, text: str) -> bool:
    """True if every codepoint in ``text`` is in the font's BMP cmap."""
    from fontTools import ttLib  # type: ignore[import-untyped]

    font = ttLib.TTFont(str(font_path))
    try:
        cmap: dict[int, str] = {}
        for table in font["cmap"].tables:
            if table.platformID == 3 and table.platEncID == 1:
                cmap = table.cmap
                break
        return all(ord(ch) in cmap for ch in text)
    finally:
        font.close()


def find_font_covering(text: str, candidates: tuple[Path, ...]) -> Path | None:
    """Return the first candidate whose cmap covers every char in ``text``."""
    for candidate in candidates:
        if candidate.exists() and _font_covers(candidate, text):
            return candidate
    return None


def find_arabic_font(text: str) -> Path | None:
    """Return the first installed font that covers the Arabic ``text``."""
    return find_font_covering(text, _ARABIC_TTF_CANDIDATES)


def pin_font_timestamps(font: object) -> None:
    """Pin a fontTools ``TTFont``'s head timestamps to a fixed epoch.

    Args:
        font: A ``fontTools.ttLib.TTFont`` instance with a ``head`` table.
    """
    head = font["head"]  # type: ignore[index]
    head.created = FIXED_FONT_EPOCH
    head.modified = FIXED_FONT_EPOCH


def build_tounicode_cmap(cid_to_unicode: dict[int, int]) -> str:
    """Build a ToUnicode CMap string mapping each CID to a Unicode value.

    Args:
        cid_to_unicode: CID (==GID under Identity) → Unicode codepoint.

    Returns:
        The CMap program text, ready to embed as a ``/ToUnicode`` stream.
    """
    bfchar_lines = [f"<{cid:04X}> <{cp:04X}>" for cid, cp in sorted(cid_to_unicode.items())]
    out = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\nbegincmap\n"
        "/CIDSystemInfo\n"
        "<< /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n"
        "/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
    )
    for i in range(0, len(bfchar_lines), 100):
        chunk = bfchar_lines[i : i + 100]
        out += f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar\n"
    out += "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    return out


def save_pdf_deterministic(pdf: pikepdf.Pdf) -> bytes:
    """Serialize a pikepdf document to deterministic, reproducible bytes.

    Args:
        pdf: The open pikepdf document.

    Returns:
        The PDF file bytes. ``deterministic_id`` + ``static_id`` make the
        ``/ID`` trailer reproducible. ``compress_streams=False`` stores
        streams raw: qpdf's flate compressor is *not* byte-stable across
        repeated saves within one process (the FontFile2 stream differs by a
        few bytes run-to-run), so for reproducible fixtures we skip
        compression. Size is irrelevant for test artifacts.
    """
    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True, static_id=True, compress_streams=False)
    return buf.getvalue()


def emit_or_write(pdf_bytes: bytes, out_path: Path | None) -> bytes:
    """Return ``pdf_bytes``, also writing them to ``out_path`` if provided.

    Args:
        pdf_bytes: The serialized PDF.
        out_path: Optional destination file. When given, bytes are written
            there before being returned.

    Returns:
        The same ``pdf_bytes`` (so callers can both write and inspect).
    """
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(pdf_bytes)
    return pdf_bytes
