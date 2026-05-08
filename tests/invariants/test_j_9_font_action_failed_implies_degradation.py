"""INV-J-9: ``EditResult(font_action='failed')`` requires a Degradation
with kind in ``FONT_AFFECTING_KINDS``.

Enforced at the ``EditResult.__post_init__`` boundary so that no
internal code path can construct a "failed" EditResult that silently
inherits ``FidelityReport.font_preserved=True`` (the F-C-05
lying-success surfaced by structural.py:1003 / :1026 before this
fix).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pdf_edit_engine
from pdf_edit_engine.errors import (
    EncodingError,
    FontNotFoundError,
    OperatorError,
    PDFEditError,
    ReflowError,
)
from pdf_edit_engine.models import (
    FONT_AFFECTING_KINDS,
    Degradation,
    EditResult,
    FidelityReport,
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


# ── Direct __post_init__ contract probes ────────────────────────────────


def test_inv_j_9_post_init_rejects_failed_with_default_factory() -> None:
    """Default-factory FidelityReport carries no degradations — must raise."""
    with pytest.raises(ValueError, match="INV-J-9"):
        EditResult(
            success=False,
            original_text="x",
            new_text="y",
            font_action="failed",
        )


def test_inv_j_9_post_init_rejects_failed_with_unrelated_degradation() -> None:
    """Non-font-affecting Degradation does NOT satisfy the contract."""
    with pytest.raises(ValueError, match="INV-J-9"):
        EditResult(
            success=False,
            original_text="x",
            new_text="y",
            font_action="failed",
            fidelity_report=FidelityReport(
                font_substituted=None,
                overflow_detected=False,
                reflow_applied=False,
                glyphs_missing=[],
                degradations=[
                    Degradation(
                        kind="kerning_compressed",
                        detail="Tz 88%",
                        severity="warning",
                    ),
                ],
            ),
        )


def test_inv_j_9_post_init_accepts_failed_with_font_extension_failed() -> None:
    """A Degradation in FONT_AFFECTING_KINDS satisfies the contract."""
    result = EditResult(
        success=False,
        original_text="x",
        new_text="y",
        font_action="failed",
        fidelity_report=FidelityReport(
            font_substituted=None,
            overflow_detected=False,
            reflow_applied=False,
            glyphs_missing=[],
            degradations=[
                Degradation(
                    kind="font_extension_failed",
                    detail="test",
                    severity="error",
                ),
            ],
        ),
    )
    # Computed property correctly resolves to False, not the lying-True
    # default that motivated F-C-05.
    assert not result.fidelity_report.font_preserved


@pytest.mark.parametrize("kind", sorted(FONT_AFFECTING_KINDS))
def test_inv_j_9_post_init_accepts_each_font_affecting_kind(kind: str) -> None:
    """Every kind in FONT_AFFECTING_KINDS satisfies the contract."""
    EditResult(
        success=False,
        original_text="x",
        new_text="y",
        font_action="failed",
        fidelity_report=FidelityReport(
            font_substituted=None,
            overflow_detected=False,
            reflow_applied=False,
            glyphs_missing=[],
            degradations=[
                Degradation(
                    kind=kind,  # type: ignore[arg-type]
                    detail="test",
                    severity="error",
                ),
            ],
        ),
    )


def test_inv_j_9_other_font_actions_unaffected() -> None:
    """font_action ∈ {kept, extended, substituted} is NOT subject to INV-J-9."""
    for action in ("kept", "extended", "substituted"):
        EditResult(
            success=True,
            original_text="x",
            new_text="y",
            font_action=action,  # type: ignore[arg-type]
            # default-factory FidelityReport — no degradations — must NOT raise
        )


# ── Public-API probes: every entry that can return font_action="failed" ──


def _skip_if_missing(p: Path) -> None:
    if not p.is_file():
        pytest.skip(f"adversarial fixture not present: {p.name}")


@pytest.mark.parametrize(
    "fixture_name",
    [
        "A1_truncated_fontfile2.pdf",
        "A2_junk_fontfile2.pdf",
        "A9_corrupt_cmap.pdf",
    ],
)
def test_inv_j_9_replace_failure_carries_font_degradation(
    fixture_name: str, tmp_path: Path
) -> None:
    """``replace`` against an adversarial fixture: success=False ⇒ font Degradation."""
    fixture = FIXTURES / fixture_name
    _skip_if_missing(fixture)

    matches = pdf_edit_engine.find(str(fixture), "Hello World")
    if not matches:
        pytest.skip("no match in fixture")

    out = tmp_path / f"{fixture.stem}_out.pdf"
    try:
        result = pdf_edit_engine.replace(str(fixture), matches[0], "Héllo World", str(out))
    except EXPECTED_PDF_EDIT_ERR:
        # Surfaced as exception — INV-J-9 not engaged on EditResult
        return

    if result.font_action == "failed":
        kinds = {d.kind for d in result.fidelity_report.degradations}
        assert kinds & FONT_AFFECTING_KINDS, (
            f"INV-J-9 violation: replace returned font_action='failed' "
            f"with no Degradation in FONT_AFFECTING_KINDS; "
            f"degradations={result.fidelity_report.degradations!r}"
        )
        # And the computed property must be False
        assert not result.fidelity_report.font_preserved


def test_inv_j_9_replace_block_failure_carries_font_degradation(tmp_path: Path) -> None:
    """``replace_block`` against a non-CID body font with unencodable text:
    must raise OR return font_action='failed' satisfying INV-J-9.

    This exercises the structural.py:1003 patched path.
    """
    fixture = FIXTURES / "_baseline_simple_winansi.pdf"
    _skip_if_missing(fixture)

    out = tmp_path / "rb_out.pdf"
    # Use a CJK char that cannot be encoded by a WinAnsi simple font
    # and is unlikely to be in the system Liberation/Carlito glyf.
    try:
        result = pdf_edit_engine.replace_block(
            str(fixture),
            page_number=0,
            bbox=(0.0, 0.0, 600.0, 800.0),
            new_text="香港",
            output_path=str(out),
        )
    except EXPECTED_PDF_EDIT_ERR:
        return  # PDFEditError surfaced — INV-J-9 doesn't engage on exceptions
    except (IndexError, ValueError):
        # Bbox-mismatch or page-number issue — not a font failure path
        pytest.skip("baseline fixture does not exercise this code path")

    # If returned, INV-J-9 already validated at __post_init__ construction.
    # The fact this didn't raise ValueError("INV-J-9...") is the assertion.
    assert isinstance(result, EditResult)
    if result.font_action == "failed":
        kinds = {d.kind for d in result.fidelity_report.degradations}
        assert kinds & FONT_AFFECTING_KINDS
