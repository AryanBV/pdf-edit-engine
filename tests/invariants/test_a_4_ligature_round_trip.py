"""INV-A-4 (P1): ligature round-trip via greedy-longest-match encode."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf
import pytest

# B.9 (INV-A-4 STRENGTHENED): module-level import so a missing _classify_ligature
# is a hard collection/RED failure, NOT silently swallowed by the corpus-open
# broad `except` below (which would mask the strengthened fidelity assertions as
# a skip). GREEN adds _classify_ligature; until then this import fails RED.
from pdf_edit_engine.encoding import FontResolverCache, _classify_ligature

if TYPE_CHECKING:
    from pathlib import Path

    from pdf_edit_engine.encoding import FontResolver


def test_inv_a_4_ligature_round_trip(corpus: Path) -> None:
    """For every CID resolver with ``_max_ligature_len > 1``: greedy
    encode of a known multi-codepoint Unicode produces bytes that
    decode back to that same Unicode (CLOSURE), AND — B.9 strengthening —
    a discretionary ligature whose component glyphs are all present does NOT
    collapse on the DEFAULT encode (FIDELITY), while the opt-in path still
    reaches the ligature CID. (Skipped if no ligature-bearing font exists in
    the corpus.)"""
    cache = FontResolverCache()
    found = False
    # Collect (resolver, cid, ustr) candidates INSIDE the corpus-open broad
    # try/except (which must stay broad to tolerate unreadable corpus PDFs),
    # then run the closure + STRENGTHENED fidelity assertions OUTSIDE it so an
    # AssertionError is a genuine RED failure, never swallowed into a skip.
    candidates: list[tuple[FontResolver, int, str]] = []
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
                        for cid, ustr in r._cid_to_unicode.items():  # type: ignore[attr-defined]
                            if len(ustr) > 1:
                                candidates.append((r, cid, ustr))
        except Exception:  # noqa: BLE001
            continue

    for r, cid, ustr in candidates:
        kind = _classify_ligature(ustr)
        components_present = all(c in r._unicode_to_cid for c in ustr)  # type: ignore[attr-defined]
        # Closure: encode-then-decode equals the source. Choose the encode mode
        # B.9 says can actually realise this ligature. A DISCRETIONARY ligature
        # whose component glyphs are ABSENT (the resume's F1 maps CID 302->"fi"
        # but has no standalone 'f' CID) CANNOT encode under default-OFF — the
        # default correctly refuses to silently reuse the ligature glyph for
        # typed-separate text — so its closure is proven via the opt-in path.
        # A discretionary ligature WITH components, or a MANDATORY ligature,
        # closes on the default encode. (B.9 corrects the legacy unconditional
        # default-encode closure assert, which falsely assumed separate-glyph
        # fallthrough is always available.)
        use_opt_in = kind == "discretionary" and not components_present
        encoded = r.encode(ustr, allow_discretionary_ligatures=use_opt_in)
        decoded = r.decode(encoded)
        assert decoded == ustr, (
            f"ligature {ustr!r} (CID {cid:#06x}) round-trip failed: "
            f"encoded={encoded.hex()} decoded={decoded!r}"
        )
        # B.9 (INV-A-4 STRENGTHENED): the original probe only proved CLOSURE
        # (encode-then-decode equals), which a BUGGY greedy collapse ALSO
        # satisfies (collapse "fi"->302, decode 302->"fi"), making it blind to
        # the very corruption B.9 fixes. Strengthen to a FIDELITY check: for a
        # DISCRETIONARY ligature whose component glyphs are all present, the
        # DEFAULT encode must NOT collapse to the single ligature CID — it must
        # emit > 2 bytes (separate glyphs). The opt-in path must always reach the
        # ligature CID. Against the old greedy code the > 2 assertion FAILS (it
        # collapsed to one 2-byte CID) = RED for the right reason. (Assertions
        # ADDED, none removed.)
        if kind == "discretionary":
            if components_present:
                assert len(r.encode(ustr)) > 2, (
                    f"discretionary ligature {ustr!r} collapsed by default"
                )
            assert r.encode(ustr, allow_discretionary_ligatures=True) == bytes(
                [cid >> 8, cid & 0xFF]
            )
        found = True

    if not found:
        pytest.skip("no ligature-bearing CIDFont in corpus")
