"""INV-B-5 (P0): content-stream parse/unparse failures → OperatorError, never raw pikepdf.

``pikepdf.parse_content_stream`` and ``pikepdf.unparse_content_stream`` can
raise three exception families on a malformed or un-serializable content
stream:

- ``pikepdf.PdfError`` — the residual ``raise e from e`` branch of
  ``parse_content_stream`` (e.g. "ignoring non-stream in an array of
  streams").
- ``pikepdf.models.PdfParsingError`` — raised by ``unparse_content_stream``
  on malformed operand/operator items, and by some parse failures. It is
  **NOT** a subclass of ``pikepdf.PdfError`` (it derives straight from
  ``Exception``), so an ``except pikepdf.PdfError`` would miss it entirely.
- ``TypeError`` — ``parse_content_stream``'s guard for non-stream / non-page
  inputs and the "supposed to be a stream or an array" remap.

Before A1.1, only ``_pathutil.open_pdf`` translated *open*-time pikepdf
errors. The parse/unparse call sites in ``surgeon`` / ``structural`` /
``reflow`` had no translator, and ``locator._build_index`` caught a narrower
set ``(UnicodeDecodeError, ValueError, TypeError, KeyError)`` that excluded
both pikepdf parse types — so a ``PdfParsingError`` or ``PdfError`` from
parse/unparse escaped raw to public callers (INV-L-1 family).

The root fix is ``_pathutil._with_content_stream_translation`` (plus the
``_parse_content_stream`` / ``_unparse_content_stream`` wrappers), the
parse/unparse analogue of ``open_pdf``'s open-time translation. Every
parse/unparse call site now routes through it.

These probes drive the real pikepdf exception types through public paths via
monkeypatch (the same rigor as INV-C-7's hand-wired translator probes), plus
a hand-wired translator unit check and a best-effort real-malformed-fixture
probe. INV-B-5 minted as the next collision-free B-layer slot (INV-B-{1..4}
taken).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf
import pytest
from pikepdf.models import PdfParsingError
from reportlab.pdfgen import canvas as rl_canvas

import pdf_edit_engine as engine
from pdf_edit_engine._pathutil import (
    _parse_content_stream,
    _unparse_content_stream,
    _with_content_stream_translation,
)
from pdf_edit_engine.errors import OperatorError, PDFEditError

if TYPE_CHECKING:
    from pathlib import Path


def _make_simple_pdf(path: Path) -> None:
    """Build a trivial reportlab PDF with one editable text run."""
    c = rl_canvas.Canvas(str(path))
    c.setFont("Helvetica", 12)
    c.drawString(100, 700, "Hello there")
    c.save()


# ──────────────────────────────────────────────────────────────────────────
# Public-path probes: real pikepdf exception types must surface as
# OperatorError, not leak raw. Monkeypatch injects the exception at the
# parse boundary so the test is deterministic across pikepdf versions
# (whose tolerant parser otherwise rarely raises on disk-loaded streams).
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raise_exc",
    [
        pytest.param(lambda: pikepdf.PdfError("simulated parse failure"), id="PdfError"),
        pytest.param(lambda: PdfParsingError("simulated parse failure"), id="PdfParsingError"),
        pytest.param(lambda: TypeError("simulated non-stream object"), id="TypeError"),
    ],
)
def test_inv_b_5_parse_failure_surfaces_operator_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raise_exc: object,
) -> None:
    """A pikepdf parse failure on a public path → OperatorError, not raw pikepdf.

    Drives ``replace_all`` (a public surgeon entry that parses the content
    stream) with the pikepdf parser monkeypatched to raise each real
    failure type. The pre-A1.1 code leaked ``PdfParsingError`` raw here.
    """
    src = tmp_path / "src.pdf"
    _make_simple_pdf(src)
    out = tmp_path / "out.pdf"

    def boom(_target: object, *_a: object, **_k: object) -> object:
        raise raise_exc()  # type: ignore[operator,no-any-return]

    monkeypatch.setattr(pikepdf, "parse_content_stream", boom)

    raised: BaseException | None = None
    try:
        engine.replace_all(str(src), "Hello", "Howdy", str(out))
    except BaseException as exc:  # noqa: BLE001
        raised = exc

    assert raised is not None, "expected the injected parse failure to surface"
    assert isinstance(raised, OperatorError), (
        f"parse failure leaked non-OperatorError: {type(raised).__module__}.{type(raised).__name__}"
    )


def test_inv_b_5_unparse_failure_surfaces_operator_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pikepdf unparse failure on a public path → OperatorError, not raw pikepdf.

    ``unparse_content_stream`` raises ``PdfParsingError`` (not a ``PdfError``
    subclass) on malformed ops. Monkeypatching it to raise lets us assert
    the serialize-side boundary is translated too.
    """
    src = tmp_path / "src.pdf"
    _make_simple_pdf(src)
    out = tmp_path / "out.pdf"

    def boom(_ops: object, *_a: object, **_k: object) -> object:
        raise PdfParsingError("simulated unparse failure")

    monkeypatch.setattr(pikepdf, "unparse_content_stream", boom)

    matches = engine.find(str(src), "Hello")
    assert matches, "expected to find 'Hello' in the reportlab fixture"

    raised: BaseException | None = None
    try:
        engine.replace(str(src), matches[0], "Howdy", str(out))
    except BaseException as exc:  # noqa: BLE001
        raised = exc

    assert raised is not None, "expected the injected unparse failure to surface"
    assert isinstance(raised, OperatorError), (
        f"unparse failure leaked non-OperatorError: "
        f"{type(raised).__module__}.{type(raised).__name__}"
    )


