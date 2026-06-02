"""INV-W-3: a linearized (Fast Web View) input is preserved on save.

A *linearized* PDF places a linearization parameter dictionary at the front
of the file so a viewer can render the first page before the whole file has
downloaded ("Fast Web View"). pikepdf detects this on an opened document via
``pdf.is_linearized`` and can re-emit it via ``pdf.save(..., linearize=True)``.

TODAY the engine's single canonical save helper (``_pathutil._save_pdf``)
calls ``pdf.save(output_path)`` with no ``linearize`` flag, so EVERY edit
silently down-converts a linearized input to a non-linearized output — a
fidelity loss with ZERO caller signal (verified RED below: the output of a
public ``replace`` on a linearized input reports ``is_linearized is False``
and the ``FidelityReport`` carries no degradation).

Root fix (A2.2, NOT a patch): ``_save_pdf`` reads ``pdf.is_linearized``
BEFORE serializing; when True it passes ``linearize=True`` so the property
round-trips. If pikepdf cannot re-linearize (raises ``pikepdf.PdfError`` on
``linearize=True``), the helper falls back to a normal save AND records the
loss so the edit verb can surface a typed ``linearization_dropped``
``Degradation`` (severity ``"info"``, NOT in ``FONT_AFFECTING_KINDS`` — a
file-layout change does not alter glyph identity, so ``font_preserved``
stays True) in ``EditResult.fidelity_report.degradations``. A NON-linearized
input is unaffected (the linearize flag is never set, so the save path is
byte-identical to today).

INV-W-3 is minted as the next collision-free slot of the ``W`` robustness
layer (INV-W-1 = width-cache objgen hygiene; INV-W-2 = q/Q graphics-state
depth cap, A1.2). A grep of ``tests/invariants/`` for ``INV-W-[0-9]+`` at
authoring time returned only W-1 and W-2, so W-3 is free. A save-time
FILE-FIDELITY guard (preserve a structural property of the input on write)
belongs in the ``W`` robustness layer alongside its save-path siblings,
rather than the content-stream-format ``B`` layer, the input-rejection
``M`` layer, the exception-translation ``L`` layer (which governs that save
errors *translate* to ``PDFEditError`` — orthogonal to *what* we save), or
the ``J`` fidelity-report layer (which governs the report's internal
contracts, not the act of detecting + preserving a save-time property).

RED EXPECTATION (this phase, no ``src/`` changes):
- ``test_inv_w_3_linearized_input_preserved_on_edit`` is GENUINELY RED:
  the engine saves non-linearized today, so the reopened output reports
  ``is_linearized is False`` and the assertion fails.
- ``test_inv_w_3_relinearize_failure_surfaces_degradation`` is RED: today
  there is no ``linearization_dropped`` degradation and no fallback, so the
  forced-failure either errors out or saves silently without the signal.
- ``test_inv_w_3_nonlinearized_control_unchanged`` PASSES today (control:
  a non-linearized input already stays non-linearized with no degradation)
  and must keep passing after the fix — it pins the no-over-surfacing /
  byte-identical-default half of the invariant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf
import pytest

from pdf_edit_engine import find, replace
from pdf_edit_engine.errors import PDFEditError
from tests.corpus_builders.linearized import (
    FIND_ANCHOR,
    REPLACEMENT,
    build_linearized_pdf,
    build_nonlinearized_pdf,
)

if TYPE_CHECKING:
    from pathlib import Path


def _edit_anchor(inp: Path, out: Path) -> object:
    """Locate the anchor word in ``inp`` and replace it, writing ``out``.

    Returns the ``EditResult``. Asserts the find + edit both succeed so a
    fixture regression surfaces as a clear failure rather than an opaque
    index error downstream.
    """
    matches = find(str(inp), FIND_ANCHOR)
    assert matches, f"fixture regression: anchor {FIND_ANCHOR!r} not found"
    result = replace(str(inp), matches[0], REPLACEMENT, str(out))
    assert result.success, "edit did not succeed on the fixture"
    return result


def _has_linearization_dropped(result: object) -> bool:
    """True iff ``result``'s FidelityReport carries a ``linearization_dropped``."""
    degs = result.fidelity_report.degradations  # type: ignore[attr-defined]
    return any(d.kind == "linearization_dropped" for d in degs)


