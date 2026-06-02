"""Builders: CID-keyed CFF (ROS) injection fixtures for C.3 (INV-C-11/12/13).

C.3 adds in-place glyph injection for a CID-keyed (ROS) CFF embedded as
``/FontFile3`` (the dominant real shape for a CFF CIDFont is a BARE CFF —
``/Subtype /Type1C``, no sfnt directory). The pre-C.3 engine REFUSES every
CFF extension honestly (C.1 outline-table gate → ``font_extension_failed``),
so these fixtures are the adversarial inputs that drive the RED proof:

- :func:`build_cff_cid_missing_glyph_pdf` — the font UNDER EDIT: a genuinely
  CID-keyed (``hasattr(TopDict, "ROS")``) bare CFF carrying ``.notdef`` + ``A``
  + ``B`` (CIDs 1, 2) and renders ``<00010002>`` ("AB"). It deliberately
  LACKS the glyph for ``C`` — extension to "ABC" must inject ``C`` from the
  donor at CID == GID == 3.
- :func:`build_cff_donor_bytes` — a synthetic CFF donor (sfnt-wrapped OTTO so
  ``TTFont.getBestCmap()`` / ``getGlyphSet()`` / ``["hmtx"]`` work uniformly)
  carrying ``C`` with a DISTINCT triangle outline, so the injected outline is
  provably the donor's, not a box artifact. ``upem`` is parameterised so the
  INV-C-13 UPEM-mismatch case can build a 2048-upem donor.
- Out-of-scope shape builders for INV-C-13 hard-fails: CFF2 embedded,
  name-keyed (non-ROS) simple OTF, a TrueType (``glyf``) donor, and a
  composite/seac donor.

Every font is synthesised in-process via ``fontTools`` — NO commercial /
host font, NO ``skipif``. The CID-keyed assembly mirrors the proven spike
``experiments/v020_c3_cff_spike/probe_verify.py`` (``build_cid_font_bytes``):
``FontBuilder`` produces a name-keyed CFF, then ``ROS`` / ``CIDFontVersion``
/ ``CIDCount`` / ``charset`` / ``FDArray`` / ``FDSelect`` are attached and the
per-glyph private dict is rewired so the saved binary is genuinely CID-keyed.

Deterministic (``FIXED_FONT_EPOCH``) and host-font-free; no builder returns
``None``.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pikepdf

from ._common import FIXED_FONT_EPOCH, build_tounicode_cmap, emit_or_write, save_pdf_deterministic

if TYPE_CHECKING:
    from pathlib import Path

# The font under edit carries CIDs 1, 2 = A, B and renders "AB"; the donor
# carries C. Extension to "ABC" must inject C at CID == GID == 3.
_EMBEDDED_CHARS = ["A", "B"]
_EMBEDDED_PS_NAME = "SynthCIDCFF-Regular"
_DONOR_PS_NAME = "SynthCFFDonor-Regular"
_UPM = 1000

# ── Sparse / non-contiguous-CID collision fixture (INV-C-12) ─────────────
#
# Real subsetted CID-keyed CFF fonts keep their ORIGINAL (non-contiguous)
# CIDs in the charset while packing the GIDs densely, so a pre-existing CID
# can be >= the glyph count. The COLLISION shape pins exactly that: ``A`` at
# CID 1 / GID 1 and ``B`` at CID 3 / GID 2 (CID 2 is skipped — genuinely
# non-contiguous), so the glyph count is 3 yet CID 3 is already taken. The
# naive injector picks ``new_cid = new_gid = len(glyphOrder) = 3``, which
# collides with the pre-existing ``cid00003`` ("B"): the donor outline lands
# at GID 2's CID, the charset gains a DUPLICATE ``cid00003``, and the
# ``/ToUnicode`` entry ``<0003> -> B`` is silently overwritten by
# ``<0003> -> C`` (``get_text`` "AB" -> "AC"). This is the ARY-278
# "1ova,ndustries" no-renumber failure ported to CFF.
_COLLISION_PS_NAME = "SynthSparseCIDCFF-Regular"
# (CID for GID 0, 1, 2). GID 0 is .notdef (CID 0); A at CID 1; B at CID 3.
_COLLISION_CID_FOR_GID = [0, 1, 3]
_COLLISION_CHARS = ["A", "B"]

# ── ToUnicode-only-CID collision fixture (INV-C-12, remediation #2) ──────
#
# The sparse fixture above pins a CHARSET-CID collision (the colliding CID 3
# IS in the embedded charset). A SECOND, distinct collision vector exists: a
# CID referenced ONLY by ``/ToUnicode`` (so the rendered text depends on it)
# but ABSENT from the embedded CFF charset. The embedded charset is a tight
# ``.notdef`` + ``cid00001`` ("A") (charset max CID 1, glyph count 2), yet the
# ``/ToUnicode`` maps CID 1 -> A AND CID 2 -> Q, and the content renders
# ``<0001 0002>`` ("AQ"). The injector's collision math only folds in the
# EMBEDDED charset CIDs (``max_existing_cid + 1`` == 2) and ``len(glyphOrder)``
# == 2 — it does NOT see the ``/ToUnicode``-only CID 2 unless the production
# caller threads ``min_cid = max(/ToUnicode CIDs) + 1``. Without that thread,
# injecting "C" picks ``new_cid = max(2, 2, 0) = 2``, which COLLIDES with the
# ToUnicode-only CID 2: the appended ``/ToUnicode`` block remaps CID 2 to "C",
# so ``get_text`` reads "AQ" -> "AC" — the SAME ARY-278 corruption class, a
# different vector that the charset-CID collision test cannot catch (its CID 3
# is in the charset, so ``max_existing_cid`` already covers it). The glyf
# sibling preserves CID 2 -> Q because it folds the ``/ToUnicode`` CIDs into
# its placement floor; the CFF path must match that behaviour.
_TOUNICODE_ONLY_PS_NAME = "SynthTUOnlyCIDCFF-Regular"
_TOUNICODE_ONLY_CHARS = ["A"]


def _box_charstring(width: int) -> object:
    """A simple filled box charstring (the embedded-font glyph shape)."""
    from fontTools.pens.t2CharStringPen import T2CharStringPen  # type: ignore[import-untyped]

    pen = T2CharStringPen(width, None)
    pen.moveTo((50, 0))
    pen.lineTo((50, 700))
    pen.lineTo((width - 50, 700))
    pen.lineTo((width - 50, 0))
    pen.closePath()
    return pen.getCharString()


def _triangle_charstring(width: int) -> object:
    """A DISTINCT triangle charstring (the donor glyph shape).

    The triangle (moveTo + two lineTo + closePath, a 3-point contour) is
    visually and structurally distinct from the 4-point box, so a test can
    prove the injected outline is the donor's, not a copy of an embedded box.
    """
    from fontTools.pens.t2CharStringPen import T2CharStringPen  # type: ignore[import-untyped]

    pen = T2CharStringPen(width, None)
    pen.moveTo((100, 0))
    pen.lineTo((300, 650))
    pen.lineTo((500, 0))
    pen.closePath()
    return pen.getCharString()


def _build_cid_keyed_cff_ttfont(
    glyph_chars: list[str],
    ps_name: str,
    *,
    upem: int = _UPM,
    distinct_outline: bool = False,
    num_fd: int = 1,
) -> object:
    """Build a genuinely CID-keyed (ROS) sfnt-CFF ``TTFont`` (CID == GID).

    Mirrors ``experiments/v020_c3_cff_spike/probe_verify.py``
    ``build_cid_font_bytes``: a ``FontBuilder`` name-keyed CFF, upgraded to
    CID-keyed by attaching ``ROS`` / ``CIDFontVersion`` / ``CIDCount`` /
    ``charset`` / ``FDArray`` / ``FDSelect`` and rewiring per-glyph privates.

    Glyph ``cidNNNNN`` names are assigned at CID == GID (``.notdef`` is GID 0,
    then ``glyph_chars`` at GIDs 1..N), and a Unicode cmap maps each char's
    codepoint to its ``cidNNNNN`` name so a donor's ``getBestCmap()`` resolves.

    Args:
        glyph_chars: Characters to embed (besides ``.notdef``), CID order.
        ps_name: PostScript / FontBuilder font name.
        upem: unitsPerEm (parameterised for the UPEM-mismatch INV-C-13 case).
        distinct_outline: When True every non-``.notdef`` glyph is the donor
            triangle; otherwise the embedded box.
        num_fd: Number of FontDicts in the FDArray. 1 is the supported
            slice-1 shape; >1 builds the multi-FD out-of-scope refusal case.

    Returns:
        A ``fontTools.ttLib.TTFont`` with a genuinely CID-keyed ``CFF `` table.
    """
    from fontTools.cffLib import (  # type: ignore[import-untyped]
        FDArrayIndex,
        FDSelect,
        TopDict,
    )
    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]

    glyph_names = [".notdef"] + [f"cid{i + 1:05d}" for i in range(len(glyph_chars))]
    cmap = {ord(c): f"cid{i + 1:05d}" for i, c in enumerate(glyph_chars)}

    charstrings: dict[str, object] = {}
    for gn in glyph_names:
        if gn != ".notdef" and distinct_outline:
            charstrings[gn] = _triangle_charstring(500)
        else:
            charstrings[gn] = _box_charstring(500)

    fb = FontBuilder(upem, isTTF=False)
    fb.setupGlyphOrder(glyph_names)
    fb.setupCharacterMap(cmap)
    fb.setupCFF(ps_name, {"FullName": ps_name}, charstrings, {})
    fb.setupHorizontalMetrics({gn: (500, 0) for gn in glyph_names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": ps_name, "styleName": "Regular", "psName": ps_name})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=700)
    fb.setupPost()
    fb.setupMaxp()
    f = fb.font
    f["head"].created = FIXED_FONT_EPOCH
    f["head"].modified = FIXED_FONT_EPOCH

    cff = f["CFF "].cff
    td = cff[cff.fontNames[0]]
    # ── Upgrade the name-keyed CFF to genuinely CID-keyed (ROS). ──
    td.ROS = ("Adobe", "Identity", 0)
    td.CIDFontVersion = 0
    td.CIDCount = len(glyph_names)
    td.charset = list(glyph_names)

    # FDArray: one or more FontDicts. The slice-1 supported shape has exactly
    # one; num_fd > 1 builds the multi-FD out-of-scope refusal case.
    fd_array = FDArrayIndex()
    base_private = td.Private
    for _ in range(max(1, num_fd)):
        font_dict = TopDict()
        font_dict.Private = base_private
        font_dict.rawDict["Private"] = (0, 0)
        fd_array.append(font_dict)
    td.FDArray = fd_array

    fd_select = FDSelect(numGlyphs=len(glyph_names), format=3)
    fd_select.gidArray = [0] * len(glyph_names)  # every glyph routes to FD 0
    td.FDSelect = fd_select

    td.CharStrings.fdArray = fd_array
    td.CharStrings.fdSelect = fd_select
    for gn in glyph_names:
        td.CharStrings[gn].private = fd_array[0].Private

    if "Private" in td.rawDict:
        del td.rawDict["Private"]
    if hasattr(td, "Private"):
        del td.Private
    return f


def _build_sparse_cid_keyed_cff_ttfont(
    cid_for_gid: list[int],
    glyph_chars: list[str],
    ps_name: str,
    *,
    upem: int = _UPM,
) -> object:
    """Build a SPARSE (non-contiguous-CID) CID-keyed CFF ``TTFont``.

    Unlike :func:`_build_cid_keyed_cff_ttfont` (which assigns CID == GID for
    every glyph), this assigns each GID an EXPLICIT, possibly non-contiguous
    CID via ``cid_for_gid``. The charset glyph name for GID ``g`` is
    ``cid{cid_for_gid[g]:05d}`` (``.notdef`` for CID 0), so the charset
    encodes the real CIDs while the GIDs stay densely packed — the dominant
    real shape for a subsetted CID-keyed CFF. ``glyph_chars`` map to GIDs
    ``1..N`` (their codepoints point at the corresponding charset names) so
    ``get_text`` reads them back through ``/ToUnicode``.

    Args:
        cid_for_gid: CID assigned to each GID, in GID order; ``cid_for_gid[0]``
            must be 0 (``.notdef``).
        glyph_chars: Characters at GIDs ``1..N`` (one per non-``.notdef`` GID).
        ps_name: PostScript / FontBuilder font name.
        upem: unitsPerEm.

    Returns:
        A genuinely CID-keyed (``ROS``) ``fontTools.ttLib.TTFont`` whose
        charset CIDs are exactly ``cid_for_gid``.
    """
    from fontTools.cffLib import (  # type: ignore[import-untyped]
        FDArrayIndex,
        FDSelect,
        TopDict,
    )
    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]

    glyph_names = [".notdef" if cid == 0 else f"cid{cid:05d}" for cid in cid_for_gid]
    cmap = {ord(c): glyph_names[i + 1] for i, c in enumerate(glyph_chars)}

    charstrings: dict[str, object] = {gn: _box_charstring(500) for gn in glyph_names}

    fb = FontBuilder(upem, isTTF=False)
    fb.setupGlyphOrder(glyph_names)
    fb.setupCharacterMap(cmap)
    fb.setupCFF(ps_name, {"FullName": ps_name}, charstrings, {})
    fb.setupHorizontalMetrics({gn: (500, 0) for gn in glyph_names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": ps_name, "styleName": "Regular", "psName": ps_name})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=700)
    fb.setupPost()
    fb.setupMaxp()
    f = fb.font
    f["head"].created = FIXED_FONT_EPOCH
    f["head"].modified = FIXED_FONT_EPOCH

    cff = f["CFF "].cff
    td = cff[cff.fontNames[0]]
    td.ROS = ("Adobe", "Identity", 0)
    td.CIDFontVersion = 0
    # CIDCount must cover the largest pre-existing CID (so a viewer treats the
    # charset CIDs as in-range) — this is what makes CID 3 a genuine occupied
    # slot the naive injector collides with.
    td.CIDCount = max(cid_for_gid) + 1
    td.charset = list(glyph_names)

    fd_array = FDArrayIndex()
    base_private = td.Private
    font_dict = TopDict()
    font_dict.Private = base_private
    font_dict.rawDict["Private"] = (0, 0)
    fd_array.append(font_dict)
    td.FDArray = fd_array

    fd_select = FDSelect(numGlyphs=len(glyph_names), format=3)
    fd_select.gidArray = [0] * len(glyph_names)
    td.FDSelect = fd_select

    td.CharStrings.fdArray = fd_array
    td.CharStrings.fdSelect = fd_select
    for gn in glyph_names:
        td.CharStrings[gn].private = fd_array[0].Private

    if "Private" in td.rawDict:
        del td.rawDict["Private"]
    if hasattr(td, "Private"):
        del td.Private
    return f


def _bare_cff_from_ttfont(ttfont: object) -> bytes:
    """Save a ``TTFont`` and return ONLY its raw ``CFF `` table (bare Type1C)."""
    from fontTools.ttLib.sfnt import SFNTReader  # type: ignore[import-untyped]

    buf = io.BytesIO()
    ttfont.save(buf)  # type: ignore[attr-defined]
    return SFNTReader(io.BytesIO(buf.getvalue()))["CFF "]


def _sfnt_bytes_from_ttfont(ttfont: object) -> bytes:
    """Save a ``TTFont`` and return the full sfnt (OTTO) bytes."""
    buf = io.BytesIO()
    ttfont.save(buf)  # type: ignore[attr-defined]
    return buf.getvalue()


def _assemble_cid_cff_pdf(
    cff_stream_bytes: bytes,
    subtype: str,
    ps_name: str,
    cid_to_unicode: dict[int, int],
    content_cids: list[int],
    out_path: Path | None,
) -> bytes:
    """Assemble a Type0 / CIDFontType0 / Identity-H PDF around a CFF binary.

    Args:
        cff_stream_bytes: The ``/FontFile3`` payload (bare CFF or full sfnt).
        subtype: ``"/Type1C"`` (bare) or ``"/OpenType"`` (sfnt-wrapped).
        ps_name: PostScript / BaseFont name.
        cid_to_unicode: CID → Unicode codepoint map for ``/ToUnicode``.
        content_cids: CIDs to render in the content stream (one ``Tj``).
        out_path: Optional destination file.

    Returns:
        The deterministic PDF bytes.
    """
    pdf = pikepdf.Pdf.new()
    try:
        font_stream = pikepdf.Stream(pdf, cff_stream_bytes)
        font_stream["/Subtype"] = pikepdf.Name(subtype)

        font_descriptor = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/FontDescriptor"),
                    "/FontName": pikepdf.Name("/" + ps_name),
                    "/Flags": 4,
                    "/FontBBox": pikepdf.Array([0, -200, 600, 800]),
                    "/ItalicAngle": 0,
                    "/Ascent": 800,
                    "/Descent": -200,
                    "/CapHeight": 700,
                    "/StemV": 80,
                    "/FontFile3": font_stream,
                }
            )
        )

        # /W lists each existing CID width. Sparse on purpose is fine; the
        # widths are real for the embedded CIDs.
        w_flat: list[object] = []
        for cid in sorted(cid_to_unicode):
            w_flat.append(cid)
            w_flat.append(pikepdf.Array([500]))

        cid_font = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/CIDFontType0"),
                    "/BaseFont": pikepdf.Name("/" + ps_name),
                    "/CIDSystemInfo": pikepdf.Dictionary(
                        {
                            "/Registry": pikepdf.String("Adobe"),
                            "/Ordering": pikepdf.String("Identity"),
                            "/Supplement": 0,
                        }
                    ),
                    "/FontDescriptor": font_descriptor,
                    "/DW": 1000,
                    "/W": pikepdf.Array(w_flat),
                    "/CIDToGIDMap": pikepdf.Name("/Identity"),
                }
            )
        )

        tounicode = build_tounicode_cmap(cid_to_unicode)
        type0 = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/" + ps_name),
                "/Encoding": pikepdf.Name("/Identity-H"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
                "/ToUnicode": pikepdf.Stream(pdf, tounicode.encode("latin-1")),
            }
        )

        hex_cids = "".join(f"{cid:04X}" for cid in content_cids)
        content = (f"BT\n/F1 24 Tf\n1 0 0 1 72 720 Tm\n<{hex_cids}> Tj\nET").encode("latin-1")

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
        return emit_or_write(save_pdf_deterministic(pdf), out_path)
    finally:
        pdf.close()


# ── In-scope fixtures (the C.3 happy path) ──────────────────────────────


def build_cff_cid_missing_glyph_pdf(out_path: Path | None = None) -> bytes:
    """The font UNDER EDIT: a CID-keyed BARE CFF missing the glyph for 'C'.

    Genuinely CID-keyed (``hasattr(TopDict, "ROS")``), embedded as
    ``/FontFile3 /Type1C`` (bare CFF, no sfnt directory — the dominant real
    shape). Carries ``.notdef`` + ``A`` + ``B`` (CIDs 1, 2) and renders
    ``<00010002>`` ("AB"). It LACKS the glyph for ``C`` (no CID 3, no
    ``/ToUnicode`` entry for 'C'), so editing "AB" → "ABC" forces extension.

    Args:
        out_path: Optional file to write the PDF to.

    Returns:
        The deterministic PDF bytes. Synthesised in-process; never None.
    """
    embedded = _build_cid_keyed_cff_ttfont(_EMBEDDED_CHARS, _EMBEDDED_PS_NAME)
    try:
        cff_bytes = _bare_cff_from_ttfont(embedded)
    finally:
        embedded.close()  # type: ignore[attr-defined]
    return _assemble_cid_cff_pdf(
        cff_bytes,
        subtype="/Type1C",
        ps_name=_EMBEDDED_PS_NAME,
        cid_to_unicode={1: ord("A"), 2: ord("B")},
        content_cids=[1, 2],
        out_path=out_path,
    )


def build_cff_cid_missing_glyph_wrapped_pdf(out_path: Path | None = None) -> bytes:
    """Same as :func:`build_cff_cid_missing_glyph_pdf` but sfnt-WRAPPED.

    Embedded as ``/FontFile3 /OpenType`` (magic ``b'OTTO'``). Exercises the
    wrapped-vs-bare path of the C.3 ``_load_cff_as_ttfont`` helper; the C.1
    outline-table gate refuses this with the explicit "CFF injection is
    deferred" message TODAY.

    Args:
        out_path: Optional file to write the PDF to.

    Returns:
        The deterministic PDF bytes.
    """
    embedded = _build_cid_keyed_cff_ttfont(_EMBEDDED_CHARS, _EMBEDDED_PS_NAME)
    try:
        sfnt_bytes = _sfnt_bytes_from_ttfont(embedded)
    finally:
        embedded.close()  # type: ignore[attr-defined]
    return _assemble_cid_cff_pdf(
        sfnt_bytes,
        subtype="/OpenType",
        ps_name=_EMBEDDED_PS_NAME,
        cid_to_unicode={1: ord("A"), 2: ord("B")},
        content_cids=[1, 2],
        out_path=out_path,
    )


def build_cff_cid_sparse_collision_pdf(out_path: Path | None = None) -> bytes:
    """The COLLISION fixture: a SPARSE (non-contiguous-CID) CID-keyed bare CFF.

    A genuinely CID-keyed (``hasattr(TopDict, "ROS")``) bare CFF
    (``/FontFile3 /Type1C``) carrying ``.notdef`` + ``A`` (CID 1 / GID 1) +
    ``B`` (CID 3 / GID 2). CID 2 is deliberately SKIPPED (genuinely
    non-contiguous), so the glyph count is 3 yet CID 3 is already occupied by
    ``B``. Renders ``<00010003>`` ("AB") and LACKS the glyph for ``C``.

    This is the adversarial input the naive injector mishandles: it picks
    ``new_cid = new_gid = len(glyphOrder) = 3`` with no free-CID check, which
    COLLIDES with the pre-existing ``cid00003`` ("B"). The donor outline
    lands at GID 2's CID, the charset gains a duplicate ``cid00003``, and the
    ``/ToUnicode`` entry ``<0003> -> B`` is overwritten by ``<0003> -> C`` —
    ``get_text`` reads "AB" -> "AC", silently corrupting unrelated text with
    ``success=True`` and ZERO degradations (the ARY-278 "1ova,ndustries"
    no-renumber failure ported to CFF). The collision-free fix mirrors the
    ``glyf`` path: ``new_cid = max(len(glyphOrder), max_existing_cid + 1)``.

    Args:
        out_path: Optional file to write the PDF to.

    Returns:
        The deterministic PDF bytes. Synthesised in-process; never None.
    """
    embedded = _build_sparse_cid_keyed_cff_ttfont(
        _COLLISION_CID_FOR_GID, _COLLISION_CHARS, _COLLISION_PS_NAME
    )
    try:
        cff_bytes = _bare_cff_from_ttfont(embedded)
    finally:
        embedded.close()  # type: ignore[attr-defined]
    return _assemble_cid_cff_pdf(
        cff_bytes,
        subtype="/Type1C",
        ps_name=_COLLISION_PS_NAME,
        # CID 1 -> A, CID 3 -> B (CID 2 skipped).
        cid_to_unicode={1: ord("A"), 3: ord("B")},
        content_cids=[1, 3],
        out_path=out_path,
    )


def build_cff_cid_tounicode_only_collision_pdf(out_path: Path | None = None) -> bytes:
    """The TOUNICODE-ONLY-CID collision fixture (INV-C-12 remediation #2).

    A genuinely CID-keyed (``hasattr(TopDict, "ROS")``) bare CFF
    (``/FontFile3 /Type1C``) whose embedded charset is the TIGHT
    ``.notdef`` + ``cid00001`` ("A") (charset max CID 1, glyph count 2) — it
    does NOT carry a glyph for CID 2. Yet the ``/ToUnicode`` maps CID 1 -> A
    AND CID 2 -> Q, and the content stream renders ``<0001 0002>`` ("AQ"), so
    the rendered text DEPENDS on the ToUnicode-only CID 2. It LACKS the glyph
    for the inject target ``C``.

    This is the second, charset-independent collision vector: the injector's
    placement math folds in the EMBEDDED charset CIDs (``max_existing_cid + 1``
    == 2) and ``len(glyphOrder)`` (== 2) but NOT the ``/ToUnicode``-only CID 2
    — unless the production caller threads ``min_cid = max(/ToUnicode CIDs)+1``.
    Without that thread, injecting ``C`` picks ``new_cid = max(2, 2, 0) = 2``,
    which COLLIDES with the ToUnicode-only CID 2: the appended ``/ToUnicode``
    block remaps CID 2 -> "C", so ``get_text`` reads "AQ" -> "AC" — silent
    corruption of pre-existing text with ``success=True`` and ZERO
    degradations. The charset-CID collision fixture
    (:func:`build_cff_cid_sparse_collision_pdf`) cannot catch this: its
    colliding CID 3 IS in the charset, so ``max_existing_cid`` already covers
    it. The fix mirrors the ``glyf`` path: the caller threads the
    ``/ToUnicode`` CID floor so ``new_cid`` clears BOTH the charset and the
    ToUnicode CIDs (lands at CID >= 3).

    Args:
        out_path: Optional file to write the PDF to.

    Returns:
        The deterministic PDF bytes. Synthesised in-process; never None.
    """
    embedded = _build_cid_keyed_cff_ttfont(_TOUNICODE_ONLY_CHARS, _TOUNICODE_ONLY_PS_NAME)
    try:
        cff_bytes = _bare_cff_from_ttfont(embedded)
    finally:
        embedded.close()  # type: ignore[attr-defined]
    return _assemble_cid_cff_pdf(
        cff_bytes,
        subtype="/Type1C",
        ps_name=_TOUNICODE_ONLY_PS_NAME,
        # CID 1 -> A is in the charset; CID 2 -> Q is ToUnicode-ONLY (no glyph).
        cid_to_unicode={1: ord("A"), 2: ord("Q")},
        content_cids=[1, 2],
        out_path=out_path,
    )


def build_cff_donor_bytes(chars: tuple[str, ...] = ("C",), upem: int = _UPM) -> bytes:
    """Synthesize a CID-keyed sfnt CFF donor carrying ``chars`` (triangles).

    The donor is sfnt-WRAPPED (OTTO) so ``TTFont(BytesIO(...)).getBestCmap()``,
    ``getGlyphSet()`` and ``["hmtx"]`` work uniformly without bare-CFF
    wrapping — it is the explicit ``full_font_path`` a test passes. Each glyph
    is a DISTINCT triangle so the injected outline is provably the donor's.

    Args:
        chars: Donor characters (each gets a triangle outline).
        upem: unitsPerEm (2048 builds the UPEM-mismatch INV-C-13 donor).

    Returns:
        The full sfnt (OTTO) donor bytes.
    """
    donor = _build_cid_keyed_cff_ttfont(
        list(chars), _DONOR_PS_NAME, upem=upem, distinct_outline=True
    )
    try:
        return _sfnt_bytes_from_ttfont(donor)
    finally:
        donor.close()  # type: ignore[attr-defined]


# ── Out-of-scope shape builders (INV-C-13 hard-fail inputs) ──────────────


def build_cff2_cid_pdf(out_path: Path | None = None) -> bytes:
    """A CFF2-outlined CID font (``/FontFile3``) — INV-C-13 hard-fail input.

    CFF2 charstrings cannot be injected by the slice-1 CFF (Type2) injector;
    extension must refuse via ``font_extension_failed``.

    Args:
        out_path: Optional file to write the PDF to.

    Returns:
        The deterministic PDF bytes.
    """
    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]
    from fontTools.pens.t2CharStringPen import T2CharStringPen  # type: ignore[import-untyped]

    glyph_names = [".notdef", "cid00001", "cid00002"]
    cmap = {ord("A"): "cid00001", ord("B"): "cid00002"}
    charstrings: dict[str, object] = {}
    for gn in glyph_names:
        pen = T2CharStringPen(500, None)
        pen.moveTo((50, 0))
        pen.lineTo((50, 700))
        pen.lineTo((450, 700))
        pen.lineTo((450, 0))
        pen.closePath()
        charstrings[gn] = pen.getCharString()

    fb = FontBuilder(_UPM, isTTF=False)
    fb.setupGlyphOrder(glyph_names)
    fb.setupCharacterMap(cmap)
    fb.setupCFF2(charstrings)
    fb.setupHorizontalMetrics({gn: (500, 0) for gn in glyph_names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "SynthCFF2", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=700)
    fb.setupPost()
    fb.font["head"].created = FIXED_FONT_EPOCH
    fb.font["head"].modified = FIXED_FONT_EPOCH
    sfnt_bytes = _sfnt_bytes_from_ttfont(fb.font)

    return _assemble_cid_cff_pdf(
        sfnt_bytes,
        subtype="/OpenType",
        ps_name="SynthCFF2-Regular",
        cid_to_unicode={1: ord("A"), 2: ord("B")},
        content_cids=[1, 2],
        out_path=out_path,
    )


def build_namekeyed_otf_cff_pdf(out_path: Path | None = None) -> bytes:
    """A NAME-keyed (non-ROS) simple OTF/CFF CID font — INV-C-13 hard-fail.

    The embedded CFF has NO ``ROS`` TopDict entry (it is name-keyed), so the
    slice-1 CID-keyed injector must refuse it (the ``hasattr(e_td, "ROS")``
    gate). Embedded inside a Type0 chain so the extension path is reached.

    Args:
        out_path: Optional file to write the PDF to.

    Returns:
        The deterministic PDF bytes.
    """
    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]
    from fontTools.pens.t2CharStringPen import T2CharStringPen  # type: ignore[import-untyped]

    glyph_names = [".notdef", "cid00001", "cid00002"]
    cmap = {ord("A"): "cid00001", ord("B"): "cid00002"}
    charstrings: dict[str, object] = {}
    for gn in glyph_names:
        pen = T2CharStringPen(500, None)
        pen.moveTo((50, 0))
        pen.lineTo((50, 700))
        pen.lineTo((450, 700))
        pen.lineTo((450, 0))
        pen.closePath()
        charstrings[gn] = pen.getCharString()

    fb = FontBuilder(_UPM, isTTF=False)
    fb.setupGlyphOrder(glyph_names)
    fb.setupCharacterMap(cmap)
    # NO ROS upgrade → stays name-keyed.
    fb.setupCFF("SynthNameKeyed-Regular", {"FullName": "SynthNameKeyed-Regular"}, charstrings, {})
    fb.setupHorizontalMetrics({gn: (500, 0) for gn in glyph_names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "SynthNameKeyed", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=700)
    fb.setupPost()
    fb.font["head"].created = FIXED_FONT_EPOCH
    fb.font["head"].modified = FIXED_FONT_EPOCH
    sfnt_bytes = _sfnt_bytes_from_ttfont(fb.font)

    return _assemble_cid_cff_pdf(
        sfnt_bytes,
        subtype="/OpenType",
        ps_name="SynthNameKeyed-Regular",
        cid_to_unicode={1: ord("A"), 2: ord("B")},
        content_cids=[1, 2],
        out_path=out_path,
    )


def build_truetype_glyf_donor_bytes(chars: tuple[str, ...] = ("C",)) -> bytes:
    """Synthesize a TrueType (``glyf``) donor carrying ``chars``.

    A glyf donor for a CFF embedded font is the INV-C-13 TrueType-donor
    hard-fail: there is no glyf → Type2-charstring bridge in slice-1, so the
    injector must refuse (the ``"CFF " in system`` donor gate). Returned as
    full sfnt bytes so a test can write it as a ``full_font_path``.

    Args:
        chars: Donor characters (each a triangle ``glyf`` contour).

    Returns:
        The TrueType (``glyf``) donor sfnt bytes.
    """
    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]
    from fontTools.pens.ttGlyphPen import TTGlyphPen  # type: ignore[import-untyped]

    glyph_names = [".notdef"] + [f"g{i}" for i in range(len(chars))]
    cmap = {ord(c): f"g{i}" for i, c in enumerate(chars)}

    glyf: dict[str, object] = {}
    for gn in glyph_names:
        pen = TTGlyphPen(None)
        if gn != ".notdef":
            pen.moveTo((100, 0))
            pen.lineTo((300, 650))
            pen.lineTo((500, 0))
            pen.closePath()
        glyf[gn] = pen.glyph()

    fb = FontBuilder(_UPM, isTTF=True)
    fb.setupGlyphOrder(glyph_names)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyf)
    fb.setupHorizontalMetrics({gn: (500, 0) for gn in glyph_names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "SynthGlyfDonor", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=700)
    fb.setupPost()
    fb.font["head"].created = FIXED_FONT_EPOCH
    fb.font["head"].modified = FIXED_FONT_EPOCH
    return _sfnt_bytes_from_ttfont(fb.font)


def build_seac_composite_donor_bytes(chars: tuple[str, ...] = ("C",)) -> bytes:
    """Synthesize a CFF donor whose glyph is a seac composite — INV-C-13.

    The donor charstring for each char ends with the legacy ``endchar``-with-4
    -args seac form (accent composition: ``adx ady bchar achar endchar``),
    which the slice-1 injector must refuse (``_cff_charstring_is_composite``).
    Built CID-keyed so only the composite gate (not the ROS gate) trips.

    Args:
        chars: Donor characters (each given a seac composite charstring).

    Returns:
        The full sfnt (OTTO) donor bytes.
    """
    from fontTools.cffLib import (  # type: ignore[import-untyped]
        FDArrayIndex,
        FDSelect,
        TopDict,
    )
    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]
    from fontTools.misc.psCharStrings import T2CharString  # type: ignore[import-untyped]
    from fontTools.pens.t2CharStringPen import T2CharStringPen  # type: ignore[import-untyped]

    glyph_names = [".notdef"] + [f"cid{i + 1:05d}" for i in range(len(chars))]
    cmap = {ord(c): f"cid{i + 1:05d}" for i, c in enumerate(chars)}

    # .notdef is a plain box; the donor chars use the Type2 seac endchar form.
    # Type2 seac endchar: <adx> <ady> <bchar> <achar> endchar (4 stack args
    # — accent composition of two StandardEncoding glyphs). The injector's
    # composite predicate scans for an endchar preceded by >= 4 numeric args.
    charstrings: dict[str, object] = {}
    notdef_pen = T2CharStringPen(500, None)
    notdef_pen.moveTo((50, 0))
    notdef_pen.lineTo((50, 700))
    notdef_pen.lineTo((450, 700))
    notdef_pen.lineTo((450, 0))
    notdef_pen.closePath()
    charstrings[".notdef"] = notdef_pen.getCharString()
    for gn in glyph_names[1:]:
        # seac args: adx ady bchar achar (StandardEncoding codes 67='C', 96).
        seac_program = [0, 0, 67, 96, "endchar"]
        charstrings[gn] = T2CharString(program=seac_program)

    fb = FontBuilder(_UPM, isTTF=False)
    fb.setupGlyphOrder(glyph_names)
    fb.setupCharacterMap(cmap)
    fb.setupCFF("SynthSeacDonor-Regular", {"FullName": "SynthSeacDonor-Regular"}, charstrings, {})
    fb.setupHorizontalMetrics({gn: (500, 0) for gn in glyph_names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "SynthSeacDonor", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=700)
    fb.setupPost()
    fb.setupMaxp()
    f = fb.font
    f["head"].created = FIXED_FONT_EPOCH
    f["head"].modified = FIXED_FONT_EPOCH
    cff = f["CFF "].cff
    td = cff[cff.fontNames[0]]
    td.ROS = ("Adobe", "Identity", 0)
    td.CIDFontVersion = 0
    td.CIDCount = len(glyph_names)
    td.charset = list(glyph_names)
    font_dict = TopDict()
    font_dict.Private = td.Private
    font_dict.rawDict["Private"] = (0, 0)
    fd_array = FDArrayIndex()
    fd_array.append(font_dict)
    td.FDArray = fd_array
    fd_select = FDSelect(numGlyphs=len(glyph_names), format=3)
    fd_select.gidArray = [0] * len(glyph_names)
    td.FDSelect = fd_select
    td.CharStrings.fdArray = fd_array
    td.CharStrings.fdSelect = fd_select
    for gn in glyph_names:
        td.CharStrings[gn].private = font_dict.Private
    if "Private" in td.rawDict:
        del td.rawDict["Private"]
    if hasattr(td, "Private"):
        del td.Private
    return _sfnt_bytes_from_ttfont(f)