# ──────────────────────────────────────────────────────────────────────────
# Hand-wired translator unit probes (mirror INV-C-7's style): the manager
# translates each real exception type and preserves the cause chain, and
# does NOT mask programmer errors.
# ──────────────────────────────────────────────────────────────────────────


def test_inv_b_5_translator_present_in_pathutil() -> None:
    """The translator and both wrappers are importable from _pathutil."""
    assert callable(_with_content_stream_translation)
    assert callable(_parse_content_stream)
    assert callable(_unparse_content_stream)


@pytest.mark.parametrize(
    "exc_factory",
    [
        pytest.param(lambda: pikepdf.PdfError("boom"), id="PdfError"),
        pytest.param(lambda: PdfParsingError("boom"), id="PdfParsingError"),
        pytest.param(lambda: TypeError("boom"), id="TypeError"),
    ],
)
def test_inv_b_5_translator_translates_and_preserves_cause(exc_factory: object) -> None:
    """Each parse/unparse exception type → OperatorError with __cause__ set."""
    original = exc_factory()  # type: ignore[operator]
    with pytest.raises(OperatorError) as ei, _with_content_stream_translation("unit_test"):
        raise original
    # Type-name-only message — no attacker-controlled bytes (INV-W0-9).
    assert type(original).__name__ in str(ei.value)
    assert ei.value.__cause__ is original


def test_inv_b_5_translator_message_has_no_exc_bytes() -> None:
    """INV-W0-9: the user-visible message carries the type name, not str(exc)."""
    secret = "ATTACKER_CONTROLLED_BYTES_abcdef"
    with pytest.raises(OperatorError) as ei, _with_content_stream_translation("unit_test"):
        raise PdfParsingError(secret)
    assert secret not in str(ei.value)
    assert "PdfParsingError" in str(ei.value)


def test_inv_b_5_translator_does_not_mask_programmer_errors() -> None:
    """KeyError/IndexError/AttributeError/ValueError must propagate unchanged.

    Programmer typos must NOT silently rebrand to OperatorError — the
    catch list is narrowed to the documented parse/unparse failure types.
    Note: ``TypeError`` IS in the catch list (parse_content_stream raises it
    on non-stream inputs), so it is intentionally excluded here.
    """
    for exc_cls in (KeyError, IndexError, AttributeError, ValueError):
        with pytest.raises(exc_cls), _with_content_stream_translation("unit_test"):
            raise exc_cls("programmer typo")


# ──────────────────────────────────────────────────────────────────────────
# Best-effort real-fixture probe: a genuinely malformed content stream
# through a public path must never leak raw pikepdf. pikepdf's parser is
# tolerant, so success is also acceptable — the invariant is exception
# TYPE, not outcome (same tolerance as INV-C-7 / INV-M-3).
# ──────────────────────────────────────────────────────────────────────────


def test_inv_b_5_malformed_stream_real_fixture_no_raw_leak(tmp_path: Path) -> None:
    """A page whose /Contents array carries a non-stream item must not leak raw pikepdf."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    page = pdf.pages[0]
    good = pdf.make_stream(b"BT /F1 12 Tf (hi) Tj ET")
    # Poison the Contents array with a non-stream object — the documented
    # trigger for parse_content_stream's "non-stream in an array" PdfError.
    page.Contents = pikepdf.Array([good, pikepdf.String("bogus-non-stream")])
    bad = tmp_path / "poison.pdf"
    pdf.save(str(bad))
    pdf.close()

    out = tmp_path / "out.pdf"
    for label, op in [
        ("get_text", lambda: engine.get_text(str(bad))),
        ("find", lambda: engine.find(str(bad), "hi")),
        ("replace_all", lambda: engine.replace_all(str(bad), "hi", "yo", str(out))),
    ]:
        try:
            op()
        except PDFEditError:
            continue  # acceptable — translated
        except (FileNotFoundError, OSError):
            continue  # well-known stdlib, allowed by contract
        except BaseException as exc:  # noqa: BLE001
            pytest.fail(
                f"INV-B-5 violation [{label}]: raw "
                f"{type(exc).__module__}.{type(exc).__name__} escaped a public path"
            )
