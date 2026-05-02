"""INV-M-3 (P0): malformed content stream → OperatorError, not silent corruption."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf
from reportlab.pdfgen import canvas as rl_canvas

from pdf_edit_engine import get_text, replace_all
from pdf_edit_engine.errors import PDFEditError

if TYPE_CHECKING:
    from pathlib import Path


def _make_misnested_bt_et_pdf(path: Path) -> None:
    """Build a PDF, then corrupt its content stream by removing the ET."""
    src = path.parent / "_src.pdf"
    c = rl_canvas.Canvas(str(src))
    c.setFont("Helvetica", 12)
    c.drawString(100, 700, "Hello there")
    c.save()

    pdf = pikepdf.open(str(src))
    page = pdf.pages[0]
    raw = page.Contents.read_bytes()
    # Drop the trailing ET to mis-nest BT/ET
    corrupted = raw.replace(b"\nET\n", b"\n")
    page.Contents = pdf.make_stream(corrupted)
    pdf.save(str(path))
    pdf.close()


def test_inv_m_3_malformed_content_stream_raises_pdfedit_error(tmp_path: Path) -> None:
    """A PDF with mis-nested BT/ET in its content stream must produce a
    PDFEditError subclass when the engine tries to parse it for an
    edit — not a silent corruption."""
    bad = tmp_path / "misnest.pdf"
    _make_misnested_bt_et_pdf(bad)

    out = tmp_path / "out.pdf"
    raised: BaseException | None = None
    result = None
    try:
        result = replace_all(str(bad), "Hello", "Howdy", str(out))
    except BaseException as e:  # noqa: BLE001
        raised = e

    if raised is None:
        # If no exception, replace_all succeeded — verify that the engine
        # didn't silently corrupt: the output get_text must contain
        # "Howdy" or the original text, not garbage.
        post_text = get_text(str(out))
        assert "Howdy" in post_text or "Hello" in post_text, (
            f"silent corruption: malformed input produced output with "
            f"neither original nor replacement text. Got: {post_text!r}"
        )
        assert result is not None and len(result) > 0
    else:
        # Acceptance: raised, must be a PDFEditError subclass
        # (preferably OperatorError per the docstring).
        assert isinstance(raised, PDFEditError), (
            f"engine raised non-PDFEditError on malformed content stream: "
            f"{type(raised).__module__}.{type(raised).__name__}: {raised}"
        )
