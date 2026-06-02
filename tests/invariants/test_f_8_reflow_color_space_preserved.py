"""INV-F-8: reflow preserves fill color-setting operators verbatim.

Block F CORE color slice. The pre-Block-F ``reflow._build_replacement_ops``
rebuilds fill color from the lossy in-memory ``fill_color`` FLOAT tuple via a
length guess (len 1 -> ``g``, 3 -> ``rg``, 4 -> ``k``). For a device-RGB run
the 3-float guess happens to round-trip, but for a **Separation** (or DeviceN
/ ICCBased / Pattern) run the color-space IDENTITY is silently lost: a
``/CS0 cs 0.8 scn`` spot-color fill collapses to ``0.8 g`` (80% device gray),
with NO degradation surfaced — a silent fidelity break.

INV-F-8 — verbatim color-operator preservation through reflow:
  (a) A Separation-colored paragraph reflowed to a wider replacement keeps its
      Separation color-setting operator subsequence (the ``cs`` + ``scn``
      tokens, NOT collapsed to ``g``).
  (b) A device-RGB paragraph reflowed the same way still round-trips its
      ``rg`` fill (regression control — Block F must not break the path that
      already worked).

The honest-degradation half of the contract (INV-F-9 —
``color_space_approximated`` and its no-false-positive guards) lives in its own
file, ``test_f_9_color_approx_degradation`` (probe-schema S2: one invariant per
file).

Collision note: the F-layer invariant ids run F-1..F-7 today (F-7 == E.6
``line_height_compressed``); a repo-wide grep for ``INV-F-`` confirms no
F-8 / F-9 exists, so this probe mints them cleanly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf

from pdf_edit_engine import find, replace

# tests/ is on sys.path via conftest; corpus_builders is a package under it.
_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from corpus_builders.colored_runs import (  # noqa: E402
    BODY_FIND_ANCHOR,
    SEPARATION_CS_NAME,
    build_devicergb_run_pdf,
    build_separation_run_pdf,
)

# A replacement meaningfully wider than the anchored last line so the engine
# routes through paragraph reflow (surgeon: new_width > old_width + 1.0) and
# re-wraps, exercising _build_replacement_ops' color rebuild.
_WIDER = (
    "when a wider replacement no longer fits the original line breaks and must "
    "be re-wrapped onto considerably more lines than before this edit."
)


def _page_stream(pdf_path: str) -> str:
    """Return page 0's content stream as a latin-1 string for token scanning."""
    with pikepdf.open(pdf_path) as pdf:
        return pdf.pages[0]["/Contents"].read_bytes().decode("latin-1", "replace")


def test_inv_f_8_separation_color_preserved_through_reflow(tmp_path: Path) -> None:
    """A Separation spot-color fill survives reflow (not collapsed to device gray).

    Regression guard — RED on the pre-Block-F engine, which collapses
    ``/CS0 cs 0.8 scn`` to ``0.8 g`` on re-wrap.
    """
    src = tmp_path / "separation_run.pdf"
    build_separation_run_pdf(src)
    out = tmp_path / "separation_out.pdf"

    matches = find(str(src), BODY_FIND_ANCHOR)
    assert matches, f"anchor {BODY_FIND_ANCHOR!r} not found in fixture"

    result = replace(str(src), matches[0], _WIDER, str(out), reflow=True)
    assert result.success, f"reflow replace must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied, "edit must route through reflow"

    stream = _page_stream(str(out))

    # The Separation color space must still be selected via its resource name
    # and tinted via scn — i.e. the color-setting operator subsequence is
    # replayed verbatim rather than rebuilt from the float tuple.
    assert f"/{SEPARATION_CS_NAME} cs" in stream, (
        "reflow must preserve the Separation color-space selection "
        f"'/{SEPARATION_CS_NAME} cs'; the spot-color identity was lost. "
        f"output stream:\n{stream}"
    )
    assert "scn" in stream, (
        "reflow must preserve the 'scn' tint operator; the Separation fill was "
        f"silently reinterpreted. output stream:\n{stream}"
    )
    # And it must NOT have been collapsed to a device-gray fill.
    assert "0.8 g" not in stream, (
        "reflow collapsed the Separation tint 0.8 to device gray '0.8 g' — the "
        f"exact pre-Block-F silent-degradation symptom. output stream:\n{stream}"
    )


def test_inv_f_8_devicergb_color_preserved_through_reflow(tmp_path: Path) -> None:
    """Regression control: a device-RGB fill still round-trips through reflow.

    The pre-Block-F length-guess already preserves a 3-float ``rg`` fill; this
    pins that Block F's verbatim-replay change does not regress it.
    """
    src = tmp_path / "devicergb_run.pdf"
    build_devicergb_run_pdf(src)
    out = tmp_path / "devicergb_out.pdf"

    matches = find(str(src), BODY_FIND_ANCHOR)
    assert matches, f"anchor {BODY_FIND_ANCHOR!r} not found in fixture"

    result = replace(str(src), matches[0], _WIDER, str(out), reflow=True)
    assert result.success, f"reflow replace must succeed: {result!r}"
    assert result.fidelity_report.reflow_applied, "edit must route through reflow"

    stream = _page_stream(str(out))
    assert "1 0 0 rg" in stream, (
        "reflow must preserve the device-RGB fill '1 0 0 rg'; Block F regressed "
        f"the path that already worked. output stream:\n{stream}"
    )
