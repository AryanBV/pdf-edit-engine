"""System font discovery — find installed fonts matching PostScript names."""

from __future__ import annotations

import logging
import os
import platform
import sys
from pathlib import Path

from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# Module-level cache: PostScript name (str) → (resolved_realpath, origin)
# where origin ∈ {"system", "user"}. The metric-equivalent fallback
# branch in `_find_font_with_origin` derives the third "metric_equivalent"
# state at lookup time (not stored in the cache itself).
#
# Origin is load-bearing for the F-D-CC9 security surface: a font sourced
# from a user-writable directory (Windows: `~/AppData/Local/Microsoft/
# Windows/Fonts`; macOS: `~/Library/Fonts`; Linux: `~/.local/share/fonts`)
# can be primed by an attacker with write access there. The Degradation
# `font_substituted_from_user_fonts` (severity warning, NOT in
# FONT_AFFECTING_KINDS — origin surface, not a fidelity break) lets
# callers detect this without rejecting the otherwise-valid font.
#
# WARNING: Thread-unsafe global cache. This library is single-threaded.
# The planned MCP wrapper (pdf-edit-mcp) must serialize all calls to the
# Python engine. Do not use concurrent.futures or multiprocessing to call
# find()/replace() in parallel.
_FONT_CACHE: dict[str, tuple[str, str]] | None = None

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
    """Return platform-specific system font directories.

    Order is load-bearing: system-default dirs come BEFORE user-writable
    dirs so that, on a host where the same PostScript name is present in
    both, the system-default copy wins the cache slot. Combined with the
    iteration order in `_find_font_with_origin` (exact match first, then
    metric-equivalent fallback), this codifies the order of preference
    **system > metric_equivalent > user**.
    """
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


