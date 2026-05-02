"""INV-E-1: dry_run=True does not modify input bytes."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from pdf_edit_engine import replace_all

if TYPE_CHECKING:
    from pathlib import Path


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_e_1_dry_run_preserves_bytes(reportlab_simple: Path, tmp_path: Path) -> None:
    """`replace_all(p, X, Y, out, dry_run=True)` does not modify `p`'s bytes."""
    before = _sha256(reportlab_simple)
    out = tmp_path / "out.pdf"
    results = replace_all(str(reportlab_simple), "Test", "Demo", str(out), dry_run=True)
    after = _sha256(reportlab_simple)
    assert before == after, "dry_run mutated input PDF bytes"
    # And the dry-run should not write the output file (or if it did, that's
    # also a violation of dry_run semantics).
    assert not out.exists(), "dry_run wrote output file"
    # We expect at least one synthetic result (Test appears once in fixture).
    assert isinstance(results, list)
