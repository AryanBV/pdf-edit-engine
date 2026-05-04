"""Tests for the ReflowEngine — paragraph detection, line breaking, and reflow."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.encoding import FontResolverCache
from pdf_edit_engine.locator import find, get_text
from pdf_edit_engine.models import FontInfo, TextCharacter, TextMatch
from pdf_edit_engine.reflow import (
    S1_MIN,
    S2_MAX,
    S3_MIN,
    _low_confidence_diagnostics,
    break_into_lines,
    detect_paragraphs,
    find_paragraph_for_match,
    is_low_confidence_paragraph,
)
from pdf_edit_engine.surgeon import replace

CORPUS = Path(__file__).parent / "corpus"
SIMPLE_PDF = str(CORPUS / "reportlab_simple.pdf")
RESUME_PDF = str(CORPUS / "Aryan_BV_Resume_2026.pdf")


# ── Paragraph detection ───────────────────────────────────────────────


class TestParagraphDetection:
    """Tests for detect_paragraphs and _detect_paragraphs_from_index."""

    def test_detect_on_reportlab_simple(self) -> None:
        paras = detect_paragraphs(SIMPLE_PDF, page=0)
        assert len(paras) >= 2
        # Body paragraph should be multi-line
        body = [p for p in paras if p.line_count >= 3]
        assert len(body) >= 1

    def test_paragraph_width_reasonable(self) -> None:
        paras = detect_paragraphs(SIMPLE_PDF, page=0)
        wide = [p for p in paras if p.paragraph_width > 200]
        assert len(wide) >= 1, "Expected at least one wide paragraph (body text)"

    def test_paragraph_excludes_title(self) -> None:
        paras = detect_paragraphs(SIMPLE_PDF, page=0)
        # Title "Test Document" is F2/18pt, body is F1/11pt — separate paragraphs
        title_para = [p for p in paras if "Test Document" in p.full_text]
        body_para = [p for p in paras if "simple test document" in p.full_text]
        assert title_para and body_para
        assert title_para[0] is not body_para[0]

    def test_paragraph_excludes_separated_section(self) -> None:
        paras = detect_paragraphs(SIMPLE_PDF, page=0)
        body = [p for p in paras if "simple test document" in p.full_text]
        section2 = [p for p in paras if "Section two" in p.full_text]
        assert body and section2
        assert body[0] is not section2[0]

    def test_paragraph_operator_indices(self) -> None:
        paras = detect_paragraphs(SIMPLE_PDF, page=0)
        body = [p for p in paras if "simple test document" in p.full_text]
        assert body
        p = body[0]
        # Operator indices should be specific integers, sorted
        assert isinstance(p.operator_indices, list)
        assert all(isinstance(i, int) for i in p.operator_indices)
        assert p.operator_indices == sorted(p.operator_indices)
        assert len(p.operator_indices) == len(p.elements)

    def test_single_line_paragraph(self) -> None:
        paras = detect_paragraphs(SIMPLE_PDF, page=0)
        single = [p for p in paras if p.line_count == 1]
        assert single
        p = single[0]
        # Single-line: line_height defaults to font_size * 1.2
        assert abs(p.line_height - p.font_size * 1.2) < 0.1

    def test_detect_on_resume(self) -> None:
        if not (CORPUS / "Aryan_BV_Resume_2026.pdf").exists():
            pytest.skip("Aryan_BV_Resume_2026.pdf not in corpus")
        paras = detect_paragraphs(RESUME_PDF, page=0)
        assert len(paras) >= 1

    def test_multiline_line_height(self) -> None:
        paras = detect_paragraphs(SIMPLE_PDF, page=0)
        body = [p for p in paras if p.line_count >= 3]
        assert body
        p = body[0]
        # Line height should be approximately 1.0-1.5x font size
        assert p.font_size * 0.8 <= p.line_height <= p.font_size * 2.0


# ── find_paragraph_for_match ──────────────────────────────────────────


class TestFindParagraphForMatch:
    """Tests for find_paragraph_for_match."""

    def test_find_matching_paragraph(self) -> None:
        paras = detect_paragraphs(SIMPLE_PDF, page=0)
        matches = find(SIMPLE_PDF, "simple test document")
        assert matches
        result = find_paragraph_for_match(paras, matches[0])
        assert result is not None
        assert "simple test document" in result.full_text

    def test_no_match_returns_none(self) -> None:
        paras = detect_paragraphs(SIMPLE_PDF, page=0)
        # Fabricate a match with nonexistent operator refs
        fake_match = TextMatch(
            matched_text="nonexistent",
            page_number=0,
            bounding_box=(0, 0, 10, 10),
            characters=[
                TextCharacter(
                    unicode_char="x",
                    page_x=0,
                    page_y=0,
                    width=5,
                    height=10,
                    font_name="F1",
                    font_size=11,
                    color=(0.0,),
                    operator_index=99999,
                    byte_position=0,
                    tj_fragment_index=None,
                ),
            ],
            font_info=FontInfo(
                name="F1",
                postscript_name="Helvetica",
                encoding_type="WinAnsi",
                is_subset=False,
                glyph_count=100,
                embedded_type="TrueType",
            ),
            operator_refs=[99999],
        )
        result = find_paragraph_for_match(paras, fake_match)
        assert result is None


# ── Line breaking ─────────────────────────────────────────────────────


class TestLineBreaking:
    """Tests for break_into_lines."""

    @pytest.fixture()
    def font_context(self) -> tuple[pikepdf.Pdf, pikepdf.Page, object, object]:
        """Get font resolver and reference from reportlab_simple."""
        pdf = pikepdf.Pdf.open(SIMPLE_PDF)
        page = pdf.pages[0]
        cache = FontResolverCache()
        resolver = cache.get_resolver(page, "F1")
        font_ref = page["/Resources"]["/Font"]["/F1"]
        return pdf, page, resolver, font_ref

    def test_short_text_single_line(self, font_context: tuple) -> None:  # type: ignore[type-arg]
        pdf, page, resolver, font_ref = font_context
        lines = break_into_lines("Hello", 500.0, resolver, font_ref, 11.0)
        assert lines == ["Hello"]
        pdf.close()

    def test_word_wrap_basic(self, font_context: tuple) -> None:  # type: ignore[type-arg]
        pdf, page, resolver, font_ref = font_context
        text = "This is a somewhat longer sentence that should wrap"
        lines = break_into_lines(text, 100.0, resolver, font_ref, 11.0)
        assert len(lines) >= 2
        # All words preserved
        assert " ".join(lines) == text
        pdf.close()

    def test_hard_newline_preserved(self, font_context: tuple) -> None:  # type: ignore[type-arg]
        pdf, page, resolver, font_ref = font_context
        lines = break_into_lines("Line1\nLine2", 500.0, resolver, font_ref, 11.0)
        assert len(lines) == 2
        assert lines[0] == "Line1"
        assert lines[1] == "Line2"
        pdf.close()

    def test_long_word_not_broken(self, font_context: tuple) -> None:  # type: ignore[type-arg]
        pdf, page, resolver, font_ref = font_context
        long_word = "Supercalifragilisticexpialidocious"
        lines = break_into_lines(long_word, 50.0, resolver, font_ref, 11.0)
        assert len(lines) == 1
        assert lines[0] == long_word
        pdf.close()

    def test_empty_text(self, font_context: tuple) -> None:  # type: ignore[type-arg]
        pdf, page, resolver, font_ref = font_context
        lines = break_into_lines("", 500.0, resolver, font_ref, 11.0)
        assert lines == [""]
        pdf.close()

    def test_preserves_word_boundaries(self, font_context: tuple) -> None:  # type: ignore[type-arg]
        pdf, page, resolver, font_ref = font_context
        text = "word1 word2 word3 word4"
        lines = break_into_lines(text, 80.0, resolver, font_ref, 11.0)
        # No partial words
        all_words = []
        for line in lines:
            all_words.extend(line.split(" "))
        assert all_words == ["word1", "word2", "word3", "word4"]
        pdf.close()


# ── End-to-end reflow ─────────────────────────────────────────────────


class TestReflowParagraph:
    """End-to-end reflow tests on reportlab_simple.pdf."""

    def test_reflow_wider_text(self, tmp_path: Path) -> None:
        output = str(tmp_path / "reflowed.pdf")
        # Replace short text with much longer text
        matches = find(SIMPLE_PDF, "simple test document")
        assert matches
        result = replace(
            SIMPLE_PDF,
            matches[0],
            "comprehensive and thoroughly detailed testing document",
            output,
            reflow=True,
        )
        assert result.success
        assert result.fidelity_report.reflow_applied
        # Output PDF valid
        pdf = pikepdf.Pdf.open(output)
        assert len(pdf.pages) > 0
        pdf.close()
        # Text present
        text = get_text(output)
        assert "comprehensive" in text

    def test_reflow_shorter_text_no_overflow(self, tmp_path: Path) -> None:
        output = str(tmp_path / "shorter.pdf")
        matches = find(SIMPLE_PDF, "simple test document")
        assert matches
        # Shorter replacement still triggers reflow if wider overall
        result = replace(
            SIMPLE_PDF,
            matches[0],
            "test",
            output,
            reflow=True,
        )
        assert result.success
        # Shorter text should NOT trigger reflow (narrower than original)
        assert not result.fidelity_report.reflow_applied

    def test_reflow_preserves_other_text(self, tmp_path: Path) -> None:
        output = str(tmp_path / "preserved.pdf")
        matches = find(SIMPLE_PDF, "simple test document")
        assert matches
        result = replace(
            SIMPLE_PDF,
            matches[0],
            "comprehensive and thoroughly detailed testing document",
            output,
            reflow=True,
        )
        assert result.success
        text = get_text(output)
        # Title and section two should still be present
        assert "Test Document" in text
        assert "Section two" in text

    def test_reflow_dry_run(self, tmp_path: Path) -> None:
        output = str(tmp_path / "dry.pdf")
        matches = find(SIMPLE_PDF, "simple test document")
        assert matches
        replace(
            SIMPLE_PDF,
            matches[0],
            "comprehensive and thoroughly detailed testing document",
            output,
            dry_run=True,
            reflow=True,
        )
        # dry_run should not create file
        assert not Path(output).exists()

    def test_reflow_overflow_many_lines(self, tmp_path: Path) -> None:
        output = str(tmp_path / "overflow.pdf")
        matches = find(SIMPLE_PDF, "simple test document")
        assert matches
        # Very long text that will produce more lines than original 3
        long_text = (
            "extraordinarily comprehensive and thoroughly detailed testing "
            "document with many words that will definitely exceed the original "
            "paragraph boundaries and require significantly more vertical space "
            "than was originally allocated in the source document layout"
        )
        result = replace(
            SIMPLE_PDF,
            matches[0],
            long_text,
            output,
            reflow=True,
        )
        assert result.success
        assert result.fidelity_report.reflow_applied
        assert result.fidelity_report.overflow_detected


# ── Surgeon integration ───────────────────────────────────────────────


class TestSurgeonReflowIntegration:
    """Tests for surgeon.replace() with reflow parameter."""

    def test_replace_reflow_false_skips_reflow(self, tmp_path: Path) -> None:
        output = str(tmp_path / "no_reflow.pdf")
        matches = find(SIMPLE_PDF, "simple test document")
        assert matches
        result = replace(
            SIMPLE_PDF,
            matches[0],
            "comprehensive and thoroughly detailed testing document",
            output,
            reflow=False,
        )
        assert result.success
        assert not result.fidelity_report.reflow_applied

    def test_replace_narrow_text_no_reflow(self, tmp_path: Path) -> None:
        output = str(tmp_path / "narrow.pdf")
        matches = find(SIMPLE_PDF, "simple test document")
        assert matches
        # Same-length replacement: should not trigger reflow
        result = replace(
            SIMPLE_PDF,
            matches[0],
            "basic test material",
            output,
            reflow=True,
        )
        assert result.success
        # Narrower or same width — no reflow
        assert not result.fidelity_report.reflow_applied

    def test_no_paragraph_found_fallback(self, tmp_path: Path) -> None:
        output = str(tmp_path / "fallback.pdf")
        # Title "Test Document" is isolated (different font from body)
        matches = find(SIMPLE_PDF, "Test Document")
        assert matches
        result = replace(
            SIMPLE_PDF,
            matches[0],
            "A Much Longer Title That Exceeds Original Width By Far",
            output,
            reflow=True,
        )
        assert result.success
        # Title is a single-line paragraph — reflow should still work
        # or fall back to simple replacement

    def test_replace_reflow_true_wider(self, tmp_path: Path) -> None:
        output = str(tmp_path / "reflow_on.pdf")
        matches = find(SIMPLE_PDF, "simple test document")
        assert matches
        result = replace(
            SIMPLE_PDF,
            matches[0],
            "comprehensive and thoroughly detailed testing document",
            output,
            reflow=True,
        )
        assert result.success
        assert result.fidelity_report.reflow_applied
        # Verify output
        text = get_text(output)
        assert "comprehensive" in text


# ── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases for reflow."""

    def test_single_line_paragraph_reflow(self, tmp_path: Path) -> None:
        output = str(tmp_path / "single.pdf")
        # "Section two..." is a single-line paragraph
        matches = find(SIMPLE_PDF, "Section two")
        assert matches
        result = replace(
            SIMPLE_PDF,
            matches[0],
            "Section two now has a much much much longer description here",
            output,
            reflow=True,
        )
        assert result.success
        text = get_text(output)
        assert "Section two" in text


