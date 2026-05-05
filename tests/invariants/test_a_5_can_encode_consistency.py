"""INV-A-5: can_encode and encode agree on success and failure."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.encoding import FontResolverCache

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def _find_resolver(is_cid: bool):
    """Find first resolver in corpus that actually CAN encode 'Hello'.

    v0.1.3: can_encode strengthened to verify glyph coverage, not just
    encoding-map membership. This helper used to filter on encoding-map
    presence ('H' in _unicode_to_byte) but post-Phase-5 strengthening,
    that's no longer sufficient (e.g. F2 Calibri-Bold WinAnsi in resume
    has 'H' in WinAnsi but /Widths only declares space). The probe now
    asks the resolver itself whether it can encode "Hello"; we use the
    first one that says yes — the consistency between can_encode True
    and encode success is the actual invariant under test.
    """
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
                    if r._is_cid != is_cid:
                        continue
                    ok, _ = r.can_encode("Hello")
                    if ok:
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
