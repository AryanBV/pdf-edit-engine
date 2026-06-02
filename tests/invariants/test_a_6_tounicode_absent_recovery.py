"""INV-A-6: ToUnicode-absent Identity-H recovery via embedded-cmap reversal.

When a Type0/Identity-H CIDFont has no ``/ToUnicode``, the encoder must
recover a CID→Unicode map by inverting the embedded font's
``getBestCmap() + getGlyphID()`` (CID==GID under Identity-H) so that
``find()`` can locate visible text instead of silently returning zero
matches (the documented ``encoding.py:136-138`` failure).

Recovery is GATED (M0 Rank-2.5 spike verdict — READ = partial-with-
degradation, WRITE = refuse-on-gap):

- only when ``/CIDToGIDMap`` is ``/Identity``-or-absent AND ``/Encoding``
  is ``Identity-H``;
- recovered codepoints in the Private-Use-Area are rejected; a
  majority-PUA recovered map is treated as UNRECOVERED (``find()`` still
  returns zero, no garbage text fabricated).

INV-A-7 (sibling probe, same file) covers the WRITE path: a NEW-GLYPH
replace on a recovered font must REFUSE with a typed ``Degradation``,
while a same-subset replace (no new glyph) may proceed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine import replace
from pdf_edit_engine.encoding import FontResolverCache
from pdf_edit_engine.locator import find, get_text

# tests/ is on sys.path for conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders.no_tounicode import (  # noqa: E402
    build_no_tounicode_identity_h_pdf,
    build_pua_no_tounicode_identity_h_pdf,
)

_FONT_OK = build_no_tounicode_identity_h_pdf() is not None
_no_font = pytest.mark.skipif(
    not _FONT_OK, reason="no host TrueType font for Identity-H construction"
)


@pytest.fixture
def recoverable_pdf(tmp_path: Path) -> Path:
    """A no-/ToUnicode Identity-H PDF whose embedded cmap is recoverable."""
    out = tmp_path / "no_tounicode.pdf"
    assert build_no_tounicode_identity_h_pdf(out) is not None
    return out


@pytest.fixture
def pua_pdf(tmp_path: Path) -> Path:
    """A no-/ToUnicode Identity-H PDF whose embedded cmap is PUA-only."""
    out = tmp_path / "pua_no_tounicode.pdf"
    assert build_pua_no_tounicode_identity_h_pdf(out) is not None
    return out


@_no_font
def test_inv_a_6_no_tounicode_absent_field(recoverable_pdf: Path) -> None:
    """Sanity: the fixture really has no /ToUnicode (precondition)."""
    with pikepdf.open(str(recoverable_pdf)) as pdf:
        type0 = pdf.pages[0].Resources.Font["/F1"]
        assert "/ToUnicode" not in type0


@_no_font
def test_inv_a_6_find_recovers_visible_text(recoverable_pdf: Path) -> None:
    """find() locates visible text via embedded-cmap recovery (the RED→GREEN)."""
    matches = find(str(recoverable_pdf), "Acme")
    assert matches, "find('Acme') must recover via embedded cmap when ToUnicode absent"
    # The visible title and body both contain 'Acme Corporation'.
    assert len(matches) >= 1
    assert "Acme" in get_text(str(recoverable_pdf))


@_no_font
def test_inv_a_6_get_text_recovers(recoverable_pdf: Path) -> None:
    """get_text returns the human-readable string, not glyph indices."""
    text = get_text(str(recoverable_pdf))
    assert "Acme Corporation" in text


@_no_font
def test_inv_a_6_pua_map_treated_unrecovered(pua_pdf: Path) -> None:
    """A majority-PUA recovered map is rejected; find() stays at zero."""
    matches = find(str(pua_pdf), "Acme")
    assert matches == [], "PUA-only cmap must NOT be used for recovery"
    text = get_text(str(pua_pdf))
    # No Basic-Latin letters fabricated; PUA codepoints (if any leak) are not
    # 'Acme'. The strong assertion: the title text is absent.
    assert "Acme" not in text


@_no_font
def test_inv_a_6_gate_requires_identity_cidtogidmap(tmp_path: Path) -> None:
    """Recovery refuses when /CIDToGIDMap is not /Identity (or absent)."""
    out = tmp_path / "nonident.pdf"
    assert build_no_tounicode_identity_h_pdf(out) is not None
    # Mutate the descendant CIDFont's /CIDToGIDMap to a non-Identity name.
    with pikepdf.open(str(out), allow_overwriting_input=True) as pdf:
        cidf = pdf.pages[0].Resources.Font["/F1"]["/DescendantFonts"][0]
        cidf["/CIDToGIDMap"] = pikepdf.Name("/Custom")
        pdf.save(str(out))
    assert find(str(out), "Acme") == [], (
        "non-Identity /CIDToGIDMap breaks the CID==GID assumption; recovery must refuse"
    )


@_no_font
def test_inv_a_6_resolver_reports_recovery(recoverable_pdf: Path) -> None:
    """The resolver exposes its recovered state for callers (degradation gating)."""
    cache = FontResolverCache()
    with pikepdf.open(str(recoverable_pdf)) as pdf:
        resolver = cache.get_resolver(pdf.pages[0], "F1")
        assert resolver.is_cid_font
        assert resolver.is_tounicode_recovered is True
        # A normal ToUnicode-present font reports False — covered by the
        # baseline corpus in other probes; here we assert the recovered flag.


@_no_font
def test_inv_a_6_pua_resolver_reports_unrecovered(pua_pdf: Path) -> None:
    """PUA-only font: recovery attempted but classified unrecoverable."""
    cache = FontResolverCache()
    with pikepdf.open(str(pua_pdf)) as pdf:
        resolver = cache.get_resolver(pdf.pages[0], "F1")
        assert resolver.is_tounicode_recovered is False
        assert resolver.is_untextable_cidfont is True


# ── INV-A-7: WRITE-path refusal on recovered fonts ──────────────────────────


@_no_font
def test_inv_a_7_same_subset_replace_proceeds(recoverable_pdf: Path, tmp_path: Path) -> None:
    """A same-subset replace (no new glyph) on a recovered font may proceed.

    'Acme' → 'Corp' reuses only glyphs already in the subset (C, o, r, p are
    all present in 'Acme Corporation'), so no extension is needed.
    """
    out = tmp_path / "same_subset.pdf"
    matches = find(str(recoverable_pdf), "Acme")
    assert matches
    result = replace(str(recoverable_pdf), matches[0], "Corp", str(out))
    assert result.success, f"same-subset replace should succeed: {result!r}"
    assert out.exists()


@_no_font
def test_inv_a_7_new_glyph_replace_refuses(recoverable_pdf: Path, tmp_path: Path) -> None:
    """A NEW-GLYPH replace on a recovered font REFUSES with a typed Degradation.

    'z' is not in the subset 'Acme Corporation' + body, so it triggers
    extension — which would need /ToUnicode write (out of scope here). The
    write path must refuse, not crash, surfacing a tounicode_recovered
    Degradation and font_action='failed'.
    """
    out = tmp_path / "new_glyph.pdf"
    matches = find(str(recoverable_pdf), "Acme")
    assert matches
    result = replace(str(recoverable_pdf), matches[0], "zzzz", str(out))
    assert result.success is False, "new-glyph replace on recovered font must refuse"
    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert "tounicode_recovered" in kinds, f"expected tounicode_recovered Degradation; got {kinds}"
