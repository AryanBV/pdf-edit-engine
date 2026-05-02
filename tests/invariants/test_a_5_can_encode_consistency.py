"""INV-A-5: can_encode and encode agree on success and failure."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.encoding import FontResolverCache

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def _find_resolver(is_cid: bool):
    """Find first resolver in corpus matching the CID-ness criterion."""
    cache = FontResolverCache()
    for path in sorted(CORPUS_DIR.glob("*.pdf")):
        try:
            pdf = pikepdf.open(str(path))
        except Exception:
            continue
        with pdf:
            for page in pdf.pages:
                resources = page.get("/Resources", {}) or {}
                fonts = resources.get("/Font", {}) or {}
                for fname in list(fonts.keys()):
                    name = str(fname).lstrip("/")
                    try:
                        r = cache.get_resolver(page, name)
                    except Exception:
                        continue
                    if (
                        r._is_cid == is_cid and "H" in r._unicode_to_cid
                        if is_cid
                        else "H" in r._unicode_to_byte
                    ):
                        return r
    return None


def test_inv_a_5_can_encode_consistency_cid() -> None:
    """can_encode and encode agree for one CID font."""
    resolver = _find_resolver(is_cid=True)
    if resolver is None:
        pytest.skip("no CID font with 'H' available in corpus")
    ok, missing = resolver.can_encode("Hello")
    assert ok and missing == [], f"can_encode said False for 'Hello': missing={missing}"
    resolver.encode("Hello")
    ok2, missing2 = resolver.can_encode("Hello香")
    assert not ok2 and missing2, "expected '香' to be missing"
    with pytest.raises(KeyError) as ei:
        resolver.encode("Hello香")
    assert "香" in repr(ei.value.args[0]) or missing2[0] in repr(ei.value.args[0])


def test_inv_a_5_can_encode_consistency_simple() -> None:
    """can_encode and encode agree for one simple (non-CID) font."""
    resolver = _find_resolver(is_cid=False)
    if resolver is None:
        pytest.skip("no simple font available in corpus")
    ok, missing = resolver.can_encode("Hello")
    assert ok and missing == []
    resolver.encode("Hello")
    ok2, missing2 = resolver.can_encode("Hello香")
    assert not ok2 and missing2
    with pytest.raises(KeyError):
        resolver.encode("Hello香")