class TestBuildParagraphSpaceThreshold:
    """ARY-277: `_build_paragraph` used to join adjacent text elements with
    a phantom space when their gap exceeded ``font_size * 0.25``.

    For a 12pt Arial-like font, the natural space width is ~3pt, so 0.25 *
    font_size was effectively one full space width — not the "half-space
    threshold" the code comment claimed. Glyph-side-bearing gaps
    (e.g. a comma's ~0.15-0.2 * font_size offset from the preceding word)
    exceeded the threshold and emitted a phantom space.

    After tightening to 0.125 * font_size, typical punctuation-adjacency
    gaps stay below threshold while legitimate word-boundary gaps (real
    spaces in the content stream) still exceed it.
    """

    def _make_element(
        self,
        text: str,
        start_x: float,
        end_x_offset: float,
        y: float = 100.0,
        font_name: str = "F1",
        font_size: float = 12.0,
    ) -> object:
        """Build a minimal text ContentElement for unit testing."""
        from pdf_edit_engine.models import ContentElement, GraphicsStateSnapshot

        # Spread characters evenly across the width
        char_w = (end_x_offset - start_x) / max(len(text), 1)
        chars = [
            TextCharacter(
                unicode_char=c,
                page_x=start_x + i * char_w,
                page_y=y,
                width=char_w,
                height=font_size,
                font_name=font_name,
                font_size=font_size,
                color=(0.0,),
                operator_index=i,
                byte_position=i * 2,
                tj_fragment_index=None,
            )
            for i, c in enumerate(text)
        ]
        state = GraphicsStateSnapshot(
            ctm=(1, 0, 0, 1, 0, 0),
            fill_color=(0.0,),
            font_name=font_name,
            font_size=font_size,
            text_matrix=(1, 0, 0, 1, start_x, y),
        )
        return ContentElement(
            type="text",
            page=0,
            operator_range=(0, 1),
            bbox=(start_x, y - 2, end_x_offset, y + font_size),
            graphics_state=state,
            text_content=text,
            characters=chars,
        )

    def test_small_gap_does_not_emit_phantom_space(self) -> None:
        """A gap equal to 0.18 * font_size (typical comma left-bearing) must
        NOT trigger a space insertion between two adjacent elements."""
        from pdf_edit_engine.reflow import _build_paragraph

        # Post-ARY-277 threshold is 0.125 * font_size = 1.5pt for 12pt font.
        # A 1.0pt gap between word and trailing comma (< threshold) must
        # produce NO phantom space.
        elem_a = self._make_element("Konstantinidis", start_x=100.0, end_x_offset=184.0)
        elem_b = self._make_element(",", start_x=185.0, end_x_offset=188.0)  # 1.0pt gap
        tight = _build_paragraph([elem_a, elem_b])
        assert tight.full_text == "Konstantinidis,", (
            f"ARY-277 regression: 1.0pt gap between word and comma must NOT "
            f"emit a phantom space. Got: {tight.full_text!r}"
        )

    def test_real_space_gap_does_emit_space(self) -> None:
        """A gap equal to a full space width MUST still trigger space
        insertion — we must not over-tighten the threshold."""
        from pdf_edit_engine.reflow import _build_paragraph

        # 12pt font; gap = 4pt (~1/3 of font_size) is well above any
        # reasonable space-width threshold and represents a real word
        # boundary in a content stream.
        elem_a = self._make_element("Hello", start_x=100.0, end_x_offset=130.0)
        elem_b = self._make_element("world", start_x=134.0, end_x_offset=164.0)

        paragraph = _build_paragraph([elem_a, elem_b])
        assert paragraph.full_text == "Hello world", (
            f"Word-boundary gap MUST emit a space. Got: {paragraph.full_text!r}"
        )


