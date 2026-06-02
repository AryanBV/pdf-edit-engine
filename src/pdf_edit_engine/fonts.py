"""FontExtender module — analyze and extend font subsets in PDFs."""

from __future__ import annotations

import contextlib
import io
import logging
import os
import struct
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pikepdf
from fontTools.ttLib import TTFont, TTLibError, newTable  # type: ignore[import-untyped]
from pdfminer.cmapdb import CMapParser, FileUnicodeMap

from pdf_edit_engine._pathutil import (
    MAX_FONT_STREAM_BYTES,
    MAX_TOUNICODE_BYTES,
    open_pdf,
    read_stream_bounded,
)
from pdf_edit_engine.errors import EncodingError, FontNotFoundError, FontStreamTooLargeError
from pdf_edit_engine.models import Degradation, FontInfo
from pdf_edit_engine.system_fonts import _strip_subset_prefix

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


# Exception tuple for font-extension failures that should degrade to an
# EditResult failure instead of propagating. Single canonical home (this
# module — font extension is what the tuple describes); reflow,
# structural, and surgeon all import from here. The prior asymmetry
# where each caller maintained its own catch list let a corrupt
# /FontFile2 (TTLibError) escape surgeon while reflow + structural
# degraded gracefully — Phase 13.4 probe 12 surfaced this. Centralising
# the tuple here is the root fix; widening any single module's catch
# would have been a patch.
_FONT_EXTEND_FAIL_EXCS = (FontNotFoundError, EncodingError, OSError, TTLibError)

# Hard upper bound on composite-glyph nesting. Real Latin composites
# nest 2-3 deep; the deepest known commercial font hits ~16. 64 leaves
# headroom for unforeseen typography while clearly bounding adversarial
# inputs (per F-B-03 / INV-W0-8). Hitting this bound is a content
# signal, not a config knob — surfaced as font_extension_failed via
# _FONT_EXTEND_FAIL_EXCS at the caller. Adding RecursionError to the
# fail-tuple would be a patch; the depth cap is the root fix.
MAX_COMPOSITE_DEPTH = 64

# sfnt table-directory magic numbers. A font binary beginning with one of
# these carries an sfnt directory (TrueType / OpenType-CFF / CFF2);
# anything else under /FontFile3 is a BARE CFF table (raw Type1C program,
# no directory) that fontTools' TTFont cannot open directly. Shared by
# _introspect_embedded_font (C.2) and _load_cff_as_ttfont (C.3) so the
# bare-vs-wrapped decision has one source of truth.
_SFNT_MAGICS = frozenset({b"OTTO", b"\x00\x01\x00\x00", b"true", b"ttcf"})


@contextlib.contextmanager
def _with_fonttools_translation(context: str) -> Iterator[None]:
    """Translate fontTools exceptions on a fontTools-using block.

    INV-C-7: every fontTools entry point in ``src/pdf_edit_engine/`` runs
    inside this manager. Catch list is **narrowed deliberately** per
    Skeptic-B masking-risk rebuttal:
    ``(TTLibError, AssertionError, struct.error, OSError, MemoryError,
    OverflowError)``. Programmer errors (``KeyError``, ``IndexError``,
    ``AttributeError``, ``ValueError``) propagate as-is so typos surface
    in tests rather than silently rebrand to ``FontNotFoundError``.

    Per Skeptic-A: fontTools defers parsing — the ``TTFont(BytesIO(...))``
    constructor SUCCEEDS even on a truncated ``/FontFile2``; the
    ``AssertionError`` fires later in ``getGlyphOrder()`` /
    ``getBestCmap()`` / ``glyf`` table accesses. The wrapped block must
    enclose every downstream lazy call AND ``embedded.save(buf)``
    (which can raise during fontTools serialization), not just the
    constructor.

    A forensic ``logger.error`` line preserves the original exception
    type and message for debugging even though ``{exc}`` is dropped from
    the user-visible ``FontNotFoundError`` message (R-13).

    Args:
        context: Short identifier of the call site (e.g.
            ``"_extend_simple_tier_one_five:/F1"``) — included in the log line
            to localise failures.

    Raises:
        FontNotFoundError: when any of the caught exception types fires
            inside the ``with`` block. ``__cause__`` is set to the
            original exception so the chain is preserved.
    """
    try:
        yield
    except (TTLibError, AssertionError, struct.error, OSError, MemoryError, OverflowError) as exc:
        logger.error(
            "fontTools boundary [%s]: %s: %s",
            context,
            type(exc).__name__,
            exc,
        )
        raise FontNotFoundError(f"font_extension_failed: {type(exc).__name__}") from exc


# ── Public coverage helper (used by encoding.FontResolver.can_encode) ────


def font_has_codepoint(
    font_dict: pikepdf.Object,
    codepoint: int,
) -> bool:
    """Return True iff the font's embedded /FontFile2 covers ``codepoint``.

    Used by ``encoding.FontResolver.can_encode`` to verify glyph coverage
    end-to-end (not just encoding-map membership). Encapsulates the
    fontTools dependency in this module — encoding.py must not import
    fontTools (CLAUDE.md dependency-boundary table).

    Algorithm: load the /FontFile2 via fontTools, get its best cmap,
    and check whether ``codepoint`` maps to a glyph name present in
    ``getGlyphOrder()``. Returns True (best-effort) when /FontFile2 is
    absent or unparseable so that can_encode does not regress on fonts
    where coverage cannot be verified.

    No caching: a prior implementation kept a module-global
    ``_FONTFILE2_CACHE`` keyed on ``(id(pdf), *objgen)`` to avoid
    re-parsing on every call. That cache had two latent issues —
    ``id(pdf)`` recycles across closed Pdf instances, and the cache
    populate site (FontResolverCache._make_resolver, which copied the
    indirect font_obj into a direct ``pikepdf.Dictionary`` with
    objgen=(0,0)) never matched the eviction sites in
    ``_extend_tier2`` / ``_extend_simple_tier_one_five`` (which had the
    real objgen). Phase 13.4 probes surfaced both issues. The cache
    has been functionally a no-op since pikepdf 10.5.1 (cf. ARY-349
    diagnosis), so deleting it has zero observable performance
    regression for users who shipped against 10.5.1+. A clean per-
    Pdf-instance cache may be re-introduced in a later release if
    profiling identifies this path as a hot spot.

    Args:
        font_dict: The pikepdf font dictionary or descendant CIDFont dict.
        codepoint: Unicode codepoint to check.

    Returns:
        True if the codepoint has a glyph in the embedded font binary,
        OR if /FontFile2 cannot be loaded (best-effort fallback).
    """
    try:
        # /FontDescriptor is on the font dict itself for simple fonts;
        # for Type0/CID it lives on the descendant CIDFont. Caller passes
        # whichever dict has /FontDescriptor.
        font_descriptor = font_dict.get("/FontDescriptor")
        if font_descriptor is None:
            # Try descending into Type0's DescendantFonts[0] for CID case.
            descendants = font_dict.get("/DescendantFonts")
            if descendants is not None and len(descendants) > 0:
                font_descriptor = descendants[0].get("/FontDescriptor")
        if font_descriptor is None:
            return True  # No descriptor → can't verify; best-effort True

        font_file_obj = font_descriptor.get("/FontFile2")
        if font_file_obj is None:
            # C.3: /FontFile3 CFF coverage check so can_encode stops lying
            # on CFF. A codepoint whose glyph is genuinely ABSENT from the
            # embedded CFF must return False so the extension path runs and
            # the C.3 CFF injector can supply it. Other slots: /FontFile
            # (Type1) — still out of scope, best-effort True.
            ff3 = font_descriptor.get("/FontFile3")
            if ff3 is None:
                return True
            cff_bytes = read_stream_bounded(ff3, max_decoded=MAX_FONT_STREAM_BYTES, label="font")
            return _cff_has_codepoint(cff_bytes, font_dict, codepoint)

        font_bytes = read_stream_bounded(
            font_file_obj, max_decoded=MAX_FONT_STREAM_BYTES, label="font"
        )
        with _with_fonttools_translation("font_has_codepoint"):
            tt = TTFont(io.BytesIO(font_bytes))
            try:
                best_cmap = tt.getBestCmap() or {}
                glyph_order = set(tt.getGlyphOrder())
                covered: set[int] = {cp for cp, gname in best_cmap.items() if gname in glyph_order}
                if codepoint in covered:
                    return True
                # INV-C-8: symbol / symbolic fonts often expose glyphs ONLY
                # through a (3,0) Symbol or (1,0) Macintosh cmap subtable,
                # which getBestCmap() does not scan. Fall through to those.
                return _symbol_cmap_has_codepoint(tt, codepoint, glyph_order)
            finally:
                tt.close()
    except (TTLibError, OSError, FontNotFoundError):
        # Narrowed catch (m-11): fontTools parse failures and IO errors
        # only. A1.3 / INV-W-4: also catch FontNotFoundError so a bombed
        # /FontFile2 (FontStreamTooLargeError, a FontNotFoundError subclass)
        # is best-effort True here — can_encode does not regress; the
        # downstream extension path refuses honestly via _extract_font_bytes.
        # Best-effort True so can_encode does not regress; downstream
        # extension path will still raise FontNotFoundError if the font
        # turns out to be uninjectable.
        logger.debug("font_has_codepoint: stream too large / parse failed", exc_info=True)
        return True


def _symbol_cmap_has_codepoint(
    tt: TTFont,
    codepoint: int,
    glyph_order: set[str],
) -> bool:
    """Return True iff ``codepoint`` resolves via a (3,0) or (1,0) cmap.

    Additive fallback for ``font_has_codepoint`` (INV-C-8): consulted only
    after the Unicode ``getBestCmap()`` set misses ``codepoint``, so it can
    never regress a Unicode-cmap lookup.

    Two symbolic-font conventions are handled:

    - **(3, 0) Symbol** — glyphs are stored in the U+F000-F0FF PUA range
      under the ``0xF000 | byte`` convention. A PDF byte ``c`` (0x00-0xFF) is
      looked up at ``0xF000 | c``. ``codepoint`` is matched both directly
      (when ``/ToUnicode`` already reports the PUA value, e.g. U+F041) and
      via ``0xF000 | (codepoint & 0xFF)`` (raw byte form, e.g. U+0041).
    - **(1, 0) Macintosh** — glyphs are stored at the raw byte code; matched
      at ``codepoint & 0xFF``.

    Args:
        tt: the already-loaded fontTools ``TTFont``.
        codepoint: Unicode codepoint to resolve.
        glyph_order: set of glyph names present in the font binary.

    Returns:
        True if a symbol / Mac subtable maps ``codepoint`` to a present glyph.
    """
    low_byte = codepoint & 0xFF
    cmap_table = tt.get("cmap")
    if cmap_table is None:
        return False

    symbol_sub = cmap_table.getcmap(3, 0)
    if symbol_sub is not None:
        for key in (codepoint, 0xF000 | low_byte):
            gname = symbol_sub.cmap.get(key)
            if gname is not None and gname in glyph_order:
                return True

    mac_sub = cmap_table.getcmap(1, 0)
    if mac_sub is not None:
        gname = mac_sub.cmap.get(low_byte)
        if gname is not None and gname in glyph_order:
            return True

    return False


def _load_cff_as_ttfont(font_bytes: bytes) -> tuple[TTFont, bool]:
    """Load a CFF ``/FontFile3`` binary as a ``TTFont``, wrapping bare CFF.

    A ``/FontFile3 /Type1C`` is a BARE CFF table (raw Type1C program, no
    sfnt directory) that ``TTFont(BytesIO(...))`` cannot open. A ``/FontFile3
    /OpenType`` is already sfnt-WRAPPED (magic ``b'OTTO'``) and loads
    directly. This helper unifies both into a ``TTFont`` so the C.3 injector
    can read ``["head"]`` / ``["hmtx"]`` / ``["maxp"]`` uniformly — the bare
    case is wrapped into a minimal in-memory sfnt via ``cffLib`` +
    ``FontBuilder``.

    Args:
        font_bytes: The raw ``/FontFile3`` payload (bare CFF or full sfnt).

    Returns:
        A ``(ttfont, was_bare)`` tuple. ``was_bare`` is True when the input
        was a bare CFF table that this helper wrapped — the caller re-extracts
        the bare ``CFF `` table on write-back to preserve the ``/Type1C``
        shape; a wrapped input saves the full sfnt unchanged.

    Raises:
        TTLibError: If the bytes cannot be parsed as either an sfnt font or a
            bare CFF table (rebranded to ``FontNotFoundError`` by the caller's
            ``_with_fonttools_translation`` block).
    """
    if font_bytes[:4] in _SFNT_MAGICS:
        return TTFont(io.BytesIO(font_bytes)), False

    from fontTools.cffLib import CFFFontSet  # type: ignore[import-untyped]
    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]

    cff = CFFFontSet()
    cff.decompile(io.BytesIO(font_bytes), None)
    top_dict = cff[cff.fontNames[0]]
    glyph_order = list(top_dict.charset)
    upem = 1000
    font_matrix = top_dict.rawDict.get("FontMatrix")
    if font_matrix and font_matrix[0]:
        upem = int(round(1.0 / font_matrix[0]))

    # Build a minimal sfnt shell and attach the parsed CFF table, then fill in
    # the metric/head tables the C.3 injector reads (["head"], ["hmtx"],
    # ["maxp"]). Widths come from each charstring's own advance.
    builder = FontBuilder(upem, isTTF=False)
    builder.setupGlyphOrder(glyph_order)
    cff_table = newTable("CFF ")
    cff_table.cff = cff
    builder.font["CFF "] = cff_table
    char_strings = top_dict.CharStrings
    metrics: dict[str, tuple[int, int]] = {}
    for name in glyph_order:
        char_string = char_strings[name]
        if char_string.needsDecompilation():
            char_string.decompile()
        width = getattr(char_string, "width", None)
        if width is None:
            private = getattr(char_string, "private", None)
            width = getattr(private, "defaultWidthX", upem) if private is not None else upem
        metrics[name] = (int(width) if width is not None else upem, 0)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=upem, descent=0)
    builder.setupMaxp()
    # head carries unitsPerEm, which the injector's UPEM-parity gate reads.
    # cmap/OS-2/post/name are intentionally OMITTED — the injector never reads
    # them, and FontBuilder.setupOS2 requires a cmap we do not synthesize.
    builder.setupHead(unitsPerEm=upem, created=0, modified=0)
    return builder.font, True