def test_inv_w_3_linearized_input_preserved_on_edit(tmp_path: Path) -> None:
    """A linearized input edited via the public API yields a linearized output.

    Regression guard (INV-W-3): the engine must DETECT that the input was
    linearized (``pdf.is_linearized``) and PRESERVE that property on save.
    Pre-fix the engine always saves non-linearized, so this is RED today.
    """
    inp = tmp_path / "linearized_in.pdf"
    out = tmp_path / "linearized_out.pdf"
    inp.write_bytes(build_linearized_pdf())

    # Sanity: the fixture really is linearized on input.
    with pikepdf.open(str(inp)) as pdf:
        assert pdf.is_linearized, "fixture regression: input is not linearized"

    result = _edit_anchor(inp, out)

    with pikepdf.open(str(out)) as pdf:
        assert pdf.is_linearized, (
            "INV-W-3 violated: a linearized input was silently saved "
            "NON-linearized (Fast Web View lost with no caller signal)"
        )

    # Preservation succeeded → no linearization_dropped degradation, and the
    # font identity is untouched regardless.
    assert not _has_linearization_dropped(result), (
        "linearization_dropped must NOT be emitted when preservation succeeds"
    )
    assert result.fidelity_report.font_preserved is True


def test_inv_w_3_nonlinearized_control_unchanged(tmp_path: Path) -> None:
    """Control: a non-linearized input stays non-linearized, no degradation.

    Regression guard (INV-W-3): the preservation logic must key on the
    INPUT's ``is_linearized`` — a non-linearized input must follow the
    byte-identical default save path and never gain a spurious
    ``linearization_dropped`` (no over-surfacing). PASSES today; pins the
    control half of the invariant after the fix lands.
    """
    inp = tmp_path / "plain_in.pdf"
    out = tmp_path / "plain_out.pdf"
    inp.write_bytes(build_nonlinearized_pdf())

    with pikepdf.open(str(inp)) as pdf:
        assert not pdf.is_linearized, "fixture regression: control is linearized"

    result = _edit_anchor(inp, out)

    with pikepdf.open(str(out)) as pdf:
        assert not pdf.is_linearized, (
            "INV-W-3 violated: a non-linearized input was unexpectedly "
            "linearized on save (over-surfacing the preservation)"
        )

    assert not _has_linearization_dropped(result), (
        "linearization_dropped must NOT be emitted for a non-linearized input"
    )
    assert result.fidelity_report.font_preserved is True


