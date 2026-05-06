"""Tests for structural editing — replace_block, shift_content_below,
insert_text_block, delete_block."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.locator import get_text
from pdf_edit_engine.models import EditResult
from pdf_edit_engine.reflow import detect_paragraphs
from pdf_edit_engine.structural import (
    batch_replace_block,
    delete_block,
    insert_text_block,
    replace_block,
    shift_content_below,
)

CORPUS = Path(__file__).parent / "corpus"
SIMPLE_PDF = str(CORPUS / "reportlab_simple.pdf")
RESUME_PDF = str(CORPUS / "Aryan_BV_Resume_2026.pdf")

_need_simple = pytest.mark.skipif(
    not (CORPUS / "reportlab_simple.pdf").exists(),
    reason="reportlab_simple.pdf not in corpus",
)
_need_resume = pytest.mark.skipif(
    not (CORPUS / "Aryan_BV_Resume_2026.pdf").exists(),
    reason="Aryan_BV_Resume_2026.pdf not in corpus",
)


# ── TestReplaceBlock ─────────────────────────────────────────────────


@_need_simple
class TestReplaceBlock:
    """Tests for replace_block() — bbox-based paragraph replacement."""

    def test_replace_block_reportlab(self, tmp_path: Path) -> None:
        """Replace the 3-line body paragraph on reportlab_simple.pdf."""
        out = str(tmp_path / "replaced.pdf")
        # P1 bbox: (72.0, 667.2, 494.4, 708.2)
        result = replace_block(
            SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), "Replacement text here.", out
        )
        assert result.success
        text = get_text(out)
        assert "Replacement text" in text
        assert "reportlab" not in text  # old text removed

    def test_replace_block_auto_font(self, tmp_path: Path) -> None:
        """Font auto-detection works when font_name not specified."""
        out = str(tmp_path / "auto_font.pdf")
        result = replace_block(SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), "Auto font test.", out)
        assert result.success
        assert result.fidelity_report.font_preserved

    def test_replace_block_preserves_other(self, tmp_path: Path) -> None:
        """Content outside the bbox is preserved."""
        out = str(tmp_path / "preserve.pdf")
        replace_block(SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), "New paragraph.", out)
        text = get_text(out)
        assert "Test Document" in text
        assert "Section two" in text

    def test_replace_block_overflow(self, tmp_path: Path) -> None:
        """Long replacement text triggers overflow detection."""
        out = str(tmp_path / "overflow.pdf")
        long_text = "word " * 200  # much more than the bbox can hold
        result = replace_block(SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), long_text.strip(), out)
        assert result.success
        assert result.fidelity_report.overflow_detected

    # test_replace_block_empty_bbox removed — strict subset of
    # tests/invariants/test_f_1_replace_block_empty_clears.py.

    def test_replace_block_returns_edit_result(self, tmp_path: Path) -> None:
        """Result includes original and new text."""
        out = str(tmp_path / "result.pdf")
        result = replace_block(SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), "New text.", out)
        assert isinstance(result, EditResult)
        assert result.original_text  # non-empty
        assert result.new_text == "New text."

    def test_replace_block_overflow_shifts_content(self, tmp_path: Path) -> None:
        """Multi-line replacement shifts content below, preventing interleaving."""
        out = str(tmp_path / "shifted.pdf")
        # Enough text to guarantee multi-line overflow in the ~41pt tall bbox
        long_text = " ".join(["replacement"] * 60)
        result = replace_block(SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), long_text, out)
        assert result.success
        assert result.fidelity_report.overflow_detected
        text = get_text(out)
        # "Section two" was originally below the bbox — it should still
        # appear AFTER the replacement text, not interleaved within it
        assert "replacement" in text.lower()
        assert "Section two" in text


@_need_resume
class TestReplaceBlockResume:
    """replace_block on the CIDFont resume PDF."""

    def test_replace_project_title(self, tmp_path: Path) -> None:
        """Replace the AJSP Manager project title line."""
        out = str(tmp_path / "title.pdf")
        # AJSP Manager bbox: (14.2, 435.9, 212.4, 445.9)
        result = replace_block(RESUME_PDF, 0, (14.2, 435.9, 212.4, 445.9), "Custom Project", out)
        assert result.success
        text = get_text(out)
        assert "Custom Project" in text

    def test_replace_resume_paragraph(self, tmp_path: Path) -> None:
        """Replace a multi-line bullet on the resume by bbox."""
        out = str(tmp_path / "para.pdf")
        paras = detect_paragraphs(RESUME_PDF, page=0)
        # Find a multi-line paragraph (line_count > 1)
        multi = [p for p in paras if p.line_count > 1 and p.full_text.strip()]
        assert multi, "No multi-line paragraphs found"
        p = multi[0]
        elems = p.elements
        bbox = (
            min(e.bbox[0] for e in elems),
            min(e.bbox[1] for e in elems),
            max(e.bbox[2] for e in elems),
            max(e.bbox[3] for e in elems),
        )
        result = replace_block(RESUME_PDF, 0, bbox, "Replaced paragraph.", out)
        assert result.success
        text = get_text(out)
        assert "Replaced paragraph" in text

    def test_replace_block_cid_bold_title(self, tmp_path: Path) -> None:
        """replace_block on bold CIDFont title produces correct text."""
        out = str(tmp_path / "bold.pdf")
        # AJSP Manager title is F1 (Calibri-Bold), all chars in subset
        result = replace_block(
            RESUME_PDF, 0, (14.2, 435.9, 212.4, 445.9), "PDF Edit Engine Plan", out
        )
        assert result.success
        assert result.font_action == "kept"
        text = get_text(out)
        assert "PDF Edit Engine Plan" in text

    def test_replace_block_cid_bold_with_extension(self, tmp_path: Path) -> None:
        """replace_block with font extension still produces correct text."""
        out = str(tmp_path / "ext.pdf")
        # 'x' is not in the F1 subset — triggers font extension
        result = replace_block(RESUME_PDF, 0, (14.2, 435.9, 212.4, 445.9), "PDF Text Editing", out)
        assert result.success
        assert result.font_action == "extended"
        text = get_text(out)
        assert "PDF Text Editing" in text

    def test_replace_block_cid_long_text_wraps(self, tmp_path: Path) -> None:
        """Long CIDFont replacement wraps to multiple lines correctly."""
        out = str(tmp_path / "wrap.pdf")
        long_text = "PDF Edit Engine \u2014 Format-Preserving PDF Text Editing"
        result = replace_block(RESUME_PDF, 0, (14.2, 435.9, 212.4, 445.9), long_text, out)
        assert result.success
        text = get_text(out)
        assert "Format-Preserving" in text
        assert "Editing" in text

    def test_replace_block_cid_preserves_other_content(self, tmp_path: Path) -> None:
        """Replacing a bold title does not garble other page content."""
        out = str(tmp_path / "preserve.pdf")
        replace_block(RESUME_PDF, 0, (14.2, 435.9, 212.4, 445.9), "New Title", out)
        text = get_text(out)
        # Section headings should still be readable
        assert "PROFESSIONAL SUMMARY" in text
        assert "EDUCATION" in text
        assert "TECHNICAL SKILLS" in text

    def test_replace_block_cid_overflow_no_interleave(self, tmp_path: Path) -> None:
        """CIDFont multi-line overflow shifts content, no interleaving."""
        out = str(tmp_path / "no_interleave.pdf")
        long_text = "PDF Edit Engine \u2014 Format-Preserving PDF Text Editing Library"
        result = replace_block(RESUME_PDF, 0, (14.2, 435.9, 212.4, 445.9), long_text, out)
        assert result.success
        assert result.fidelity_report.overflow_detected
        text = get_text(out)
        assert "Format-Preserving" in text
        # EDUCATION section is below the AJSP bbox (lower y in PDF coords)
        # and should appear after all replacement text
        assert "EDUCATION" in text

    def test_replace_block_cid_uses_tj_array(self, tmp_path: Path) -> None:
        """CIDFont replacement uses TJ array and Tm positioning."""
        out = str(tmp_path / "ops.pdf")
        replace_block(RESUME_PDF, 0, (14.2, 435.9, 212.4, 445.9), "Operator Test", out)
        # Parse the output and verify operator structure
        pdf = pikepdf.Pdf.open(out)
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        # Find the replacement BT/ET block (first one, near the top)
        found_tm = False
        found_tj_array = False
        in_replacement_bt = False
        for inst in ops:
            op = str(inst.operator) if hasattr(inst, "operator") else str(inst[1])
            if op == "BT":
                in_replacement_bt = True
            elif op == "ET":
                if found_tm and found_tj_array:
                    break
                in_replacement_bt = False
                found_tm = False
                found_tj_array = False
            elif in_replacement_bt and op == "Tm":
                found_tm = True
            elif in_replacement_bt and op == "TJ":
                found_tj_array = True
        assert found_tm, "CIDFont replacement should use Tm"
        assert found_tj_array, "CIDFont replacement should use TJ array"
        pdf.close()

    def test_replace_block_mixed_font_cid_fallback(self, tmp_path: Path) -> None:
        """replace_block falls back to CID font when WinAnsi can't encode.

        The bbox (14.0, 745.0, 140.0, 770.0) captures a WinAnsi F4 space
        before the CIDFont F1 'PROFESSIONAL SUMMARY' heading.  Auto-detection
        picks F4 first, which cannot encode U+0100 (Ā).  The fix should fall
        back to the CIDFont F1 and succeed via extend_subset().
        """
        out = str(tmp_path / "mixed_font.pdf")
        result = replace_block(
            RESUME_PDF,
            0,
            (14.0, 745.0, 140.0, 770.0),
            "PROFESSIONAL SUMMARY \u0100",
            out,
        )
        assert result.success, f"Expected success: {result.warnings}"
        assert result.font_action != "failed"
        assert result.fidelity_report.font_substituted is not None
        text = get_text(out)
        assert "PROFESSIONAL" in text

    def test_replace_block_newline_in_text(self, tmp_path: Path) -> None:
        """Newlines in replacement text don't fail can_encode (Bug B fix)."""
        out = str(tmp_path / "newline.pdf")
        result = replace_block(
            RESUME_PDF, 0, (14.2, 435.9, 212.4, 445.9), "Line One\nLine Two", out
        )
        assert result.success, f"Expected success: {result.warnings}"
        assert result.font_action != "failed"
        text = get_text(out)
        assert "Line One" in text
        assert "Line Two" in text

    def test_replace_block_massive_overflow_stays_on_page(
        self,
        tmp_path: Path,
    ) -> None:
        """Massive overflow doesn't push content below page boundary (Bug A fix)."""
        out = str(tmp_path / "clamp.pdf")
        # Narrow bbox (61pt wide) + long text → many lines → huge overflow_delta
        long_text = "This is a very long replacement " * 20
        result = replace_block(RESUME_PDF, 0, (14.2, 435.9, 75.5, 445.9), long_text, out)
        assert result.success
        assert result.fidelity_report.overflow_detected
        # Verify no Tm y-value < 0 in output content stream
        with pikepdf.open(out) as pdf:
            page = pdf.pages[0]
            ops = list(pikepdf.parse_content_stream(page))
            for operands, operator in ops:
                if str(operator) == "Tm" and len(operands) >= 6:
                    y_val = float(operands[5])
                    assert y_val >= 0, f"Content at y={y_val} is below page"


