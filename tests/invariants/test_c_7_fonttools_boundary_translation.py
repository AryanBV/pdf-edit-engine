"""INV-C-7: Every fontTools entry point in the engine runs inside
``_with_fonttools_translation``.

Adversarial ``/FontFile2`` payloads (truncated, junk header, corrupt
cmap) must never escape as raw fontTools exceptions
(``AssertionError``, ``struct.error``, ``TTLibError``,
``OSError``, ``MemoryError``, ``OverflowError``). Every public-API
path that touches fontTools must either succeed or raise a
``PDFEditError`` subclass.

This is the post-fix regression guard for findings B-1, F-B-01,
F-B-02, F-B-05. Before the boundary translator landed,
``replace`` and ``extend_subset`` against the A1/A9 fixtures leaked
``AssertionError`` and ``struct.error`` respectively.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

import pdf_edit_engine
from pdf_edit_engine.errors import (
    EncodingError,
    FontNotFoundError,
    OperatorError,
    PDFEditError,
    ReflowError,
)

FIXTURES = (
    Path(__file__).parent.parent.parent
    / "experiments"
    / "v013_block_3_review"
    / "security-fixtures"
)

EXPECTED_PDF_EDIT_ERR: tuple[type[BaseException], ...] = (
    PDFEditError,
    FontNotFoundError,
    EncodingError,
    OperatorError,
    ReflowError,
)


# Adversarial fixtures targeting the fontTools boundary. Each one
# triggers a different fontTools failure mode the translator must
# convert into ``FontNotFoundError``.
_BOUNDARY_FIXTURES = [
    # (filename, label, exception_class_pre_fix)
    ("A1_truncated_fontfile2.pdf", "A1_truncated", "AssertionError"),
    ("A2_junk_fontfile2.pdf", "A2_junk", "TTLibError"),
    ("A9_corrupt_cmap.pdf", "A9_corrupt_cmap", "struct.error"),
]


def _skip_if_missing(p: Path) -> None:
    if not p.is_file():
        pytest.skip(f"adversarial fixture not present: {p.name}")


@pytest.mark.parametrize(("fixture_name", "label", "_pre_fix_exc"), _BOUNDARY_FIXTURES)
def test_inv_c_7_extend_subset_translates_boundary_exceptions(
    fixture_name: str, label: str, _pre_fix_exc: str
) -> None:
    """``extend_subset`` against A1/A2/A9 fixtures must surface PDFEditError, not raw fontTools."""
    fixture = FIXTURES / fixture_name
    _skip_if_missing(fixture)

    with pikepdf.Pdf.open(str(fixture)) as pdf:
        page = pdf.pages[0]
        # ``é`` forces the simple-font Tier 1.5 path on the synthetic
        # /F1 (WinAnsi TrueType), which loads /FontFile2 via fontTools.
        try:
            pdf_edit_engine.extend_subset(pdf, page, "/F1", "é")
        except EXPECTED_PDF_EDIT_ERR:
            return  # CLEAN — translator did its job
        except (AssertionError, OverflowError, MemoryError) as exc:
            pytest.fail(
                f"INV-C-7 violation [{label}]: raw {type(exc).__name__} "
                f"escaped extend_subset: {exc!s}"
            )
        # Some fixtures may succeed (no fontTools failure on this codepoint)
        # — that's fine; the invariant is about exception type, not outcome.


@pytest.mark.parametrize(("fixture_name", "label", "_pre_fix_exc"), _BOUNDARY_FIXTURES)
def test_inv_c_7_replace_translates_boundary_exceptions(
    fixture_name: str, label: str, _pre_fix_exc: str, tmp_path: Path
) -> None:
    """A1/A2/A9 ``replace``: EditResult or PDFEditError; never raw fontTools."""
    fixture = FIXTURES / fixture_name
    _skip_if_missing(fixture)

    matches = pdf_edit_engine.find(str(fixture), "Hello World")
    if not matches:
        pytest.skip(f"{label}: no 'Hello World' match in fixture")

    out = tmp_path / f"{fixture.stem}_out.pdf"
    try:
        result = pdf_edit_engine.replace(str(fixture), matches[0], "Héllo World", str(out))
    except EXPECTED_PDF_EDIT_ERR:
        return  # CLEAN
    except (AssertionError, OverflowError, MemoryError) as exc:
        pytest.fail(
            f"INV-C-7 violation [{label}]: raw {type(exc).__name__} escaped replace: {exc!s}"
        )

    # Replace returned an EditResult — failure modes must be surfaced
    # via degradations, not silent.
    if not result.success:
        kinds = {d.kind for d in result.fidelity_report.degradations}
        assert "font_extension_failed" in kinds, (
            f"{label}: replace returned success=False but no "
            f"font_extension_failed Degradation in {result.fidelity_report.degradations!r}"
        )


def test_inv_c_7_translator_present_in_fonts_module() -> None:
    """The boundary translator is named and importable from fonts.py."""
    from pdf_edit_engine.fonts import _with_fonttools_translation

    assert callable(_with_fonttools_translation)


def test_inv_c_7_translator_translates_assertion_error() -> None:
    """Hand-wired probe: AssertionError inside the manager → FontNotFoundError."""
    from pdf_edit_engine.fonts import _with_fonttools_translation

    with (
        pytest.raises(FontNotFoundError) as ei,
        _with_fonttools_translation("unit_test"),
    ):
        raise AssertionError("simulated truncated /FontFile2")
    assert "AssertionError" in str(ei.value)
    assert isinstance(ei.value.__cause__, AssertionError)


def test_inv_c_7_translator_translates_struct_error() -> None:
    """Hand-wired probe: struct.error inside the manager → FontNotFoundError."""
    import struct

    from pdf_edit_engine.fonts import _with_fonttools_translation

    with (
        pytest.raises(FontNotFoundError) as ei,
        _with_fonttools_translation("unit_test"),
    ):
        raise struct.error("simulated corrupt sfnt header")
    assert "error" in str(ei.value)
    assert isinstance(ei.value.__cause__, struct.error)


def test_inv_c_7_translator_does_not_mask_programmer_errors() -> None:
    """Skeptic-B masking-risk: KeyError/IndexError/AttributeError must propagate as-is.

    Programmer typos must NOT silently rebrand to FontNotFoundError —
    that would defeat the v0.1.3 honesty theme. The catch list is
    narrowed deliberately (TTLibError, AssertionError, struct.error,
    OSError, MemoryError, OverflowError) to keep these escape paths
    open for tests to surface.
    """
    from pdf_edit_engine.fonts import _with_fonttools_translation

    for exc_cls in (KeyError, IndexError, AttributeError, ValueError, TypeError):
        with pytest.raises(exc_cls), _with_fonttools_translation("unit_test"):
            raise exc_cls("programmer typo")