def test_inv_w_3_relinearize_failure_surfaces_degradation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When re-linearization fails, the edit still succeeds + surfaces the loss.

    Regression guard (INV-W-3 honest fallback): if pikepdf raises on
    ``linearize=True``, ``_save_pdf`` must retry WITHOUT linearize so the
    edit still succeeds, AND record the loss so the verb surfaces a typed
    ``linearization_dropped`` (severity ``"info"``, NOT font-affecting). The
    loss must be HONEST, never silent.

    The failure is injected by monkeypatching ``pikepdf.Pdf.save`` (the same
    bound method ``_save_pdf`` invokes) to raise ``pikepdf.PdfError`` only
    when called with ``linearize=True``; a normal save proceeds. This forces
    exactly the fallback path the invariant governs.

    RED today: there is no fallback and no ``linearization_dropped`` kind, so
    the forced failure surfaces neither a successful edit with the
    degradation nor the typed signal.
    """
    inp = tmp_path / "linearized_in.pdf"
    out = tmp_path / "linearized_out.pdf"
    inp.write_bytes(build_linearized_pdf())

    original_save = pikepdf.Pdf.save

    def failing_save(self: pikepdf.Pdf, *args: object, **kwargs: object) -> None:
        if kwargs.get("linearize"):
            raise pikepdf.PdfError("forced re-linearization failure (INV-W-3 probe)")
        return original_save(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pikepdf.Pdf, "save", failing_save)

    matches = find(str(inp), FIND_ANCHOR)
    assert matches, f"fixture regression: anchor {FIND_ANCHOR!r} not found"
    result = replace(str(inp), matches[0], REPLACEMENT, str(out))

    # The edit must NOT hard-fail just because re-linearization was impossible.
    assert result.success, (
        "INV-W-3 violated: re-linearization failure must fall back to a "
        "normal save (the edit still succeeds), not abort the edit"
    )

    # The loss must be surfaced as a typed, info-severity, non-font-affecting
    # degradation — never dropped silently.
    degs = result.fidelity_report.degradations
    dropped = [d for d in degs if d.kind == "linearization_dropped"]
    assert dropped, (
        "INV-W-3 violated: re-linearization failure was not surfaced as a "
        f"linearization_dropped degradation; got kinds={[d.kind for d in degs]}"
    )
    assert dropped[0].severity == "info"
    # Non font-affecting: a file-layout change leaves glyph identity intact.
    assert result.fidelity_report.font_preserved is True


def test_inv_w_3_linearized_save_oserror_translates_no_path_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError on the ``linearize=True`` save translates + strips the path.

    Regression guard (INV-W-3 / INV-L-1 / F-C-03 parity): the ``linearize=True``
    attempt in ``_save_pdf`` must be covered by the SAME exception translation
    as the normal save. A linearized input saved to an unwritable target makes
    ``pdf.save(linearize=True)`` raise a RAW ``OSError`` subclass (e.g.
    ``PermissionError`` / ``FileNotFoundError`` / ``NotADirectoryError``) whose
    message carries the ABSOLUTE output path. Pre-fix the linearize attempt's
    only ``except`` catches ``pikepdf.PdfError`` — which does NOT cover
    ``OSError`` — so the raw exception escapes ``_save_pdf``, bypassing the
    friendly ``PDFEditError`` translation AND leaking the absolute path. The
    identical NON-linearized save translates it to a clean ``PDFEditError`` with
    the directory portion stripped, so the linearized path was strictly less
    safe.

    The failure is injected by monkeypatching ``pikepdf.Pdf.save`` to raise a
    ``PermissionError`` whose message embeds the absolute output path ONLY when
    called with ``linearize=True`` (a normal save would proceed). This is
    deterministic and cross-platform: it forces exactly the IO-failure branch
    the fix must route through the outer translator. An IO failure is NOT a
    can't-linearize failure, so NO ``linearization_dropped`` degradation is
    emitted — the raw OSError simply translates like any other save IO error.

    RED today: the raw ``PermissionError`` leaks (not a ``PDFEditError``) and
    its message contains the absolute output path.
    """
    inp = tmp_path / "linearized_in.pdf"
    out = tmp_path / "linearized_out.pdf"
    inp.write_bytes(build_linearized_pdf())

    original_save = pikepdf.Pdf.save
    # Embed the absolute output path in the OSError message so the path-leak
    # assertion has something concrete to detect (mirrors how a real
    # PermissionError / FileNotFoundError from the OS carries the target path).
    leak_marker = str(out)

    def failing_save(self: pikepdf.Pdf, *args: object, **kwargs: object) -> None:
        if kwargs.get("linearize"):
            raise PermissionError(f"forced IO failure on {leak_marker}")
        return original_save(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pikepdf.Pdf, "save", failing_save)

    matches = find(str(inp), FIND_ANCHOR)
    assert matches, f"fixture regression: anchor {FIND_ANCHOR!r} not found"

    # The raw OSError from the linearize attempt must be translated to a
    # PDFEditError, identically to the non-linearized save's IO-error path —
    # NOT leaked raw (which would be a strictly-less-safe linearized branch).
    with pytest.raises(PDFEditError) as excinfo:  # noqa: PT012 — public-API call drives the raise
        replace(str(inp), matches[0], REPLACEMENT, str(out))

    # The translated message must NOT leak the absolute output path. The
    # directory portion is the sensitive part; the bare basename is acceptable
    # (the existing PermissionError translation surfaces ``Path(out).name``).
    message = str(excinfo.value)
    parent_dir = str(out.parent)
    assert parent_dir not in message, (
        "INV-W-3 / F-C-03 violated: the translated save error leaked the "
        f"absolute output-path directory portion {parent_dir!r}: {message!r}"
    )