def _cff_has_codepoint(
    cff_bytes: bytes,
    font_dict: pikepdf.Object,
    codepoint: int,
) -> bool:
    """Return True iff the embedded CFF covers ``codepoint`` (C.3 coverage).

    Stops ``can_encode`` from lying on a CFF font: a codepoint whose glyph is
    genuinely ABSENT from the embedded CFF returns False so the extension path
    runs and the C.3 injector can supply it. The conservative goal is "False
    only when provably absent"; on any parse ambiguity the helper returns
    best-effort True so it never regresses a font whose coverage cannot be
    determined (mirrors :func:`font_has_codepoint`'s fallback contract).

    For a CID-keyed Identity-H CFF the content uses CID == GID and
    ``/ToUnicode`` owns the Unicode mapping, so coverage is resolved as: which
    CID does the font's ``/ToUnicode`` assign ``codepoint`` to, and is that
    CID's ``cidNNNNN`` glyph present in the CFF charset? A codepoint absent
    from ``/ToUnicode`` has no embedded CID → absent → False. For a name-keyed
    CFF the codepoint is mapped through the CFF's own encoding; an unresolvable
    lookup is best-effort True.

    Args:
        cff_bytes: The raw ``/FontFile3`` CFF payload (bare or sfnt-wrapped).
        font_dict: The top-level Type0 font dictionary (owns ``/ToUnicode``).
        codepoint: Unicode codepoint to check.

    Returns:
        True if the codepoint's glyph is present in the embedded CFF (or
        coverage cannot be determined); False only when provably absent.
    """
    try:
        from fontTools.cffLib import CFFFontSet

        if cff_bytes[:4] in _SFNT_MAGICS:
            tt = TTFont(io.BytesIO(cff_bytes))
            try:
                cff = tt["CFF "].cff
                top_dict = cff[cff.fontNames[0]]
                charset = list(top_dict.charset)
            finally:
                tt.close()
        else:
            cff = CFFFontSet()
            cff.decompile(io.BytesIO(cff_bytes), None)
            top_dict = cff[cff.fontNames[0]]
            charset = list(top_dict.charset)

        if hasattr(top_dict, "ROS"):
            # CID-keyed: resolve codepoint → CID via /ToUnicode, then check
            # the cidNNNNN name is in the charset.
            reverse = {ord(u): cid for cid, u in _parse_existing_tounicode(font_dict).items()}
            cid = reverse.get(codepoint)
            if cid is None:
                return False  # no embedded CID assigned this codepoint
            return f"cid{cid:05d}" in charset
        # Name-keyed CFF: best-effort True (coverage via private encoding is
        # not modelled in slice-1; never regress).
        return True
    except (TTLibError, AssertionError, struct.error, OSError, ValueError, KeyError):
        logger.debug("_cff_has_codepoint: CFF parse failed; best-effort True", exc_info=True)
        return True


def reverse_embedded_cmap(ttfont: TTFont) -> dict[int, str]:
    """Recover a CID→Unicode map by inverting the embedded font's cmap.

    Used by ``encoding._init_identity_h`` (via a function-local lazy import,
    mirroring ``font_has_codepoint``) to recover text-extraction mappings for
    Type0/Identity-H CIDFonts that ship **no** ``/ToUnicode`` CMap. Keeps the
    fontTools dependency in this module — ``encoding.py`` must not import
    fontTools (CLAUDE.md dependency-boundary table).

    Under Identity-H the descendant CIDFont's ``/CIDToGIDMap`` defaults to
    ``/Identity``, so the content-stream CID equals the glyph GID.
    ``getBestCmap()`` maps a Unicode codepoint → glyph *name*;
    ``getGlyphID(name)`` maps that name → GID. Composing the two and
    inverting yields ``CID(==GID) → Unicode char``. When two codepoints map
    to the same GID the lower codepoint wins (deterministic — real fonts
    rarely alias except for space variants).

    The CALLER is responsible for the recovery gates (Identity ``/CIDToGIDMap``
    + ``Identity-H`` encoding) and for PUA rejection / majority-PUA
    classification; this helper performs only the raw inversion so it stays a
    pure fontTools utility with no PDF-dictionary or policy knowledge.

    Args:
        ttfont: The embedded font, already loaded from ``/FontFile2``.

    Returns:
        A dict mapping CID (int) to a single-character Unicode string. Empty
        when the font exposes no usable best cmap.
    """
    best = ttfont.getBestCmap() or {}
    cid_to_unicode: dict[int, str] = {}
    for codepoint, glyph_name in best.items():
        try:
            gid = ttfont.getGlyphID(glyph_name)
        except KeyError:
            continue
        existing = cid_to_unicode.get(gid)
        if existing is None or codepoint < ord(existing):
            cid_to_unicode[gid] = chr(codepoint)
    return cid_to_unicode


def recover_cid_to_unicode_from_fontfile2(font_bytes: bytes) -> dict[int, str]:
    """Load a TrueType ``/FontFile2`` and recover its CID→Unicode map.

    Thin bytes→map adapter around :func:`reverse_embedded_cmap` so that
    ``encoding._init_identity_h`` can recover ToUnicode-absent Identity-H
    fonts via a single function-local lazy import without itself importing
    fontTools (CLAUDE.md dependency-boundary table — encoding.py keeps NO
    fontTools import). All fontTools parse/IO failures are swallowed to an
    empty dict so the caller treats the font as untextable rather than
    crashing.

    Args:
        font_bytes: Raw bytes of the embedded ``/FontFile2`` TrueType binary.

    Returns:
        The recovered ``CID(==GID) → single-char Unicode`` map, or ``{}`` on
        any parse failure or when the font exposes no usable cmap.
    """
    try:
        with _with_fonttools_translation("recover_cid_to_unicode_from_fontfile2"):
            tt = TTFont(io.BytesIO(font_bytes))
            try:
                return reverse_embedded_cmap(tt)
            finally:
                tt.close()
    except _FONT_EXTEND_FAIL_EXCS:
        return {}


# ── Private helpers ──────────────────────────────────────────────────────


def _get_font_objects(
    page: pikepdf.Page,
    font_name: str,
) -> tuple[pikepdf.Object, pikepdf.Object | None, pikepdf.Object]:
    """Extract font dict, descendant CIDFont dict, and font descriptor.

    Returns the raw pikepdf.Object references (not Dictionary copies) so that
    in-place modifications propagate back to the PDF object tree.

    Args:
        page: The pikepdf Page object.
        font_name: Font resource name (e.g., 'F1', without leading '/').

    Returns:
        Tuple of (font_dict, descendant_font_dict_or_None, font_descriptor).

    Raises:
        FontNotFoundError: If the font or font descriptor is not found.
    """
    font_key = font_name if font_name.startswith("/") else f"/{font_name}"
    resources = page.get("/Resources")
    if resources is None:
        msg = f"Font {font_name} not found in page resources"
        raise FontNotFoundError(msg)
    fonts = resources.get("/Font")
    if fonts is None or font_key not in fonts:
        msg = f"Font {font_name} not found in page resources"
        raise FontNotFoundError(msg)

    font_obj = fonts[font_key]
    subtype_obj = font_obj.get("/Subtype")
    subtype = str(subtype_obj) if subtype_obj is not None else ""

    if subtype == "/Type0":
        desc_fonts = font_obj.get("/DescendantFonts")
        if desc_fonts is None or len(list(desc_fonts)) == 0:  # type: ignore[call-overload]
            msg = f"Type0 font {font_name} has no DescendantFonts"
            raise FontNotFoundError(msg)
        cid_font = desc_fonts[0]
        fd_obj = cid_font.get("/FontDescriptor")
        if fd_obj is None:
            msg = f"CIDFont for {font_name} has no FontDescriptor"
            raise FontNotFoundError(msg)
        return font_obj, cid_font, fd_obj

    # Simple font (TrueType, Type1)
    fd_obj = font_obj.get("/FontDescriptor")
    if fd_obj is None:
        msg = f"Font {font_name} has no FontDescriptor"
        raise FontNotFoundError(msg)
    return font_obj, None, fd_obj


def _classify_outline_table(
    ttfont: TTFont,
) -> Literal["glyf", "cff", "cff2", "unknown"]:
    """Classify a loaded font by the outline table it ACTUALLY carries.

    Pure function of the loaded ``TTFont`` — no PDF/pikepdf objects, no
    I/O. Sniffs the sfnt tables present rather than trusting the
    ``/FontFile2`` vs ``/FontFile3`` slot the binary was embedded under.
    This is the root of the C.1 dispatch split (INV-C-9): a ``/FontFile3``
    may carry CFF (Type1C), CFF2, OR ``glyf`` (OpenType-with-TrueType-
    outlines), and only the real table decides whether Tier 1.5 glyph
    injection (which requires ``glyf``) is applicable.

    Glyf is checked FIRST so an OpenType binary that carries ``glyf``
    classifies as ``glyf`` regardless of slot. ``CFF2`` precedes ``CFF``
    because a font carrying both is CFF2-primary. Note the trailing space
    in the sfnt tag ``"CFF "``.

    Args:
        ttfont: A loaded ``fontTools.ttLib.TTFont``.

    Returns:
        ``"glyf"``, ``"cff"``, ``"cff2"``, or ``"unknown"``.
    """
    if "glyf" in ttfont:
        return "glyf"
    if "CFF2" in ttfont:
        return "cff2"
    if "CFF " in ttfont:
        return "cff"
    return "unknown"


def classify_embedded_outline(fd: pikepdf.Object) -> str:
    """Return the truthful ``embedded_type`` label for a font descriptor.

    Single truth source for ``FontInfo.embedded_type`` (INV-C-9): loads
    the embedded binary via :func:`_extract_font_bytes`, loads a
    ``TTFont`` INSIDE this module under
    :func:`_with_fonttools_translation`, classifies via the pure
    :func:`_classify_outline_table`, and maps the table kind to the
    ``embedded_type`` string. Both producers — this helper (used by
    ``locator._detect_embedded_type``) and :func:`_extract_font_bytes`
    (used on the extension path) — route through the SAME classifier so
    they can never disagree.

    The fontTools dependency lives here, NOT in ``locator`` (CLAUDE.md
    dependency-boundary table): ``locator`` calls this helper rather than
    importing fontTools for outline-table logic.

    Mapping: ``glyf`` →``"TrueType"`` (slot-agnostic; covers
    OpenType-with-glyf, which gets the honest ``"opentype-glyf"`` label
    only when sourced from ``/FontFile3``), ``cff`` →``"CFF"``, ``cff2``
    →``"cff2"``. ``/FontFile`` (Type1) short-circuits to ``"Type1"``
    without a ``TTFont`` load. On a load/classification failure the SLOT
    label is returned as a best-effort fallback so callers never crash on
    a malformed binary.

    Args:
        fd: The /FontDescriptor dictionary.

    Returns:
        The truthful ``embedded_type`` string.

    Raises:
        FontNotFoundError: If no embedded font stream is present.
    """
    # Type1 (/FontFile) has no sfnt table directory; do not attempt a
    # TTFont load. Match the historical slot label.
    if "/FontFile2" not in fd and "/FontFile3" not in fd:
        if "/FontFile" in fd:
            return "Type1"
        msg = "No embedded font stream (FontFile/FontFile2/FontFile3) found"
        raise FontNotFoundError(msg)

    slot_is_fontfile3 = "/FontFile2" not in fd
    slot_label = "CFF" if slot_is_fontfile3 else "TrueType"
    try:
        if "/FontFile2" in fd:
            font_bytes = read_stream_bounded(
                fd["/FontFile2"], max_decoded=MAX_FONT_STREAM_BYTES, label="font"
            )
        else:
            font_bytes = read_stream_bounded(
                fd["/FontFile3"], max_decoded=MAX_FONT_STREAM_BYTES, label="font"
            )
    except FontStreamTooLargeError:
        # A1.3 / INV-W-4: a bombed embedded stream cannot be classified;
        # mirror the best-effort slot-label fallback used below on a
        # fontTools load failure.
        return slot_label

    try:
        with _with_fonttools_translation("classify_embedded_outline"):
            tt = TTFont(io.BytesIO(font_bytes))
            try:
                kind = _classify_outline_table(tt)
            finally:
                tt.close()
    except FontNotFoundError:
        # _with_fonttools_translation rebrands fontTools load failures to
        # FontNotFoundError. Honour the best-effort contract: fall back to
        # the slot label rather than escalating a metadata read.
        return slot_label

    if kind == "glyf":
        # glyf-in-/FontFile3 is OpenType-with-TrueType-outlines — surface
        # the honest distinct label; glyf-in-/FontFile2 is plain TrueType.
        return "opentype-glyf" if slot_is_fontfile3 else "TrueType"
    if kind == "cff":
        return "CFF"
    if kind == "cff2":
        return "cff2"
    return slot_label


def _extract_font_bytes(fd: pikepdf.Object) -> tuple[bytes, str]:
    """Extract embedded font binary and determine embedded type.

    The ``embedded_type`` is derived from the outline table the binary
    ACTUALLY carries (via :func:`classify_embedded_outline`), NOT from the
    ``/FontFile2`` vs ``/FontFile3`` slot — so a ``/FontFile3`` that is
    truly CFF reports ``"CFF"`` while one carrying ``glyf`` reports
    ``"opentype-glyf"`` (INV-C-9).

    Args:
        fd: Font descriptor dictionary.

    Returns:
        Tuple of (font_bytes, embedded_type).

    Raises:
        FontNotFoundError: If no embedded font stream is found.
    """
    if "/FontFile2" in fd:
        return (
            read_stream_bounded(fd["/FontFile2"], max_decoded=MAX_FONT_STREAM_BYTES, label="font"),
            classify_embedded_outline(fd),
        )
    if "/FontFile3" in fd:
        return (
            read_stream_bounded(fd["/FontFile3"], max_decoded=MAX_FONT_STREAM_BYTES, label="font"),
            classify_embedded_outline(fd),
        )
    if "/FontFile" in fd:
        return (
            read_stream_bounded(fd["/FontFile"], max_decoded=MAX_FONT_STREAM_BYTES, label="font"),
            "Type1",
        )
    msg = "No embedded font stream (FontFile/FontFile2/FontFile3) found"
    raise FontNotFoundError(msg)


