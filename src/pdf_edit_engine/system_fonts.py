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


def _strip_subset_prefix(ps_name: str) -> str:
    """Remove a 6-letter PDF subset prefix (e.g. ``ABCDEF+Calibri-Bold``).

    PDF embedders prepend a six uppercase-letter prefix + '+' to subsetted
    PostScript names. Lookups against the operating system want the
    underlying font name, not the prefixed form. This helper is the
    single source of truth for that normalization; ``find_font`` applies
    it on every lookup so that callers (including ``fonts._extend_tier2``)
    do not have to remember to pre-strip.
    """
    if len(ps_name) > 7 and ps_name[6] == "+":
        prefix = ps_name[:6]
        if prefix.isalpha() and prefix.isupper():
            return ps_name[7:]
    return ps_name


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

    Backward-compatible thin wrapper over :func:`_find_font_with_origin`
    that drops the substitution-name component. Exists because the
    public-ish ``find_font`` signature has been ``str -> str | None``
    since v0.1.0; callers that need to know whether a metric-equivalent
    was used should call ``_find_font_with_origin`` instead.
    """
    found = _find_font_with_origin(postscript_name)
    return None if found is None else found[0]


def _find_font_with_origin(postscript_name: str) -> tuple[str, str | None] | None:
    """Like :func:`find_font` but reports whether a metric equivalent was used.

    INV-C-4 plumbing: when the requested font is absent and a metric
    equivalent is substituted (Calibri → Carlito, Arial →
    LiberationSans, …), the caller needs to know so it can surface the
    substitution through ``FidelityReport.font_substituted``. Returning
    only the resolved path (the v0.1.1 ``find_font`` contract) makes the
    substitution invisible.

    Returns:
        ``None`` if no font found; otherwise ``(path, substituted_name)``
        where ``substituted_name`` is ``None`` if the exact font was
        located on the host or the PostScript name of the metric
        equivalent that was used (e.g. ``"Carlito-Regular"``).
    """
    global _FONT_CACHE  # noqa: PLW0603

    postscript_name = _strip_subset_prefix(postscript_name)

    # Fast pass — filename heuristic. Always returns the exact name
    # (see _fast_lookup which verifies the embedded nameID-6).
    fast = _fast_lookup(postscript_name)
    if fast is not None:
        return (fast, None)

    if _FONT_CACHE is None:
        logger.info("Building system font cache (one-time scan)...")
        _FONT_CACHE = _build_font_cache()

    if postscript_name in _FONT_CACHE:
        return (_FONT_CACHE[postscript_name], None)

    # Metric-equivalent fallback. Record which equivalent was used
    # so the caller can surface it through the FidelityReport.
    equivalents = _METRIC_EQUIVALENTS.get(postscript_name, [])
    for equiv_name in equivalents:
        if equiv_name in _FONT_CACHE:
            logger.info(
                "Using metric equivalent %s for %s",
                equiv_name,
                postscript_name,
            )
            return (_FONT_CACHE[equiv_name], equiv_name)

    return None
