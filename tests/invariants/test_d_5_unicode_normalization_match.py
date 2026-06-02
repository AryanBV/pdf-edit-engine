"""INV-D-5: find() matches canonically-equivalent (NFC/NFD) queries.

A query for an accented word in one Unicode normalization form (e.g. NFC
"é" = U+00E9) must locate the same target text stored in the other form
(e.g. NFD "é" = U+0065 U+0301), without regressing exact-codepoint matches
and without corrupting the operator/byte addressing the subsequent replace
relies on.

The probe builds a deterministic Identity-H PDF whose embedded text is in a
known normalization form, then asserts a canonically-equivalent query in the
*other* form finds it and that every returned TextMatch still round-trips
its recorded characters (matched_text == join of unicode_chars), so the
replace splice stays addressable.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine import find, get_text

# tests/ is on sys.path via the package layout; import the shared builders.
_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders._common import (  # noqa: E402
    find_font_covering,
    save_pdf_deterministic,
)
from corpus_builders._truetype_assembler import embed_identity_h_font  # noqa: E402

# Fonts that cover precomposed Latin accents (NFC test) on each platform.
_NFC_CANDIDATES: tuple[Path, ...] = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/calibri.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
)


def _build_identity_h_pdf(out_path: Path, rendered_text: str) -> bool:
    """Build a 1-page Identity-H PDF rendering ``rendered_text`` verbatim.

    Returns False (caller skips) if no installed font covers every
    codepoint — for NFD the combining mark (U+0301) must be a real glyph.
    """
    ttf = find_font_covering(rendered_text, _NFC_CANDIDATES)
    if ttf is None:
        return False
    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf, rendered_text)
        # Guard: every codepoint we render must have its own CID, else the
        # fixture would silently drop the combining mark.
        if any(ord(c) not in font.cp_to_gid for c in rendered_text):
            return False
        ops = [
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 700 Tm",
            f"<{font.encode(rendered_text)}> Tj",
            "ET",
        ]
        content = "\n".join(ops).encode("latin-1")
        page = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Page"),
                "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
                "/Resources": pikepdf.Dictionary(
                    {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(font.type0)})}
                ),
                "/Contents": pikepdf.Stream(pdf, content),
            }
        )
        pdf.pages.append(pikepdf.Page(page))
        out_path.write_bytes(save_pdf_deterministic(pdf))
        return True
    finally:
        pdf.close()


def _assert_addressable(matches: list, expected_text: str) -> None:
    assert matches, "expected at least one match"
    for m in matches:
        joined = "".join(c.unicode_char for c in m.characters)
        assert joined == m.matched_text, (
            f"INV-D-5: matched_text={m.matched_text!r} != joined-chars={joined!r}; "
            "operator/byte addressing would be corrupt for the replace splice"
        )
        # matched_text must be canonically equivalent to the query.
        assert unicodedata.normalize("NFC", m.matched_text) == unicodedata.normalize(
            "NFC", expected_text
        )


def test_inv_d_5_nfd_query_finds_nfc_text(tmp_path: Path) -> None:
    """NFD query (U+0065 U+0301) finds NFC-stored text (U+00E9)."""
    word_nfc = unicodedata.normalize("NFC", "Café")
    out = tmp_path / "cafe_nfc.pdf"
    if not _build_identity_h_pdf(out, word_nfc):
        pytest.skip("no installed font covers the precomposed accent")

    extracted = get_text(str(out))
    assert "é" in extracted  # sanity: extraction stored the NFC form

    query_nfd = unicodedata.normalize("NFD", "Café")
    assert query_nfd != word_nfc  # the forms genuinely differ
    matches = find(str(out), query_nfd)
    _assert_addressable(matches, query_nfd)


def test_inv_d_5_nfc_query_finds_nfd_text(tmp_path: Path) -> None:
    """NFC query (U+00E9) finds NFD-stored text (U+0065 + U+0301).

    This is the cluster-model direction: the combining mark is its own CID,
    so two extracted TextCharacters collapse into one normalized position.
    The match must cover the *whole* cluster (all original characters) so
    the splice stays addressable, never a partial cluster.
    """
    word_nfd = unicodedata.normalize("NFD", "café")
    out = tmp_path / "cafe_nfd.pdf"
    if not _build_identity_h_pdf(out, word_nfd):
        pytest.skip("no installed font covers the combining acute U+0301")

    query_nfc = unicodedata.normalize("NFC", "café")
    assert query_nfc != word_nfd
    matches = find(str(out), query_nfc)
    _assert_addressable(matches, query_nfc)
    # The match must include the combining mark (whole cluster), so the
    # returned characters carry the full NFD sequence.
    joined = matches[0].matched_text
    assert any(ord(c) == 0x0301 for c in joined), (
        "match must cover the whole combining-mark cluster, not a partial splice"
    )


def test_inv_d_5_exact_match_not_regressed(tmp_path: Path) -> None:
    """An exact NFC query still matches NFC text (no normalization regression)."""
    word_nfc = unicodedata.normalize("NFC", "Café")
    out = tmp_path / "cafe_nfc2.pdf"
    if not _build_identity_h_pdf(out, word_nfc):
        pytest.skip("no installed font covers the precomposed accent")
    matches = find(str(out), word_nfc)
    _assert_addressable(matches, word_nfc)


def test_inv_d_5_case_insensitive_cross_form(tmp_path: Path) -> None:
    """Case-insensitive search matches across NFC/NFD without desyncing addressing.

    Guards the casefold path: lower-casing is folded into the normalized
    search view per character (U+0130 → "i" + U+0307 expands length), so a
    naive ``norm.lower()`` that changed length would misalign the position
    map. Here a lower-case NFD query finds upper-case NFC text.
    """
    word_nfc = unicodedata.normalize("NFC", "Café")
    out = tmp_path / "cafe_ci.pdf"
    if not _build_identity_h_pdf(out, word_nfc):
        pytest.skip("no installed font covers the precomposed accent")
    query = unicodedata.normalize("NFD", "café")  # lower-case + decomposed
    matches = find(str(out), query, case_sensitive=False)
    assert matches, "case-insensitive cross-form query should match"
    for m in matches:
        joined = "".join(c.unicode_char for c in m.characters)
        assert joined == m.matched_text
        assert unicodedata.normalize("NFC", m.matched_text.lower()) == unicodedata.normalize(
            "NFC", query
        )