def _introspect_embedded_font(font_bytes: bytes, outline_kind: str) -> int:
    """Return the truthful glyph count of an embedded font binary.

    Best-effort, dispatch-on-outline-kind introspection (INV-C-10). Parses
    the embedded binary per outline type and returns the real glyph count:

    - ``glyf`` / ``TrueType`` / ``opentype-glyf`` →
      ``len(TTFont.getGlyphOrder())`` (a bare ``glyf`` always carries an
      sfnt directory).
    - ``cff`` / ``CFF`` / ``cff2`` → the embedded CFF can arrive in EITHER of
      two shapes: an sfnt-WRAPPED OpenType-CFF/CFF2 (``/FontFile3 /OpenType``,
      magic ``b'OTTO'`` — the DOMINANT real-world shape) OR a BARE CFF table
      (``/FontFile3 /Type1C``, raw Type1C program, no sfnt directory). The
      sfnt-wrapped case is read via ``len(TTFont.getGlyphOrder())`` (``TTFont``
      reads OpenType-CFF/CFF2 fine); only the bare-table case falls back to
      ``len(cffLib.CFFFontSet[...].CharStrings)`` (``CharStrings``, not
      ``.charset``, because CFF2 has no named charset). Calling
      ``CFFFontSet.decompile`` on a raw sfnt stream raises ``AssertionError``,
      so the ``TTFont``-first order is load-bearing.
    - ``Type1`` (``/FontFile``) → ``len(t1Lib.T1Font.font['CharStrings'])``.
      ``t1Lib.T1Font`` only reads from a PATH (its ``__init__`` requires a
      positional ``path`` and has no in-memory byte entry point), so the bytes
      are written to a temp file, parsed, and the temp file is removed.
    - anything else (``unknown``) → 0.

    CRITICAL (INV-C-10 dep-boundary firewall): this helper does NOT route its
    parse through :func:`_with_fonttools_translation` (that manager RAISES
    ``FontNotFoundError`` on a parse failure). Each branch wraps its parser in
    a local broad-but-bounded ``try``/``except`` and returns 0 on ANY failure,
    so a present-but-unparseable binary yields 0 (unknown) rather than raising.
    The public :func:`embedded_glyph_count` is the dependency-boundary
    firewall ``locator`` imports — never cffLib/t1Lib/TTFont directly.

    No caching (mirrors :func:`font_has_codepoint`'s no-cache decision).

    Args:
        font_bytes: The raw embedded font binary.
        outline_kind: The outline-table label from
            :func:`classify_embedded_outline` (or :func:`_extract_font_bytes`).

    Returns:
        The truthful glyph count, or 0 when the binary is absent/unparseable
        or the outline kind is unknown. Never raises on a read.
    """
    # Bounded-but-broad failure tuple. Mirrors the `except Exception` pattern
    # already used in locator._build_font_info's TTFont load — a present-but-
    # corrupt binary must yield 0, not propagate.
    _read_fail = (
        TTLibError,
        AssertionError,
        struct.error,
        OSError,
        ValueError,
        KeyError,
        IndexError,
        MemoryError,
        OverflowError,
        Exception,
    )

    if outline_kind in {"glyf", "TrueType", "opentype-glyf"}:
        try:
            tt = TTFont(io.BytesIO(font_bytes))
            try:
                return len(tt.getGlyphOrder())
            finally:
                tt.close()
        except _read_fail:
            logger.debug("_introspect_embedded_font: glyf parse failed", exc_info=True)
            return 0

    if outline_kind in {"cff", "CFF", "cff2"}:
        # The CFF can be sfnt-WRAPPED (OpenType-CFF/CFF2 — magic OTTO/0001/true/
        # ttcf, the dominant real-world shape) OR a BARE CFF table (raw Type1C
        # program, no sfnt directory). TTFont reads the sfnt-wrapped case fine;
        # CFFFontSet.decompile only reads the bare table (it raises
        # AssertionError on a raw sfnt stream). Try TTFont FIRST, fall back to
        # the bare-table reader.
        if font_bytes[:4] in _SFNT_MAGICS:
            try:
                tt = TTFont(io.BytesIO(font_bytes))
                try:
                    return len(tt.getGlyphOrder())
                finally:
                    tt.close()
            except _read_fail:
                logger.debug("_introspect_embedded_font: sfnt-CFF parse failed", exc_info=True)
                return 0
        try:
            from fontTools.cffLib import CFFFontSet

            cff = CFFFontSet()
            cff.decompile(io.BytesIO(font_bytes), None)
            top_dict = cff[cff.fontNames[0]]
            return len(top_dict.CharStrings)
        except _read_fail:
            logger.debug("_introspect_embedded_font: bare-CFF parse failed", exc_info=True)
            return 0

    if outline_kind == "Type1":
        # t1Lib.T1Font reads ONLY from a path (its __init__ requires a
        # positional `path` and offers no in-memory byte entry point). The
        # prior no-arg `t1Lib.T1Font()` raised TypeError before any parse, so
        # this branch was dead code that always returned 0. Write the embedded
        # bytes to a temp file, parse, and remove the file.
        tmp_path: str | None = None
        try:
            from fontTools import t1Lib  # type: ignore[import-untyped]

            fd, tmp_path = tempfile.mkstemp(suffix=".pfa")
            os.close(fd)
            with open(tmp_path, "wb") as handle:
                handle.write(font_bytes)
            t1 = t1Lib.T1Font(tmp_path)
            t1.parse()
            return len(t1.font["CharStrings"])
        except _read_fail:
            logger.debug("_introspect_embedded_font: Type1 parse failed", exc_info=True)
            return 0
        finally:
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

    return 0


def embedded_glyph_count(fd: pikepdf.Object) -> int:
    """Return the truthful embedded glyph count for a font descriptor.

    Dependency-boundary-safe route ``locator.get_fonts`` uses to introspect
    the real glyph count WITHOUT importing fontTools/cffLib/t1Lib (CLAUDE.md
    dependency-boundary table). Best-effort: NEVER raises on a present stream
    — returns 0 when the binary is absent, the classifier fails, or the
    parser cannot read it.

    Reuses the C.1 classifier (:func:`classify_embedded_outline`, already
    best-effort) for the outline-kind dispatch, reads the raw bytes from
    whichever of ``/FontFile2`` / ``/FontFile3`` / ``/FontFile`` is present,
    and delegates the per-kind parse to :func:`_introspect_embedded_font`.

    Args:
        fd: The /FontDescriptor dictionary.

    Returns:
        The truthful glyph count, or 0 when no embedded stream is present or
        the binary cannot be parsed.
    """
    try:
        if "/FontFile2" in fd:
            font_bytes = read_stream_bounded(
                fd["/FontFile2"], max_decoded=MAX_FONT_STREAM_BYTES, label="font"
            )
        elif "/FontFile3" in fd:
            font_bytes = read_stream_bounded(
                fd["/FontFile3"], max_decoded=MAX_FONT_STREAM_BYTES, label="font"
            )
        elif "/FontFile" in fd:
            font_bytes = read_stream_bounded(
                fd["/FontFile"], max_decoded=MAX_FONT_STREAM_BYTES, label="font"
            )
        else:
            return 0
        outline_kind = classify_embedded_outline(fd)
    except (FontNotFoundError, TTLibError, OSError, ValueError, KeyError):
        # A1.3 / INV-W-4: FontStreamTooLargeError is a FontNotFoundError
        # subclass, so a bombed stream is caught here -> glyph_count 0
        # (unknown) and locator surfaces font_subset_introspection_failed.
        return 0
    return _introspect_embedded_font(font_bytes, outline_kind)


def _parse_existing_tounicode(
    font_dict: pikepdf.Object,
) -> dict[int, str]:
    """Parse existing ToUnicode CMap into CID → Unicode mapping.

    Args:
        font_dict: The top-level font dictionary (Type0).

    Returns:
        Dict mapping CID (int) to Unicode string.
    """
    if "/ToUnicode" not in font_dict:
        return {}
    tu_bytes: bytes = read_stream_bounded(
        font_dict["/ToUnicode"], max_decoded=MAX_TOUNICODE_BYTES, label="ToUnicode"
    )
    cmap = FileUnicodeMap()
    CMapParser(cmap, io.BytesIO(tu_bytes)).run()
    return dict(cmap.cid2unichr)


def _append_to_unicode_cmap(
    font_dict: pikepdf.Object,
    new_mappings: dict[int, str],
    pdf: pikepdf.Pdf,
) -> None:
    """Append new CID→Unicode entries to the ToUnicode CMap stream.

    Deduplicates against existing CIDs: entries whose CID is already
    mapped in the current CMap with the same Unicode value are silently
    skipped. Prevents O(n × extensions) on-disk bloat from repeated
    ``extend_subset`` calls on the same font.

    Adds a new bfchar block before ``endcmap`` — does NOT splice into
    existing blocks (avoids fragile CMap parsing).

    Args:
        font_dict: The top-level font dictionary containing /ToUnicode.
        new_mappings: Dict of {CID: unicode_char_string} to add.
        pdf: The open PDF for creating the new stream.
    """
    if not new_mappings:
        return

    # Dedup: drop entries whose CID is already mapped to the same value.
    # Preserves existing mappings when the caller passes duplicates;
    # allows legitimate overrides (CID mapped to a different char).
    existing = _parse_existing_tounicode(font_dict)
    deduped = {cid: ustr for cid, ustr in new_mappings.items() if existing.get(cid) != ustr}
    if not deduped:
        return

    raw = read_stream_bounded(
        font_dict["/ToUnicode"], max_decoded=MAX_TOUNICODE_BYTES, label="ToUnicode"
    ).decode("latin-1")
    endcmap_pos = raw.rfind("endcmap")
    if endcmap_pos < 0:
        logger.warning("ToUnicode CMap has no 'endcmap' marker; cannot append")
        return

    # Build bfchar block(s), max 100 entries per block per PDF spec
    entries = list(deduped.items())
    blocks: list[str] = []
    for chunk_start in range(0, len(entries), 100):
        chunk = entries[chunk_start : chunk_start + 100]
        lines = [f"{len(chunk)} beginbfchar"]
        for cid, ustr in chunk:
            cid_hex = f"<{cid:04X}>"
            uni_hex = "<" + "".join(f"{ord(ch):04X}" for ch in ustr) + ">"
            lines.append(f"{cid_hex} {uni_hex}")
        lines.append("endbfchar")
        blocks.append("\n".join(lines))

    insert = "\n".join(blocks) + "\n"
    new_cmap = raw[:endcmap_pos] + insert + raw[endcmap_pos:]
    font_dict["/ToUnicode"] = pdf.make_stream(new_cmap.encode("latin-1"))


def _append_w_entries(
    cid_font: pikepdf.Object,
    new_widths: dict[int, float],
) -> None:
    """Append new CID width entries to the /W array.

    Deduplicates against existing entries: CIDs already present with
    the same width are silently skipped. Prevents /W array bloat from
    repeated ``extend_subset`` calls.

    Args:
        cid_font: The CIDFont dictionary containing /W.
        new_widths: Dict of {CID: width_in_font_units} to add.
    """
    if not new_widths:
        return

    from pdf_edit_engine.widths import parse_cid_widths

    existing_widths = parse_cid_widths(pikepdf.Dictionary(cid_font))  # type: ignore[arg-type]
    deduped = {cid: w for cid, w in new_widths.items() if existing_widths.get(cid) != w}
    if not deduped:
        return

    existing: list[object] = []
    if "/W" in cid_font:
        existing = list(cid_font["/W"])  # type: ignore[call-overload]

    for cid, width in sorted(deduped.items()):
        existing.append(cid)
        existing.append(pikepdf.Array([width]))

    cid_font["/W"] = pikepdf.Array(existing)


def _update_cid_to_gid_map(
    cid_font: pikepdf.Object,
    new_mappings: dict[int, int],
    pdf: pikepdf.Pdf,
) -> None:
    """Update CIDToGIDMap stream with new CID→GID entries.

    For Identity-H fonts with CIDToGIDMap = /Identity (a Name), no update is
    needed. Only updates when CIDToGIDMap is an explicit binary stream.

    Args:
        cid_font: The CIDFont dictionary containing /CIDToGIDMap.
        new_mappings: Dict of {CID: GID} to add.
        pdf: The open PDF for creating the updated stream.
    """
    if not new_mappings:
        return
    cidtogidmap = cid_font.get("/CIDToGIDMap")
    if cidtogidmap is None or isinstance(cidtogidmap, pikepdf.Name):
        return  # /Identity or absent — implicit identity mapping

    # Explicit stream — update the binary CID→GID table
    data = bytearray(
        read_stream_bounded(cidtogidmap, max_decoded=MAX_TOUNICODE_BYTES, label="CIDToGIDMap")
    )

    for cid, gid in new_mappings.items():
        offset = cid * 2
        if offset + 2 > len(data):
            data.extend(b"\x00" * (offset + 2 - len(data)))
        data[offset] = (gid >> 8) & 0xFF
        data[offset + 1] = gid & 0xFF

    cid_font[pikepdf.Name("/CIDToGIDMap")] = pdf.make_stream(bytes(data))
    logger.info("Updated CIDToGIDMap stream with %d new entries", len(new_mappings))


def _strip_glyph_hinting(glyph: object) -> None:
    """Replace a glyph's TrueType hinting program with an empty program.

    Injected glyphs from a system font carry hinting bytecode that
    references the source font's fpgm/prep/cvt tables. Those tables
    are not in the destination (embedded) font, so the hinting would
    fail at render time. Stripping the hinting produces an unhinted
    glyph that renders correctly at typical text sizes (9pt+).

    Args:
        glyph: A fontTools Glyph object (simple or composite).
    """
    from fontTools.ttLib.tables import ttProgram  # type: ignore[import-untyped]

    empty = ttProgram.Program()
    empty.fromBytecode(b"")
    if hasattr(glyph, "program"):
        glyph.program = empty