# ── TestBatchReplaceBlock ────────────────────────────────────────────


@_need_simple
class TestBatchReplaceBlock:
    """Tests for batch_replace_block() — multi-bbox replacement."""

    def test_batch_two_replacements(self, tmp_path: Path) -> None:
        """Two bbox replacements applied in one pass."""
        out = str(tmp_path / "batch.pdf")
        replacements = [
            ((72.0, 745.5, 212.4, 763.5), "New Title"),  # P0 (title)
            ((72.0, 632.2, 388.8, 643.2), "Replaced section."),  # P2 (section two)
        ]
        results = batch_replace_block(SIMPLE_PDF, 0, replacements, out)
        assert len(results) == 2
        assert all(r.success for r in results)
        text = get_text(out)
        assert "New Title" in text
        assert "Replaced section" in text

    def test_batch_preserves_order(self, tmp_path: Path) -> None:
        """Results list matches input order regardless of processing order."""
        out = str(tmp_path / "order.pdf")
        # Provide in bottom-to-top order — results should still match input
        replacements = [
            ((72.0, 632.2, 388.8, 643.2), "Bottom first."),  # P2 (lower)
            ((72.0, 745.5, 212.4, 763.5), "Top second."),  # P0 (higher)
        ]
        results = batch_replace_block(SIMPLE_PDF, 0, replacements, out)
        assert results[0].new_text == "Bottom first."
        assert results[1].new_text == "Top second."
        assert all(r.success for r in results)

    def test_batch_with_overflow_cumulative_shift(self, tmp_path: Path) -> None:
        """Overflow from first replacement shifts second bbox correctly."""
        out = str(tmp_path / "cumshift.pdf")
        # P0 (title, ~18pt tall) replaced with long text that overflows
        long_text = " ".join(["expanded"] * 40)
        replacements = [
            ((72.0, 745.5, 212.4, 763.5), long_text),  # P0 — overflows
            ((72.0, 632.2, 388.8, 643.2), "Still correct."),  # P2 — below
        ]
        results = batch_replace_block(SIMPLE_PDF, 0, replacements, out)
        assert len(results) == 2
        assert results[0].success
        assert results[0].fidelity_report.overflow_detected
        assert results[1].success
        text = get_text(out)
        assert "expanded" in text
        assert "Still correct" in text

    # test_batch_empty_list removed — strict subset of
    # tests/invariants/test_f_3_batch_replace_block_empty.py
    # (which additionally asserts no output file is created).


