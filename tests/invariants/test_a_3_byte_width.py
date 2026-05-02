"""INV-A-3: byte_width matches CID-ness across the corpus."""

from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_edit_engine.encoding import FontResolverCache

CORPUS_DIR = Path(__file__).parent.parent / "corpus"


def test_inv_a_3_byte_width_matches_cid() -> None:
    """For every font in every corpus PDF, _is_cid implies _byte_width == 2; else 1."""
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
                resources = page.get("/Resources", {})
                if resources is None:
                    continue
                fonts = resources.get("/Font", {}) or {}
                for fname in list(fonts.keys()):
                    name = str(fname).lstrip("/")
                    try:
                        resolver = cache.get_resolver(page, name)
                    except Exception:
                        continue
                    if resolver._is_cid:
                        assert resolver._byte_width == 2, (
                            f"{path.name}/{name}: CID font has byte_width={resolver._byte_width}"
                        )
                    else:
                        assert resolver._byte_width == 1, (
                            f"{path.name}/{name}: non-CID font has byte_width="
                            f"{resolver._byte_width}"
                        )
                    checked += 1
    assert checked > 0, "no fonts checked across the corpus"
