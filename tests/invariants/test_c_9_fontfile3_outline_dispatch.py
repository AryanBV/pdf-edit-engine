"""INV-C-9 — /FontFile3 outline-table dispatch split (honest CFF refusal).

C.1 closes a raw-``KeyError`` leak on the CID font-extension path.

``fonts._extract_font_bytes`` labels EVERY ``/FontFile3`` as ``"CFF"`` by
SLOT — it never inspects which outline table the embedded binary actually
carries. For a Type0/CID font whose ``/FontFile3`` is a CFF (Type1C
charstrings, no ``glyf`` table), ``extend_subset`` routes a missing glyph to
``fonts._extend_tier2``. That helper unconditionally reads
``fd["/FontFile2"]`` (``fonts.py`` line ~1184) BEFORE the CFF check inside
``_inject_glyph_in_place`` is ever reached. A CFF CID-font has no
``/FontFile2`` key, so the subscript raises a **raw ``KeyError``**.

``KeyError`` is NOT in ``fonts._FONT_EXTEND_FAIL_EXCS`` (``FontNotFoundError,
EncodingError, OSError, TTLibError``), so it LEAKS out of the edit verb
instead of surfacing as the honest ``font_extension_failed`` Degradation
(``EditResult.success=False``). The leak is MASKED only when the synthetic
PostScript name fails to resolve to a system font (the system-font-not-found
check at ``fonts.py`` ~line 1164 raises ``FontNotFoundError`` first); supply
an explicit ``full_font_path`` (or install a font with the embedded PS name)
and the system-font gate passes, the ``fd["/FontFile2"]`` read is reached,
and the raw ``KeyError`` escapes.

The fix adds a pure ``fonts._classify_outline_table(ttfont)`` that sniffs the
ACTUAL tables present (``glyf`` / ``CFF `` / ``CFF2``), refuses any non-``glyf``
embedded outline in ``_extend_tier2`` BEFORE the ``/FontFile2`` read (raising
``FontNotFoundError`` — in the fail tuple), and routes BOTH ``embedded_type``
producers (``fonts._extract_font_bytes`` and ``locator._detect_embedded_type``)
through the same classifier so they can never disagree. C.1 is an honest
REFUSAL boundary, NOT CFF injection (that is C.3, deferred).

INV-C-9 minted as the next collision-free C-layer (font-extension) slot;
INV-C-{1..8} taken (``test_c_8_symbol_cmap_dual_lookup.py`` is the prior max).

RED today:
* ``test_inv_c_9_cff_cid_extension_surfaces_not_keyerror`` — the public
  ``extend_subset(..., full_font_path=<any glyf ttf>)`` LEAKS a raw
  ``KeyError: '/FontFile2'`` on a CFF CID-font (post-fix: ``FontNotFoundError``
  in ``_FONT_EXTEND_FAIL_EXCS``).
* ``test_inv_c_9_classify_outline_table_cff_vs_glyf`` — the pure
  ``fonts._classify_outline_table`` helper does not exist yet
  (``AttributeError``).
"""

from __future__ import annotations

import glob
import io
import os
from typing import TYPE_CHECKING

import pikepdf
import pytest

if TYPE_CHECKING:
    from pathlib import Path

import pdf_edit_engine
from pdf_edit_engine.fonts import (
    _FONT_EXTEND_FAIL_EXCS,
    _extract_font_bytes,
    _get_font_objects,
    extend_subset,
)
from pdf_edit_engine.locator import _detect_embedded_type
from tests.corpus_builders import build_cff_font_pdf, build_truetype_baseline_pdf


def _any_installed_glyf_ttf() -> str | None:
    """Return the path to any installed TrueType (``glyf``) font, or None.

    The CFF builder's synthetic PostScript name (``CorpusCFF-Regular``) does
    not resolve to a system font, so the ``_extend_tier2`` system-font gate
    would raise ``FontNotFoundError`` and MASK the ``KeyError`` leak. Passing
    a real ``full_font_path`` makes that gate pass so the leak site (the
    ``fd["/FontFile2"]`` read) is actually reached.
    """
    search_dirs = [
        r"C:\Windows\Fonts",
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        "/Library/Fonts",
        "/System/Library/Fonts",
        os.path.expanduser("~/.fonts"),
        os.path.expanduser("~/Library/Fonts"),
    ]
    for directory in search_dirs:
        if not os.path.isdir(directory):
            continue
        for pattern in ("*.ttf", "*.TTF"):
            hits = glob.glob(os.path.join(directory, "**", pattern), recursive=True)
            if hits:
                return sorted(hits)[0]
    return None


