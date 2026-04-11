"""System font discovery — find installed fonts matching PostScript names."""

from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path

from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# Module-level cache: PostScript name (str) → absolute path (str).
# Populated lazily on first call to find_font() via the slow path.
#
# WARNING: Thread-unsafe global cache. This library is single-threaded.
# The planned MCP wrapper (pdf-edit-mcp) must serialize all calls to the
# Python engine. Do not use concurrent.futures or multiprocessing to call
# find()/replace() in parallel.
_FONT_CACHE: dict[str, str] | None = None

# Metrically similar open-source alternatives for common proprietary fonts.
_METRIC_EQUIVALENTS: dict[str, list[str]] = {
    "Calibri": ["Carlito-Regular", "LiberationSans-Regular", "Arimo-Regular"],
    "Calibri-Bold": ["Carlito-Bold", "LiberationSans-Bold", "Arimo-Bold"],
    "Calibri-Italic": ["Carlito-Italic", "LiberationSans-Italic", "Arimo-Italic"],
    "Calibri-BoldItalic": [
        "Carlito-BoldItalic",
        "LiberationSans-BoldItalic",
        "Arimo-BoldItalic",
    ],
    "Arial": ["LiberationSans-Regular", "Arimo-Regular"],
    "ArialMT": ["LiberationSans-Regular", "Arimo-Regular"],
    "Arial-BoldMT": ["LiberationSans-Bold", "Arimo-Bold"],
    "Helvetica": ["LiberationSans-Regular", "Arimo-Regular"],
    "Helvetica-Bold": ["LiberationSans-Bold", "Arimo-Bold"],
    "TimesNewRomanPSMT": ["LiberationSerif-Regular", "Tinos-Regular"],
    "TimesNewRoman": ["LiberationSerif-Regular", "Tinos-Regular"],
    "Times-Roman": ["LiberationSerif-Regular", "Tinos-Regular"],
    "CourierNewPSMT": ["LiberationMono-Regular", "Cousine-Regular"],
    "CourierNew": ["LiberationMono-Regular", "Cousine-Regular"],
    "Courier": ["LiberationMono-Regular", "Cousine-Regular"],
}


def _font_directories() -> list[Path]:
    """Return platform-specific system font directories."""
    system = platform.system()
    if system == "Windows" or sys.platform == "win32":
        windir = Path("C:/Windows/Fonts")
        localappdata = Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
        dirs = [windir, localappdata]
    elif system == "Darwin":
        dirs = [
            Path("/Library/Fonts"),
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
            Path.home() / "Library" / "Fonts",
        ]
    else:
        dirs = [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path.home() / ".local" / "share" / "fonts",
        ]
    return [d for d in dirs if d.is_dir()]


def _fast_lookup(postscript_name: str) -> str | None:
    """Attempt to find a font file by filename heuristic (no font parsing)."""
    # Build candidate filenames from the PostScript name
    lower = postscript_name.lower()
    no_dash = lower.replace("-", "")
    candidates = [
        f"{lower}.ttf",
        f"{lower}.otf",
        f"{no_dash}.ttf",
        f"{no_dash}.otf",
    ]
    # Also try abbreviated bold/italic patterns: "CalibriB" → "calibrib.ttf"
    if lower.endswith("bold"):
        stem = lower[: -len("bold")]
        candidates.append(f"{stem}b.ttf")
    if lower.endswith("italic"):
        stem = lower[: -len("italic")]
        candidates.append(f"{stem}i.ttf")
    if lower.endswith("bolditalic"):
        stem = lower[: -len("bolditalic")]
        candidates.append(f"{stem}bi.ttf")
        candidates.append(f"{stem}z.ttf")

    for font_dir in _font_directories():
        for candidate in candidates:
            path = font_dir / candidate
            if path.is_file():
                # Verify the PostScript name actually matches
                try:
                    font = TTFont(str(path), fontNumber=0)
                    ps_name = font["name"].getDebugName(6)
                    font.close()
                    if ps_name and ps_name == postscript_name:
                        return str(path)
                except Exception:  # noqa: BLE001
                    continue
    return None


def _build_font_cache() -> dict[str, str]:
    """Scan all system font files and build PostScript name → path mapping."""
    cache: dict[str, str] = {}
    for font_dir in _font_directories():
        for ext in ("**/*.ttf", "**/*.otf", "**/*.ttc"):
            for path in font_dir.glob(ext):
                try:
                    if path.suffix.lower() == ".ttc":
                        # TrueType Collection: scan all faces
                        font = TTFont(str(path), fontNumber=0)
                        num_fonts = font.reader.numFonts if hasattr(font.reader, "numFonts") else 1
                        font.close()
                        for i in range(num_fonts):
                            try:
                                f = TTFont(str(path), fontNumber=i)
                                ps_name = f["name"].getDebugName(6)
                                f.close()
                                if ps_name and ps_name not in cache:
                                    cache[ps_name] = str(path)
                            except Exception:  # noqa: BLE001
                                continue
                    else:
                        font = TTFont(str(path), fontNumber=0)
                        ps_name = font["name"].getDebugName(6)
                        font.close()
                        if ps_name and ps_name not in cache:
                            cache[ps_name] = str(path)
                except Exception:  # noqa: BLE001
                    continue
    return cache


def find_font(postscript_name: str) -> str | None:
    """Find a system font file matching the given PostScript name.

    Uses a two-pass strategy for speed:
    1. Fast pass: filename heuristic (covers ~80% of cases instantly).
    2. Slow pass: full nameID-6 scan of all system fonts (cached after first run).

    If no exact match is found, tries metrically similar fallback fonts.

    Args:
        postscript_name: PostScript name from the PDF (e.g., 'Calibri-Bold').

    Returns:
        Absolute path to the font file, or None if not found.
    """
    global _FONT_CACHE  # noqa: PLW0603

    # Fast pass — filename heuristic
    fast = _fast_lookup(postscript_name)
    if fast is not None:
        return fast

    # Slow pass — full scan (cached)
    if _FONT_CACHE is None:
        logger.info("Building system font cache (one-time scan)...")
        _FONT_CACHE = _build_font_cache()

    if postscript_name in _FONT_CACHE:
        return _FONT_CACHE[postscript_name]

    # Fallback — try metric equivalents
    equivalents = _METRIC_EQUIVALENTS.get(postscript_name, [])
    for equiv_name in equivalents:
        if equiv_name in _FONT_CACHE:
            logger.info(
                "Using metric equivalent %s for %s",
                equiv_name,
                postscript_name,
            )
            return _FONT_CACHE[equiv_name]

    return None