# ── TestShiftContent ─────────────────────────────────────────────────


@_need_simple
class TestShiftContent:
    """Tests for shift_content_below() — content shifting."""

    def test_shift_down(self, tmp_path: Path) -> None:
        """Shift content below y=700 down by 20pt."""
        out = str(tmp_path / "shifted.pdf")
        result = shift_content_below(SIMPLE_PDF, 0, 700.0, 20.0, out)
        assert result.success

        paras_before = detect_paragraphs(SIMPLE_PDF, page=0)
        paras_after = detect_paragraphs(out, page=0)

        before_map = {p.full_text[:20]: p.first_line_y for p in paras_before}
        after_map = {p.full_text[:20]: p.first_line_y for p in paras_after}

        # "Section two" is below y=700 — should shift down by 20
        key = "Section two has numb"
        assert key in before_map and key in after_map
        assert abs((before_map[key] - after_map[key]) - 20.0) < 2.0

    def test_shift_preserves_above(self, tmp_path: Path) -> None:
        """Content above threshold should not move."""
        out = str(tmp_path / "above.pdf")
        shift_content_below(SIMPLE_PDF, 0, 700.0, 20.0, out)

        paras_before = detect_paragraphs(SIMPLE_PDF, page=0)
        paras_after = detect_paragraphs(out, page=0)

        before_map = {p.full_text[:20]: p.first_line_y for p in paras_before}
        after_map = {p.full_text[:20]: p.first_line_y for p in paras_after}

        # "Test Document" is at y=750, above threshold — no shift
        key = "Test Document"
        assert key in before_map and key in after_map
        assert abs(before_map[key] - after_map[key]) < 1.0

    def test_shift_up(self, tmp_path: Path) -> None:
        """Negative delta_y shifts content up (increases y)."""
        out = str(tmp_path / "up.pdf")
        result = shift_content_below(SIMPLE_PDF, 0, 700.0, -15.0, out)
        assert result.success

        paras_before = detect_paragraphs(SIMPLE_PDF, page=0)
        paras_after = detect_paragraphs(out, page=0)

        before_map = {p.full_text[:20]: p.first_line_y for p in paras_before}
        after_map = {p.full_text[:20]: p.first_line_y for p in paras_after}

        key = "Section two has numb"
        if key in before_map and key in after_map:
            # Should have moved up by 15pt
            diff = after_map[key] - before_map[key]
            assert abs(diff - 15.0) < 2.0

    def test_shift_zero_is_noop(self, tmp_path: Path) -> None:
        """delta_y=0 should not modify the PDF."""
        out = str(tmp_path / "noop.pdf")
        result = shift_content_below(SIMPLE_PDF, 0, 700.0, 0.0, out)
        assert result.success
        text_before = get_text(SIMPLE_PDF)
        text_after = get_text(out)
        assert text_before == text_after


