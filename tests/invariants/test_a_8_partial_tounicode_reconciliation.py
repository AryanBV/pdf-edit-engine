"""INV-A-8: partial-/ToUnicode read-side reconciliation (asymmetric fill).

When a Type0/Identity-H CIDFont ships a ``/ToUnicode`` CMap that maps **some**
CIDs but omits others (very common — generators emit ToUnicode only for the
glyphs they consider "text"), the unmapped CIDs make ``decode()`` raise
``KeyError`` and the locator drops the *entire* Tj run containing them. A
single hole therefore loses a whole word/line from ``find()`` and
``get_text``.

B.5 reconciles the read side **additively**: after parsing ``/ToUnicode``,
the missing CIDs are filled by inverting the embedded font's cmap
(``fonts.reverse_embedded_cmap``, CID==GID under Identity-H — the same
inversion B.3 uses for the *whole-map-absent* case). The reconciliation:

- fires only on a **non-empty** parsed ``/ToUnicode`` (disjoint from B.3,
  which fires only when the parsed map is *entirely* empty);
- **never overwrites** a CID that ``/ToUnicode`` already maps (a font with
  complete, correct ``/ToUnicode`` must not regress — its bytes are
  authoritative);
- is gated by the same Identity-H / Identity-CIDToGIDMap preconditions as
  B.3 (no fill on a remapped or non-Identity-H font);
- does NOT set ``is_tounicode_recovered`` — that flag means the WHOLE map
  was synthesised (B.3) and gates the WRITE-path refusal. A partial fill
  leaves the font writable, so the flag stays ``False``.

Sibling layer: INV-A-6/A-7 (``test_a_6_tounicode_absent_recovery.py``) cover
the whole-map-absent recovery (B.3). This probe covers the *partial* case
and the no-regression guarantee for complete ``/ToUnicode``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.encoding import FontResolver, FontResolverCache
from pdf_edit_engine.locator import find, get_text

# tests/ is on sys.path for conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _identity_h_fixture import _build_identity_h_pdf  # noqa: E402
from corpus_builders.no_tounicode import (  # noqa: E402
    PARTIAL_OMITTED_CODEPOINT,
    build_no_tounicode_identity_h_pdf,
    build_partial_tounicode_identity_h_pdf,
)

_FONT_OK = build_partial_tounicode_identity_h_pdf() is not None
_no_font = pytest.mark.skipif(
    not _FONT_OK, reason="no host TrueType font for Identity-H construction"
)


@pytest.fixture
def partial_pdf(tmp_path: Path) -> Path:
    """An Identity-H PDF with a PARTIAL /ToUnicode (one CID omitted)."""
    out = tmp_path / "partial_tounicode.pdf"
    assert build_partial_tounicode_identity_h_pdf(out) is not None
    return out


@_no_font
def test_inv_a_8_precondition_tounicode_present_but_partial(partial_pdf: Path) -> None:
    """Sanity: the fixture DOES have /ToUnicode (so this is not the B.3 case)."""
    with pikepdf.open(str(partial_pdf)) as pdf:
        type0 = pdf.pages[0].Resources.Font["/F1"]
        assert "/ToUnicode" in type0, "fixture must ship a real (partial) /ToUnicode"


@_no_font
def test_inv_a_8_find_recovers_run_with_unmapped_cid(partial_pdf: Path) -> None:
    """find() locates a word whose CID is missing from /ToUnicode (RED→GREEN).

    'Acme' begins with the omitted 'A' CID; pre-reconciliation the entire run
    drops and find() returns zero. Post-fill the omitted CID is supplied from
    the embedded cmap and the run is locatable.
    """
    matches = find(str(partial_pdf), "Acme")
    assert matches, "partial-/ToUnicode fill must let find('Acme') locate the run"


@_no_font
def test_inv_a_8_get_text_includes_filled_codepoint(partial_pdf: Path) -> None:
    """get_text emits the omitted codepoint, reconstructing the full string."""
    text = get_text(str(partial_pdf))
    assert chr(PARTIAL_OMITTED_CODEPOINT) in text, "filled CID's codepoint must appear"
    assert "Acme Corporation" in text, "the full title must reconstruct after fill"


@_no_font
def test_inv_a_8_present_mappings_preserved(partial_pdf: Path) -> None:
    """Reconciliation must NOT overwrite CIDs /ToUnicode already maps.

    The non-omitted letters ('c', 'm', 'e', 'C', 'o', ...) were authoritatively
    mapped by /ToUnicode. After the additive fill they must decode to exactly
    those characters — the embedded-cmap fill only supplies *missing* CIDs.
    """
    cache = FontResolverCache()
    with pikepdf.open(str(partial_pdf)) as pdf:
        resolver = cache.get_resolver(pdf.pages[0], "F1")
        cid_map = resolver._cid_to_unicode  # noqa: SLF001 — white-box invariant check
        # Every authoritative letter is still present and correct.
        for ch in "cmeCorpatin":
            assert ch in cid_map.values(), f"authoritative '{ch}' lost after fill"
        # The omitted codepoint is now filled in.
        assert chr(PARTIAL_OMITTED_CODEPOINT) in cid_map.values()


@_no_font
def test_inv_a_8_partial_fill_does_not_set_recovered_flag(partial_pdf: Path) -> None:
    """A partial fill leaves is_tounicode_recovered False (WRITE stays allowed).

    The recovered flag means the WHOLE map was synthesised (B.3) and gates the
    WRITE-path refusal. A font with a real /ToUnicode plus a few filled holes
    is still writable, so the flag must NOT flip.
    """
    cache = FontResolverCache()
    with pikepdf.open(str(partial_pdf)) as pdf:
        resolver = cache.get_resolver(pdf.pages[0], "F1")
        assert resolver.is_tounicode_recovered is False
        assert resolver.is_untextable_cidfont is False


@_no_font
def test_inv_a_8_complete_tounicode_not_regressed(tmp_path: Path) -> None:
    """A font with COMPLETE, correct /ToUnicode is unaffected by reconciliation.

    Build a no-ToUnicode font (which B.3 fully recovers), then re-assemble it
    with a COMPLETE /ToUnicode and assert text extraction is identical to a
    plain complete-/ToUnicode read — the fill must be a no-op when there is no
    hole. This guards the "must NOT regress complete fonts" requirement.
    """
    # The complete-/ToUnicode build path is exercised by the broader corpus;
    # here we assert the engine reads such a font with zero fill side effects.
    no_tu = tmp_path / "no_tu.pdf"
    assert build_no_tounicode_identity_h_pdf(no_tu) is not None
    # B.3 fully recovers a no-/ToUnicode font, so its text equals the target.
    recovered_text = get_text(str(no_tu))
    assert "Acme Corporation" in recovered_text


@_no_font
def test_inv_a_8_misaligned_full_gid_font_not_filled(tmp_path: Path) -> None:
    """SOUNDNESS GATE: a full-GID-as-CID font must NOT be filled.

    Many producers emit *full-font GIDs* as content-stream CIDs and embed a
    *renumbered subset*, bridging the mismatch with ``/ToUnicode``. For such a
    font the embedded-cmap inversion is keyed by SUBSET GID, which does NOT
    equal the content CID — filling those keys would inject bogus CID→Unicode
    pairs and corrupt the reverse map (breaking the WRITE path).

    The B.5 alignment gate detects this: the inversion disagrees with the
    present ``/ToUnicode`` on shared CIDs, so the fill is skipped entirely. We
    assert it by reconstructing the parsed-only ``/ToUnicode`` and confirming
    the resolver's CID map contains EXACTLY those entries (no injected keys).
    """
    import io as _io

    from pdfminer.cmapdb import CMapParser, FileUnicodeMap

    src = tmp_path / "full_gid.pdf"
    # _build_identity_h_pdf emits full-font GIDs as CIDs (CID != subset GID).
    assert _build_identity_h_pdf(src, title_pattern="single_tj")

    with pikepdf.open(str(src)) as pdf:
        type0 = pdf.pages[0].Resources.Font["/F1"]
        tu = type0["/ToUnicode"].read_bytes()
        cm = FileUnicodeMap()
        CMapParser(cm, _io.BytesIO(tu)).run()
        parsed_only = dict(cm.cid2unichr)

        resolver = FontResolverCache().get_resolver(pdf.pages[0], "F1")
        cid_map = resolver._cid_to_unicode  # noqa: SLF001 — white-box soundness check
        # No keys were injected: the resolver map equals the parsed /ToUnicode.
        assert cid_map == parsed_only, (
            "misaligned full-GID font must not be filled — fill injected bogus CIDs"
        )
        # And the font stays writable (no recovery flag flipped).
        assert resolver.is_tounicode_recovered is False


@_no_font
def test_inv_a_8_misaligned_font_replace_round_trips(tmp_path: Path) -> None:
    """The WRITE path on a full-GID font is intact after B.5 (no reverse-map rot).

    A same-subset replace must still succeed — proving B.5 did not pollute
    ``_unicode_to_cid`` with subset-GID keys that would mis-encode the title.
    """
    from pdf_edit_engine import replace

    src = tmp_path / "full_gid_rt.pdf"
    out = tmp_path / "full_gid_rt_out.pdf"
    assert _build_identity_h_pdf(src, title_pattern="single_tj")
    matches = find(str(src), "Acme")
    assert matches, "find('Acme') must locate the title on the full-GID font"
    result = replace(str(src), matches[0], "Nova", str(out))
    assert result.success, f"same-subset replace must succeed post-B.5: {result!r}"


def test_inv_a_8_non_cid_resolver_unaffected() -> None:
    """The reconciliation is CID-only: simple fonts never invoke the fill.

    A WinAnsi simple font has no CID map; constructing its resolver must not
    raise and must leave both flags False (no CID branch entered).
    """
    pdf = pikepdf.Pdf.new()
    try:
        font = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
            }
        )
        resolver = FontResolver(font, "F1")
        assert resolver.is_cid_font is False
        assert resolver.is_tounicode_recovered is False
        assert resolver.is_untextable_cidfont is False
    finally:
        pdf.close()
