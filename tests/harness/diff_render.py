"""Differential PDF rendering and pixel-diff comparison.

Net-new test tooling. Renders a PDF page to a raster image with pypdfium2,
then compares two renders pixel-for-pixel to produce a similarity score, a
changed-region bounding box, and a saved diff PNG. Used by visual-fidelity
probes that need to prove an edit changed *only* the region it claimed to.

Optional-dependency policy
--------------------------
The rendering path needs pypdfium2 (page rasterization) and numpy (vectorized
pixel diff). Pillow is already a transitive test dependency. All three are
imported lazily and guarded by :data:`HAVE_RENDER`, so importing this module —
and therefore ``pytest --collect-only`` — never fails when the optional deps
are absent. Tests that actually rasterize must apply :data:`requires_render`
(a ``skipif``) and the ``render`` pytest marker.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from PIL.Image import Image

# Detect optional rendering dependencies without importing them at module
# load time. importlib.util.find_spec never executes the package, so this is
# cheap and side-effect-free even when the deps are missing.
HAVE_RENDER: bool = (
    importlib.util.find_spec("pypdfium2") is not None
    and importlib.util.find_spec("numpy") is not None
    and importlib.util.find_spec("PIL") is not None
)

requires_render = pytest.mark.skipif(
    not HAVE_RENDER,
    reason="differential-render harness requires pypdfium2 + numpy + Pillow",
)

# Default render scale. pypdfium2's scale=1.0 maps 1 PDF point -> 1 pixel
# (72 DPI). 2.0 gives 144 DPI — enough resolution to catch sub-glyph shifts
# without ballooning the raster.
_DEFAULT_SCALE: float = 2.0

# A render is considered "visually equal" above this similarity. Anti-aliasing
# and pypdfium2 sub-pixel rounding mean a true round-trip lands a hair below a
# perfect 1.0, so the default tolerance leaves a small margin.
_DEFAULT_SIMILARITY_THRESHOLD: float = 0.999


def render_page(
    pdf_path_or_bytes: str | Path | bytes,
    page_index: int = 0,
    *,
    scale: float = _DEFAULT_SCALE,
) -> Image:
    """Rasterize a single PDF page to a Pillow image.

    Args:
        pdf_path_or_bytes: Filesystem path (``str`` or :class:`~pathlib.Path`)
            to a PDF, or the raw PDF bytes.
        page_index: Zero-based page index to render.
        scale: pypdfium2 render scale. ``1.0`` is 72 DPI; the default ``2.0``
            is 144 DPI.

    Returns:
        An RGB :class:`PIL.Image.Image` of the rendered page.

    Raises:
        RuntimeError: If pypdfium2 / numpy / Pillow are not installed.
        IndexError: If ``page_index`` is out of range for the document.
    """
    if not HAVE_RENDER:
        raise RuntimeError(
            "render_page requires pypdfium2 + numpy + Pillow; install the "
            "project's [dev] extra to enable the differential-render harness"
        )

    import pypdfium2  # type: ignore[import-untyped]  # noqa: PLC0415 — lazy by optional-dep policy
    from PIL.Image import Image as PILImage

    source: str | bytes = (
        pdf_path_or_bytes if isinstance(pdf_path_or_bytes, bytes) else str(pdf_path_or_bytes)
    )

    pdf = pypdfium2.PdfDocument(source)
    try:
        n_pages = len(pdf)
        if not 0 <= page_index < n_pages:
            raise IndexError(f"page_index {page_index} out of range for {n_pages}-page document")
        page = pdf[page_index]
        bitmap = page.render(scale=scale)
        # to_pil() copies into a standalone Pillow image, so it stays valid
        # after the pypdfium2 document/page objects are closed below.
        rendered = bitmap.to_pil().convert("RGB")
        assert isinstance(rendered, PILImage)
        return rendered
    finally:
        pdf.close()


def compare_pdfs(
    before: str | Path | bytes,
    after: str | Path | bytes,
    page: int = 0,
    *,
    scale: float = _DEFAULT_SCALE,
    diff_png_path: str | Path | None = None,
) -> dict[str, object]:
    """Render two PDFs and diff one page pixel-for-pixel.

    The two renders are aligned to a common canvas (the union of their pixel
    dimensions) so that page-size or scale differences do not crash the diff;
    any non-overlapping border counts as changed.

    Args:
        before: Path or bytes of the "before" PDF.
        after: Path or bytes of the "after" PDF.
        page: Zero-based page index to compare in both documents.
        scale: Render scale passed to :func:`render_page` for both PDFs.
        diff_png_path: Where to write the diff visualization PNG. When ``None``
            a temporary file is created and its path returned in the result.

    Returns:
        A dict with keys:

        * ``similarity`` (:class:`float`): fraction of pixels that are
            identical, in ``0.0..1.0``. ``1.0`` means the renders are
            pixel-identical.
        * ``changed_bbox`` (``tuple[int, int, int, int] | None``): bounding box
            ``(left, top, right, bottom)`` of all changed pixels in raster
            coordinates, or ``None`` when nothing changed.
        * ``diff_png_path`` (:class:`str`): filesystem path to the written diff
            PNG (changed pixels highlighted in red over a dimmed "after").

    Raises:
        RuntimeError: If the rendering dependencies are not installed.
    """
    if not HAVE_RENDER:
        raise RuntimeError(
            "compare_pdfs requires pypdfium2 + numpy + Pillow; install the "
            "project's [dev] extra to enable the differential-render harness"
        )

    import numpy as np
    from PIL import Image as PILImage

    img_before = render_page(before, page, scale=scale)
    img_after = render_page(after, page, scale=scale)

    # Align both renders onto a common canvas (union of dimensions). Region
    # outside a render is treated as opaque white, matching a blank PDF page.
    width = max(img_before.width, img_after.width)
    height = max(img_before.height, img_after.height)
    canvas_before = PILImage.new("RGB", (width, height), (255, 255, 255))
    canvas_after = PILImage.new("RGB", (width, height), (255, 255, 255))
    canvas_before.paste(img_before, (0, 0))
    canvas_after.paste(img_after, (0, 0))

    arr_before = np.asarray(canvas_before, dtype=np.int16)
    arr_after = np.asarray(canvas_after, dtype=np.int16)

    # A pixel is "changed" if any channel differs. Per-pixel boolean mask.
    changed_mask = np.any(arr_before != arr_after, axis=2)
    total_pixels = int(changed_mask.size)
    changed_pixels = int(np.count_nonzero(changed_mask))
    similarity = 1.0 if total_pixels == 0 else 1.0 - (changed_pixels / total_pixels)

    changed_bbox: tuple[int, int, int, int] | None = None
    if changed_pixels:
        rows = np.any(changed_mask, axis=1)
        cols = np.any(changed_mask, axis=0)
        top = int(np.argmax(rows))
        bottom = int(height - np.argmax(rows[::-1]))
        left = int(np.argmax(cols))
        right = int(width - np.argmax(cols[::-1]))
        changed_bbox = (left, top, right, bottom)

    # Build the diff visualization: dimmed "after" with changed pixels in red.
    diff_arr = (arr_after // 2 + 64).astype(np.uint8)
    diff_arr[changed_mask] = (255, 0, 0)
    diff_img = PILImage.fromarray(diff_arr, mode="RGB")

    if diff_png_path is None:
        import os  # noqa: PLC0415 — only needed on the no-path branch
        import tempfile  # noqa: PLC0415

        fd, tmp_name = tempfile.mkstemp(prefix="pdf_diff_", suffix=".png")
        os.close(fd)
        resolved_path = Path(tmp_name)
    else:
        resolved_path = Path(diff_png_path)
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

    diff_img.save(str(resolved_path))

    return {
        "similarity": float(similarity),
        "changed_bbox": changed_bbox,
        "diff_png_path": str(resolved_path),
    }


def assert_visual_equal_except(
    before: str | Path | bytes,
    after: str | Path | bytes,
    allowed_region: tuple[int, int, int, int] | None = None,
    *,
    page: int = 0,
    scale: float = _DEFAULT_SCALE,
    threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
) -> dict[str, object]:
    """Assert two PDFs render identically, except inside an allowed region.

    When ``allowed_region`` is ``None`` the two renders must be visually equal
    everywhere (similarity at or above ``threshold``). When a region is given,
    any change must fall entirely within it: a change whose bounding box
    escapes the allowed region fails the assertion regardless of similarity.

    Args:
        before: Path or bytes of the "before" PDF.
        after: Path or bytes of the "after" PDF.
        allowed_region: ``(left, top, right, bottom)`` raster-coordinate box
            inside which changes are permitted, or ``None`` to forbid all
            visible change. Coordinates are in the same render-pixel space as
            :func:`compare_pdfs`'s ``changed_bbox`` (i.e. scaled by ``scale``).
        page: Zero-based page index to compare.
        scale: Render scale for both PDFs.
        threshold: Minimum similarity for the "no allowed region" case.

    Returns:
        The :func:`compare_pdfs` result dict, so callers can inspect the diff
        PNG path or exact similarity after a passing assertion.

    Raises:
        AssertionError: If the visual change violates the contract above.
        RuntimeError: If the rendering dependencies are not installed.
    """
    result = compare_pdfs(before, after, page, scale=scale)
    similarity = result["similarity"]
    assert isinstance(similarity, float)
    changed_bbox = result["changed_bbox"]
    diff_path = result["diff_png_path"]

    if allowed_region is None:
        assert similarity >= threshold, (
            f"visual change exceeds tolerance: similarity={similarity:.6f} "
            f"< threshold={threshold} (diff: {diff_path})"
        )
        return result

    if changed_bbox is None:
        # Nothing changed at all — trivially within any allowed region.
        return result

    assert isinstance(changed_bbox, tuple)
    c_left, c_top, c_right, c_bottom = changed_bbox
    a_left, a_top, a_right, a_bottom = allowed_region
    within = c_left >= a_left and c_top >= a_top and c_right <= a_right and c_bottom <= a_bottom
    assert within, (
        f"visual change escaped the allowed region: changed_bbox={changed_bbox} "
        f"not contained in allowed_region={allowed_region} (diff: {diff_path})"
    )
    return result