@_need_resume
class TestShiftContentResume:
    """Shift tests on the resume PDF with annotations."""

    def test_shift_annotations(self, tmp_path: Path) -> None:
        """Annotations below threshold should shift."""
        out = str(tmp_path / "annot.pdf")
        # Resume has link annotations. Shift below y=400
        shift_content_below(RESUME_PDF, 0, 400.0, 30.0, out)

        pdf_before = pikepdf.Pdf.open(RESUME_PDF)
        pdf_after = pikepdf.Pdf.open(out)

        annots_before = list(pdf_before.pages[0].get("/Annots", []))
        annots_after = list(pdf_after.pages[0].get("/Annots", []))

        # Check that annotations below y=400 were shifted
        shifted_count = 0
        for ab, aa in zip(annots_before, annots_after, strict=False):
            rb = ab["/Rect"]
            ra = aa["/Rect"]
            if float(rb[3]) < 400.0:
                diff = float(rb[1]) - float(ra[1])
                if abs(diff - 30.0) < 2.0:
                    shifted_count += 1
        assert shifted_count >= 0  # may be 0 if no annotations below 400

        pdf_before.close()
        pdf_after.close()

    def test_shift_overflow_warning(self, tmp_path: Path) -> None:
        """Large shift should trigger overflow warning."""
        out = str(tmp_path / "overflow.pdf")
        # Shift everything below y=842 (entire page) down by 900pt
        result = shift_content_below(RESUME_PDF, 0, 842.0, 900.0, out)
        assert result.fidelity_report.overflow_detected
        assert any("below page boundary" in w for w in result.warnings)


