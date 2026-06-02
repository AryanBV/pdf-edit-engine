"""Self-test for the differential-render harness (tests/harness/diff_render.py).

INV-RENDER-1: a pikepdf round-trip (open + save, no content edits) of the
repo's simple WinAnsi fixture renders pixel-identical to the original — the
harness must report similarity ~1.0 and an empty changed region. This proves
the harness is sensitive enough to trust as a fidelity oracle: if a true
no-op round-trip did NOT land at ~1.0, every downstream visual probe would be
measuring rendering noise instead of real edits.

The whole module is import-safe without pypdfium2/numpy: the ``render`` marker
plus ``requires_render`` skipif guarantee ``pytest --collect-only`` succeeds
even when the optional rendering deps are absent.
"""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
import pytest

from tests.harness.diff_render import (
    assert_visual_equal_except,
    compare_pdfs,
    render_page,
    requires_render,
)

pytestmark = [pytest.mark.render, requires_render]

CORPUS_DIR = Path(__file__).parent.parent / "corpus"
_SIMPLE_FIXTURE = CORPUS_DIR / "reportlab_simple.pdf"

_have_fixture = pytest.mark.skipif(
    not _SIMPLE_FIXTURE.exists(),
    reason="reportlab_simple.pdf corpus fixture not present",
)


def _roundtrip_bytes(pdf_path: Path) -> bytes:
    """Open and re-save a PDF through pikepdf without editing content.

    A pure structural round-trip: the rendered output must be visually
    identical to the source, which is exactly what the harness self-test
    asserts.
    """
    out = io.BytesIO()
    with pikepdf.open(str(pdf_path)) as pdf:
        pdf.save(out)
    return out.getvalue()


@_have_fixture
def test_inv_render_1_roundtrip_similarity_is_one() -> None:
    """A no-op pikepdf round-trip renders at ~1.0 similarity, no changed bbox."""
    after_bytes = _roundtrip_bytes(_SIMPLE_FIXTURE)

    result = compare_pdfs(_SIMPLE_FIXTURE, after_bytes, page=0)

    similarity = result["similarity"]
    assert isinstance(similarity, float)
    assert similarity >= 0.999, f"round-trip similarity too low: {similarity:.6f}"
    assert result["changed_bbox"] is None, (
        f"no-op round-trip reported a changed region: {result['changed_bbox']}"
    )
    diff_path = result["diff_png_path"]
    assert isinstance(diff_path, str)
    assert Path(diff_path).exists(), "diff PNG was not written"


@_have_fixture
def test_render_page_returns_rgb_image() -> None:
    """render_page yields a non-empty RGB raster for the fixture's first page."""
    img = render_page(_SIMPLE_FIXTURE, page_index=0)
    assert img.mode == "RGB"
    assert img.width > 0 and img.height > 0


@_have_fixture
def test_assert_visual_equal_except_passes_on_roundtrip() -> None:
    """The pytest helper accepts a visually-identical no-op round-trip."""
    after_bytes = _roundtrip_bytes(_SIMPLE_FIXTURE)
    # allowed_region=None ⇒ require equality everywhere.
    assert_visual_equal_except(_SIMPLE_FIXTURE, after_bytes, allowed_region=None)


@_have_fixture
def test_compare_detects_a_genuinely_different_page() -> None:
    """Two visibly different PDFs score below 1.0 with a non-empty changed bbox.

    Guards against a false-positive oracle: if compare_pdfs reported ~1.0 for
    everything, the round-trip assertion above would be meaningless.
    """
    other = CORPUS_DIR / "reportlab_multipage.pdf"
    if not other.exists():
        pytest.skip("reportlab_multipage.pdf corpus fixture not present")

    result = compare_pdfs(_SIMPLE_FIXTURE, other, page=0)
    similarity = result["similarity"]
    assert isinstance(similarity, float)
    assert similarity < 0.999, (
        f"two different PDFs scored as visually equal: similarity={similarity:.6f}"
    )
    assert result["changed_bbox"] is not None
