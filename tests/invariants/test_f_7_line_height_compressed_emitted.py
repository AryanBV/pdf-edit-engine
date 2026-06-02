"""INV-F-7: structural reflow that compresses line height surfaces it typed.

Roadmap item E.6 (``line_height_compressed`` ACTUALLY emitted). The kind is
declared in the ``DegradationKind`` Literal but, pre-E.6, no source site
emits it. When a bbox-bounded ``replace_block`` overfills a short fixed-
height region, ``structural._replace_block_on_page`` compresses the applied
line height below the document's natural single-line ratio so every wrapped
line fits. That compression must surface as a typed
``Degradation(kind="line_height_compressed", severity="info")`` instead of
silently squeezing the text.

INV-F-5 locks ``compute_uniform_layout`` purity: this probe drives the
``replace_block`` compression site directly (not the batch
``compute_uniform_layout`` path), and the E.6 implementation must NOT mutate
the pure function — it surfaces the degradation from the caller by comparing
applied-vs-natural line height against the 0.95 ratio threshold (the same
5%-deadzone convention the kerning decision uses). ``line_height_compressed``
is NOT in ``FONT_AFFECTING_KINDS`` (it is a layout, not a font, signal), so
``font_preserved`` stays True.

The no-compression regression control pins the over-surfacing boundary: a
replacement that fits the bbox at the natural line height must NOT emit the
degradation.

Regression guard — fails on the pre-E.6 behaviour where the overfilled bbox
compresses line height with no ``line_height_compressed`` event.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pdf_edit_engine import replace_block

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders.reflow_quality import (  # noqa: E402
    build_compression_text,
    build_reflow_quality_pdf,
)

_COMPRESSED_KIND = "line_height_compressed"

# A short fixed-height region over the body block. 50pt of vertical space
# cannot hold the overfilled replacement at the natural 15pt leading, so the
# structural reflow compresses line height (applied/natural ~= 0.48 << 0.95).
_BBOX = (72.0, 655.0, 500.0, 705.0)


def test_inv_f_7_line_height_compression_surfaces_degradation(tmp_path: Path) -> None:
    """An overfilled fixed-height bbox emits a typed line_height_compressed event.

    Regression guard — fails on the pre-E.6 behaviour (success=True, line
    height silently compressed, no typed event).
    """
    src = tmp_path / "reflow_quality.pdf"
    build_reflow_quality_pdf(src)
    out = tmp_path / "compressed_out.pdf"

    tall = build_compression_text(6)
    result = replace_block(str(src), 0, _BBOX, tall, str(out))

    assert result.success, f"compression replace_block must succeed: {result!r}"

    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _COMPRESSED_KIND in kinds, (
        "an overfilled fixed-height bbox compresses line height below the natural "
        f"single-line ratio and must surface a typed {_COMPRESSED_KIND} "
        f"Degradation; got kinds={kinds}"
    )
    deg = next(d for d in result.fidelity_report.degradations if d.kind == _COMPRESSED_KIND)
    assert deg.severity == "info", (
        f"{_COMPRESSED_KIND} must be severity='info'; got {deg.severity!r}"
    )


def test_inv_f_7_compression_not_font_affecting(tmp_path: Path) -> None:
    """The compression event is non font-affecting: font_preserved stays True."""
    from pdf_edit_engine.models import FONT_AFFECTING_KINDS

    assert _COMPRESSED_KIND not in FONT_AFFECTING_KINDS, (
        f"{_COMPRESSED_KIND} is a layout signal and must NOT be in FONT_AFFECTING_KINDS"
    )

    src = tmp_path / "reflow_quality2.pdf"
    build_reflow_quality_pdf(src)
    out = tmp_path / "compressed_out2.pdf"
    tall = build_compression_text(6)
    result = replace_block(str(src), 0, _BBOX, tall, str(out))

    assert result.success
    assert result.fidelity_report.font_preserved, (
        "a line-height-compression signal must not flip font_preserved — glyph "
        f"identity is untouched; report={result.fidelity_report!r}"
    )


def test_inv_f_7_no_compression_does_not_surface(tmp_path: Path) -> None:
    """Over-surfacing control: text that fits must NOT flag compression.

    A replacement short enough to render at the natural line height in the
    same bbox is healthy output; emitting line_height_compressed there would
    be a false positive.
    """
    src = tmp_path / "reflow_quality3.pdf"
    build_reflow_quality_pdf(src)
    out = tmp_path / "fits_out.pdf"

    # One short line easily fits the 50pt region at the natural 15pt leading.
    result = replace_block(str(src), 0, _BBOX, "short replacement line", str(out))

    assert result.success, f"fitting replace_block must succeed: {result!r}"
    kinds = {d.kind for d in result.fidelity_report.degradations}
    assert _COMPRESSED_KIND not in kinds, (
        "a replacement that fits at the natural line height must NOT be flagged "
        f"as compressed; got kinds={kinds}"
    )
