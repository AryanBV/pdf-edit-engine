"""INV-A-4 (P1): ligature round-trip via greedy-longest-match encode."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf
import pytest

from pdf_edit_engine.encoding import FontResolverCache

if TYPE_CHECKING:
    from pathlib import Path


def test_inv_a_4_ligature_round_trip(corpus: Path) -> None:
    """For every CID resolver with ``_max_ligature_len > 1``: greedy
    encode of a known multi-codepoint Unicode produces bytes that
    decode back to that same Unicode. (Skipped if no ligature-bearing
    font exists in the corpus.)"""
    cache = FontResolverCache()
    found = False
    for pdf_path in sorted(corpus.glob("*.pdf")):
        try:
            with pikepdf.open(str(pdf_path)) as pdf:
                for page in pdf.pages:
                    fonts = page.get("/Resources", {}).get("/Font", {}) or {}
                    for fname in fonts:
                        try:
                            r = cache.get_resolver(page, str(fname).lstrip("/"))
                        except Exception:  # noqa: BLE001
                            continue
                        if not r._is_cid:  # type: ignore[attr-defined]
                            continue
                        max_len = r._max_ligature_len  # type: ignore[attr-defined]
                        if max_len <= 1:
                            continue
                        # Find a ligature-mapped Unicode value of length > 1
                        for cid, ustr in r._cid_to_unicode.items():  # type: ignore[attr-defined]
                            if len(ustr) > 1:
                                # Round-trip
                                encoded = r.encode(ustr)
                                decoded = r.decode(encoded)
                                assert decoded == ustr, (
                                    f"ligature {ustr!r} (CID {cid:#06x}) "
                                    f"round-trip failed in {pdf_path.name}: "
                                    f"encoded={encoded.hex()} decoded={decoded!r}"
                                )
                                found = True
        except Exception:  # noqa: BLE001
            continue

    if not found:
        pytest.skip("no ligature-bearing CIDFont in corpus")
