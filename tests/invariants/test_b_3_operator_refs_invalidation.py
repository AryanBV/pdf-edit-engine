"""INV-B-3 (P0): operator_refs invalidation after mutation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pdf_edit_engine import find, replace, replace_all
from pdf_edit_engine.errors import PDFEditError

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_b_3_textmatch_after_replace_does_not_silent_corrupt(
    reportlab_simple: Path,
    tmp_path: Path,
) -> None:
    """A TextMatch from before a replace() must NOT silently corrupt
    when reused after the replace. Either it raises (OperatorError /
    other PDFEditError) or it produces an EditResult that is correctly
    flagged success=False — but never silent unrelated mutation."""
    out1 = tmp_path / "after1.pdf"
    out2 = tmp_path / "after2.pdf"

    matches = find(str(reportlab_simple), "and")
    if not matches:
        pytest.skip("'and' not in reportlab_simple")

    # First, drive a real mutation via replace_all (does not consume the
    # match list above; matches are still pointing at original op indices).
    replace_all(str(reportlab_simple), "and", "but", str(out1))

    stale_match = matches[0]

    raised: BaseException | None = None
    result = None
    try:
        result = replace(str(out1), stale_match, "ZZZ", str(out2))
    except BaseException as e:  # noqa: BLE001
        raised = e

    if raised is not None:
        # Acceptance: contained error, surfaced as PDFEditError subclass.
        assert isinstance(raised, PDFEditError), (
            f"reusing stale TextMatch raised non-engine exception {type(raised).__name__}: {raised}"
        )
    else:
        # Acceptance: ran but flagged failure / warning.
        assert result is not None
        assert (not result.success) or result.warnings, (
            "stale TextMatch silently produced success=True with no warning"
        )