class TestReflowOverflowShift:
    """ARY-277 followup: when reflow_paragraph produces more lines than the
    original paragraph occupied, content below the paragraph is shifted
    down by (extra_lines × line_height) before the replacement ops are
    spliced. Prior behaviour: extra lines wrote on top of whatever was
    below, creating visual overlap and garbled extracted text.

    This test exercises the CLEAN case (multi-line reflow with standalone
    content below). The narrow-single-line paragraph with inline
    continuation (the `Sarah Johnson, stated: ...` Acme Word-PDF case)
    remains a known limitation tracked for v0.1.3 — see CHANGELOG.
    """

    def test_wide_paragraph_overflow_shifts_content_below(self, tmp_path: Path) -> None:
        """When replacement adds a line to a wide paragraph, downstream
        content must remain readable (not overwritten by the extra
        reflowed line)."""
        out = tmp_path / "out.pdf"
        # reportlab_simple has "Section two has numbers..." body
        # followed by terminating content. Replace "Section two" with
        # enough text to force at least one extra reflow line.
        matches = find(SIMPLE_PDF, "Section two")
        assert matches
        long_repl = (
            "Section two is a much much much much much much much much "
            "much much much much much longer section header"
        )
        result = replace(
            SIMPLE_PDF,
            matches[0],
            long_repl,
            str(out),
            reflow=True,
        )
        assert result.success, result
        text = get_text(str(out))
        # Post-shift invariant: the original "numbers" body text that
        # followed "Section two" must still be readable (not overwritten
        # by the extended reflow output).
        assert "numbers" in text, (
            f"Shift-overflow regression: content below the reflowed paragraph "
            f"was overwritten. Extracted text: {text!r}"
        )