def _collect_component_names(
    glyph: object,
    font: TTFont,
    _seen: set[str] | None = None,
    _depth: int = 0,
) -> list[str]:
    """Recursively enumerate component glyph names for a composite glyph.

    Composite TrueType glyphs (common for accented Latin) reference child
    glyphs by name. To inject a composite into a new font, the child
    glyphs must also be present. Walks the composite graph and returns
    every referenced component name in injection order (leaves first,
    roots last). Returns an empty list for simple (non-composite) glyphs.

    Recursion is bounded by ``MAX_COMPOSITE_DEPTH`` (INV-W0-8 / F-B-03).
    A pathological or adversarial composite chain deeper than the cap
    raises ``FontNotFoundError`` rather than ``RecursionError``, so the
    failure routes through the existing ``_FONT_EXTEND_FAIL_EXCS`` catch
    at the caller and surfaces as a ``font_extension_failed`` Degradation.

    Args:
        glyph: A fontTools Glyph object.
        font: The TTFont the glyph belongs to (for recursive lookups).
        _seen: Internal visited set to prevent cycles.
        _depth: Internal recursion depth counter; bounded by
            ``MAX_COMPOSITE_DEPTH``.

    Returns:
        Deduplicated list of component glyph names in injection order.

    Raises:
        FontNotFoundError: If the composite glyph chain depth exceeds
            ``MAX_COMPOSITE_DEPTH`` (INV-W0-8).
    """
    if _seen is None:
        _seen = set()
    if _depth > MAX_COMPOSITE_DEPTH:
        raise FontNotFoundError(
            f"composite glyph depth exceeds {MAX_COMPOSITE_DEPTH} "
            f"for {getattr(glyph, 'name', '<unnamed>')}"
        )
    if not hasattr(glyph, "isComposite") or not glyph.isComposite():
        return []
    order: list[str] = []
    for component in glyph.components:  # type: ignore[attr-defined]
        name = component.glyphName
        if name in _seen:
            continue
        _seen.add(name)
        if name in font["glyf"].glyphs:
            sub = font["glyf"][name]
            order.extend(_collect_component_names(sub, font, _seen, _depth + 1))
        order.append(name)
    return order


def _pad_glyph_order(embedded: TTFont, target_length: int) -> None:
    """Pad ``embedded.glyphOrder`` up to ``target_length`` with empty glyphs.

    Used by Tier 1.5 when the font's ToUnicode CMap references CIDs
    beyond the current glyph order length. Padding each slot with an
    empty simple glyph (zero contours, zero advance) preserves the
    slot numbering so subsequent injections land at safe, unused GIDs
    without colliding with existing CMap CIDs.

    Each padding slot gets a unique glyph name so fontTools does not
    alias multiple GIDs to the same glyph.

    Args:
        embedded: Destination TTFont.
        target_length: Desired glyph order length after padding.
            If the current length is already >= target_length, this
            is a no-op.
    """
    from fontTools.ttLib.tables._g_l_y_f import Glyph  # type: ignore[import-untyped]

    current = len(embedded.getGlyphOrder())
    for gid in range(current, target_length):
        placeholder = f"_ary278_pad_{gid:05X}"
        empty_glyph = Glyph()
        empty_glyph.numberOfContours = 0
        embedded["glyf"][placeholder] = empty_glyph
        embedded["hmtx"][placeholder] = (0, 0)
    embedded["maxp"].numGlyphs = len(embedded.getGlyphOrder())


def _append_glyph_to_font(
    embedded: TTFont,
    system: TTFont,
    glyph_name: str,
) -> int:
    """Append a single glyph (by name) from system to embedded at a fresh GID.

    Low-level helper for ``_inject_glyph_in_place``. Assumes both fonts
    are TrueType with compatible upem (caller validates). Updates glyf,
    hmtx, glyph order, and maxp.numGlyphs. Strips hinting from the
    injected copy so it does not reference the source font's
    fpgm/prep/cvt tables (which are not present in the destination).

    Args:
        embedded: Destination TTFont.
        system: Source TTFont.
        glyph_name: Glyph name to copy (must exist in system["glyf"]).

    Returns:
        The new GID assigned in embedded.
    """
    import copy

    system_glyph = system["glyf"][glyph_name]
    new_glyph = copy.deepcopy(system_glyph)
    _strip_glyph_hinting(new_glyph)

    # Assigning to glyf[name] auto-appends the name to the glyph order
    # when the name is new. hmtx assignment does not.
    embedded["glyf"][glyph_name] = new_glyph

    advance, lsb = system["hmtx"][glyph_name]
    embedded["hmtx"][glyph_name] = (advance, lsb)

    order = list(embedded.getGlyphOrder())
    embedded["maxp"].numGlyphs = len(order)
    return order.index(glyph_name)


def _inject_glyph_in_place(
    embedded: TTFont,
    system: TTFont,
    ch: str,
) -> int:
    """Append a system-font glyph into an embedded TTFont at a new GID.

    Copies the glyph outline and hmtx entry for a single Unicode
    character ``ch`` from ``system`` into ``embedded``. For composite
    glyphs, recursively injects component glyphs first. Strips hinting
    bytecode from injected glyphs.

    Updates the embedded font's ``glyf``, ``hmtx``, internal ``cmap``,
    ``glyph order``, and ``maxp.numGlyphs``. Does NOT modify the PDF
    font dictionary or ToUnicode/W — that is the caller's responsibility.

    Args:
        embedded: Destination TTFont (the embedded subset from /FontFile2).
        system: Source TTFont (full system font).
        ch: Single Unicode character to inject (e.g., "Z" or "é").

    Returns:
        The new GID assigned to the injected glyph in ``embedded``.

    Raises:
        FontNotFoundError: If the character is absent from ``system``,
            if the fonts have mismatched ``unitsPerEm``, if ``embedded``
            is not TrueType (``glyf`` table missing), or if a composite
            component is missing from both fonts.
    """
    if "glyf" not in embedded:
        raise FontNotFoundError(
            "embedded font is not TrueType (no glyf table); "
            "Tier 1.5 requires TrueType — CFF not supported"
        )
    if "glyf" not in system:
        raise FontNotFoundError(
            "system font is not TrueType (no glyf table); Tier 1.5 requires TrueType"
        )
    embedded_upem = embedded["head"].unitsPerEm
    system_upem = system["head"].unitsPerEm
    if embedded_upem != system_upem:
        raise FontNotFoundError(
            f"unitsPerEm mismatch: embedded={embedded_upem}, "
            f"system={system_upem}. Tier 1.5 does not rescale outlines."
        )

    system_cmap = system.getBestCmap() or {}
    cp = ord(ch)
    if cp not in system_cmap:
        raise FontNotFoundError(f"character {ch!r} (U+{cp:04X}) not in system font cmap")
    glyph_name = system_cmap[cp]
    system_glyph = system["glyf"][glyph_name]

    # Recursively inject composite components (leaves first)
    components = _collect_component_names(system_glyph, system)
    for comp_name in components:
        if comp_name in list(embedded.getGlyphOrder()):
            continue
        if comp_name not in system["glyf"].glyphs:
            raise FontNotFoundError(f"composite component {comp_name!r} missing from system font")
        _append_glyph_to_font(embedded, system, comp_name)

    # Inject the top-level glyph (caller ensures it is not already present)
    if glyph_name in list(embedded.getGlyphOrder()):
        # Glyph name already in embedded font; just update cmap below
        new_gid = list(embedded.getGlyphOrder()).index(glyph_name)
    else:
        new_gid = _append_glyph_to_font(embedded, system, glyph_name)

    # Update embedded cmap: Unicode -> glyph name (BMP Unicode table preferred)
    updated = False
    for table in embedded["cmap"].tables:
        if table.platformID == 3 and table.platEncID == 1:
            table.cmap[cp] = glyph_name
            updated = True
            break
    if not updated:
        for table in embedded["cmap"].tables:
            if table.isUnicode():
                table.cmap[cp] = glyph_name
                updated = True
                break

    return new_gid


def _cff_charstring_is_composite(charstring: object) -> bool:
    """Return True iff a Type2 charstring is a seac/composite glyph.

    Pure predicate. The slice-1 CFF injector (:func:`_inject_cff_glyph_in_place`)
    supports only single, non-composite outlines. The legacy Type2 ``seac``
    accent-composition form encodes a composite as ``adx ady bchar achar
    endchar`` — an ``endchar`` operator preceded by exactly four numeric
    operands. A simple glyph's ``endchar`` has zero (or, with a trailing
    width, one) preceding numeric operand. This decompiles the charstring and
    scans its program for that 4-arg ``endchar`` form.

    Args:
        charstring: A ``fontTools.misc.psCharStrings.T2CharString``.

    Returns:
        True if the charstring is a seac composite (must be refused).
    """
    if charstring.needsDecompilation():  # type: ignore[attr-defined]
        charstring.decompile()  # type: ignore[attr-defined]
    program = charstring.program  # type: ignore[attr-defined]
    pending_args = 0
    for token in program:
        if isinstance(token, (int, float)):
            pending_args += 1
            continue
        if token == "endchar":
            return pending_args >= 4
        pending_args = 0
    return False


def _cff_cid_from_charset_name(name: str) -> int | None:
    """Parse the integer CID encoded by a CID-keyed CFF charset glyph name.

    Pure helper for the collision-free CID selection in
    :func:`_inject_cff_glyph_in_place`. A CID-keyed CFF charset entry is the
    string ``cid{N:05d}`` (fontTools' ``getCIDfromName`` does ``int(name[3:])``
    when serializing), so the integer CID a pre-existing glyph occupies can be
    recovered from its name. The ``.notdef`` sentinel (CID 0, GID 0) and any
    other non-``cid`` name carry no parseable CID.

    Args:
        name: A charset glyph name (e.g. ``"cid00003"`` or ``".notdef"``).

    Returns:
        The integer CID, or ``None`` for ``.notdef`` / any non-``cidNNNNN`` name.
    """
    if not name.startswith("cid"):
        return None
    try:
        return int(name[3:])
    except ValueError:
        return None


