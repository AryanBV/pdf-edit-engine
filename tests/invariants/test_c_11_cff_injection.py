"""INV-C-11/12/13 — CID-keyed CFF (Type1C) in-place glyph injection.

C.3 adds in-place glyph injection for a CID-keyed (ROS) CFF embedded as
``/FontFile3`` — the slice-1 sibling of the existing ``glyf`` Tier 1.5
injector. The pre-C.3 engine REFUSES every CFF extension honestly: the C.1
outline-table gate in ``fonts._extend_tier2`` raises ``FontNotFoundError``
for any non-``glyf`` outline (and a BARE CFF additionally fails the
``TTFont(BytesIO(...))`` load in ``extend_subset``, translated to the same
``FontNotFoundError``), so every write path surfaces a
``font_extension_failed`` Degradation with ``success=False``.

C.3 turns the single refusal site into a BRANCH: a CID-keyed (``ROS``),
single-FD, ``glyf``-free CFF whose donor is also a non-composite CFF of
matching ``unitsPerEm`` is INJECTED (new helper
``fonts._inject_cff_glyph_in_place`` + ``fonts._extend_cff_tier``), appending
the donor glyph at a fresh CID == GID at the additive tail (pre-existing CIDs
untouched, mirroring the ARY-278 no-renumber discipline for ``glyf``). Every
OTHER shape (CFF2, composite/seac donor, TrueType ``glyf`` donor, ``unitsPerEm``
mismatch, multi-FD, name-keyed non-ROS) still HARD-FAILS via
``FontNotFoundError`` (in ``_FONT_EXTEND_FAIL_EXCS``) → ``font_extension_failed``.
``extend_subset`` returns ``"full_extension"`` on success, which the existing
surgeon/reflow/structural funnel already maps to a ``font_coverage_substituted``
Degradation + ``success=True`` + ``font_preserved=True``.

INV-C-11/12/13 minted as the next collision-free C-layer slots (a repo-wide
``grep INV-C-11`` returns 0 hits outside the C.3 fixture/probe at design time;
``test_c_10_*`` is the prior max).

RED today (the injection / round-trip cases FAIL because injection is not
implemented — ``_inject_cff_glyph_in_place`` is undefined, and ``extend_subset``
REFUSES every CFF with ``FontNotFoundError``):

* ``test_inv_c_11_cff_injection_lands_at_cid_equals_gid`` — RED:
  ``extend_subset`` refuses the CFF (``FontNotFoundError``) instead of injecting
  C at CID == GID == 3.
* ``test_inv_c_12_preexisting_cids_byte_stable`` — RED: no injection occurs,
  so the post-injection charset / charstrings the assertion inspects never
  materialise (the edit refuses first).
* ``test_inv_c_11_raw_bytes_round_trip_unit`` — RED:
  ``fonts._inject_cff_glyph_in_place`` does not exist (``AttributeError``).
* ``test_inv_c_11_public_replace_e2e_cff_success`` — RED: ``replace`` returns
  ``success=False`` with ``font_extension_failed`` (CFF refused), not the
  ``success=True`` + ``font_coverage_substituted`` C.3 must deliver.
* ``test_inv_c_11_save_reopen_survives`` — RED: the edit refuses, so there is
  no injected glyph to survive a save→reopen round-trip.

PASS today (the out-of-scope refusal already holds — C.1 refuses every CFF;
C.3 must KEEP these refusing, so they are forward-guards, GREEN both before
and after):

* ``test_inv_c_13_out_of_scope_cff_shapes_refuse_via_font_extension_failed``
  (parametrized over CFF2 / name-keyed / TT-donor / UPEM-mismatch / composite).
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

import pdf_edit_engine
import pdf_edit_engine.fonts as fonts_mod
from pdf_edit_engine._pathutil import open_pdf
from pdf_edit_engine.fonts import _FONT_EXTEND_FAIL_EXCS, extend_subset
from tests.corpus_builders import (
    build_cff2_cid_pdf,
    build_cff_cid_missing_glyph_pdf,
    build_cff_cid_sparse_collision_pdf,
    build_cff_cid_tounicode_only_collision_pdf,
    build_cff_donor_bytes,
    build_namekeyed_otf_cff_pdf,
    build_seac_composite_donor_bytes,
    build_truetype_glyf_donor_bytes,
)

# The embedded font carries CIDs 1, 2 (A, B). The donor carries C. C.3 must
# inject C at the additive tail: CID == GID == 3.
_NEW_CID = 3
_NEW_NAME = f"cid{_NEW_CID:05d}"


def _embedded_cff_topdict(pdf_bytes: bytes, tmp_path: Path) -> tuple[object, object]:
    """Parse the embedded /FontFile3 CFF and return (CFFFontSet, TopDict).

    Handles both a BARE CFF (``/Type1C``, no sfnt directory) and an
    sfnt-wrapped OpenType-CFF (``/OpenType``, magic ``b'OTTO'``).

    The PDF bytes are written to a unique temp file and opened through the
    canonical ``open_pdf`` entry point (INV-L-1) rather than a raw
    ``pikepdf.open(io.BytesIO(...))`` — every PDF open in the package routes
    through ``_pathutil.open_pdf`` so pikepdf exceptions are translated.
    """
    from fontTools.cffLib import CFFFontSet  # type: ignore[import-untyped]
    from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

    inspect_path = tmp_path / f"_inspect_{abs(hash(pdf_bytes)) & 0xFFFFFFFF:08x}.pdf"
    inspect_path.write_bytes(pdf_bytes)
    pdf = open_pdf(str(inspect_path))
    try:
        fd = pdf.pages[0]["/Resources"]["/Font"]["/F1"]["/DescendantFonts"][0]["/FontDescriptor"]
        raw = bytes(fd["/FontFile3"].read_bytes())
    finally:
        pdf.close()

    if raw[:4] in (b"OTTO", b"\x00\x01\x00\x00", b"true", b"ttcf"):
        tt = TTFont(io.BytesIO(raw))
        cff = tt["CFF "].cff
        return cff, cff[cff.fontNames[0]]
    cff = CFFFontSet()
    cff.decompile(io.BytesIO(raw), None)
    return cff, cff[cff.fontNames[0]]


def _extract_fontfile3_after_edit(out_path: str) -> bytes:
    """Read the (possibly extended) /FontFile3 bytes from an edited PDF."""
    pdf = open_pdf(out_path)
    try:
        fd = pdf.pages[0]["/Resources"]["/Font"]["/F1"]["/DescendantFonts"][0]["/FontDescriptor"]
        return bytes(fd["/FontFile3"].read_bytes())
    finally:
        pdf.close()


def _decompile_all(topdict: object) -> dict[str, list[object]]:
    """Decompile every charstring and snapshot its program list."""
    cs = topdict.CharStrings  # type: ignore[attr-defined]
    snap: dict[str, list[object]] = {}
    # fontTools CharStrings is not iterable (no __iter__); .keys() is required.
    for name in cs.keys():  # noqa: SIM118
        prog = cs[name]
        if prog.needsDecompilation():
            prog.decompile()
        snap[name] = list(prog.program)
    return snap


def _write_donor(tmp_path: Path, name: str, donor_bytes: bytes) -> str:
    """Write donor sfnt bytes to a temp file and return its path string."""
    p = tmp_path / name
    p.write_bytes(donor_bytes)
    return str(p)


def _install_cff_donor(monkeypatch: pytest.MonkeyPatch, donor_path: str) -> None:
    """Route system-font discovery to a synthetic CFF donor.

    The public ``replace`` verb has NO ``full_font_path`` kwarg — it resolves a
    donor via ``system_fonts._find_font_with_origin`` (CLAUDE.md font-pipeline).
    The synthetic embedded PostScript name (``SynthCIDCFF-Regular``) resolves to
    no installed font, and the open-source metric equivalents are all
    TrueType/glyf (which the CFF injector must hard-refuse). So the e2e cases
    deterministically point discovery at the synthetic CFF donor rather than
    polluting a real user-fonts directory (a filesystem side effect). This
    exercises the FULL public ``replace`` funnel — the intent of these probes —
    without depending on any host font.
    """
    import pdf_edit_engine.system_fonts as system_fonts

    def _fake_find(_ps_name: str) -> tuple[str, str, str | None]:
        return donor_path, "system", None

    monkeypatch.setattr(system_fonts, "_find_font_with_origin", _fake_find)


# ── (a) RED: injection must land the new glyph at CID == GID ────────────


def test_inv_c_11_cff_injection_lands_at_cid_equals_gid(tmp_path: Path) -> None:
    """INV-C-11: CFF injection appends the donor glyph at CID == GID == N.

    The embedded CID-keyed CFF carries CIDs 1, 2 (A, B); the donor carries C.
    ``extend_subset`` must inject C at the additive tail (CID == GID == 3),
    re-serialize ``/FontFile3``, and leave the new glyph's charset CID equal to
    its GID with a non-empty (donor) outline.

    PRE-FIX (RED): ``extend_subset`` REFUSES the CFF — the C.1 outline-table
    gate raises ``FontNotFoundError`` (or the bare-CFF ``TTFont`` load fails,
    translated to the same) — so no injection happens.
    POST-FIX (GREEN): ``extend_subset`` returns ``"full_extension"`` and the
    re-parsed embedded CFF carries ``cid00003`` at ``charset.index == 3``.
    """
    donor_path = _write_donor(tmp_path, "donor.otf", build_cff_donor_bytes(("C",)))
    src = tmp_path / "cff_cid.pdf"
    src.write_bytes(build_cff_cid_missing_glyph_pdf())

    pdf = open_pdf(str(src))
    try:
        page = pdf.pages[0]
        try:
            tier = extend_subset(pdf, page, "F1", "C", full_font_path=donor_path)
        except _FONT_EXTEND_FAIL_EXCS as exc:
            pytest.fail(
                "INV-C-11 violation (RED until C.3 lands): extend_subset REFUSED "
                f"a CID-keyed CFF extension ({type(exc).__name__}: {exc}) instead "
                "of injecting the donor glyph for 'C'. C.3 must turn the C.1 "
                "refusal site into a CFF injection branch."
            )
        assert tier == "full_extension", (
            f"INV-C-11 violation: CFF injection must report 'full_extension'; got {tier!r}"
        )

        # Re-extract and re-parse the now-extended /FontFile3.
        buf = io.BytesIO()
        pdf.save(buf)
    finally:
        pdf.close()

    _cff, td = _embedded_cff_topdict(buf.getvalue(), tmp_path)
    charset = list(td.charset)  # type: ignore[attr-defined]
    cs_keys = list(td.CharStrings.keys())  # type: ignore[attr-defined]

    assert _NEW_NAME in cs_keys, (
        f"INV-C-11 violation: injected glyph {_NEW_NAME!r} absent from "
        f"CharStrings after extension; have {cs_keys}"
    )
    assert charset.index(_NEW_NAME) == _NEW_CID, (
        "INV-C-11 violation: CID == GID broken — injected glyph "
        f"{_NEW_NAME!r} is at charset index {charset.index(_NEW_NAME)}, "
        f"expected {_NEW_CID}"
    )
    new_cs = td.CharStrings[_NEW_NAME]  # type: ignore[attr-defined]
    if new_cs.needsDecompilation():
        new_cs.decompile()
    assert new_cs.program, (
        "INV-C-11 violation: injected glyph has an empty charstring program "
        "(no outline copied from the donor)"
    )


# ── (b) RED: pre-existing CIDs must be byte-stable through injection ─────


def test_inv_c_12_preexisting_cids_byte_stable(tmp_path: Path) -> None:
    """INV-C-12: injection must not renumber/mutate pre-existing CIDs.

    The ARY-278 no-renumber discipline ported to CFF: appending a new glyph at
    the tail must leave every pre-existing glyph's decompiled charstring
    byte-identical and the charset prefix unchanged (only one name appended).

    PRE-FIX (RED): ``extend_subset`` refuses the CFF, so the post-injection
    state the assertion compares against never materialises (the test fails at
    the ``extend_subset`` refusal, pinning that injection is absent).
    POST-FIX (GREEN): pre-existing programs are byte-identical and the charset
    grows by exactly one tail entry.
    """
    src = pdf_bytes = build_cff_cid_missing_glyph_pdf()
    _cff0, td0 = _embedded_cff_topdict(pdf_bytes, tmp_path)
    before_programs = _decompile_all(td0)
    before_charset = list(td0.charset)  # type: ignore[attr-defined]

    donor_path = _write_donor(tmp_path, "donor.otf", build_cff_donor_bytes(("C",)))
    srcfile = tmp_path / "cff_cid.pdf"
    srcfile.write_bytes(src)

    pdf = open_pdf(str(srcfile))
    try:
        try:
            extend_subset(pdf, pdf.pages[0], "F1", "C", full_font_path=donor_path)
        except _FONT_EXTEND_FAIL_EXCS as exc:
            pytest.fail(
                "INV-C-12 violation (RED until C.3 lands): extend_subset REFUSED "
                f"the CFF extension ({type(exc).__name__}: {exc}); no injection "
                "occurred, so pre-existing-CID byte-stability cannot be verified. "
                "C.3 must inject additively without renumbering."
            )
        buf = io.BytesIO()
        pdf.save(buf)
    finally:
        pdf.close()

    _cff1, td1 = _embedded_cff_topdict(buf.getvalue(), tmp_path)
    after_programs = _decompile_all(td1)
    after_charset = list(td1.charset)  # type: ignore[attr-defined]

    for name, prog in before_programs.items():
        assert name in after_programs, (
            f"INV-C-12 violation: pre-existing glyph {name!r} vanished after injection"
        )
        assert after_programs[name] == prog, (
            f"INV-C-12 violation: pre-existing glyph {name!r} charstring program "
            "changed after injection (renumbering / mutation of unrelated text)"
        )
    assert after_charset[: len(before_charset)] == before_charset, (
        "INV-C-12 violation: the pre-existing charset prefix changed — injection "
        f"must be append-only at the tail. before={before_charset} "
        f"after={after_charset}"
    )
    assert len(after_charset) == len(before_charset) + 1, (
        "INV-C-12 violation: exactly one glyph must be appended; charset grew by "
        f"{len(after_charset) - len(before_charset)}"
    )


# ── (b2) RED: a SPARSE / non-contiguous-CID font must not collide ────────


def test_inv_c_12_sparse_cid_injection_no_collision(tmp_path: Path) -> None:
    """INV-C-12: injecting into a SPARSE CID-keyed CFF must not corrupt a CID.

    The font under edit is non-contiguous: ``A`` at CID 1 / GID 1 and ``B`` at
    CID 3 / GID 2 (CID 2 skipped), so the glyph count is 3 yet CID 3 is already
    occupied by ``B`` — the dominant real shape for a subsetted CID-keyed CFF.
    Injecting the UNRELATED char ``C`` must place it at a COLLISION-FREE CID,
    not blindly at ``new_cid == len(glyphOrder) == 3``.

    PRE-FIX (RED): the injector picks ``new_cid == new_gid == 3`` with NO
    free-CID check, colliding with the pre-existing ``cid00003`` ("B"):

    * the donor outline overwrites/duplicates B's CID,
    * the charset gains a DUPLICATE ``cid00003``,
    * ``/ToUnicode`` ``<0003> -> B`` is overwritten by ``<0003> -> C``,
    * ``get_text`` reads "AB" -> "AC" — silent corruption of unrelated text
      with ``success=True`` and ZERO degradations (the ARY-278
      "1ova,ndustries" no-renumber failure ported to CFF).

    POST-FIX (GREEN): mirroring the ``glyf`` path
    (``new_cid = max(len(glyphOrder), max_existing_cid + 1)``), C lands at a
    free CID (>= 4); ``cid00003``'s charstring stays byte-identical, its
    ``/ToUnicode`` mapping is untouched, and ``get_text`` still reads "AB"
    (+ the new "C").
    """
    pdf_bytes = build_cff_cid_sparse_collision_pdf()

    # Snapshot the pre-existing CID-3 ("B") charstring program BEFORE the edit.
    _cff0, td0 = _embedded_cff_topdict(pdf_bytes, tmp_path)
    before_programs = _decompile_all(td0)
    assert "cid00003" in before_programs, (
        "fixture invariant broken: the sparse font must carry cid00003 ('B')"
    )
    before_b_program = before_programs["cid00003"]
    before_charset = list(td0.charset)  # type: ignore[attr-defined]

    donor_path = _write_donor(tmp_path, "donor.otf", build_cff_donor_bytes(("C",)))
    src = tmp_path / "sparse_cff_cid.pdf"
    src.write_bytes(pdf_bytes)
    out = tmp_path / "sparse_cff_cid_out.pdf"

    pdf = open_pdf(str(src))
    try:
        try:
            extend_subset(pdf, pdf.pages[0], "F1", "C", full_font_path=donor_path)
        except _FONT_EXTEND_FAIL_EXCS as exc:
            pytest.fail(
                "INV-C-12 sparse: extend_subset REFUSED the sparse CFF extension "
                f"({type(exc).__name__}: {exc}); cannot verify collision-freedom."
            )
        pdf.save(str(out))
    finally:
        pdf.close()

    # 1. The pre-existing CID-3 ('B') charstring must be byte-identical.
    _cff1, td1 = _embedded_cff_topdict_from_bytes(_extract_fontfile3_after_edit(str(out)))
    after_programs = _decompile_all(td1)
    after_charset = list(td1.charset)  # type: ignore[attr-defined]

    assert "cid00003" in after_programs, (
        "INV-C-12 sparse violation: pre-existing cid00003 ('B') vanished after injection"
    )
    assert after_programs["cid00003"] == before_b_program, (
        "INV-C-12 sparse violation: pre-existing cid00003 ('B') charstring program "
        "changed after injecting an UNRELATED char — the donor outline collided "
        f"onto B's CID. before={before_b_program} after={after_programs['cid00003']}"
    )

    # 2. No pre-existing CID may be renumbered/duplicated; charset prefix stable.
    assert after_charset.count("cid00003") == 1, (
        "INV-C-12 sparse violation: cid00003 appears "
        f"{after_charset.count('cid00003')}x — the collision duplicated B's CID. "
        f"after_charset={after_charset}"
    )
    assert after_charset[: len(before_charset)] == before_charset, (
        "INV-C-12 sparse violation: the pre-existing charset prefix changed — "
        f"injection must be append-only at a free tail. before={before_charset} "
        f"after={after_charset}"
    )

    # 3. /ToUnicode entry for CID 3 must STILL map to 'B' (U+0042), not 'C'.
    #    Hold the open_pdf handle in a variable across the read: the inline
    #    `open_pdf(...).pages[0][...].read_bytes()` form drops the only
    #    reference to the Pdf, so CPython destroys it (closing the pikepdf
    #    handle) before read_bytes() runs — raising "object of type
    #    destroyed". Mirrors this module's _extract_fontfile3_after_edit.
    tu_pdf = open_pdf(str(out))
    try:
        tounicode = bytes(tu_pdf.pages[0]["/Resources"]["/Font"]["/F1"]["/ToUnicode"].read_bytes())
    finally:
        tu_pdf.close()
    norm = tounicode.upper().replace(b" ", b"").replace(b"\n", b"").replace(b"\r", b"")
    assert b"<0003><0042>" in norm, (
        "INV-C-12 sparse violation: /ToUnicode no longer maps CID 3 -> 'B' (U+0042); "
        "the injection overwrote the pre-existing mapping."
    )
    assert b"<0003><0043>" not in norm, (
        "INV-C-12 sparse violation: /ToUnicode now maps CID 3 -> 'C' (U+0043) — the "
        "injected glyph collided onto the pre-existing B's CID."
    )

    # 4. End-to-end: the rendered text must still read 'B', not the injected 'C'.
    text = pdf_edit_engine.get_text(str(out))
    assert "B" in text, (
        "INV-C-12 sparse violation: 'B' is gone from the output text — the "
        f"pre-existing CID was corrupted by the collision. get_text={text!r}"
    )
    assert text.startswith("AB"), (
        "INV-C-12 sparse violation: the original 'AB' run was corrupted by injecting "
        f"an unrelated char (expected to still start with 'AB'); get_text={text!r}"
    )


# ── (b3) RED: a /ToUnicode-ONLY CID must not be overwritten ──────────────


def test_inv_c_12_tounicode_only_cid_injection_no_collision(tmp_path: Path) -> None:
    """INV-C-12: injecting must not clobber a CID referenced ONLY by /ToUnicode.

    A second, charset-independent collision vector. The font under edit has a
    TIGHT embedded charset (``.notdef`` + ``cid00001`` = "A"; charset max CID 1,
    glyph count 2) yet its ``/ToUnicode`` maps CID 1 -> A AND CID 2 -> Q, and
    the content renders ``<0001 0002>`` ("AQ") — so the rendered text DEPENDS
    on the ToUnicode-only CID 2 even though no glyph for it lives in the
    charset. The font LACKS the inject target "C".

    PRE-FIX (RED): the production caller (``fonts._extend_cff_tier``) invokes
    ``_inject_cff_glyph_in_place`` WITHOUT threading ``min_cid``, so it defaults
    to 0. The injector's placement folds in only the EMBEDDED charset CIDs
    (``max_existing_cid + 1`` == 2) and ``len(glyphOrder)`` (== 2) — NOT the
    ToUnicode-only CID 2 — so it picks ``new_cid = max(2, 2, 0) = 2``, COLLIDING
    with the ToUnicode-only CID 2: the appended ``/ToUnicode`` block remaps
    ``<0002> -> Q`` to ``<0002> -> C``, and ``get_text`` reads "AQ" -> "AC" —
    silent corruption of pre-existing text with ``success=True`` and ZERO
    degradations (the ARY-278 "1ova,ndustries" no-renumber failure, a vector
    the charset-CID sparse case cannot catch since its colliding CID 3 IS in
    the charset). The glyf sibling folds the ToUnicode CIDs into its placement
    floor and so preserves CID 2 -> Q.

    POST-FIX (GREEN): the caller threads ``min_cid = max(/ToUnicode CIDs) + 1``
    so the injected "C" lands at a free CID (>= 3); ``/ToUnicode`` still maps
    CID 2 -> "Q" (U+0051), never -> "C" (U+0043), and ``get_text`` still reads
    "AQ" for the pre-existing run.
    """
    pdf_bytes = build_cff_cid_tounicode_only_collision_pdf()
    donor_path = _write_donor(tmp_path, "donor.otf", build_cff_donor_bytes(("C",)))
    src = tmp_path / "tu_only_cff_cid.pdf"
    src.write_bytes(pdf_bytes)
    out = tmp_path / "tu_only_cff_cid_out.pdf"

    pdf = open_pdf(str(src))
    try:
        try:
            tier = extend_subset(pdf, pdf.pages[0], "F1", "C", full_font_path=donor_path)
        except _FONT_EXTEND_FAIL_EXCS as exc:
            pytest.fail(
                "INV-C-12 tounicode-only: extend_subset REFUSED the CFF extension "
                f"({type(exc).__name__}: {exc}); cannot verify collision-freedom."
            )
        assert tier == "full_extension", (
            f"INV-C-12 tounicode-only: CFF injection must report 'full_extension'; got {tier!r}"
        )
        pdf.save(str(out))
    finally:
        pdf.close()

    # 1. /ToUnicode entry for CID 2 must STILL map to 'Q' (U+0051), not 'C'.
    #    Hold the open_pdf handle across the read (see the sparse case note).
    tu_pdf = open_pdf(str(out))
    try:
        tounicode = bytes(tu_pdf.pages[0]["/Resources"]["/Font"]["/F1"]["/ToUnicode"].read_bytes())
    finally:
        tu_pdf.close()
    norm = tounicode.upper().replace(b" ", b"").replace(b"\n", b"").replace(b"\r", b"")
    assert b"<0002><0043>" not in norm, (
        "INV-C-12 tounicode-only violation: /ToUnicode now maps CID 2 -> 'C' "
        "(U+0043) — the injected glyph collided onto the pre-existing "
        "ToUnicode-only CID 2 ('Q')."
    )
    assert b"<0002><0051>" in norm, (
        "INV-C-12 tounicode-only violation: /ToUnicode no longer maps CID 2 -> 'Q' "
        "(U+0051); the injection overwrote the pre-existing mapping."
    )

    # 2. End-to-end: the rendered pre-existing text must still read 'AQ', not 'AC'.
    text = pdf_edit_engine.get_text(str(out))
    assert text.startswith("AQ"), (
        "INV-C-12 tounicode-only violation: the original 'AQ' run was corrupted by "
        "injecting an unrelated char — the donor 'C' overwrote the ToUnicode-only "
        f"CID 2 ('Q'). Expected get_text to still start with 'AQ'; got {text!r}"
    )
    assert "Q" in text, (
        "INV-C-12 tounicode-only violation: 'Q' is gone from the output text — the "
        f"ToUnicode-only CID 2 was corrupted by the collision. get_text={text!r}"
    )


# ── (c) PASS today + forward-guard: out-of-scope shapes refuse honestly ──

# Embedded-font shapes — the refusal is a property of the EMBEDDED binary
# (CFF2 outline / name-keyed non-ROS), so the public replace() verb exercises
# the gate without needing a specific donor.
_EMBEDDED_SHAPE_CASES = [
    pytest.param(build_cff2_cid_pdf, id="cff2_embedded"),
    pytest.param(build_namekeyed_otf_cff_pdf, id="namekeyed_non_ros"),
]


@pytest.mark.parametrize("builder", _EMBEDDED_SHAPE_CASES)
def test_inv_c_13_out_of_scope_embedded_shape_refuses_via_replace(
    builder: object, tmp_path: Path
) -> None:
    """INV-C-13: out-of-scope EMBEDDED CFF shapes refuse via public replace.

    CFF2-outlined and name-keyed (non-ROS) embedded fonts cannot be injected by
    the slice-1 CID-keyed CFF(Type2) injector. Driven through the public
    ``replace`` verb (forcing a missing 'C'), the edit must NEVER inject a
    wrong/approximate glyph and never raise: ``success=False`` carrying a
    ``font_extension_failed`` Degradation (or a translated ``PDFEditError``).

    Already PASSES today (C.1 refuses every CFF). FORWARD-GUARD: C.3 adds the
    in-scope CID-keyed-CFF injection branch and these must KEEP refusing.
    """
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    src.write_bytes(builder())  # type: ignore[operator]

    matches = pdf_edit_engine.find(str(src), "AB")
    assert matches, "fixture must contain the literal 'AB' to edit"

    try:
        result = pdf_edit_engine.replace(str(src), matches[0], "ABC", str(out))
    except pdf_edit_engine.PDFEditError:
        # A translated PDFEditError subclass is an acceptable honest surface.
        return
    except KeyError as exc:  # noqa: PT017 — a raw KeyError leak would be the bug
        pytest.fail(
            f"INV-C-13 violation: leaked a raw KeyError({exc!r}) instead of a "
            "typed font_extension_failed refusal."
        )

    assert not result.success, (
        "INV-C-13 violation: an out-of-scope embedded CFF shape must NOT "
        "silently succeed (it would inject a wrong/approximate glyph)."
    )
    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert "font_extension_failed" in kinds, (
        "INV-C-13 violation: returned success=False without a "
        f"font_extension_failed Degradation; kinds={kinds}"
    )


# Donor shapes — the refusal is a property of the DONOR passed to the
# in-scope embedded font, so they must be driven through extend_subset with an
# explicit full_font_path (replace() has no such kwarg). Each must raise a
# typed FontNotFoundError (in _FONT_EXTEND_FAIL_EXCS), never crash silently.
_DONOR_SHAPE_CASES = [
    pytest.param(
        lambda: build_truetype_glyf_donor_bytes(("C",)), "tt_donor.ttf", id="truetype_glyf_donor"
    ),
    pytest.param(
        lambda: build_cff_donor_bytes(("C",), upem=2048),
        "donor_2048.otf",
        id="upem_mismatch_donor",
    ),
    pytest.param(
        lambda: build_seac_composite_donor_bytes(("C",)),
        "seac_donor.otf",
        id="composite_seac_donor",
    ),
]


@pytest.mark.parametrize(("donor_factory", "donor_name"), _DONOR_SHAPE_CASES)
def test_inv_c_13_out_of_scope_donor_shape_refuses_via_extend_subset(
    donor_factory: object, donor_name: str, tmp_path: Path
) -> None:
    """INV-C-13: out-of-scope DONOR shapes refuse via extend_subset.

    The in-scope CID-keyed bare CFF is extended with an OUT-OF-SCOPE donor: a
    TrueType (``glyf``) donor (no glyf→Type2 bridge), a ``unitsPerEm``-mismatch
    donor (embedded 1000 vs donor 2048), and a seac composite donor glyph.
    ``extend_subset`` must REFUSE each with a ``FontNotFoundError`` (in
    ``_FONT_EXTEND_FAIL_EXCS``) — never inject a wrong glyph, never crash with
    an untyped exception (INV-L-1).

    Already PASSES today (C.1 refuses every CFF before any donor gate). It is a
    FORWARD-GUARD: C.3 adds the in-scope injection branch and these donor
    shapes must KEEP refusing rather than producing wrong output.
    """
    donor_path = _write_donor(tmp_path, donor_name, donor_factory())  # type: ignore[operator]
    src = tmp_path / "cff_cid.pdf"
    src.write_bytes(build_cff_cid_missing_glyph_pdf())

    pdf = open_pdf(str(src))
    try:
        with pytest.raises(_FONT_EXTEND_FAIL_EXCS):
            extend_subset(pdf, pdf.pages[0], "F1", "C", full_font_path=donor_path)
    finally:
        pdf.close()


# ── (d) RED: the private injector + raw round-trip ──────────────────────


def test_inv_c_11_raw_bytes_round_trip_unit(tmp_path: Path) -> None:
    """INV-C-11: ``_inject_cff_glyph_in_place`` survives a raw-bytes round-trip.

    Direct unit probe on the new injector, mirroring the spike
    ``experiments/v020_c3_cff_spike/probe_verify.py`` checks 2-6: load embedded
    + donor CFF as sfnt-wrapped ``TTFont``, inject 'C', ``save()`` to bytes,
    reopen, and assert the CID identity (``ROS``), clean decompilation, a real
    moveTo+lineTo outline, the new glyph's hmtx advance, and an extended
    ``FDSelect.gidArray`` all survive.

    PRE-FIX (RED): ``fonts._inject_cff_glyph_in_place`` does not exist
    (``AttributeError``).
    """
    from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

    inject = getattr(fonts_mod, "_inject_cff_glyph_in_place", None)
    assert inject is not None, (
        "INV-C-11 violation: fonts._inject_cff_glyph_in_place is not defined — "
        "the CID-keyed CFF (Type2) glyph injector is the root of C.3."
    )

    # Build an sfnt-wrapped CID-keyed embedded font (so TTFont loads it
    # directly) and a distinct-triangle donor.
    from tests.corpus_builders.cff_cid_injection import (
        _build_cid_keyed_cff_ttfont,
        _sfnt_bytes_from_ttfont,
    )

    emb_tt = _build_cid_keyed_cff_ttfont(["A", "B"], "SynthEmbed-Regular")
    embedded_bytes = _sfnt_bytes_from_ttfont(emb_tt)
    emb_tt.close()
    donor_bytes = build_cff_donor_bytes(("C",))

    embedded = TTFont(io.BytesIO(embedded_bytes))
    donor = TTFont(io.BytesIO(donor_bytes))
    try:
        new_gid = inject(embedded, donor, "C")
        assert new_gid == _NEW_CID, (
            f"INV-C-11 violation: injector returned GID {new_gid}, expected {_NEW_CID}"
        )
        raw = io.BytesIO()
        embedded.save(raw)
    finally:
        embedded.close()
        donor.close()

    # Raw round-trip: reopen from bytes (mirrors reading /FontFile3 back).
    reopened = TTFont(io.BytesIO(raw.getvalue()))
    try:
        rtd = reopened["CFF "].cff[reopened["CFF "].cff.fontNames[0]]
        assert hasattr(rtd, "ROS"), (
            "INV-C-11 violation: CID identity (ROS) lost across the raw round-trip"
        )
        assert tuple(rtd.ROS) == ("Adobe", "Identity", 0), (
            f"INV-C-11 violation: ROS changed: {rtd.ROS}"
        )
        # Every glyph decompiles clean.
        for name in reopened.getGlyphOrder():
            rtd.CharStrings[name].decompile()
        # New glyph has a real outline.
        from fontTools.pens.recordingPen import RecordingPen  # type: ignore[import-untyped]

        glyphset = reopened.getGlyphSet()
        rp = RecordingPen()
        glyphset[_NEW_NAME].draw(rp)
        ops = [op for op, _ in rp.value]
        assert "moveTo" in ops and "lineTo" in ops, (
            f"INV-C-11 violation: injected glyph has no real outline; draw ops={ops}"
        )
        # hmtx advance survived.
        assert reopened["hmtx"][_NEW_NAME][0] > 0, (
            "INV-C-11 violation: injected glyph hmtx advance did not survive "
            "(the probe_cid.py hmtx.metrics[...] bug)."
        )
        # FDSelect extended by one.
        assert len(rtd.FDSelect.gidArray) == len(reopened.getGlyphOrder()), (
            "INV-C-11 violation: FDSelect.gidArray not extended to cover the new glyph"
        )
    finally:
        reopened.close()


# ── (e) RED: full public-verb e2e success ───────────────────────────────


def test_inv_c_11_public_replace_e2e_cff_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INV-C-11: ``replace`` 'AB'->'ABC' on a CID-keyed CFF SUCCEEDS.

    Full public-verb e2e proving the funnel maps CFF injection success
    identically to ``glyf`` success: ``success=True``, ``font_preserved=True``,
    a ``font_coverage_substituted`` (or ``font_coverage_extended``) Degradation,
    and ``get_text`` on the output contains the new char.

    PRE-FIX (RED): ``replace`` returns ``success=False`` with
    ``font_extension_failed`` (CFF refused), not the success C.3 must deliver.

    NOTE: ``replace`` resolves the donor via system-font discovery (no public
    ``full_font_path`` kwarg). The synthetic embedded PS name
    (``SynthCIDCFF-Regular``) resolves to no installed font, and every
    open-source metric equivalent is TrueType/glyf (which the CFF injector
    correctly hard-refuses). To exercise the FULL public funnel without
    polluting a real user-fonts directory, discovery is monkeypatched at
    ``system_fonts._find_font_with_origin`` to return the synthetic CFF donor
    (``_install_cff_donor``). The injection, save, and Degradation-funnel
    mapping all run through the real ``replace`` path.
    """
    src = tmp_path / "cff_cid.pdf"
    out = tmp_path / "cff_cid_out.pdf"
    src.write_bytes(build_cff_cid_missing_glyph_pdf())
    _install_cff_donor(
        monkeypatch, _write_donor(tmp_path, "donor.otf", build_cff_donor_bytes(("C",)))
    )

    matches = pdf_edit_engine.find(str(src), "AB")
    assert matches, "fixture must contain 'AB'"

    result = pdf_edit_engine.replace(str(src), matches[0], "ABC", str(out))

    assert result.success, (
        "INV-C-11 violation (RED until C.3 lands): replace 'AB'->'ABC' on a "
        "CID-keyed CFF must SUCCEED via injection; today it refuses with "
        f"degradations={[d.kind for d in result.fidelity_report.degradations]}."
    )
    assert result.fidelity_report.font_preserved, (
        "INV-C-11 violation: a successful CFF injection must keep "
        "font_preserved=True (glyph identity unchanged)."
    )
    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert kinds & {"font_coverage_substituted", "font_coverage_extended"}, (
        "INV-C-11 violation: a successful CFF injection must surface a "
        f"font_coverage_substituted/extended Degradation; kinds={kinds}"
    )
    assert "C" in pdf_edit_engine.get_text(str(out)), (
        "INV-C-11 violation: the injected 'C' is not visible in the output text"
    )