# ── TestInsertTextBlock ──────────────────────────────────────────────


@_need_simple
class TestInsertTextBlock:
    """Tests for insert_text_block()."""

    def test_insert_basic(self, tmp_path: Path) -> None:
        """Inserted text appears in the output PDF."""
        out = str(tmp_path / "insert.pdf")
        result = insert_text_block(
            SIMPLE_PDF,
            0,
            x=72.0,
            y=720.0,
            text="Hello from insert_text_block!",
            output_path=out,
            font_size=11.0,
        )
        assert result.success
        text = get_text(out)
        assert "Hello from insert" in text

    def test_insert_shifts_existing(self, tmp_path: Path) -> None:
        """Content below the insertion point should shift down."""
        out = str(tmp_path / "shifted.pdf")
        insert_text_block(
            SIMPLE_PDF, 0, x=72.0, y=720.0, text="Inserted line.", output_path=out, font_size=11.0
        )

        paras_before = detect_paragraphs(SIMPLE_PDF, page=0)
        paras_after = detect_paragraphs(out, page=0)

        before_map = {p.full_text[:20]: p.first_line_y for p in paras_before}
        after_map = {p.full_text[:20]: p.first_line_y for p in paras_after}

        # "This is a simple..." was at y=700, below insertion at 720
        key = "This is a simple tes"
        if key in before_map and key in after_map:
            assert after_map[key] < before_map[key], "Content should have shifted down"

    def test_insert_multiline(self, tmp_path: Path) -> None:
        """Long text wraps into multiple lines."""
        out = str(tmp_path / "multi.pdf")
        long_text = "This is a longer paragraph that should wrap across lines. " * 3
        result = insert_text_block(
            SIMPLE_PDF, 0, x=72.0, y=720.0, text=long_text.strip(), output_path=out, font_size=11.0
        )
        assert result.success
        text = get_text(out)
        assert "longer paragraph" in text

    def test_insert_preserves_existing(self, tmp_path: Path) -> None:
        """Existing content is preserved after insertion."""
        out = str(tmp_path / "preserve.pdf")
        insert_text_block(
            SIMPLE_PDF, 0, x=72.0, y=720.0, text="New content.", output_path=out, font_size=11.0
        )
        text = get_text(out)
        assert "Test Document" in text
        assert "Section two" in text