def _inject_cff_glyph_in_place(
    embedded: TTFont,
    system: TTFont,
    ch: str,
    *,
    min_cid: int = 0,
) -> int:
    """Append a donor CFF glyph into an embedded CID-keyed CFF at a new GID.

    The CFF (Type2 charstring) sibling of :func:`_inject_glyph_in_place`
    (which handles ``glyf`` outlines). Adapts the proven spike idiom
    ``experiments/v020_c3_cff_spike/probe_verify.py``: the donor outline is
    DRAWN into the embedded font's CFF context via a ``T2CharStringPen`` bound
    to the embedded private + global subrs (so donor subroutine indices are
    flattened, never carried as dangling references), appended at a fresh
    COLLISION-FREE ``CID == GID`` at the additive tail (INV-C-11; pre-existing
    CIDs untouched, the ARY-278 no-renumber discipline for CFF — INV-C-12), and
    the CID-keyed ``charset`` / ``FDSelect`` / ``CIDCount`` / glyph order /
    ``maxp`` / hmtx are maintained. Does NOT modify the PDF font dictionary or
    ToUnicode/W — that is the caller's responsibility (:func:`_extend_cff_tier`).

    **Collision-free CID selection (INV-C-12).** This mirrors the ``glyf`` Tier
    1.5 body (``_extend_tier2`` → :func:`_pad_glyph_order`): a real subsetted
    CID-keyed CFF keeps its ORIGINAL non-contiguous CIDs in the charset while
    packing GIDs densely, so a pre-existing CID can be ``>= len(glyphOrder)``
    (e.g. ``B`` at CID 3 / GID 2 in a 3-glyph font). Picking
    ``new_cid = len(glyphOrder)`` blindly would COLLIDE with that pre-existing
    CID and silently corrupt unrelated text (the ARY-278 "1ova,ndustries"
    failure ported to CFF). Instead ``new_cid`` is taken above BOTH the largest
    integer CID the embedded ``charset`` encodes AND the caller-supplied
    ``min_cid`` floor (the largest ``/ToUnicode`` CID, threaded by
    :func:`_extend_cff_tier`): ``new_cid = max(len(glyphOrder),
    max_existing_cid + 1, min_cid)``. Under Identity-H (``/CIDToGIDMap
    /Identity``) ``CID == GID`` is REQUIRED, so the real glyph is placed at
    ``GID == new_cid`` by padding GIDs ``[len(glyphOrder), new_cid)`` with empty
    placeholder glyphs (``.notdef``-equivalent, ``cidNNNNN`` names at FREE CIDs,
    ``(0, 0)`` hmtx, FDSelect → FD 0) — the CFF analogue of
    :func:`_pad_glyph_order`. Pre-existing charstrings / charset entries stay
    byte-identical (the injection is append-only beyond the pad).

    Slice-1 scope is enforced by hard refusals (each a ``FontNotFoundError``,
    in ``_FONT_EXTEND_FAIL_EXCS``): non-CFF embedded/donor, non-CID-keyed
    (no ``ROS``) embedded, multi-FD embedded, ``unitsPerEm`` mismatch, donor
    char absent from the donor cmap, and seac/composite donor glyph. Every
    out-of-scope shape REFUSES rather than producing a wrong glyph (INV-C-13).

    Args:
        embedded: Destination ``TTFont`` (the embedded CID-keyed CFF).
        system: Source/donor ``TTFont`` (a non-composite CFF of matching upem).
        ch: Single Unicode character to inject.
        min_cid: Lower bound (exclusive of collisions) the new CID must clear,
            in addition to the embedded charset's own CIDs. The caller
            (:func:`_extend_cff_tier`) passes ``max(/ToUnicode CIDs) + 1`` so a
            CID referenced only by the PDF-level CMap (not yet by the embedded
            charset) cannot be re-used either. Defaults to 0 (charset-only).

    Returns:
        The new GID (== new collision-free CID) assigned to the injected glyph.

    Raises:
        FontNotFoundError: On any out-of-scope shape (see slice-1 scope above).
    """
    from fontTools.misc.psCharStrings import T2CharString  # type: ignore[import-untyped]
    from fontTools.pens.t2CharStringPen import T2CharStringPen  # type: ignore[import-untyped]

    # 1. Outline-table gate: both embedded and donor must be CFF.
    if "CFF " not in embedded:
        raise FontNotFoundError(
            "embedded font is not CFF; CFF injection requires a CFF embedded font"
        )
    if "CFF " not in system:
        raise FontNotFoundError(
            "CFF injection requires a CFF donor; got a non-CFF (TrueType/glyf) donor"
        )

    # 2. Bind TopDicts.
    e_cff = embedded["CFF "].cff
    e_td = e_cff[e_cff.fontNames[0]]
    s_cff = system["CFF "].cff
    s_td = s_cff[s_cff.fontNames[0]]

    # 3. CID-keyed gate + single-FD gate.
    # "slice-1" = the v0.2.0 C.3 CFF-injection scope; kept out of user-facing
    # strings (B10) — internal scope jargon belongs in comments, not messages.
    if not hasattr(e_td, "ROS"):
        raise FontNotFoundError("CFF injection requires a CID-keyed (ROS) embedded font")
    if len(e_td.FDArray) != 1:
        raise FontNotFoundError("multi-FD CID CFF is unsupported")

    # 4. unitsPerEm parity (no outline rescaling in slice-1).
    embedded_upem = embedded["head"].unitsPerEm
    system_upem = system["head"].unitsPerEm
    if embedded_upem != system_upem:
        raise FontNotFoundError(
            f"unitsPerEm mismatch: embedded={embedded_upem}, "
            f"system={system_upem}. CFF injection does not rescale outlines."
        )

    # 5. Resolve donor glyph + reject composite/seac.
    cp = ord(ch)
    s_cmap = system.getBestCmap() or {}
    if cp not in s_cmap:
        raise FontNotFoundError(f"character {ch!r} (U+{cp:04X}) not in donor cmap")
    donor_name = s_cmap[cp]
    if _cff_charstring_is_composite(s_td.CharStrings[donor_name]):
        raise FontNotFoundError("CFF composite/seac glyph injection is unsupported")

    # 6. Collision-free placement: CID == GID at the additive tail, above BOTH
    #    the embedded charset's own CIDs and the caller's /ToUnicode floor
    #    (min_cid). A sparse subsetted CID CFF keeps non-contiguous CIDs (e.g.
    #    B at CID 3 / GID 2 in a 3-glyph font), so new_cid == len(glyphOrder)
    #    would collide. Mirror the glyf _pad_glyph_order discipline.
    glyph_order = list(embedded.getGlyphOrder())
    used_cids: set[int] = {0}  # CID 0 is reserved for .notdef (GID 0).
    for name in e_td.charset:
        cid = _cff_cid_from_charset_name(name)
        if cid is not None:
            used_cids.add(cid)
    max_existing_cid = max(used_cids)
    new_cid = max(len(glyph_order), max_existing_cid + 1, min_cid)
    new_gid = new_cid
    new_name = f"cid{new_cid:05d}"
    used_cids.add(new_cid)

    # 6b. Force the embedded hmtx table to decompile NOW, while maxp.numGlyphs
    #     still reflects the OLD glyph count. hmtx decompilation is lazy and
    #     sizes itself against maxp.numGlyphs; bumping numGlyphs first (step 10)
    #     would make a later first access read against the new (larger) count
    #     and raise "not enough hmtx table data". Reading the table here binds
    #     it to the current glyph order before the count changes.
    embedded_hmtx = embedded["hmtx"]

    cs = e_td.CharStrings
    fd_private = e_td.FDArray[0].Private

    def _append_cff_glyph(name: str, charstring: object, advance: int, lsb: int) -> None:
        """Append one glyph (charstring + charset + FDSelect + order + hmtx).

        Shared by the empty pad placeholders and the real donor glyph. Uses the
        manual indexed idiom (direct ``cs[name] =`` raises on a new name in the
        indexed case) and the ``embedded_hmtx[name] = (adv, lsb)`` form that
        bumps ``numberOfHMetrics`` (the ``hmtx.metrics[name] =`` form does not
        and fails re-serialization with "not enough hmtx data").
        """
        if cs.charStringsAreIndexed:
            idx = len(cs.charStringsIndex)
            cs.charStringsIndex.append(charstring)
            cs.charStrings[name] = idx
        else:
            cs.charStrings[name] = charstring
        e_td.charset.append(name)
        e_td.FDSelect.gidArray.append(0)
        glyph_order.append(name)
        embedded_hmtx[name] = (advance, lsb)

    # 7. Pad GIDs [len(glyphOrder), new_cid) with empty placeholder glyphs at
    #    FREE CIDs (the CFF analogue of _pad_glyph_order). Each placeholder is a
    #    .notdef-equivalent empty Type2 charstring routed to FD 0 with (0, 0)
    #    hmtx; its charset CID must be a unique, unused integer (a CID-keyed
    #    charset name MUST parse as cidNNNNN — fontTools does int(name[3:])).
    def _next_free_cid() -> int:
        cid = 1
        while cid in used_cids:
            cid += 1
        used_cids.add(cid)
        return cid

    for _ in range(len(glyph_order), new_cid):
        pad_cid = _next_free_cid()
        pad_name = f"cid{pad_cid:05d}"
        empty_cs = T2CharString(
            program=["endchar"], private=fd_private, globalSubrs=e_td.GlobalSubrs
        )
        _append_cff_glyph(pad_name, empty_cs, 0, 0)

    # 8. Draw the donor outline into the embedded CFF context (flattens subrs)
    #    and append the real glyph at GID == new_cid.
    donor_glyph_set = system.getGlyphSet()
    donor_advance, donor_lsb = system["hmtx"][donor_name]
    pen = T2CharStringPen(donor_advance, donor_glyph_set)
    donor_glyph_set[donor_name].draw(pen)
    drawn = pen.getCharString(private=fd_private, globalSubrs=e_td.GlobalSubrs)
    new_cs = T2CharString(
        program=list(drawn.program), private=fd_private, globalSubrs=e_td.GlobalSubrs
    )
    _append_cff_glyph(new_name, new_cs, donor_advance, donor_lsb)

    # 9. Finalize CID-keyed dicts + glyph order + maxp now that every slot has
    #    been appended (FDSelect.numGlyphs, CIDCount, glyphOrder, maxp).
    e_td.FDSelect.numGlyphs = len(e_td.FDSelect.gidArray)
    e_td.CIDCount = max(e_td.CIDCount, new_cid + 1)
    embedded.setGlyphOrder(glyph_order)
    embedded["maxp"].numGlyphs = len(glyph_order)

    return new_gid


def _detect_postscript_name(fd: pikepdf.Object) -> str:
    """Extract PostScript name from a font descriptor."""
    name_obj = fd.get("/FontName")
    if name_obj is None:
        return ""
    name = str(name_obj).lstrip("/")
    return name


# ── Public API ───────────────────────────────────────────────────────────


def analyze_subset(pdf_path: str | Path, font_name: str) -> FontInfo:
    """Analyze a font's embedded subset — how many glyphs, what's available.

    Args:
        pdf_path: Path to the PDF file.
        font_name: Name of the font to analyze (as it appears in the PDF, e.g. 'F1').

    Returns:
        FontInfo with subset details including glyph count, encoding type,
        and font_cmap populated with the embedded font's cmap table.

    Raises:
        FontNotFoundError: If the font or its embedded data is not found.
    """
    pdf = open_pdf(str(pdf_path))
    try:
        return _analyze_from_page(pdf.pages[0], font_name, pdf_path=pdf_path)
    finally:
        pdf.close()


def _analyze_from_page(
    page: pikepdf.Page,
    font_name: str,
    *,
    pdf_path: str | Path | None = None,
) -> FontInfo:
    """Analyze a font on an already-open page.

    Args:
        page: pikepdf Page object.
        font_name: Font resource name (e.g. 'F1').
        pdf_path: Optional source path for error messages.

    Returns:
        FontInfo with all fields populated.
    """
    font_dict, cid_font, fd = _get_font_objects(page, font_name)
    subtype_obj = font_dict.get("/Subtype")
    subtype = str(subtype_obj) if subtype_obj is not None else ""

    # Determine encoding type
    if subtype == "/Type0":
        encoding_type: str = "Identity-H"
    else:
        enc_obj = font_dict.get("/Encoding")
        enc_str = str(enc_obj) if enc_obj is not None else ""
        if enc_str == "/WinAnsiEncoding":
            encoding_type = "WinAnsi"
        elif enc_str == "/MacRomanEncoding":
            encoding_type = "MacRoman"
        else:
            encoding_type = "Custom"

    # PostScript name and subset detection
    raw_ps_name = _detect_postscript_name(fd)
    is_subset = (
        len(raw_ps_name) > 7
        and raw_ps_name[6] == "+"
        and raw_ps_name[:6].isalpha()
        and raw_ps_name[:6].isupper()
    )
    postscript_name = _strip_subset_prefix(raw_ps_name)

    # Extract font binary and introspect the truthful glyph count per outline
    # type (INV-C-10). _extract_font_bytes still raises FontNotFoundError when
    # NO embedded stream is present (its documented contract is unchanged);
    # _introspect_embedded_font is best-effort and returns 0 — never raises —
    # on a PRESENT-but-non-glyf or unparseable binary, so analyze_subset stops
    # raising on a bare CFF / Type1.
    font_bytes, embedded_type = _extract_font_bytes(fd)
    glyph_count = _introspect_embedded_font(font_bytes, embedded_type)

    # font_cmap (getBestCmap) is only meaningful for glyf outlines — a bare
    # CFF / Type1 cannot supply a Unicode cmap via TTFont. Guard the load
    # behind the glyf branch; best-effort None otherwise.
    font_cmap: dict[int, str] | None = None
    if embedded_type in {"glyf", "TrueType", "opentype-glyf"}:
        with _with_fonttools_translation(f"analyze_subset:{font_name}"):
            font = TTFont(io.BytesIO(font_bytes))
            try:
                font_cmap = font.getBestCmap()
            finally:
                font.close()

    return FontInfo(
        name=font_name,
        postscript_name=postscript_name,
        encoding_type=encoding_type,  # type: ignore[arg-type]
        is_subset=is_subset,
        glyph_count=glyph_count,
        embedded_type=embedded_type,  # type: ignore[arg-type]
        font_cmap=font_cmap,
    )


def can_render(font_info: FontInfo, text: str) -> tuple[bool, list[str]]:
    """Check if a font can render all characters in the given text.

    Checks the embedded font's cmap table (from fonttools getBestCmap).
    Characters present in the cmap have glyph data; missing characters
    would require Tier 2 (system font) extension.

    Args:
        font_info: FontInfo from analyze_subset(), must have font_cmap populated.
        text: Text to check renderability for.

    Returns:
        Tuple of (can_render_all, list_of_missing_characters).
    """
    if not text:
        return (True, [])

    if font_info.font_cmap is None:
        return (False, list(text))

    missing: list[str] = []
    for ch in text:
        if ord(ch) not in font_info.font_cmap:
            missing.append(ch)
    return (len(missing) == 0, missing)


def extend_subset(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    font_name: str,
    additional_chars: str,
    full_font_path: str | Path | None = None,
    *,
    substitution_log: list[str] | None = None,
    degradations: list[Degradation] | None = None,
) -> str:
    """Extend a font's character coverage in the open PDF object.

    Modifies the PDF's font objects in-place (CMap, /W, potentially font binary).
    The caller (surgeon.py) is responsible for saving the PDF afterward.

    Uses two-tier approach:
    1. Tier 1 — CMap-only extension if the glyph already exists in the
       embedded font data.
    2. Tier 1.5 — in-place glyph injection: append the missing glyph
       outline from the matching system font at a fresh GID and
       re-serialize /FontFile2; pre-existing CIDs are preserved. This
       replaced the removed v0.1.0 Tier 2 full re-embed (ARY-278), which
       renumbered CIDs and corrupted unrelated content-stream text.

    Args:
        pdf: The open pikepdf.Pdf object (modified in-place).
        page: The page containing the font.
        font_name: Font resource name (e.g. 'F1').
        additional_chars: String of characters to add to the subset.
        full_font_path: Optional explicit path to the full font file (Tier 2).
        substitution_log: Optional list to capture metric-equivalent
            substitution events. INV-C-4 plumbing — when Tier 1.5 falls
            back to a metric-equivalent system font (e.g. Carlito for
            Calibri), the equivalent's PostScript name is appended so
            the caller can populate ``FidelityReport.font_substituted``.
            Pass ``None`` (default) when the caller doesn't need
            substitution visibility.
        degradations: Optional list to capture typed Degradation events
            from the extension path. F-D-CC9 plumbing — when Tier 1.5
            resolves to a system font whose canonical realpath lives in
            a per-platform user-writable directory, a
            ``Degradation(kind="font_substituted_from_user_fonts",
            severity="warning")`` is appended so callers can surface
            the security-relevant origin via
            ``FidelityReport.degradations``. Pass ``None`` (default)
            when the caller does not consume Degradations.

    Returns:
        Extension tier used: ``'cmap_only'`` or ``'full_extension'``.

    Raises:
        FontNotFoundError: If font not found in PDF, or system font not available
            for Tier 2 extension.
    """
    if not additional_chars:
        return "cmap_only"

    font_dict, cid_font, fd = _get_font_objects(page, font_name)
    subtype_obj = font_dict.get("/Subtype")
    subtype = str(subtype_obj) if subtype_obj is not None else ""

    if subtype == "/Type0":
        if cid_font is None:
            msg = f"No CIDFont descendant for {font_name}"
            raise FontNotFoundError(msg)
        # B.3 backstop (M0 Rank-2.5, WRITE = refuse-on-gap): a Type0 font
        # with no /ToUnicode reached the extension path. Tier 1/1.5 both call
        # _append_to_unicode_cmap, which read_bytes() the (absent) /ToUnicode
        # and raise a raw KeyError that escapes _FONT_EXTEND_FAIL_EXCS. Refuse
        # here with FontNotFoundError (in the fail tuple) so EVERY write
        # caller — surgeon, reflow, structural — degrades to a typed
        # font_extension_failed Degradation instead of crashing. surgeon's
        # resolver-level gate (is_tounicode_recovered) refuses earlier with
        # the more specific tounicode_recovered kind; this is the shared
        # backstop for the other paths. Writing a fresh /ToUnicode to enable
        # extension on a recovered font is deferred (out of B.3 scope).
        if "/ToUnicode" not in font_dict:
            msg = (
                f"font {font_name} has no /ToUnicode; new-glyph extension would "
                f"require synthesising a ToUnicode CMap (out of scope). Refusing."
            )
            raise FontNotFoundError(msg)
        # CID Identity-H path follows below verbatim.
    elif subtype == "/TrueType":
        # Reject CFF/OpenType outlines and Type1 explicitly inside the
        # simple-font path so the failure detail is clean.
        if fd.get("/FontFile3") is not None or fd.get("/FontFile") is not None:
            msg = (
                f"simple-font extension requires /FontFile2 (TrueType outlines); "
                f"got /FontFile3 (CFF/OpenType) or /FontFile (Type1) for {font_name}"
            )
            raise FontNotFoundError(msg)
        return _extend_simple_tier_one_five(
            pdf,
            font_dict,
            fd,
            additional_chars=additional_chars,
            full_font_path=full_font_path,
            substitution_log=substitution_log,
            degradations=degradations,
        )
    elif subtype == "/Type1":
        msg = (
            f"Type1 font extension is not supported (would require Adobe Type1 "
            f"charstring surgery); got {subtype} for {font_name}. Caller should "
            f"see this as font_extension_failed via Phase 4 lying-fix path."
        )
        raise FontNotFoundError(msg)
    else:
        msg = (
            f"Font extension is only supported for Type0/Identity-H or "
            f"simple /TrueType fonts; got {subtype} for {font_name}"
        )
        raise FontNotFoundError(msg)

    # C.3 CFF route. A Type0/CID font whose outline is CFF (Type1C, bare or
    # sfnt-wrapped) cannot be loaded by the glyf path's TTFont(BytesIO(...))
    # below — a BARE CFF has no sfnt directory and raises TTLibError. Classify
    # via the bare-CFF-safe classify_embedded_outline (which falls back to the
    # slot label rather than crashing) and route CFF straight to _extend_tier2,
    # which branches to the CFF injector. Non-CFF outlines fall through to the
    # existing glyf Tier 1/1.5 split unchanged.
    if classify_embedded_outline(fd) == "CFF" and "/FontFile3" in fd:
        return _extend_tier2(
            pdf,
            font_dict,
            cid_font,
            fd,
            additional_chars,
            full_font_path,
            substitution_log,
            degradations,
        )

    # Extract and load the embedded font
    font_bytes, _embedded_type = _extract_font_bytes(fd)
    with _with_fonttools_translation(f"extend_subset:{font_name}"):
        embedded_font = TTFont(io.BytesIO(font_bytes))
        try:
            embedded_cmap = embedded_font.getBestCmap() or {}

            # Split additional_chars into two groups:
            #   tier1_chars  - glyph already in embedded font, only needs a
            #                  /ToUnicode + /W + /CIDToGIDMap entry
            #   tier15_chars - glyph missing from embedded font, needs full
            #                  in-place injection (Tier 1.5)
            tier1_chars: list[str] = []
            tier15_chars: list[str] = []
            seen: set[str] = set()
            for ch in additional_chars:
                if ch in seen:
                    continue
                seen.add(ch)
                if ord(ch) in embedded_cmap:
                    tier1_chars.append(ch)
                else:
                    tier15_chars.append(ch)

            # Apply Tier 1 for chars whose glyph is already in the embedded
            # font (no font-file change needed). _extend_tier1 reads
            # embedded_font["hmtx"] / getGlyphID — fontTools lazy calls
            # that must be inside this translation block.
            if tier1_chars:
                _extend_tier1(
                    pdf,
                    font_dict,
                    cid_font,
                    embedded_font,
                    embedded_cmap,
                    "".join(tier1_chars),
                )
        finally:
            embedded_font.close()

    # Tier 1.5 handles the remaining chars whose glyphs are absent from
    # the embedded font. If there are none, we are done with Tier 1.
    if not tier15_chars:
        return "cmap_only"

    return _extend_tier2(
        pdf,
        font_dict,
        cid_font,
        fd,
        "".join(tier15_chars),
        full_font_path,
        substitution_log,
        degradations,
    )


