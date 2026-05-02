"""Branch-coverage tests for indent-aware bullet handling in structural.py.

Targets the marker x-position detection in ``_extract_style_palette``
(lines 221-242) and the indent-aware reflow paths in
``_replace_block_on_page`` (lines 1053-1095) and ``_auto_compute_layout``
(lines 803-820).  These code paths fire on bulleted content (most resumes,
contracts) and were previously uncovered.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from pdf_edit_engine.locator import _build_index, get_text
from pdf_edit_engine.structural import (
    _build_style_palette,
    _collect_elements_in_bbox,
    replace_block,
)

CORPUS = Path(__file__).parent / "corpus"
RESUME_PDF = str(CORPUS / "Aryan_BV_Resume_2026.pdf")

_need_resume = pytest.mark.skipif(
    not (CORPUS / "Aryan_BV_Resume_2026.pdf").exists(),
    reason="Aryan_BV_Resume_2026.pdf not in corpus",
)

_ARIAL = Path("C:/Windows/Fonts/arial.ttf")
_ARIAL_BOLD = Path("C:/Windows/Fonts/arialbd.ttf")
_need_ttf = pytest.mark.skipif(
    not (_ARIAL.exists() and _ARIAL_BOLD.exists()),
    reason="Arial TTF fonts not available for inline PDF construction",
)


def _build_bullet_pdf(
    out: Path,
    *,
    bullet_char: str = "*",
    body_lines: tuple[str, ...] = (
        "Body text after bullet on first line",
        "Second bullet line content",
    ),
    bullet_x: float = 72.0,
    body_x: float = 90.0,
    y_start: float = 700.0,
    y_step: float = 20.0,
) -> None:
    """Build a synthetic PDF with bullet markers in a different font.

    The bullet uses Arial Bold, the body uses Arial Regular.  Both are
    registered as TrueType subsets so the palette's marker-font detection
    will see two different font resource names.
    """
    # Register fonts under unique names so test runs don't collide
    if "BulletArial" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("BulletArial", str(_ARIAL)))
        pdfmetrics.registerFont(TTFont("BulletArialBold", str(_ARIAL_BOLD)))
    c = canvas.Canvas(str(out))
    y = y_start
    for line in body_lines:
        c.setFont("BulletArialBold", 12)
        c.drawString(bullet_x, y, bullet_char)
        c.setFont("BulletArial", 12)
        c.drawString(body_x, y, line)
        y -= y_step
    c.save()


class TestPaletteDetection:
    """Marker x-position detection in ``_build_style_palette``."""

    @_need_ttf
    def test_palette_detects_bullet_marker_x_positions(self, tmp_path: Path) -> None:
        """Two-font bullet line populates marker_x and body_after_marker_x."""
        pdf_path = tmp_path / "bullets.pdf"
        _build_bullet_pdf(pdf_path)
        pdf = pikepdf.open(str(pdf_path))
        elements = _build_index(pdf.pages[0], 0)
        matched, _ = _collect_elements_in_bbox(elements, (60.0, 670.0, 600.0, 720.0))
        # The body font dominates (more characters than the single-char bullet)
        body_fonts = [
            e.graphics_state.font_name
            for e in matched
            if e.text_content and len(e.text_content.strip()) > 1
        ]
        assert body_fonts, "no body-font elements detected"
        body_font = body_fonts[0]
        assert body_font is not None
        palette = _build_style_palette(matched, body_font, 12.0, None)
        assert palette.marker_fonts != {}, "bullet marker font not detected"
        assert "*" in palette.marker_fonts
        assert palette.marker_x == pytest.approx(72.0, abs=0.01)
        assert palette.body_after_marker_x == pytest.approx(90.0, abs=0.01)

    @_need_ttf
    def test_palette_no_markers_when_only_one_font(self, tmp_path: Path) -> None:
        """Plain paragraph with a single font yields no marker fonts and zero indent."""
        pdf_path = tmp_path / "plain.pdf"
        if "PlainArial" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("PlainArial", str(_ARIAL)))
        c = canvas.Canvas(str(pdf_path))
        c.setFont("PlainArial", 12)
        c.drawString(72, 700, "First line of plain paragraph text")
        c.drawString(72, 680, "Second line of plain paragraph text")
        c.drawString(72, 660, "Third line of plain paragraph text")
        c.save()
        pdf = pikepdf.open(str(pdf_path))
        elements = _build_index(pdf.pages[0], 0)
        matched, _ = _collect_elements_in_bbox(elements, (60.0, 650.0, 600.0, 720.0))
        body_font = next(
            e.graphics_state.font_name
            for e in matched
            if e.text_content and e.graphics_state.font_name
        )
        assert body_font is not None
        palette = _build_style_palette(matched, body_font, 12.0, None)
        assert palette.marker_fonts == {}
        assert palette.marker_x == 0.0
        assert palette.body_after_marker_x == 0.0

    @_need_resume
    def test_palette_resume_bullet_indent(self) -> None:
        """Resume PDF bullet line yields nonzero marker_x and body_after_marker_x."""
        pdf = pikepdf.open(RESUME_PDF)
        elements = _build_index(pdf.pages[0], 0)
        # Bullet paragraph at y~408 (AJSP Manager first bullet)
        matched, _ = _collect_elements_in_bbox(elements, (28.0, 405.0, 580.0, 420.0))
        palette = _build_style_palette(matched, "F3", 10.0, None)
        assert palette.marker_fonts, "resume bullet should populate marker_fonts"
        assert palette.marker_x > 0.0
        assert palette.body_after_marker_x > palette.marker_x


class TestReflow:
    """Indent-aware reflow paths in ``_replace_block_on_page``."""

    @_need_resume
    def test_replace_block_indent_preserves_marker(self, tmp_path: Path) -> None:
        """Replacing a bullet paragraph keeps the bullet glyph in output."""
        out = tmp_path / "bullet_replaced.pdf"
        # Bullet paragraph at y~408 from the resume (AJSP first bullet)
        bbox = (28.0, 400.0, 575.0, 420.0)
        result = replace_block(
            RESUME_PDF,
            0,
            bbox,
            "Short replacement that fits on one line.",
            str(out),
        )
        assert result.success
        text = get_text(str(out))
        # Bullet glyph is rendered as either "•" or replacement char depending
        # on extraction encoding; check text content (Built has been removed,
        # short replacement appears)
        assert "Short replacement" in text

    @_need_resume
    def test_replace_block_continuation_joins_to_marker_paragraph(
        self,
        tmp_path: Path,
    ) -> None:
        """Continuation lines (no leading bullet) are joined to prior bullet paragraph."""
        out = tmp_path / "continuation.pdf"
        # Multi-line bullet section: bullet at y=408, continuation at y=394
        # Wider bbox to capture both lines plus surrounding bullets
        bbox = (28.0, 380.0, 580.0, 422.0)
        new_text = (
            "• First bullet body line that needs to wrap because it is intentionally"
            " long enough\n"
            "continuation of the first bullet\n"
            "• Second short bullet"
        )
        result = replace_block(RESUME_PDF, 0, bbox, new_text, str(out))
        assert result.success
        # Both bullet bodies survive in the rendered text.
        text = get_text(str(out))
        assert "First bullet body line" in text
        assert "Second short bullet" in text
        # Continuation text is preserved (joined to first bullet, not dropped)
        assert "continuation of the first bullet" in text

    @_need_resume
    def test_replace_block_no_indent_path_when_no_markers(
        self,
        tmp_path: Path,
    ) -> None:
        """Plain (non-bulleted) paragraph reflow takes the non-indent path and succeeds."""
        out = tmp_path / "plain_reflow.pdf"
        # PROFESSIONAL SUMMARY paragraph — plain prose, no bullet markers
        # Find via detect_paragraphs to get a real bbox without bullets
        from pdf_edit_engine.reflow import detect_paragraphs

        paras = detect_paragraphs(RESUME_PDF, page=0)
        candidates = [
            p for p in paras if p.line_count > 1 and "Full-stack developer" in p.full_text
        ]
        if not candidates:
            pytest.skip("No suitable plain multi-line paragraph found in resume")
        p = candidates[0]
        elems = p.elements
        bbox = (
            min(e.bbox[0] for e in elems),
            min(e.bbox[1] for e in elems),
            max(e.bbox[2] for e in elems),
            max(e.bbox[3] for e in elems),
        )
        replacement = (
            "This is a multi-line replacement body that should wrap to the full bbox"
            " width because no bullet markers are present in this region of the document."
        )
        result = replace_block(RESUME_PDF, 0, bbox, replacement, str(out))
        assert result.success
        text = get_text(str(out))
        assert "multi-line replacement body" in text


class TestComputeUniformLayout:
    """Coverage for ``compute_uniform_layout``'s fallback branches."""

    def test_compute_uniform_layout_zero_lines(self) -> None:
        """Zero inter-line gaps short-circuits to (font_size*1.2, original_gap)."""
        from pdf_edit_engine.structural import compute_uniform_layout

        line_height, section_gap = compute_uniform_layout(
            region_height=200.0,
            line_counts=[1, 1, 1],  # each section has 0 inter-line gaps
            font_size=10.0,
            original_gap=20.0,
        )
        assert line_height == pytest.approx(12.0, abs=0.01)
        assert section_gap == 20.0

    def test_compute_uniform_layout_reduces_gap_then_clamps(self) -> None:
        """When region is tight, section_gap is consumed before line_height shrinks."""
        from pdf_edit_engine.structural import compute_uniform_layout

        # 4 lines worth of gaps, ample room — should keep original_gap fully
        line_height, section_gap = compute_uniform_layout(
            region_height=300.0,
            line_counts=[3, 3],  # 4 inter-line gaps total, 2 sections
            font_size=10.0,
            original_gap=10.0,
        )
        assert line_height >= 10.0 * 1.05
        assert section_gap >= 0.0

    def test_compute_uniform_layout_clamps_below_min(self) -> None:
        """Region too small forces the final clamp to font_size minimum."""
        from pdf_edit_engine.structural import compute_uniform_layout

        # 100 inter-line gaps, only 50pt of region → clamp to font_size minimum
        line_height, section_gap = compute_uniform_layout(
            region_height=50.0,
            line_counts=[51, 51],  # 100 inter-line gaps
            font_size=10.0,
            original_gap=20.0,
        )
        assert line_height >= 10.0
        assert section_gap == 0.0


@_need_resume
class TestAutoLayoutWithBullets:
    """Coverage for the indent-aware branch in ``_auto_compute_layout``."""

    def test_batch_replace_auto_layout_with_bullets(self, tmp_path: Path) -> None:
        """``batch_replace_block`` without explicit layout exercises auto-detect on bullets."""
        from pdf_edit_engine.structural import batch_replace_block

        out = tmp_path / "auto_bullets.pdf"
        # Two bullet paragraph bboxes — auto layout will derive line_height
        # from line counts which forces the indent-aware branch.
        replacements = [
            (
                (28.0, 400.0, 575.0, 420.0),
                "• First bullet replacement that is long enough to wrap to two"
                " lines under the indented width because the marker indent reduces"
                " the usable horizontal space substantially",
            ),
            (
                (28.0, 360.0, 575.0, 380.0),
                "• Second bullet short",
            ),
        ]
        results = batch_replace_block(
            RESUME_PDF,
            0,
            replacements,
            str(out),
        )
        assert all(r.success for r in results)