# ── Phase 3 (v0.1.3): S5 low-confidence paragraph signal ────────────────


class TestS5LowConfidenceSignal:
    """Tests for is_low_confidence_paragraph (ARY-292 surfacing)."""

    def test_thresholds_match_design_doc(self) -> None:
        """Locked thresholds per fpr_table.md / design doc §2."""
        assert S1_MIN == 0.50
        assert S2_MAX == 0.55
        assert S3_MIN == 2

    def test_table_merge_paragraph_triggers(self) -> None:
        """Reportlab table 'Q1 2026 $1,200,000 $980,000 ...' must trigger S5."""
        path = str(CORPUS / "reportlab_table.pdf")
        if not Path(path).exists():
            pytest.skip("reportlab_table.pdf not in corpus")
        paras = detect_paragraphs(path)
        with pikepdf.open(path) as pdf:
            page_w = float(pdf.pages[0].MediaBox[2])
        # Find the table-data paragraph (multi-line financials).
        table_para = None
        for p in paras:
            if "Q1 2026" in p.full_text and "Q2 2026" in p.full_text:
                table_para = p
                break
        assert table_para is not None, "expected merged table paragraph"
        assert is_low_confidence_paragraph(table_para, page_w), (
            f"table-merge paragraph should trigger S5 — diagnostics="
            f"{_low_confidence_diagnostics(table_para, page_w)}"
        )

    def test_natural_paragraph_does_not_trigger(self) -> None:
        """A natural multi-line flowing paragraph in reportlab_simple stays under S5."""
        paras = detect_paragraphs(SIMPLE_PDF)
        with pikepdf.open(SIMPLE_PDF) as pdf:
            page_w = float(pdf.pages[0].MediaBox[2])
        body = [p for p in paras if p.line_count >= 2]
        # At least one body paragraph; none should trigger S5 in this fixture.
        assert body, "expected at least one body paragraph"
        for p in body:
            triggered = is_low_confidence_paragraph(p, page_w)
            assert not triggered, (
                f"natural paragraph triggered S5: {p.full_text[:60]!r} "
                f"diagnostics={_low_confidence_diagnostics(p, page_w)}"
            )

    def test_diagnostics_returns_three_components(self) -> None:
        """_low_confidence_diagnostics returns (s1, s2, s3) tuple."""
        paras = detect_paragraphs(SIMPLE_PDF)
        if not paras:
            pytest.skip("no paragraphs in reportlab_simple")
        with pikepdf.open(SIMPLE_PDF) as pdf:
            page_w = float(pdf.pages[0].MediaBox[2])
        s1, s2, s3 = _low_confidence_diagnostics(paras[0], page_w)
        assert isinstance(s1, float)
        assert isinstance(s2, float)
        assert isinstance(s3, int)

    def test_zero_page_width_returns_safe_diagnostics(self) -> None:
        """Degenerate page_width=0 must not crash; returns (0, 1, 0) → no trigger."""
        paras = detect_paragraphs(SIMPLE_PDF)
        if not paras:
            pytest.skip()
        s1, s2, s3 = _low_confidence_diagnostics(paras[0], 0.0)
        assert (s1, s2, s3) == (0.0, 1.0, 0)
        assert not is_low_confidence_paragraph(paras[0], 0.0)
