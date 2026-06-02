"""Smoke tests for the adversarial corpus builders.

Each builder must yield a pikepdf-parseable PDF. Builders that depend on a
host font skip cleanly (via ``pytest.skip``) when the builder returns
``None`` — the same skipif discipline the ``cidfont_synthetic`` fixture
uses. The CFF builder synthesises its font in-process and is never skipped.

Determinism is asserted directly: building twice must yield byte-identical
output.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pikepdf
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from tests.corpus_builders import (
    BUILD_SCHEMA,
    build_arabic_pdf,
    build_axis_aligned_two_run_pdf,
    build_bare_cff_font_pdf,
    build_cff2_cid_pdf,
    build_cff_cid_missing_glyph_pdf,
    build_cff_cid_missing_glyph_wrapped_pdf,
    build_cff_cid_sparse_collision_pdf,
    build_cff_cid_tounicode_only_collision_pdf,
    build_cff_font_pdf,
    build_declared_leading_pdf,
    build_devicergb_run_pdf,
    build_indent_styles_pdf,
    build_linearized_pdf,
    build_namekeyed_otf_cff_pdf,
    build_nonlinearized_pdf,
    build_reflow_quality_pdf,
    build_rotated_text_pdf,
    build_separation_run_pdf,
    build_shrink_to_fit_pdf,
    build_tagged_pdf,
    build_truetype_baseline_pdf,
    build_type1_font_pdf,
    build_xobject_text_pdf,
)

# (builder callable, may-return-None-on-missing-dep) for every public builder.
_BUILDERS: list[tuple[str, Callable[[], bytes | None], bool]] = [
    ("build_truetype_baseline_pdf", build_truetype_baseline_pdf, True),
    ("build_cff_font_pdf", build_cff_font_pdf, False),
    ("build_bare_cff_font_pdf", build_bare_cff_font_pdf, False),
    ("build_cff_cid_missing_glyph_pdf", build_cff_cid_missing_glyph_pdf, False),
    ("build_cff_cid_missing_glyph_wrapped_pdf", build_cff_cid_missing_glyph_wrapped_pdf, False),
    ("build_cff_cid_sparse_collision_pdf", build_cff_cid_sparse_collision_pdf, False),
    (
        "build_cff_cid_tounicode_only_collision_pdf",
        build_cff_cid_tounicode_only_collision_pdf,
        False,
    ),
    ("build_cff2_cid_pdf", build_cff2_cid_pdf, False),
    ("build_namekeyed_otf_cff_pdf", build_namekeyed_otf_cff_pdf, False),
    ("build_type1_font_pdf", build_type1_font_pdf, False),
    ("build_xobject_text_pdf", build_xobject_text_pdf, True),
    ("build_arabic_pdf", build_arabic_pdf, True),
    ("build_tagged_pdf", build_tagged_pdf, True),
    ("build_rotated_text_pdf", build_rotated_text_pdf, True),
    ("build_axis_aligned_two_run_pdf", build_axis_aligned_two_run_pdf, True),
    ("build_reflow_quality_pdf", build_reflow_quality_pdf, False),
    ("build_indent_styles_pdf", build_indent_styles_pdf, False),
    ("build_declared_leading_pdf", build_declared_leading_pdf, False),
    ("build_separation_run_pdf", build_separation_run_pdf, False),
    ("build_devicergb_run_pdf", build_devicergb_run_pdf, False),
    ("build_shrink_to_fit_pdf", build_shrink_to_fit_pdf, False),
    ("build_linearized_pdf", build_linearized_pdf, False),
    ("build_nonlinearized_pdf", build_nonlinearized_pdf, False),
]


def _build_or_skip(builder: Callable[[], bytes | None], may_skip: bool) -> bytes:
    """Invoke ``builder``; skip the test if it returns None and is allowed to."""
    result = builder()
    if result is None:
        if may_skip:
            pytest.skip("builder returned None — required host font not installed")
        pytest.fail("builder returned None but is not allowed to skip")
    return result


@pytest.mark.parametrize(
    ("name", "builder", "may_skip"),
    _BUILDERS,
    ids=[name for name, _, _ in _BUILDERS],
)
def test_builder_yields_parseable_pdf(
    name: str, builder: Callable[[], bytes | None], may_skip: bool
) -> None:
    pdf_bytes = _build_or_skip(builder, may_skip)
    assert pdf_bytes.startswith(b"%PDF-"), f"{name} did not emit a PDF header"
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        assert len(pdf.pages) >= 1, f"{name} produced no pages"


@pytest.mark.parametrize(
    ("name", "builder", "may_skip"),
    _BUILDERS,
    ids=[name for name, _, _ in _BUILDERS],
)
def test_builder_is_deterministic(
    name: str, builder: Callable[[], bytes | None], may_skip: bool
) -> None:
    first = _build_or_skip(builder, may_skip)
    second = builder()
    assert first == second, f"{name} is not byte-deterministic across runs"


def test_cff_pdf_embeds_fontfile3() -> None:
    """The CFF builder must embed /FontFile3, not /FontFile2 (glyf)."""
    pdf_bytes = build_cff_font_pdf()
    assert pdf_bytes is not None
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        font = pdf.pages[0]["/Resources"]["/Font"]["/F1"]
        descendant = font["/DescendantFonts"][0]
        descriptor = descendant["/FontDescriptor"]
        assert "/FontFile3" in descriptor, "CFF font must embed /FontFile3"
        assert "/FontFile2" not in descriptor, "CFF font must not embed /FontFile2"


def test_xobject_pdf_text_is_in_form_not_page() -> None:
    """The XObject builder keeps text operators out of the page stream."""
    pdf_bytes = build_xobject_text_pdf()
    if pdf_bytes is None:
        pytest.skip("no host TrueType font installed")
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        page_stream = page["/Contents"].read_bytes()
        assert b"Do" in page_stream, "page stream should invoke the form via Do"
        assert b"Tj" not in page_stream, "page stream must not draw text directly"
        xobjects = page["/Resources"]["/XObject"]
        form = xobjects["/Fm1"]
        assert b"Tj" in form.read_bytes(), "form XObject should contain the text"


def test_tagged_pdf_has_struct_tree_and_actualtext() -> None:
    """The tagged builder wires up StructTreeRoot, MarkInfo, and /ActualText."""
    pdf_bytes = build_tagged_pdf()
    if pdf_bytes is None:
        pytest.skip("no host TrueType font installed")
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        assert "/StructTreeRoot" in pdf.Root, "missing /StructTreeRoot"
        assert bool(pdf.Root["/MarkInfo"]["/Marked"]), "document not marked as tagged"
        page_stream = pdf.pages[0]["/Contents"].read_bytes()
        assert b"BDC" in page_stream and b"EMC" in page_stream, "missing marked content"
        assert b"ActualText" in page_stream, "missing /ActualText in marked content"


def test_build_schema_covers_every_public_builder() -> None:
    """BUILD_SCHEMA must describe exactly the public builders."""
    builder_names = {name for name, _, _ in _BUILDERS}
    assert set(BUILD_SCHEMA) == builder_names
    for name, meta in BUILD_SCHEMA.items():
        assert meta["deterministic"] is True, f"{name} not marked deterministic"
        assert "feature" in meta and "adversarial_for" in meta