# ── (a) RED today: public extension path leaks a raw KeyError ───────────


def test_inv_c_9_cff_cid_extension_surfaces_not_keyerror() -> None:
    """INV-C-9.1: CFF CID-font extension must SURFACE, never raw-KeyError.

    Regression guard for the ``_extend_tier2`` ``fd["/FontFile2"]`` leak. The
    public ``extend_subset`` API is invoked for a glyph absent from the
    embedded font (``'Z'`` is not in ``"CFF Outline Sample 2026"``), forcing
    the Tier 1.5 injection path. A real ``full_font_path`` is supplied so the
    system-font gate passes and the leak site is reached.

    PRE-FIX (RED): a raw ``KeyError: '/FontFile2'`` escapes — NOT in
    ``_FONT_EXTEND_FAIL_EXCS`` — so the caller crashes instead of degrading.
    POST-FIX (GREEN): ``_extend_tier2`` classifies the embedded outline as
    non-``glyf`` and raises ``FontNotFoundError`` (in the fail tuple), which
    the edit verbs translate into an honest ``font_extension_failed``
    Degradation with ``success=False``.
    """
    ttf = _any_installed_glyf_ttf()
    if ttf is None:
        pytest.skip("no installed TrueType (glyf) font to satisfy the system-font gate")

    pdf_bytes = build_cff_font_pdf()
    pdf = pikepdf.open(io.BytesIO(pdf_bytes))
    try:
        page = pdf.pages[0]
        # 'Z' is absent from the CFF font's coverage, so it routes to the
        # Tier 1.5 in-place injection path that reads fd["/FontFile2"].
        try:
            extend_subset(pdf, page, "F1", "Z", full_font_path=ttf)
        except _FONT_EXTEND_FAIL_EXCS:
            # Honest refusal in the documented fail tuple — the contract.
            return
        except KeyError as exc:  # noqa: PT017 — the leak we are pinning
            pytest.fail(
                "INV-C-9 violation: CFF CID-font extension leaked a raw "
                f"KeyError({exc!r}) from the fd['/FontFile2'] read in "
                "_extend_tier2 — KeyError is NOT in _FONT_EXTEND_FAIL_EXCS, "
                "so it escapes the edit verb instead of surfacing as "
                "font_extension_failed."
            )
        else:
            pytest.fail(
                "INV-C-9 violation: CFF CID-font extension neither refused "
                "nor injected — C.1 is a refusal boundary (CFF injection is "
                "C.3, deferred), so extend_subset must raise a "
                "FontNotFoundError here."
            )
    finally:
        pdf.close()


def test_inv_c_9_cff_cid_replace_surfaces_font_extension_failed(tmp_path: Path) -> None:
    """INV-C-9.2: the public ``replace`` path degrades, never raises KeyError.

    End-to-end through ``find`` + ``replace``. The replacement introduces a
    glyph absent from the CFF font (``'Z'``), forcing the extension path. The
    edit must NEVER propagate a raw ``KeyError``; it must return
    ``success=False`` carrying a ``font_extension_failed`` Degradation (or
    surface a translated ``PDFEditError`` subclass).

    NOTE: today this probe already returns the honest result — but for the
    WRONG reason (the synthetic ``CorpusCFF-Regular`` PS name fails the
    system-font lookup, so ``_extend_tier2`` refuses at the system-font gate
    BEFORE the ``fd['/FontFile2']`` leak). C.1 makes the refusal honest
    (outline-table classification) rather than incidental, and this probe
    permanently guards that a raw ``KeyError`` never reaches a ``replace``
    caller for a CFF CID-font.
    """
    pdf_bytes = build_cff_font_pdf()
    src = tmp_path / "cff_cid.pdf"
    out = tmp_path / "cff_cid_out.pdf"
    src.write_bytes(pdf_bytes)

    matches = pdf_edit_engine.find(str(src), "CFF")
    assert matches, "fixture must contain the literal 'CFF' to edit"

    try:
        result = pdf_edit_engine.replace(str(src), matches[0], "CFZ", str(out))
    except KeyError as exc:  # noqa: PT017 — the leak we are pinning
        pytest.fail(
            f"INV-C-9 violation: replace() on a CFF CID-font leaked a raw "
            f"KeyError({exc!r}) instead of returning font_extension_failed."
        )
    except pdf_edit_engine.PDFEditError:
        # A translated PDFEditError subclass is an acceptable honest surface.
        return

    assert not result.success, (
        "CFF CID-font extension must not silently succeed (C.1 is a refusal "
        "boundary; CFF injection is C.3, deferred)"
    )
    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert "font_extension_failed" in kinds, (
        "INV-C-9 violation: replace() returned success=False on a CFF "
        f"CID-font without a font_extension_failed Degradation; kinds={kinds}"
    )