def _extend_tier1(
    pdf: pikepdf.Pdf,
    font_dict: pikepdf.Object,
    cid_font: pikepdf.Object,
    embedded_font: TTFont,
    embedded_cmap: dict[int, str],
    additional_chars: str,
) -> str:
    """CMap-only extension — glyph exists in font, just add CMap + /W entries.

    For Identity-H: CID == GID (non-negotiable).
    """
    new_cmap_entries: dict[int, str] = {}
    new_w_entries: dict[int, float] = {}

    for ch in additional_chars:
        cp = ord(ch)
        glyph_name = embedded_cmap[cp]
        gid = embedded_font.getGlyphID(glyph_name)
        # Identity-H: CID must equal GID
        cid = gid

        new_cmap_entries[cid] = ch

        # Get advance width from hmtx, normalized to PDF 1/1000-em scale
        if "hmtx" in embedded_font and glyph_name in embedded_font["hmtx"].metrics:
            raw_width = float(embedded_font["hmtx"].metrics[glyph_name][0])
            units_per_em = embedded_font["head"].unitsPerEm
            width = raw_width * 1000.0 / units_per_em
        else:
            width = 600.0  # fallback (already in 1/1000-em scale)
        new_w_entries[cid] = width

    _append_to_unicode_cmap(font_dict, new_cmap_entries, pdf)
    _append_w_entries(cid_font, new_w_entries)
    _update_cid_to_gid_map(cid_font, {cid: cid for cid in new_cmap_entries}, pdf)

    logger.info(
        "Tier 1 (CMap-only) extension: added %d characters",
        len(additional_chars),
    )
    return "cmap_only"


def _extend_tier2(
    pdf: pikepdf.Pdf,
    font_dict: pikepdf.Object,
    cid_font: pikepdf.Object,
    fd: pikepdf.Object,
    additional_chars: str,
    full_font_path: str | Path | None,
    substitution_log: list[str] | None = None,
    degradations: list[Degradation] | None = None,
) -> str:
    """Tier 1.5 in-place glyph injection (root fix for ARY-276 Mode 2).

    Rather than replacing the embedded font file with a subset of the
    system font (which renumbers pre-existing CIDs and corrupts
    unrelated content-stream text), this function loads the existing
    embedded TTFont and APPENDS missing glyphs into its glyf table at
    fresh GIDs. Pre-existing CIDs remain valid because only the tail
    of the glyph order changes.

    For each appended glyph:
    - The glyph outline is deep-copied from the system font
    - Hinting bytecode is stripped (the source font's fpgm/prep/cvt
      tables are not available in the embedded subset)
    - Composite glyph components are injected recursively (leaves first)
    - The embedded font's glyf, hmtx, internal cmap, glyph order, and
      maxp.numGlyphs are updated
    - The embedded font is re-serialized back into /FontFile2

    Then, using the existing Tier 1 helpers, the PDF-level ToUnicode,
    /W, and /CIDToGIDMap entries are added for the new CIDs
    (CID == new GID under Identity-H).

    Returns ``"full_extension"`` for backward compatibility with existing
    tests and callers; the string is unchanged from the legacy Tier 2
    contract even though the underlying strategy is now additive.

    Args:
        pdf: Open pikepdf.Pdf (mutated in place).
        font_dict: The top-level Type0 font dictionary.
        cid_font: The CIDFontType2 descendant font dictionary.
        fd: The FontDescriptor dictionary.
        additional_chars: Unicode characters to add.
        full_font_path: Optional explicit system font path override.

    Raises:
        FontNotFoundError: If the system font cannot be found, the
            embedded font is not TrueType, upem does not match, a
            character is absent from the system font, or a composite
            component is missing from both fonts.
    """
    from pdf_edit_engine.system_fonts import _find_font_with_origin

    raw_ps_name = _detect_postscript_name(fd)
    ps_name = _strip_subset_prefix(raw_ps_name)

    if full_font_path is not None:
        system_path: str | None = str(full_font_path)
        origin: str = "system"  # explicit override — caller-supplied path
        substituted_name: str | None = None
    else:
        found = _find_font_with_origin(ps_name)
        if found is None:
            system_path = None
            origin = "system"
            substituted_name = None
        else:
            system_path, origin, substituted_name = found
    if system_path is None or not Path(system_path).is_file():
        msg = (
            f"System font not found for '{ps_name}' — install the font or "
            f"pass full_font_path=<path>."
        )
        raise FontNotFoundError(msg)
    # INV-C-4: surface metric-equivalent substitution to caller.
    if substituted_name is not None and substitution_log is not None:
        substitution_log.append(substituted_name)
    # F-D-CC9: surface user-fonts origin (severity="warning"; not in
    # FONT_AFFECTING_KINDS — the font WAS used, but the caller should
    # know the outline came from a user-writable directory that an
    # unprivileged process could have primed).
    if origin == "user" and degradations is not None:
        degradations.append(
            Degradation(
                kind="font_substituted_from_user_fonts",
                detail=f"path={system_path}",
                severity="warning",
            )
        )

    # INV-C-9/C-11 outline-table dispatch. Tier 1.5 in-place glyph injection
    # re-serialises and only understands glyf (TrueType, /FontFile2) and, as
    # of C.3, CID-keyed CFF (Type1C, /FontFile3). A Type0/CID font whose
    # /FontFile3 carries CFF/CFF2/OpenType-glyf has NO /FontFile2 key, so the
    # historical `fd["/FontFile2"]` read raised a raw KeyError that escaped
    # _FONT_EXTEND_FAIL_EXCS and leaked out of the edit verb. Classify the
    # ACTUAL embedded table via classify_embedded_outline (bare-CFF safe — a
    # raw Type1C table has no sfnt directory and would crash a direct
    # TTFont(BytesIO) probe; the classifier falls back to the slot label).
    # glyf → existing glyf body below; CFF → _extend_cff_tier (INV-C-11); any
    # OTHER shape (CFF2, OpenType-glyf via /FontFile3, name-keyed simple OTF
    # reached here, etc.) still HARD-FAILS with FontNotFoundError — which IS
    # in the fail tuple, so surgeon/reflow/structural surface an honest
    # font_extension_failed Degradation with success=False (INV-C-13).
    outline_kind = classify_embedded_outline(fd)
    if outline_kind == "CFF" and "/FontFile3" in fd:
        return _extend_cff_tier(
            pdf,
            font_dict,
            cid_font,
            fd,
            additional_chars,
            system_path,
            substitution_log=substitution_log,
            degradations=degradations,
        )
    if outline_kind not in {"glyf", "TrueType"} or "/FontFile2" not in fd:
        msg = (
            f"in-place glyph injection requires glyf (TrueType) outlines in "
            f"/FontFile2 or a CID-keyed CFF in /FontFile3; embedded outline "
            f"for {ps_name} is {outline_kind!r}. Refusing."
        )
        raise FontNotFoundError(msg)
    embedded_bytes, _ = _extract_font_bytes(fd)
    with _with_fonttools_translation(f"_extend_tier2:{ps_name}"):
        embedded = TTFont(io.BytesIO(embedded_bytes))
        system = TTFont(system_path)
        try:
            units_per_em = embedded["head"].unitsPerEm
            new_cmap_entries: dict[int, str] = {}
            new_w_entries: dict[int, float] = {}

            # Compute a collision-free starting GID for new glyphs.
            # Under Identity-H, CID == GID, so the new GID must be above
            # BOTH the current glyph order length AND any CID already used
            # by the ToUnicode CMap (which, for some synthetic/retain_gids
            # fonts, references CIDs beyond the embedded font's glyph
            # count). Pad the glyph order with unique .notdef placeholders
            # up to that point so fontTools preserves the slot numbering.
            existing_cmap_cids = _parse_existing_tounicode(font_dict).keys()
            max_existing_cid = max(existing_cmap_cids, default=-1)
            safe_start = max(len(embedded.getGlyphOrder()), max_existing_cid + 1)
            _pad_glyph_order(embedded, safe_start)

            for ch in additional_chars:
                cp = ord(ch)
                # Skip if already in the embedded cmap (defensive — caller
                # should have routed through Tier 1 first).
                if cp in (embedded.getBestCmap() or {}):
                    continue
                new_gid = _inject_glyph_in_place(embedded, system, ch)

                new_cmap_entries[new_gid] = ch
                # Width comes from the newly-injected glyph's hmtx entry
                # (which we just copied from the system font).
                glyph_name = (system.getBestCmap() or {})[cp]
                raw_w = float(system["hmtx"][glyph_name][0])
                new_w_entries[new_gid] = raw_w * 1000.0 / units_per_em

            if not new_cmap_entries:
                # Every requested char was already in the embedded cmap —
                # nothing to do at the font level. Caller's PDF-level
                # metadata is already up to date via Tier 1 or a previous
                # extension.
                return "full_extension"

            # Re-serialize the extended embedded font and replace /FontFile2.
            # embedded.save() is fontTools-driven and CAN raise during
            # serialization — must stay inside the translator block.
            buf = io.BytesIO()
            embedded.save(buf)
            fd["/FontFile2"] = pdf.make_stream(buf.getvalue())

            # No cache eviction needed: the prior _FONTFILE2_CACHE was
            # deleted (see font_has_codepoint docstring). Every subsequent
            # font_has_codepoint call now re-parses /FontFile2 fresh, so
            # the just-injected glyphs are observed on the next query
            # without explicit invalidation.

            # Apply PDF-level metadata updates using Tier 1 helpers.
            _append_to_unicode_cmap(font_dict, new_cmap_entries, pdf)
            _append_w_entries(cid_font, new_w_entries)
            _update_cid_to_gid_map(
                cid_font,
                {cid: cid for cid in new_cmap_entries},
                pdf,
            )

            logger.info(
                "Tier 1.5 (in-place glyph injection) from %s: %d new glyph(s) appended",
                system_path,
                len(new_cmap_entries),
            )
            return "full_extension"
        finally:
            embedded.close()
            system.close()


