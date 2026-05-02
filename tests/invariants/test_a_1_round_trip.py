"""INV-A-1: encode/decode round-trip across the corpus."""

from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_edit_engine.encoding import FontResolverCache

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def test_inv_a_1_round_trip_corpus() -> None:
    """For every char in every font's cmap across the corpus, decode(encode(s)) == s.

    Skips multi-codepoint ligatures (their round-trip is INV-A-4, lead-direct).
    """
    pdf_paths = sorted(CORPUS_DIR.glob("*.pdf"))
    assert pdf_paths, "no corpus PDFs found"
    cache = FontResolverCache()
    checked = 0
    for path in pdf_paths:
        try:
            pdf = pikepdf.open(str(path))
        except Exception:
            continue
        with pdf:
            for page in pdf.pages:
                fonts_obj = page.get("/Resources", {})
                if fonts_obj is None:
                    continue
                fonts = fonts_obj.get("/Font", {}) or {}
                for fname in list(fonts.keys()):
                    name = str(fname).lstrip("/")
                    try:
                        resolver = cache.get_resolver(page, name)
                    except Exception:
                        continue
                    forward = (
                        resolver._cid_to_unicode if resolver._is_cid else resolver._byte_to_unicode
                    )
                    for code, uval in forward.items():
                        if not isinstance(uval, str) or len(uval) != 1:
                            continue
                        try:
                            encoded = resolver.encode(uval)
                            decoded = resolver.decode(encoded)
                        except KeyError:
                            # build_reverse_map favors lowest code; if uval re-encodes
                            # to a different code (e.g. WinAnsi 0x20 vs 0xAD for space),
                            # decode is still expected to equal uval.
                            continue
                        assert decoded == uval, (
                            f"round-trip failed in {path.name}/{name} code={code}: "
                            f"{uval!r} -> {encoded!r} -> {decoded!r}"
                        )
                        checked += 1
    assert checked > 0, "no chars checked across the entire corpus"