# ── (b) RED today: the pure outline-table classifier does not exist ─────


def test_inv_c_9_classify_outline_table_cff_vs_glyf() -> None:
    """INV-C-9.3: ``_classify_outline_table`` sniffs ACTUAL tables, not slots.

    Unit probe for the new pure helper. It must return ``"cff"`` for a font
    whose embedded binary carries CFF (Type1C) charstrings and ``"glyf"`` for
    a TrueType (``glyf``) font — derived from the tables actually present, NOT
    from the ``/FontFile2`` vs ``/FontFile3`` slot.

    PRE-FIX (RED): ``fonts._classify_outline_table`` does not exist
    (``AttributeError``).
    """
    from fontTools.ttLib import TTFont

    import pdf_edit_engine.fonts as fonts_mod

    classify = getattr(fonts_mod, "_classify_outline_table", None)
    assert classify is not None, (
        "INV-C-9 violation: fonts._classify_outline_table is not defined — "
        "the pure outline-table classifier is the root of the C.1 dispatch "
        "split."
    )

    # CFF font (the synthetic /FontFile3 Type1C binary).
    cff_pdf = pikepdf.open(io.BytesIO(build_cff_font_pdf()))
    try:
        _, _, cff_fd = _get_font_objects(cff_pdf.pages[0], "F1")
        cff_bytes, _ = _extract_font_bytes(cff_fd)
        cff_tt = TTFont(io.BytesIO(cff_bytes))
        try:
            assert classify(cff_tt) == "cff", (
                "a CFF (Type1C) binary must classify as 'cff' by table-sniff"
            )
        finally:
            cff_tt.close()
    finally:
        cff_pdf.close()

    # TrueType (glyf) control.
    tt_bytes = build_truetype_baseline_pdf()
    if tt_bytes is None:
        pytest.skip("no host TrueType (glyf) font for the glyf control")
    tt_pdf = pikepdf.open(io.BytesIO(tt_bytes))
    try:
        _, _, tt_fd = _get_font_objects(tt_pdf.pages[0], "F1")
        glyf_bytes, _ = _extract_font_bytes(tt_fd)
        glyf_tt = TTFont(io.BytesIO(glyf_bytes))
        try:
            assert classify(glyf_tt) == "glyf", (
                "a TrueType binary with a glyf table must classify as 'glyf'"
            )
        finally:
            glyf_tt.close()
    finally:
        tt_pdf.close()


# ── (c) producers must agree on embedded_type for the same font ─────────


def test_inv_c_9_embedded_type_producers_agree_on_cff() -> None:
    """INV-C-9.4: both ``embedded_type`` producers agree for a CFF CID-font.

    ``fonts._extract_font_bytes`` and ``locator._detect_embedded_type`` are
    two independent producers of ``FontInfo.embedded_type``. After C.1 routes
    both through ``_classify_outline_table`` they must agree by construction;
    this probe pins that they never diverge for the same font.

    (For a plain ``/FontFile3`` CFF the two SLOT heuristics already agree
    today — both report ``CFF`` — so this is a forward-guard against the C.1
    routing change introducing a divergence.)
    """
    pdf = pikepdf.open(io.BytesIO(build_cff_font_pdf()))
    try:
        page = pdf.pages[0]
        font_dict, _cid, fd = _get_font_objects(page, "F1")

        _, et_fonts = _extract_font_bytes(fd)
        et_locator = _detect_embedded_type(font_dict, "/Type0")

        assert et_fonts == et_locator, (
            "INV-C-9 violation: the two embedded_type producers disagree for "
            f"the same CFF CID-font: fonts._extract_font_bytes={et_fonts!r} vs "
            f"locator._detect_embedded_type={et_locator!r}"
        )
    finally:
        pdf.close()
