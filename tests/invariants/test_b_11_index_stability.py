"""INV-B-11: batch/replace_all delete index stability (B.11).

Roadmap item **B.11 — Content Deletion Cleanup**, index-stability slice. Split
out of ``test_b_10_deletion_cleanup.py`` so INV-B-10 (residue/neighbour
cleanup) and INV-B-11 (operator-index stability) are one invariant per file.

The deletion path empties show-text operands IN PLACE rather than removing
operator tuples (the pre-B.11 list-comp filter renumbered every later op and
corrupted sibling ``operator_refs`` in a batch). Because no tuple is ever
removed, every downstream ``operator_index`` and sibling ``operator_refs`` stay
valid across a multi-match pass. This probe pins that contract: deleting an
earlier-index match must leave a strictly-later-index match byte-stable.

All PDF opens for inspection route through ``pdf_edit_engine._pathutil.open_pdf``
(INV-L-1). The fixture is built in-test with the deterministic corpus builders
(test tooling, outside ``src/``) and skips cleanly when no host TrueType font
is available.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine import find, get_text, replace_all

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders._common import (  # noqa: E402
    emit_or_write,
    find_truetype_font,
    save_pdf_deterministic,
)
from corpus_builders._truetype_assembler import embed_identity_h_font  # noqa: E402

# ---------------------------------------------------------------------------
# Skip gate
# ---------------------------------------------------------------------------

_FONT_OK = find_truetype_font() is not None
_no_font = pytest.mark.skipif(
    not _FONT_OK, reason="no host TrueType font for Identity-H construction"
)


# ---------------------------------------------------------------------------
# Synthetic fixture builder (in-test; deterministic corpus-builder pattern)
# ---------------------------------------------------------------------------


def _attach_page(pdf: pikepdf.Pdf, font: object, content: bytes) -> None:
    """Attach a single 612x792 page carrying ``content`` and the font resource."""
    page = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
            "/Resources": pikepdf.Dictionary(
                {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(font.type0)})}  # type: ignore[attr-defined]
            ),
            "/Contents": pikepdf.Stream(pdf, content),
        }
    )
    pdf.pages.append(pikepdf.Page(page))


def _build_index_stability_pdf(out: Path, first: str, second: str) -> None:
    """Two tokens on separate baselines: ``first`` at op<idx>, ``second`` LATER.

    Deleting ``first`` must leave ``second`` byte-stable (no tuple-removal index
    shift). The two runs live in separate BT/ET blocks so ``second`` occupies a
    strictly later operator index than ``first``.
    """
    ttf = find_truetype_font()
    assert ttf is not None
    pdf = pikepdf.Pdf.new()
    try:
        font = embed_identity_h_font(pdf, ttf, first + " " + second + " ")
        ops = [
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 700 Tm",
            f"<{font.encode(first)}> Tj",
            "ET",
            "BT",
            "/F1 12 Tf",
            "1 0 0 1 72 650 Tm",
            f"<{font.encode(second)}> Tj",
            "ET",
        ]
        _attach_page(pdf, font, "\n".join(ops).encode("latin-1"))
        emit_or_write(save_pdf_deterministic(pdf), out)
    finally:
        pdf.close()


# ---------------------------------------------------------------------------
# Inspection helper
# ---------------------------------------------------------------------------


def _all_success(result) -> bool:  # noqa: ANN001
    results = result if isinstance(result, list) else [result]
    return all(r.success for r in results)


# ===========================================================================
# INV-B-11 — batch/replace_all delete index stability (no tuple-removal shift)
# ===========================================================================


@_no_font
def test_inv_b_11_batch_delete_index_stability(tmp_path: Path) -> None:
    """Deleting one match must leave a LATER-operator-index match byte-stable.

    Formal guard for §3d: because no operator tuple is ever removed, every
    downstream operator_index and sibling operator_refs stay valid across a
    multi-match pass. ``second`` occupies a strictly later operator index than
    the deleted ``first``; it must survive byte-exact.
    """
    first, second = "Alpha", "Bravo"
    src = tmp_path / "idx.pdf"
    _build_index_stability_pdf(src, first, second)

    f_refs = find(str(src), first)
    s_refs = find(str(src), second)
    assert f_refs and s_refs, "both tokens must be findable"
    assert max(s_refs[0].operator_refs) > max(f_refs[0].operator_refs), (
        "precondition: second token must occupy a later operator index than first; "
        f"first={f_refs[0].operator_refs}, second={s_refs[0].operator_refs}"
    )

    out = tmp_path / "idx_out.pdf"
    res = replace_all(str(src), first, "", str(out))
    assert _all_success(res), f"deletion of {first!r} must succeed: {res!r}"

    text = get_text(str(out), page=0)
    assert first not in text, f"{first!r} must be deleted"
    assert second in text, (
        f"later-index token {second!r} corrupted by an index shift (extracted: {text!r})"
    )