def _user_font_directories() -> list[Path]:
    """Return the per-platform user-writable font directories.

    Used for origin classification: any font whose canonical realpath
    lives under one of these dirs is tagged ``origin="user"``. F-D-CC9
    surfaces this through ``Degradation(kind="font_substituted_from_user_fonts")``
    so callers can see when an injected glyph outline came from a path
    an unprivileged process could have primed.
    """
    system = platform.system()
    if system == "Windows" or sys.platform == "win32":
        return [Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"]
    if system == "Darwin":
        return [Path.home() / "Library" / "Fonts"]
    return [Path.home() / ".local" / "share" / "fonts"]


def _canonical_dir_set() -> list[str]:
    """Return ``os.path.realpath``-canonicalized + case-normalized
    directory strings for every dir in ``_font_directories()``.

    Used by ``_build_font_cache`` to enforce the link-traversal check:
    every cached font's realpath must reside inside one of these
    canonical directories. A symlink (POSIX) or directory junction
    (Windows) planted inside ``_font_directories()`` that points OUTSIDE
    the canonical set escapes; ``_build_font_cache`` skips such entries
    with a WARN log. Mirrors ``_pathutil._path_traverses_link``'s
    ``realpath`` vs ``abspath``-normcase pattern, applied to the
    containment check rather than equality.
    """
    out: list[str] = []
    for d in _font_directories():
        try:
            real = os.path.realpath(str(d))
        except OSError:
            continue
        out.append(os.path.normcase(real))
    return out


def _classify_origin(real_path: str) -> str:
    """Return ``"user"`` if *real_path* lives under a user-writable font
    dir; otherwise ``"system"``. Operates on already-canonicalized paths
    (caller has run ``os.path.realpath``).
    """
    norm = os.path.normcase(real_path)
    for user_dir in _user_font_directories():
        try:
            user_real = os.path.normcase(os.path.realpath(str(user_dir)))
        except OSError:
            continue
        if norm.startswith(user_real + os.sep) or norm == user_real:
            return "user"
    return "system"


def _safe_realpath_within(path: str, canonical_dirs: list[str]) -> str | None:
    """Return ``os.path.realpath(path)`` if it stays inside one of
    *canonical_dirs* (case-normalized comparison); else ``None``.

    Mirrors ``_pathutil._path_traverses_link``'s ``realpath`` vs
    ``abspath`` posture but applies the containment check needed for the
    font-cache traversal defence.
    """
    try:
        real = os.path.realpath(path)
    except OSError:
        return None
    norm = os.path.normcase(real)
    for cdir in canonical_dirs:
        if norm == cdir or norm.startswith(cdir + os.sep):
            return real
    return None


def _fast_lookup(postscript_name: str) -> tuple[str, str] | None:
    """Attempt to find a font file by filename heuristic (no font parsing).

    Returns ``(canonical_realpath, origin)`` on success, else ``None``.
    Origin is computed via ``_classify_origin`` on the canonicalized
    realpath; symlink/junction traversal escaping ``_font_directories()``
    is rejected (returns ``None``) with a WARN log.
    """
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

    # Deferred import to avoid circular dependency with fonts.py
    # (fonts → system_fonts via _strip_subset_prefix at module load).
    from pdf_edit_engine.fonts import _with_fonttools_translation

    canonical_dirs = _canonical_dir_set()
    for font_dir in _font_directories():
        for candidate in candidates:
            path = font_dir / candidate
            if path.is_file():
                real_path = _safe_realpath_within(str(path), canonical_dirs)
                if real_path is None:
                    logger.warning(
                        "system_fonts: skipping non-canonical font path: %s",
                        path,
                    )
                    continue
                # Verify the PostScript name actually matches. Per
                # Skeptic-A: TTFont() defers parsing — wrap the
                # constructor AND the downstream `font["name"]` access.
                try:
                    with _with_fonttools_translation(f"_filename_heuristic:{path.name}"):
                        font = TTFont(real_path, fontNumber=0)
                        try:
                            ps_name = font["name"].getDebugName(6)
                        finally:
                            font.close()
                    if ps_name and ps_name == postscript_name:
                        return (real_path, _classify_origin(real_path))
                except Exception:  # noqa: BLE001
                    continue
    return None


def _build_font_cache() -> dict[str, tuple[str, str]]:
    """Scan all system font files and build PostScript-name → (path, origin) mapping.

    Each cached path is canonicalized via ``os.path.realpath`` and verified
    to live inside one of the directories returned by ``_font_directories()``
    (case-normalized comparison). Symlink/junction traversal that escapes
    the canonical font tree is skipped with a WARN log — closes F-D-CC9.

    Origin is ``"user"`` when the canonical realpath resides under a
    per-platform user-writable font directory (Windows: ``~/AppData/Local/
    Microsoft/Windows/Fonts``; macOS: ``~/Library/Fonts``; Linux:
    ``~/.local/share/fonts``); else ``"system"``.

    Iteration order is system-dirs-first (per ``_font_directories()``);
    combined with the ``ps_name not in cache`` guard, this means a
    PostScript name present in BOTH a system and a user dir is recorded
    with ``origin="system"``. The metric-equivalent fallback in
    ``_find_font_with_origin`` derives the third ``"metric_equivalent"``
    state at lookup time.
    """
    # Deferred import to avoid circular dependency with fonts.py
    # (fonts → system_fonts via _strip_subset_prefix at module load).
    from pdf_edit_engine.fonts import _with_fonttools_translation

    cache: dict[str, tuple[str, str]] = {}
    canonical_dirs = _canonical_dir_set()
    for font_dir in _font_directories():
        for ext in ("**/*.ttf", "**/*.otf", "**/*.ttc"):
            for path in font_dir.glob(ext):
                real_path = _safe_realpath_within(str(path), canonical_dirs)
                if real_path is None:
                    logger.warning(
                        "system_fonts: skipping non-canonical font path: %s",
                        path,
                    )
                    continue
                origin = _classify_origin(real_path)
                try:
                    if path.suffix.lower() == ".ttc":
                        # TrueType Collection: scan all faces
                        with _with_fonttools_translation(f"_build_font_cache:ttc:{path.name}"):
                            font = TTFont(real_path, fontNumber=0)
                            num_fonts = (
                                font.reader.numFonts if hasattr(font.reader, "numFonts") else 1
                            )
                            font.close()
                        for i in range(num_fonts):
                            try:
                                with _with_fonttools_translation(
                                    f"_build_font_cache:ttc_face:{path.name}#{i}"
                                ):
                                    f = TTFont(real_path, fontNumber=i)
                                    try:
                                        ps_name = f["name"].getDebugName(6)
                                    finally:
                                        f.close()
                                if ps_name and ps_name not in cache:
                                    cache[ps_name] = (real_path, origin)
                            except Exception:  # noqa: BLE001
                                continue
                    else:
                        with _with_fonttools_translation(f"_build_font_cache:single:{path.name}"):
                            font = TTFont(real_path, fontNumber=0)
                            try:
                                ps_name = font["name"].getDebugName(6)
                            finally:
                                font.close()
                        if ps_name and ps_name not in cache:
                            cache[ps_name] = (real_path, origin)
                except Exception:  # noqa: BLE001
                    continue
    return cache


def find_font(postscript_name: str) -> str | None:
    """Find a system font file matching the given PostScript name.

    Backward-compatible thin wrapper over :func:`_find_font_with_origin`
    that drops the origin and substitution-name components. The public
    ``find_font`` signature has been ``str -> str | None`` since v0.1.0;
    callers that need to know whether a metric-equivalent was used or
    whether the resolved file came from a user-writable directory should
    call ``_find_font_with_origin`` instead.
    """
    found = _find_font_with_origin(postscript_name)
    return None if found is None else found[0]


# Order of preference, codified by the iteration order below:
#
#   system > metric_equivalent > user
#
# 1. Fast filename-heuristic lookup hits ``_font_directories()`` in
#    declared order (system dirs first, user dirs last) — so an exact-
#    name match in a system dir wins over the same name in a user dir.
# 2. Slow-path cache lookup runs after the fast path. The cache itself
#    was built with the same system-first iteration order, and the
#    ``ps_name not in cache`` guard preserves the first-write-wins
#    invariant: when a name appears in both a system and a user dir,
#    the system entry is the one stored.
# 3. Metric-equivalent fallback runs LAST, only when neither the fast
#    path nor the cache had an exact-name match. It walks the
#    ``_METRIC_EQUIVALENTS`` list in declared order (Carlito >
#    LiberationSans > Arimo for Calibri).
#
# Net effect: an exact-name match on a system font outranks a metric-
# equivalent match anywhere; a metric-equivalent match outranks a
# user-fonts-only exact-name match ONLY if the metric-equivalent is
# itself in a system dir; otherwise the user-dir exact match wins
# but is reported with origin="user", and Tier 1.5 emits the
# `font_substituted_from_user_fonts` Degradation so the caller sees
# the security-relevant origin (F-D-CC9).
def _find_font_with_origin(postscript_name: str) -> tuple[str, str, str | None] | None:
    """Resolve a system font by PostScript name, surfacing origin and
    metric-equivalent substitution.

    Two halves of one return value:

    * **Origin** (F-D-CC9): ``"system"`` when the resolved file lives in
      a system-default font directory, ``"user"`` when it lives in a
      per-platform user-writable directory, ``"metric_equivalent"`` when
      the requested PostScript name was absent and a name from
      ``_METRIC_EQUIVALENTS`` was substituted. Origin records the
      *security-relevant location* of the resolved font; an attacker
      with write access to the user-fonts dir can prime ``"user"`` (or
      poison a metric-equivalent target into ``"user"``), and the
      caller (Tier 1.5) emits
      ``Degradation(kind="font_substituted_from_user_fonts")`` when
      origin is ``"user"`` so the security-relevant origin is observable
      via ``FidelityReport.degradations``.
    * **substituted_name** (INV-C-4): the metric-equivalent's
      PostScript name when the requested font was absent and a fallback
      was used; ``None`` for an exact match. Surfaced via
      ``FidelityReport.font_substituted``.

    Returns:
        ``None`` if no font found; otherwise
        ``(path, origin, substituted_name)`` where ``origin`` is one of
        ``"system"``, ``"user"``, ``"metric_equivalent"``.
    """
    global _FONT_CACHE  # noqa: PLW0603

    postscript_name = _strip_subset_prefix(postscript_name)

    # Fast pass — filename heuristic. Returns the canonical realpath
    # plus origin tag (system/user). Substituted-name is None because
    # the fast path only matches exact names (see _fast_lookup which
    # verifies the embedded nameID-6).
    fast = _fast_lookup(postscript_name)
    if fast is not None:
        path, origin = fast
        return (path, origin, None)

    if _FONT_CACHE is None:
        logger.info("Building system font cache (one-time scan)...")
        _FONT_CACHE = _build_font_cache()

    if postscript_name in _FONT_CACHE:
        path, origin = _FONT_CACHE[postscript_name]
        return (path, origin, None)

    # Metric-equivalent fallback. Origin is reported as "metric_equivalent"
    # so the caller can distinguish substitution from a direct user-dir
    # hit; the storage origin from the cache is preserved in `cache_origin`
    # but the canonical reportable origin for the call is "metric_equivalent".
    # If an attacker primed the metric-equivalent name into the user-fonts
    # dir (a "metric_equivalent that is physically a user font"), the
    # _find_font_with_origin contract still flags it via origin="user" —
    # NOT "metric_equivalent" — so Tier 1.5's `font_substituted_from_user_fonts`
    # surfacing fires unambiguously. The substituted_name still records
    # the equivalent's PostScript name for INV-C-4.
    equivalents = _METRIC_EQUIVALENTS.get(postscript_name, [])
    for equiv_name in equivalents:
        if equiv_name in _FONT_CACHE:
            cached_path, cache_origin = _FONT_CACHE[equiv_name]
            logger.info(
                "Using metric equivalent %s for %s (origin=%s)",
                equiv_name,
                postscript_name,
                cache_origin,
            )
            # When the metric-equivalent itself was sourced from the user
            # dir, the security-relevant origin is "user" — the user-dir
            # surfacing supersedes the metric_equivalent label so the
            # F-D-CC9 Degradation fires. Otherwise tag as metric_equivalent.
            reported_origin = "user" if cache_origin == "user" else "metric_equivalent"
            return (cached_path, reported_origin, equiv_name)

    return None