def _extend_cff_tier(
    pdf: pikepdf.Pdf,
    font_dict: pikepdf.Object,
    cid_font: pikepdf.Object,
    fd: pikepdf.Object,
    additional_chars: str,
    system_path: str,
    *,
    substitution_log: list[str] | None,
    degradations: list[Degradation] | None,
) -> str:
    """CID-keyed CFF in-place glyph injection (INV-C-11; the CFF sibling).

    The CFF analogue of the ``glyf`` Tier 1.5 body in :func:`_extend_tier2`.
    Loads BOTH the embedded ``/FontFile3`` CFF and the donor (resolved by
    :func:`_extend_tier2` into ``system_path``) as ``TTFont`` via
    :func:`_load_cff_as_ttfont` (wrapping a bare ``/Type1C`` table into an
    in-memory sfnt), injects each missing glyph at CID == GID via
    :func:`_inject_cff_glyph_in_place`, re-serializes the CFF back into
    ``/FontFile3`` (re-extracting the bare ``CFF `` table when the embedded
    binary was bare, to preserve the ``/Type1C`` shape), and applies the
    format-agnostic PDF-level metadata helpers VERBATIM. Returns
    ``"full_extension"`` so the surgeon/reflow/structural funnel maps CFF
    success identically to ``glyf`` success.

    Every fontTools/cffLib call runs inside :func:`_with_fonttools_translation`
    so any serialization failure rebrands to ``FontNotFoundError`` (in
    ``_FONT_EXTEND_FAIL_EXCS``); out-of-scope donor shapes refuse inside
    :func:`_inject_cff_glyph_in_place` with the same typed error (INV-C-13).

    Args:
        pdf: Open ``pikepdf.Pdf`` (mutated in place).
        font_dict: The top-level Type0 font dictionary.
        cid_font: The CIDFontType0 descendant font dictionary.
        fd: The FontDescriptor dictionary (owns ``/FontFile3``).
        additional_chars: Unicode characters to inject.
        system_path: Resolved donor CFF font path.
        substitution_log: Unused for the donor name here (the donor is the
            resolved ``system_path``); present for signature symmetry with the
            glyf body. The metric-equivalent name was already appended by
            :func:`_extend_tier2`.
        degradations: Unused here; the user-fonts-origin Degradation was
            already appended by :func:`_extend_tier2`.

    Returns:
        ``"full_extension"`` on a successful injection.

    Raises:
        FontNotFoundError: On any out-of-scope shape or serialization failure.
    """
    del substitution_log, degradations  # surfaced by _extend_tier2's sourcing

    ff3_obj = fd["/FontFile3"]
    embedded_bytes = read_stream_bounded(ff3_obj, max_decoded=MAX_FONT_STREAM_BYTES, label="font")
    old_subtype = ff3_obj.get("/Subtype")
    donor_bytes = Path(system_path).read_bytes()

    with _with_fonttools_translation("_extend_cff_tier"):
        embedded, was_bare = _load_cff_as_ttfont(embedded_bytes)
        donor, _donor_bare = _load_cff_as_ttfont(donor_bytes)
        try:
            units_per_em = embedded["head"].unitsPerEm
            new_cmap_entries: dict[int, str] = {}
            new_w_entries: dict[int, float] = {}

            # Parse the existing /ToUnicode ONCE: it yields both the codepoint→
            # CID reverse map (charset-skip check) and the /ToUnicode CID floor.
            # A CID referenced ONLY by /ToUnicode (absent from the embedded CFF
            # charset) must NOT be re-used as a new CID — that is the ARY-278
            # no-renumber discipline ported to CFF. The injector folds the
            # embedded charset's own CIDs; min_cid threads the /ToUnicode floor
            # so a new glyph clears BOTH (mirrors the glyf path, INV-C-12).
            existing_tounicode = _parse_existing_tounicode(font_dict)
            reverse = {ord(u): cid for cid, u in existing_tounicode.items()}
            min_cid = max(existing_tounicode.keys(), default=-1) + 1

            for ch in additional_chars:
                cp = ord(ch)
                # Skip chars already covered by the embedded CFF charset (a
                # codepoint whose CID's cidNNNNN name is already present).
                existing_cid = reverse.get(cp)
                e_cff = embedded["CFF "].cff
                e_charset = list(e_cff[e_cff.fontNames[0]].charset)
                if existing_cid is not None and f"cid{existing_cid:05d}" in e_charset:
                    continue
                new_gid = _inject_cff_glyph_in_place(embedded, donor, ch, min_cid=min_cid)
                new_cmap_entries[new_gid] = ch
                donor_cmap = donor.getBestCmap() or {}
                donor_name = donor_cmap[cp]
                raw_w = float(donor["hmtx"][donor_name][0])
                new_w_entries[new_gid] = raw_w * 1000.0 / units_per_em

            if not new_cmap_entries:
                return "full_extension"

            # Re-serialize. A bare /Type1C embedded font must round-trip as a
            # bare CFF table so its /Subtype shape is preserved; a wrapped
            # OpenType-CFF saves the full sfnt.
            buf = io.BytesIO()
            embedded.save(buf)
            new_stream_bytes = embedded.getTableData("CFF ") if was_bare else buf.getvalue()
            new_stream = pdf.make_stream(new_stream_bytes)
            if old_subtype is not None:
                new_stream["/Subtype"] = old_subtype
            fd["/FontFile3"] = new_stream

            _append_to_unicode_cmap(font_dict, new_cmap_entries, pdf)
            _append_w_entries(cid_font, new_w_entries)
            _update_cid_to_gid_map(
                cid_font,
                {cid: cid for cid in new_cmap_entries},
                pdf,
            )

            logger.info(
                "CFF in-place glyph injection from %s: %d new glyph(s) appended",
                system_path,
                len(new_cmap_entries),
            )
            return "full_extension"
        finally:
            embedded.close()
            donor.close()


# ── Simple-font (non-CID) Tier 1.5 path ──────────────────────────────────


def _extend_simple_tier_one_five(
    pdf: pikepdf.Pdf,
    font_dict: pikepdf.Object,
    fd: pikepdf.Object,
    additional_chars: str,
    full_font_path: str | Path | None = None,
    substitution_log: list[str] | None = None,
    degradations: list[Degradation] | None = None,
) -> str:
    """Tier 1.5 in-place glyph injection for simple (non-CID) TrueType fonts.

    Mirrors ``_extend_tier2`` for the font-binary surgery (system font
    sourcing, composite resolution, hinting strip, glyf append) but
    updates ``/Encoding /Differences``, ``/Widths``, and
    ``/FirstChar..LastChar`` on the PDF side instead of ``/ToUnicode``,
    ``/W``, and ``/CIDToGIDMap``.

    Args:
        pdf: The open ``pikepdf.Pdf`` (mutated in place). Threaded
            through from the public-API entry point per INV-L-1; this
            helper must NOT call ``pikepdf.Pdf.open`` itself.
        font_dict: The simple-font dictionary (Subtype /TrueType).
        fd: The /FontDescriptor dictionary owning /FontFile2.
        additional_chars: Unicode characters to add.
        full_font_path: Optional explicit override for the system font
            path; bypasses ``_find_font_with_origin`` lookup.
        substitution_log: Optional list to receive metric-equivalent
            substitution names (INV-C-4).

    Returns:
        ``"full_extension"`` (mirrors CID Tier 1.5 contract).

    Raises:
        FontNotFoundError: /FontFile2 missing, system font unavailable,
            embedded hmtx malformed, or no free byte slots remaining.
    """
    from pdf_edit_engine.system_fonts import _find_font_with_origin

    if not additional_chars:
        return "full_extension"

    # 1. /FontFile2 must be present + parseable
    ff2 = fd.get("/FontFile2")
    if ff2 is None:
        raise FontNotFoundError(
            "simple-font Tier 1.5 requires /FontFile2; "
            "/FontFile (Type1) and /FontFile3 (CFF) not supported"
        )

    # 2. Locate system font (mirrors _extend_tier2 sourcing)
    base_font = str(font_dict.get("/BaseFont") or "").lstrip("/")
    ps_name = _strip_subset_prefix(base_font)
    found = _find_font_with_origin(ps_name)
    if found is None:
        if full_font_path is None:
            raise FontNotFoundError(
                f"system font for {ps_name!r} not found — install the font or "
                f"pass full_font_path=<path>."
            )
        system_path = str(full_font_path)
        origin = "system"  # explicit override — caller-supplied path
        substituted_name = None
    else:
        system_path, origin, substituted_name = found
    if substituted_name is not None and substitution_log is not None:
        substitution_log.append(substituted_name)
    # F-D-CC9: surface user-fonts origin (severity="warning"; not in
    # FONT_AFFECTING_KINDS — origin surface, not a fidelity break).
    if origin == "user" and degradations is not None:
        degradations.append(
            Degradation(
                kind="font_substituted_from_user_fonts",
                detail=f"path={system_path}",
                severity="warning",
            )
        )

    # 3. Partition `additional_chars` into two groups based on whether the
    # char's standard encoding byte already exists in [/FirstChar, /LastChar]
    # with zero pre-extension width — Word's "reserved-but-unused" pattern.
    #
    # Architectural root fix (2026-05-09 RCA): the prior unconditional
    # new-byte allocation interacted badly with three independently-correct
    # components — encoding._build_reverse_map's lowest-byte preference,
    # Word's zero-width sentinel for unused codepoints, and high-byte
    # /Differences allocation. The resolver picked the still-zero-width
    # standard byte, can_encode reported the char missing, and surgeon
    # emitted font_extension_failed on chars that ARE present in the system
    # font (e.g. capital K when body text didn't use K). The fix partitions:
    #
    # Group A (heal in place) — char's standard byte ∈ [fc, lc] with
    #   /Widths==0 and no conflicting pre-existing /Differences override.
    #   Inject the glyph; update /Widths[byte] = new_width. No /Differences
    #   entry; /Encoding stays as Name. The standard byte already maps to
    #   the right glyph name via /BaseEncoding, so no encoding mutation.
    #
    # Group B (allocate new byte) — everything else. Existing path:
    #   _allocate_free_bytes from /LastChar+1, _extend_simple_encoding,
    #   _extend_simple_widths. /Encoding promoted to Dict; /LastChar bumped.
    fc_obj = font_dict.get("/FirstChar")
    lc_obj = font_dict.get("/LastChar")
    fc: int | None = int(fc_obj) if fc_obj is not None else None
    lc: int | None = int(lc_obj) if lc_obj is not None else None

    effective_b2u = _build_simple_effective_byte_to_unicode(font_dict)
    # Lowest-byte-wins reverse (mirrors encoding._build_reverse_map so the
    # partition decision matches what the resolver will actually pick).
    effective_u2b: dict[str, int] = {}
    for code in sorted(effective_b2u):
        char = effective_b2u[code]
        if char not in effective_u2b:
            effective_u2b[char] = code

    # Pre-existing /Differences (byte → glyph_name) for conflict detection.
    # If a byte has a pre-existing /Differences override, /BaseEncoding's
    # mapping at that byte is shadowed — fall back to Group B for that char.
    preexisting_diffs: set[int] = set()
    enc_pre = font_dict.get("/Encoding")
    if isinstance(enc_pre, pikepdf.Dictionary):
        diffs_obj = enc_pre.get("/Differences")
        if diffs_obj is not None:
            cur = 0
            for item in list(diffs_obj):  # type: ignore[call-overload]
                s = str(item)
                if not s.startswith("/"):
                    cur = int(item)
                else:
                    preexisting_diffs.add(cur)
                    cur += 1

    raw_widths: list[object] = []
    if "/Widths" in font_dict:
        raw_widths = list(font_dict["/Widths"])  # type: ignore[call-overload]
    widths_pre: list[float] = [float(w) for w in raw_widths]  # type: ignore[arg-type]

    # Per-char categorization with dedup. Prior code passed
    # additional_chars through `zip(..., free_bytes, strict=True)` without
    # dedup — duplicates produced redundant /Differences entries (observed
    # in the cities-variant repro: two "/odieresis" entries for "Köö").
    # Dedup here defensively.
    seen: set[str] = set()
    group_a: list[tuple[str, int, str]] = []  # (ch, std_byte, glyph_name)
    group_b: list[tuple[str, str]] = []  # (ch, glyph_name)
    for ch in additional_chars:
        if ch in seen:
            continue
        seen.add(ch)
        glyph_name = _glyph_name_for_codepoint(ord(ch))
        std_byte = effective_u2b.get(ch)
        eligible_for_a = (
            std_byte is not None
            and fc is not None
            and lc is not None
            and fc <= std_byte <= lc
            and 0 <= std_byte - fc < len(widths_pre)
            and widths_pre[std_byte - fc] == 0.0
            and std_byte not in preexisting_diffs
        )
        if eligible_for_a:
            assert std_byte is not None  # narrowed by eligible_for_a guard
            group_a.append((ch, std_byte, glyph_name))
        else:
            group_b.append((ch, glyph_name))

    # 4. Allocate new bytes for Group B (existing path).
    used_bytes = _used_bytes_in_encoding(font_dict)
    last_char_for_alloc = lc if lc is not None else 0
    free_bytes = (
        _allocate_free_bytes(used_bytes, len(group_b), last_char=last_char_for_alloc)
        if group_b
        else []
    )

    # 5. Open both fonts (close in finally per existing pattern). Per
    # Skeptic-A: TTFont(BytesIO(...)) defers parsing — getBestCmap /
    # getGlyphOrder / glyf-table accesses fire downstream. Wrap the entire
    # fontTools-using block, including embedded.save(buf).
    with _with_fonttools_translation(f"_extend_simple_tier_one_five:{ps_name}"):
        embedded = TTFont(
            io.BytesIO(read_stream_bounded(ff2, max_decoded=MAX_FONT_STREAM_BYTES, label="font"))
        )
        system = TTFont(system_path)
        try:
            # 6. Inject glyphs for both groups; collect (byte, glyph_name, width).
            # _glyph_width_from_hmtx raises FontNotFoundError on missing
            # glyph_name (indicates _inject_glyph_in_place partial-failed)
            # or unitsPerEm == 0 (corrupt font metadata) — both surfaced as
            # font_extension_failed via _FONT_EXTEND_FAIL_EXCS at the call
            # site (M.4 hardening).
            group_a_assignments: list[tuple[int, str, float]] = []
            for ch, std_byte, glyph_name in group_a:
                _inject_glyph_in_place(embedded, system, ch)
                width = _glyph_width_from_hmtx(embedded, glyph_name)
                group_a_assignments.append((std_byte, glyph_name, width))

            group_b_assignments: list[tuple[int, str, float]] = []
            for (ch, glyph_name), byte_slot in zip(group_b, free_bytes, strict=True):
                _inject_glyph_in_place(embedded, system, ch)
                width = _glyph_width_from_hmtx(embedded, glyph_name)
                group_b_assignments.append((byte_slot, glyph_name, width))

            # 7. Re-serialize embedded font (one save for both groups).
            # Mirrors _extend_tier2's pattern (fonts.py:955).
            # embedded.save() can raise during fontTools serialization —
            # must stay inside the translator block.
            buf = io.BytesIO()
            embedded.save(buf)
            fd["/FontFile2"] = pdf.make_stream(buf.getvalue())

            # No cache invalidation needed: _FONTFILE2_CACHE was deleted
            # (see font_has_codepoint docstring). font_has_codepoint
            # re-parses /FontFile2 on every call, so just-injected glyphs
            # are observed without explicit eviction.

            # 8. PDF-side updates per group.
            #   Group A → heal in place (no /Differences mutation, no /LastChar bump)
            #   Group B → existing _extend_simple_encoding + _extend_simple_widths
            if group_a_assignments:
                _heal_simple_widths(font_dict, group_a_assignments)
            if group_b_assignments:
                _extend_simple_encoding(pdf, font_dict, group_b_assignments)
                _extend_simple_widths(font_dict, group_b_assignments)

            logger.info(
                "Tier 1.5 (simple-font) extension from %s: %d heal + %d new = %d total",
                system_path,
                len(group_a_assignments),
                len(group_b_assignments),
                len(group_a_assignments) + len(group_b_assignments),
            )
            return "full_extension"
        finally:
            embedded.close()
            system.close()