# ── (f) RED: save→reopen survival ────────────────────────────────────────


def test_inv_c_11_save_reopen_survives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """INV-C-11: the injected glyph survives the public save→reopen round-trip.

    After the (e) edit, reopen the output via ``open_pdf``, re-extract
    ``/FontFile3``, reparse via cffLib, and assert the new glyph persists at
    CID == GID with a clean decompile, and that ``/W`` + ``/ToUnicode`` carry
    the new CID.

    PRE-FIX (RED): the edit refuses, so there is no injected glyph to survive.

    Like case (e), discovery is monkeypatched to the synthetic CFF donor
    (no public ``full_font_path`` kwarg on ``replace``; the synthetic PS name
    has no installed CFF match) so the full public funnel runs.
    """
    src = tmp_path / "cff_cid.pdf"
    out = tmp_path / "cff_cid_out.pdf"
    src.write_bytes(build_cff_cid_missing_glyph_pdf())
    _install_cff_donor(
        monkeypatch, _write_donor(tmp_path, "donor.otf", build_cff_donor_bytes(("C",)))
    )

    matches = pdf_edit_engine.find(str(src), "AB")
    assert matches, "fixture must contain 'AB'"
    result = pdf_edit_engine.replace(str(src), matches[0], "ABC", str(out))

    assert result.success, (
        "INV-C-11 violation (RED until C.3 lands): the CFF edit refused, so there "
        "is no injected glyph to survive a save→reopen round-trip."
    )

    ff3 = _extract_fontfile3_after_edit(str(out))
    _cff, td = _embedded_cff_topdict_from_bytes(ff3)
    charset = list(td.charset)  # type: ignore[attr-defined]
    assert _NEW_NAME in td.CharStrings, (  # type: ignore[attr-defined]
        "INV-C-11 violation: injected glyph absent after save→reopen"
    )
    assert charset.index(_NEW_NAME) == _NEW_CID, (
        "INV-C-11 violation: CID == GID broken after save→reopen"
    )
    new_cs = td.CharStrings[_NEW_NAME]  # type: ignore[attr-defined]
    if new_cs.needsDecompilation():
        new_cs.decompile()
    assert new_cs.program, "INV-C-11 violation: injected glyph empty after round-trip"

    # PDF-level metadata carries the new CID: /ToUnicode maps it and /W has it.
    from pdf_edit_engine.widths import parse_cid_widths

    pdf = open_pdf(str(out))
    try:
        cid_font = pdf.pages[0]["/Resources"]["/Font"]["/F1"]["/DescendantFonts"][0]
        tounicode = bytes(pdf.pages[0]["/Resources"]["/Font"]["/F1"]["/ToUnicode"].read_bytes())
        widths = parse_cid_widths(cid_font)
    finally:
        pdf.close()

    # /ToUnicode must now carry a bfchar/bfrange entry for the new CID hex.
    cid_hex = f"{_NEW_CID:04X}".encode("latin-1")
    assert cid_hex in tounicode.upper().replace(b" ", b""), (
        "INV-C-11 violation: /ToUnicode does not carry the injected CID mapping; "
        f"expected hex {cid_hex!r}"
    )
    # /W must carry a width for the new CID.
    assert _NEW_CID in widths, (
        f"INV-C-11 violation: /W does not carry a width entry for the injected CID {_NEW_CID}"
    )


def _embedded_cff_topdict_from_bytes(raw: bytes) -> tuple[object, object]:
    """Parse raw /FontFile3 CFF bytes (bare or sfnt) into (CFFFontSet, TopDict).

    Sibling of :func:`_embedded_cff_topdict`: that one EXTRACTS ``/FontFile3``
    from a PDF (opened via ``open_pdf``) then parses; this one takes the raw
    font bytes a caller already pulled out (e.g. via
    :func:`_extract_fontfile3_after_edit`). Both share the bare-vs-sfnt
    decode tail below; kept distinct so the no-PDF callers need no temp file.
    """
    from fontTools.cffLib import CFFFontSet  # type: ignore[import-untyped]
    from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

    if raw[:4] in (b"OTTO", b"\x00\x01\x00\x00", b"true", b"ttcf"):
        tt = TTFont(io.BytesIO(raw))
        cff = tt["CFF "].cff
        return cff, cff[cff.fontNames[0]]
    cff = CFFFontSet()
    cff.decompile(io.BytesIO(raw), None)
    return cff, cff[cff.fontNames[0]]