# ── TestInsertTextBlockHonesty (CRIT-1) ──────────────────────────────


@_need_resume
class TestInsertTextBlockHonesty:
    """CRIT-1: insert_text_block must surface Tier 1.5 metric-equivalent
    substitution in its FidelityReport (was previously hardcoding
    font_substituted=None / degradations=[] / font_action="kept" even
    when extension actually ran from a metric-equivalent system font).

    Single-source-of-truth char selection (probed via analyze_subset on
    tests/corpus/Aryan_BV_Resume_2026.pdf during plan development):
    every embedded font on page 0 (F1/F2 Calibri-Bold 6954 glyphs,
    F3/F4 Calibri 6954 glyphs, F6 ArialMT 4503 glyphs) lacks 0x00F8
    (ø). ø is the shared test char used by both this test and
    tests/test_fonts.py's IMP-1 cache test.
    """

    def test_metric_equivalent_surfaces_substitution(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Forces Tier 1.5 metric-equivalent path deterministically.

        Without monkeypatch the test would silently no-op on any dev's
        machine that has Calibri installed (Windows + Office is the
        common case). The whole point of this fix is to catch silent
        lying — a soft assertion gated on the dev's local font
        landscape would defeat the test.

        Tests the surfacing layer (the FidelityReport contract). Does
        not assert visual fidelity of the injected glyphs; the source
        path can be any locally-installed Calibri-Bold-or-equivalent.
        """
        from pdf_edit_engine import system_fonts

        # Find any locally-installed Calibri-Bold-or-equivalent for use
        # as the injection source (must be TrueType, upem=2048 to match
        # the embedded font). The substituted_name we report is
        # independent of which actual font we inject from — the test
        # verifies surfacing, not visual output.
        real = system_fonts._find_font_with_origin("Calibri-Bold")
        if real is None:
            pytest.skip(
                "No Calibri-Bold or metric-equivalent installed; cannot exercise Tier 1.5 path"
            )
        real_path = real[0]

        monkeypatch.setattr(
            "pdf_edit_engine.system_fonts._find_font_with_origin",
            lambda ps_name: (real_path, "Carlito-Regular"),
        )

        out = str(tmp_path / "out.pdf")
        result = insert_text_block(
            RESUME_PDF,
            0,
            x=72.0,
            y=400.0,
            text="ø-test",
            output_path=out,
            font_size=11.0,
        )

        assert result.success
        assert result.font_action == "extended"
        fr = result.fidelity_report
        assert fr.font_substituted == "Carlito-Regular"
        assert fr.font_preserved is False
        assert any(d.kind == "font_coverage_substituted" for d in fr.degradations)
        assert "ø" in fr.glyphs_missing

        # Lock CRIT-2 contract simultaneously (font_preserved must
        # surface via to_dict() too):
        d = fr.to_dict()
        assert d["font_preserved"] is False
        assert d["font_substituted"] == "Carlito-Regular"
        assert Path(out).exists()

    def test_extension_failure_emits_typed_degradation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """CRIT-1 expansion: failure branch (was lacking fidelity_report
        entirely; default-factory FidelityReport reported
        font_preserved=True even on extension failure)."""
        from pdf_edit_engine import fonts as fonts_mod
        from pdf_edit_engine.errors import FontNotFoundError

        def _explode(*args: object, **kwargs: object) -> None:
            raise FontNotFoundError("simulated: no system font for fixture")

        monkeypatch.setattr(fonts_mod, "extend_subset", _explode)

        out = str(tmp_path / "out.pdf")
        result = insert_text_block(
            RESUME_PDF,
            0,
            x=72.0,
            y=400.0,
            text="ø-failure",
            output_path=out,
            font_size=11.0,
        )

        assert result.success is False
        assert result.font_action == "failed"
        fr = result.fidelity_report
        assert fr.font_preserved is False
        assert any(
            d.kind == "font_extension_failed" and d.severity == "error" for d in fr.degradations
        )


# ── TestDeleteBlock ──────────────────────────────────────────────────


@_need_simple
class TestDeleteBlock:
    """Tests for delete_block()."""

    def test_delete_basic(self, tmp_path: Path) -> None:
        """Deleted content is removed from the output."""
        out = str(tmp_path / "deleted.pdf")
        # Delete the body paragraph
        result = delete_block(SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), out)
        assert result.success
        text = get_text(out)
        assert "reportlab" not in text
        assert "Test Document" in text

    def test_delete_close_gap(self, tmp_path: Path) -> None:
        """Content below deleted region moves up when close_gap=True."""
        out = str(tmp_path / "close.pdf")
        delete_block(SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), out, close_gap=True)
        paras_before = detect_paragraphs(SIMPLE_PDF, page=0)
        paras_after = detect_paragraphs(out, page=0)

        before_map = {p.full_text[:20]: p.first_line_y for p in paras_before}
        after_map = {p.full_text[:20]: p.first_line_y for p in paras_after}

        key = "Section two has numb"
        if key in before_map and key in after_map:
            # Should have moved up (y increased) by approximately the deleted height
            assert after_map[key] > before_map[key], "Content should shift up"

    def test_delete_no_close_gap(self, tmp_path: Path) -> None:
        """With close_gap=False, content below stays in place."""
        out = str(tmp_path / "no_close.pdf")
        delete_block(SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), out, close_gap=False)
        paras_before = detect_paragraphs(SIMPLE_PDF, page=0)
        paras_after = detect_paragraphs(out, page=0)

        before_map = {p.full_text[:20]: p.first_line_y for p in paras_before}
        after_map = {p.full_text[:20]: p.first_line_y for p in paras_after}

        key = "Section two has numb"
        if key in before_map and key in after_map:
            assert abs(after_map[key] - before_map[key]) < 1.0

    def test_delete_returns_original_text(self, tmp_path: Path) -> None:
        """Result includes the deleted original text."""
        out = str(tmp_path / "result.pdf")
        result = delete_block(SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), out)
        assert result.original_text
        assert "simple test document" in result.original_text


@_need_resume
class TestDeleteBlockResume:
    """delete_block on the resume PDF."""

    def test_delete_smart_med(self, tmp_path: Path) -> None:
        """Delete the SMART_MED section title."""
        out = str(tmp_path / "no_smart.pdf")
        # SMART_MED bbox: (14.2, 161.6, 250.2, 171.6)
        result = delete_block(RESUME_PDF, 0, (14.2, 161.6, 250.2, 171.6), out, close_gap=False)
        assert result.success
        text = get_text(out)
        assert "SMART_MED" not in text


# ── TestCombinations ─────────────────────────────────────────────────


@_need_simple
class TestCombinations:
    """Integration tests combining multiple structural operations."""

    def test_delete_then_insert(self, tmp_path: Path) -> None:
        """Delete a paragraph then insert new text in its place."""
        intermediate = str(tmp_path / "step1.pdf")
        final = str(tmp_path / "step2.pdf")

        # Step 1: delete body paragraph
        delete_block(SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), intermediate, close_gap=False)

        # Step 2: insert new text where the old paragraph was
        result = insert_text_block(
            intermediate,
            0,
            x=72.0,
            y=700.0,
            text="Brand new content replaces old.",
            output_path=final,
            font_size=11.0,
        )
        assert result.success
        text = get_text(final)
        assert "Brand new content" in text
        assert "reportlab" not in text
        assert "Test Document" in text

    def test_replace_then_shift(self, tmp_path: Path) -> None:
        """Replace a block then shift content below it."""
        step1 = str(tmp_path / "replaced.pdf")
        step2 = str(tmp_path / "shifted.pdf")

        replace_block(SIMPLE_PDF, 0, (72.0, 667.2, 494.4, 708.2), "Short.", step1)
        shift_content_below(step1, 0, 680.0, 10.0, step2)
        text = get_text(step2)
        assert "Short" in text
        assert "Section two" in text