def _glyph_name_for_codepoint(cp: int) -> str:
    """Reverse-lookup Adobe Glyph List name for a Unicode codepoint.

    Returns the AGL name (e.g. ``"oslash"`` for U+00F8) when one exists;
    otherwise falls back to the canonical ``uniXXXX`` form. Used by
    ``_extend_simple_tier_one_five`` to populate /Encoding /Differences with
    PDF-spec-conformant glyph names.
    """
    from fontTools.agl import UV2AGL  # type: ignore[import-untyped]

    return UV2AGL.get(cp) or f"uni{cp:04X}"


def _glyph_width_from_hmtx(font: TTFont, glyph_name: str) -> float:
    """Read advance width from hmtx and normalize to PDF /Widths scale (1/1000-em).

    Raises FontNotFoundError on missing ``glyph_name`` (indicates a
    partial ``_inject_glyph_in_place`` failure) or ``unitsPerEm == 0``
    (corrupt font metadata). Both are surfaced as
    ``font_extension_failed`` via ``_FONT_EXTEND_FAIL_EXCS`` at the call
    site, satisfying INV-J-5 (M.4 hardening).
    """
    metrics = font["hmtx"].metrics
    if glyph_name not in metrics:
        raise FontNotFoundError(f"glyph {glyph_name!r} missing from embedded hmtx after injection")
    upem = font["head"].unitsPerEm
    if upem <= 0:
        raise FontNotFoundError(
            f"unitsPerEm must be > 0 for glyph {glyph_name!r}, got {upem}; cannot normalize width"
        )
    raw = float(metrics[glyph_name][0])
    result: float = raw * 1000.0 / upem
    return result


def _used_bytes_in_encoding(font_dict: pikepdf.Object) -> set[int]:
    """Return all byte slots already in use by this font's encoding.

    Combines (a) bytes in [/FirstChar, /LastChar] (the explicit /Widths
    range) and (b) bytes explicitly mapped via /Encoding /Differences.
    Bytes outside this union are free for allocation.
    """
    used: set[int] = set()
    fc = font_dict.get("/FirstChar")
    lc = font_dict.get("/LastChar")
    if fc is not None and lc is not None:
        used.update(range(int(fc), int(lc) + 1))
    enc = font_dict.get("/Encoding")
    if isinstance(enc, pikepdf.Dictionary) and "/Differences" in enc:
        cur = 0
        for item in list(enc["/Differences"]):  # type: ignore[call-overload]
            s = str(item)
            if not s.startswith("/"):
                cur = int(item)
            else:
                used.add(cur)
                cur += 1
    return used


def _allocate_free_bytes(used: set[int], n: int, *, last_char: int) -> list[int]:
    """Allocate n free byte slots, consecutively from /LastChar + 1.

    Low-end consecutive allocation: starts at ``last_char + 1``, walks
    up, skipping 127 (DEL) and any byte already in ``used`` (which would
    indicate a /Differences override on a high byte). Order is stable:
    same ``(used, n, last_char)`` → same returned list, so multiple
    extensions on the same font don't collide.

    Raises:
        FontNotFoundError: when no contiguous run of n free bytes
            exists in [last_char+1, 255].
        ValueError: when ``n < 0`` or ``last_char`` is outside [-1, 255].
    """
    # Defense-in-depth guards (m-1, m-2): unreachable under the current
    # call graph (caller always passes n=len(additional_chars) ≥ 1 and a
    # PDF-validated /LastChar), but cheap to enforce at the helper boundary.
    if n == 0:
        return []
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    if last_char < -1 or last_char > 255:
        raise ValueError(f"last_char out of range [-1, 255]: {last_char}")
    free: list[int] = []
    for byte in range(last_char + 1, 256):
        if byte == 127:
            continue
        if byte in used:
            continue
        free.append(byte)
        if len(free) == n:
            return free
    raise FontNotFoundError(
        f"no free byte slots above /LastChar={last_char}: need {n}, "
        f"only {len(free)} free in {last_char + 1}..255"
    )


def _extend_simple_encoding(
    pdf: pikepdf.Pdf,
    font_dict: pikepdf.Object,
    new_assignments: list[tuple[int, str, float]],
) -> None:
    """Append (byte, glyph_name) pairs to /Encoding /Differences.

    If /Encoding is currently a NAME (e.g. /WinAnsiEncoding), promotes
    it to a DICT first so /Differences can be added. If already a dict,
    appends to existing /Differences.
    """
    del pdf  # parameter retained for API symmetry; pikepdf.Dictionary
    # construction does not need the owning Pdf.

    enc = font_dict.get("/Encoding")
    if not isinstance(enc, pikepdf.Dictionary):
        # Promote name → dict
        base_name = str(enc) if enc is not None else "/WinAnsiEncoding"
        font_dict["/Encoding"] = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Encoding"),
                "/BaseEncoding": pikepdf.Name(base_name),
                "/Differences": pikepdf.Array(),
            }
        )
        enc = font_dict["/Encoding"]

    diffs: list[object] = list(enc.get("/Differences") or [])  # type: ignore[arg-type]
    # PDF /Differences format: [byte_start /name1 /name2 ... byte_start2 /name3 ...]
    # Consecutive bytes can share a single byte_start prefix. We emit
    # one prefix per assignment for simplicity (no correctness risk;
    # mild verbosity).
    for byte_slot, glyph_name, _width in sorted(new_assignments):
        diffs.append(byte_slot)
        diffs.append(pikepdf.Name(f"/{glyph_name}"))
    enc["/Differences"] = pikepdf.Array(diffs)


def _extend_simple_widths(
    font_dict: pikepdf.Object,
    new_assignments: list[tuple[int, str, float]],
) -> None:
    """Append /Widths entries for newly-allocated bytes; bump /LastChar.

    With consecutive low-end allocation in ``_allocate_free_bytes``, the
    new bytes are guaranteed contiguous starting at /LastChar + 1 (with
    127 skipped if encountered). So this function simply appends the n
    new widths to /Widths in byte-sorted order and bumps /LastChar.

    /FirstChar is not touched. /Widths length grows from
    (LastChar - FirstChar + 1) to (new_LastChar - FirstChar + 1).

    Raises:
        FontNotFoundError: /FirstChar or /LastChar missing or
            non-integer (M.5 hardening per F.28 atlas), or any allocated
            ``byte_slot`` < /FirstChar (m-5 defense-in-depth).
    """
    # Defense-in-depth guard (m-4): unreachable under the current call
    # graph (caller filters n>=1 in extend_subset), but cheap to enforce.
    if not new_assignments:
        return
    fc_obj = font_dict.get("/FirstChar")
    lc_obj = font_dict.get("/LastChar")
    if fc_obj is None or lc_obj is None:
        raise FontNotFoundError(
            "simple-font extension requires /FirstChar and /LastChar; "
            f"got /FirstChar={fc_obj!r}, /LastChar={lc_obj!r}"
        )
    try:
        fc = int(fc_obj)
        lc = int(lc_obj)
    except (TypeError, ValueError) as exc:
        # F-C-03 / INV-W0-9: drop {exc} (attacker-controlled bytes from a
        # malformed PDF) from the user-visible message. Forensic detail
        # survives in the logger.error.
        logger.error("malformed /FirstChar or /LastChar in simple-font", exc_info=True)
        raise FontNotFoundError(f"malformed /FirstChar or /LastChar: {type(exc).__name__}") from exc
    del lc  # only used to satisfy the M.5 guard's int() conversion check.

    raw_widths: list[object] = []
    if "/Widths" in font_dict:
        raw_widths = list(font_dict["/Widths"])  # type: ignore[call-overload]
    widths: list[float] = [float(w) for w in raw_widths]  # type: ignore[arg-type]

    # Sort assignments by byte to handle the 127-skip case (allocation
    # might produce e.g. [126, 128] if old LC=125; emit them in order).
    sorted_assignments = sorted(new_assignments)
    new_max = sorted_assignments[-1][0]

    # Pad /Widths only when the gap between LC and the first allocated
    # byte > 1 (e.g. 127 skipped). Such gap-bytes never get referenced
    # in any /Differences entry — they're padding for index consistency.
    expected_len = new_max - fc + 1
    while len(widths) < expected_len:
        widths.append(0.0)

    for byte_slot, _glyph_name, width in sorted_assignments:
        # Defense-in-depth guard (m-5): _allocate_free_bytes only emits
        # bytes ≥ /LastChar + 1 ≥ /FirstChar, so this is unreachable under
        # the current call graph; cheap to enforce at the indexing boundary.
        if byte_slot < fc:
            raise FontNotFoundError(
                f"byte_slot {byte_slot} < /FirstChar {fc}; allocator/encoding inconsistency"
            )
        widths[byte_slot - fc] = width

    font_dict["/Widths"] = pikepdf.Array(widths)
    font_dict["/LastChar"] = new_max


def _build_simple_effective_byte_to_unicode(font_dict: pikepdf.Object) -> dict[int, str]:
    """Build the effective byte→unicode map from a simple font's /Encoding.

    Mirrors ``encoding.FontResolver._init_winAnsi`` / ``_init_macRoman`` /
    ``_init_custom`` for simple (non-CID) fonts. Used by
    ``_extend_simple_tier_one_five``'s partition logic to identify chars
    whose standard encoding byte can be HEALED in place rather than
    allocated a new high byte and added to /Differences.

    Encoding resolution (matches FontResolver):
    - ``/Encoding = /WinAnsiEncoding | /MacRomanEncoding`` (Name) → standard table
    - ``/Encoding = Dict { /BaseEncoding ..., /Differences [...] }`` → table
      with /Differences overrides applied
    - Missing or unrecognized → default to /WinAnsiEncoding

    Args:
        font_dict: The simple-font dictionary (read-only — no mutation).

    Returns:
        ``dict[byte, unicode_char]`` reflecting the font's effective byte→char
        mapping per its current /Encoding state. Caller is expected to
        reverse-map (preferring lowest byte) for char→byte lookups, mirroring
        ``encoding._build_reverse_map``.
    """
    # Lazy imports keep the encoding boundary intact at module-load time
    # (CLAUDE.md dependency-boundary table: encoding may not import fonts;
    # fonts may lazy-import encoding helpers, mirroring ``font_has_codepoint``).
    from pdfminer.encodingdb import EncodingDB

    from pdf_edit_engine.encoding import _glyph_name_to_unicode

    enc = font_dict.get("/Encoding")

    if isinstance(enc, pikepdf.Dictionary):
        base_obj = enc.get("/BaseEncoding")
        base_name = str(base_obj) if base_obj is not None else "/WinAnsiEncoding"
    elif enc is not None:
        base_name = str(enc)
    else:
        base_name = "/WinAnsiEncoding"

    if base_name == "/MacRomanEncoding":
        base_table: dict[int, str] = dict(EncodingDB.mac2unicode)
    else:
        # /WinAnsiEncoding is the universal fallback (matches FontResolver).
        base_table = dict(EncodingDB.win2unicode)

    # Apply /Differences overrides when /Encoding is a Dict.
    if isinstance(enc, pikepdf.Dictionary):
        diffs_obj = enc.get("/Differences")
        if diffs_obj is not None:
            cur = 0
            for item in list(diffs_obj):  # type: ignore[call-overload]
                s = str(item)
                if not s.startswith("/"):
                    cur = int(item)
                else:
                    glyph_name = s.lstrip("/")
                    unicode_char = _glyph_name_to_unicode(glyph_name)
                    if unicode_char is not None:
                        base_table[cur] = unicode_char
                    cur += 1

    return base_table


def _heal_simple_widths(
    font_dict: pikepdf.Object,
    healed: list[tuple[int, str, float]],
) -> None:
    """Update existing /Widths entries for chars healed in place.

    Distinct from ``_extend_simple_widths``: this helper updates an entry
    that already exists in /Widths (zero-width sentinel from Word's PDF
    export), without bumping /LastChar or appending new slots. Used for
    Group A chars in ``_extend_simple_tier_one_five``'s partition logic
    (architectural fix from 2026-05-09 Tier 1.5 K-class bug RCA).

    Args:
        font_dict: The simple-font dict (mutated in place).
        healed: List of ``(byte_slot, glyph_name, width)`` tuples.
            ``glyph_name`` is unused here but retained for API symmetry
            with ``_extend_simple_widths``; the byte's mapping to the
            glyph already comes from /BaseEncoding standardly, no
            /Differences entry is needed.

    Raises:
        FontNotFoundError: /FirstChar missing or non-integer; or any
            ``byte_slot`` is outside the existing /Widths array bounds.
    """
    if not healed:
        return
    fc_obj = font_dict.get("/FirstChar")
    if fc_obj is None:
        raise FontNotFoundError(f"simple-font heal requires /FirstChar; got {fc_obj!r}")
    try:
        fc = int(fc_obj)
    except (TypeError, ValueError) as exc:
        # F-C-03 / INV-W0-9: forensic detail in logger; user-visible
        # message drops {exc} (attacker-controlled bytes).
        logger.error("malformed /FirstChar in simple-font heal", exc_info=True)
        raise FontNotFoundError(f"malformed /FirstChar: {type(exc).__name__}") from exc

    raw_widths: list[object] = []
    if "/Widths" in font_dict:
        raw_widths = list(font_dict["/Widths"])  # type: ignore[call-overload]
    widths: list[float] = [float(w) for w in raw_widths]  # type: ignore[arg-type]

    for byte_slot, _glyph_name, width in healed:
        idx = byte_slot - fc
        # Defense-in-depth (m-6): caller (partition logic) only emits
        # byte_slots in [/FirstChar, /LastChar] with existing /Widths
        # entries — but enforce at the indexing boundary.
        if not (0 <= idx < len(widths)):
            raise FontNotFoundError(
                f"heal byte_slot {byte_slot} out of /Widths range "
                f"[{fc}, {fc + len(widths) - 1}]; partition logic inconsistency"
            )
        widths[idx] = width

    font_dict["/Widths"] = pikepdf.Array(widths)
    # /LastChar is intentionally NOT touched — heal stays within existing range.
