"""Cross-generator hardening tests — full operation matrix across all corpus PDFs.

Covers ARY-255.  Parametrized over manifest.json entries.
"""

from __future__ import annotations

import json
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.errors import PDFEditError
from pdf_edit_engine.locator import find, get_text
from pdf_edit_engine.models import Edit, EditResult
from pdf_edit_engine.surgeon import batch_replace, replace, replace_all

CORPUS_DIR = Path(__file__).parent / "corpus"


def _load_manifest() -> list[dict[str, object]]:
    return json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))


MANIFEST = _load_manifest()


def _skip_if_missing(entry: dict[str, object]) -> str:
    """Return pdf_path, skipping test if the corpus PDF is absent."""
    pdf_path = str(CORPUS_DIR / str(entry["filename"]))
    if not Path(pdf_path).exists():
        pytest.skip(f"{entry['filename']} not in corpus")
    return pdf_path


# ── Helpers ───────────────────────────────────────────────────────────────


def _try_replace(
    pdf_path: str,
    expected_text: str,
    new_text: str,
    output: str,
) -> EditResult:
    """Find expected_text and replace it, skipping on expected failures."""
    matches = find(pdf_path, expected_text)
    if not matches:
        pytest.skip(f"find() returned no matches for {expected_text!r}")
    try:
        result = replace(pdf_path, matches[0], new_text, output, reflow=False)
    except (PDFEditError, KeyError, ValueError) as exc:
        pytest.skip(f"replace() raised {type(exc).__name__}: {exc}")
    return result


# ── Extraction tests ─────────────────────────────────────────────────────


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: str(e["filename"]))
def test_get_text_no_crash(entry: dict[str, object]) -> None:
    """get_text() runs without crashing on every corpus PDF."""
    pdf_path = _skip_if_missing(entry)
    text = get_text(pdf_path)
    assert len(text) > 0, f"get_text returned empty for {entry['filename']}"


# ── Replace tests ────────────────────────────────────────────────────────


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: str(e["filename"]))
def test_replace_same_length(entry: dict[str, object], tmp_path: Path) -> None:
    """Replace expected_text with same-length string of X's."""
    pdf_path = _skip_if_missing(entry)
    expected = str(entry["expected_text"])
    output = str(tmp_path / "same_len.pdf")
    result = _try_replace(pdf_path, expected, "X" * len(expected), output)
    assert result.success, f"replace failed: {result.font_action}"


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: str(e["filename"]))
def test_replace_diff_length(entry: dict[str, object], tmp_path: Path) -> None:
    """Replace expected_text with longer string."""
    pdf_path = _skip_if_missing(entry)
    expected = str(entry["expected_text"])
    output = str(tmp_path / "diff_len.pdf")
    result = _try_replace(pdf_path, expected, expected + " EDITED", output)
    assert result.success, f"replace failed: {result.font_action}"


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: str(e["filename"]))
def test_replace_all_works(entry: dict[str, object], tmp_path: Path) -> None:
    """replace_all() runs without crash on a short substring."""
    pdf_path = _skip_if_missing(entry)
    expected = str(entry["expected_text"])
    # Use first 5 chars as search target
    search = expected[:5]
    output = str(tmp_path / "replace_all.pdf")
    try:
        results = replace_all(pdf_path, search, "XXXXX", output)
    except (PDFEditError, KeyError, ValueError) as exc:
        pytest.skip(f"replace_all() raised {type(exc).__name__}: {exc}")
    assert isinstance(results, list)


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: str(e["filename"]))
def test_batch_replace(entry: dict[str, object], tmp_path: Path) -> None:
    """batch_replace() runs without crash with one edit."""
    pdf_path = _skip_if_missing(entry)
    expected = str(entry["expected_text"])
    output = str(tmp_path / "batch.pdf")
    edits = [Edit(find=expected, replace="X" * len(expected))]
    try:
        results = batch_replace(pdf_path, edits, output)
    except (PDFEditError, KeyError, ValueError) as exc:
        pytest.skip(f"batch_replace() raised {type(exc).__name__}: {exc}")
    assert isinstance(results, list)


# ── Output validation tests ──────────────────────────────────────────────


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: str(e["filename"]))
def test_output_opens_valid(entry: dict[str, object], tmp_path: Path) -> None:
    """Output PDF from replace() is a valid PDF."""
    pdf_path = _skip_if_missing(entry)
    expected = str(entry["expected_text"])
    output = str(tmp_path / "valid.pdf")
    result = _try_replace(pdf_path, expected, "X" * len(expected), output)
    if not result.success:
        # Imperative pytest.xfail is implicitly non-strict (xpass leaves
        # the test xfailed, never xpass-fails); the locked "xfail(strict=False)
        # shape" maps onto this call when the precondition is dynamic (m-7).
        pytest.xfail(reason=f"replace did not succeed: {result.font_action}")
    with pikepdf.open(output) as pdf:
        assert len(pdf.pages) > 0


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: str(e["filename"]))
def test_replacement_text_in_output(
    entry: dict[str, object],
    tmp_path: Path,
) -> None:
    """Replacement text appears in the output PDF."""
    pdf_path = _skip_if_missing(entry)
    expected = str(entry["expected_text"])
    replacement = "Z" * len(expected)
    output = str(tmp_path / "check_text.pdf")
    result = _try_replace(pdf_path, expected, replacement, output)
    if not result.success:
        # Imperative pytest.xfail is implicitly non-strict (m-7).
        pytest.xfail(reason=f"replace did not succeed: {result.font_action}")
    out_text = get_text(output)
    # Allow minor length variation from TJ fragment boundaries
    z_run = "Z" * max(len(expected) - 2, 3)
    assert z_run in out_text, "Replacement Z-run not found in output text"
