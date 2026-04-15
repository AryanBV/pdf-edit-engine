"""Tests for the OperatorSurgeon module — PDF content stream text replacement."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.errors import OperatorError, PDFEditError
from pdf_edit_engine.locator import find, get_fonts, get_text
from pdf_edit_engine.models import Edit, TextMatch
from pdf_edit_engine.surgeon import batch_replace, replace, replace_all

CORPUS_DIR = Path(__file__).parent / "corpus"
RESUME_PDF = str(CORPUS_DIR / "Aryan_BV_Resume_2026.pdf")
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")
MULTIPAGE_PDF = str(CORPUS_DIR / "reportlab_multipage.pdf")

_need_resume = pytest.mark.skipif(
    not Path(RESUME_PDF).exists(), reason="Aryan_BV_Resume_2026.pdf not in corpus"
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _first_match(pdf_path: str, text: str) -> TextMatch:
    """Find the first occurrence of text in a PDF."""
    matches = find(pdf_path, text)
    assert matches, f"No match for {text!r} in {pdf_path}"
    return matches[0]


def _validate_output(
    output_path: str,
    expected_text: str,
    original_pdf: str | None = None,
) -> None:
    """Validate a replacement output PDF."""
    # Valid PDF structure
    pdf = pikepdf.Pdf.open(output_path)
    assert len(pdf.pages) > 0
    pdf.close()

    # Contains replacement text
    text = get_text(output_path)
    assert expected_text in text, f"Expected {expected_text!r} in output text"

    # Fonts preserved
    if original_pdf is not None:
        orig_fonts = get_fonts(original_pdf)
        out_fonts = get_fonts(output_path)
        orig_names = sorted(f.postscript_name for f in orig_fonts)
        out_names = sorted(f.postscript_name for f in out_fonts)
        assert orig_names == out_names


# ── Same-length replacement ──────────────────────────────────────────────


@_need_resume
class TestSameLengthReplace:
    """Test same-length text replacement preserving kerning and layout."""

    def test_identity_h_same_length(self, tmp_path: Path) -> None:
        """Replace 'Aryan' (5 chars) with 'Bryan' (5 chars) in Identity-H PDF."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "Bryan", out)

        assert result.success is True
        assert result.font_action == "kept"
        assert result.original_text == "Aryan"
        assert result.new_text == "Bryan"
        _validate_output(out, "Bryan", RESUME_PDF)

    def test_winAnsi_same_length(self, tmp_path: Path) -> None:
        """Replace 'Test' (4 chars) with 'Best' (4 chars) in WinAnsi PDF."""
        out = str(tmp_path / "output.pdf")
        match_test = _first_match(SIMPLE_PDF, "Test")
        result = replace(SIMPLE_PDF, match_test, "Best", out)

        assert result.success is True
        _validate_output(out, "Best", SIMPLE_PDF)

    def test_fidelity_report_font_preserved(self, tmp_path: Path) -> None:
        """FidelityReport should show font_preserved=True for same-length."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "Bryan", out)

        assert result.fidelity_report.font_preserved is True
        assert result.fidelity_report.font_substituted is None
        assert result.fidelity_report.reflow_applied is False
        assert result.fidelity_report.glyphs_missing == []

    def test_editresult_fields(self, tmp_path: Path) -> None:
        """EditResult should have correct success fields."""
        match = _first_match(SIMPLE_PDF, "simple")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "sample", out)

        assert result.success is True
        assert result.original_text == "simple"
        assert result.new_text == "sample"
        assert result.font_action == "kept"


# ── Different-length replacement ─────────────────────────────────────────


@_need_resume
class TestDifferentLengthReplace:
    """Test replacement where old and new text have different lengths."""

    def test_shorter_replacement(self, tmp_path: Path) -> None:
        """Replace 'Aryan' (5 chars) with 'AB' (2 chars) — shorter."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "AB", out)

        assert result.success is True
        _validate_output(out, "AB", RESUME_PDF)

    def test_longer_replacement(self, tmp_path: Path) -> None:
        """Replace 'Aryan' (5 chars) with 'Aryana' (6 chars) — longer."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "Aryana", out)

        assert result.success is True
        _validate_output(out, "Aryana", RESUME_PDF)

    def test_winAnsi_different_length(self, tmp_path: Path) -> None:
        """Replace 'simple' (6) with 'a' (1) in WinAnsi PDF — much shorter."""
        match = _first_match(SIMPLE_PDF, "simple")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "a", out)

        assert result.success is True
        _validate_output(out, "a", SIMPLE_PDF)

    def test_overflow_detection(self, tmp_path: Path) -> None:
        """Replacing with very long text should flag overflow."""
        match = _first_match(SIMPLE_PDF, "Test")
        out = str(tmp_path / "output.pdf")
        # Use a very long replacement to trigger overflow
        long_text = "A" * 200
        result = replace(SIMPLE_PDF, match, long_text, out)

        assert result.success is True
        assert result.fidelity_report.overflow_detected is True


# ── Cross-element replacement ────────────────────────────────────────────


@_need_resume
class TestCrossElementReplace:
    """Test replacement spanning multiple TJ fragments."""

    def test_multi_fragment_replace(self, tmp_path: Path) -> None:
        """Replace text that spans multiple TJ fragments with kerning."""
        match = _first_match(RESUME_PDF, "Email")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "Phone", out)

        assert result.success is True
        _validate_output(out, "Phone", RESUME_PDF)

    def test_cross_fragment_with_space(self, tmp_path: Path) -> None:
        """Replace 'B V' which spans fragments with a space inside."""
        match = _first_match(RESUME_PDF, "B V")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "C D", out)

        assert result.success is True
        _validate_output(out, "C D", RESUME_PDF)


# ── Encoding failure ─────────────────────────────────────────────────────


class TestEncodingFailure:
    """Test behavior when replacement text cannot be encoded."""

    def test_unencodable_returns_failure(self, tmp_path: Path) -> None:
        """Replacing with characters not in font returns success=False."""
        match = _first_match(SIMPLE_PDF, "Test")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "\u4f60\u597d", out)

        assert result.success is False
        assert result.font_action == "failed"
        assert len(result.fidelity_report.glyphs_missing) > 0

    def test_unencodable_no_output_file(self, tmp_path: Path) -> None:
        """Failed encoding should not create an output PDF."""
        match = _first_match(SIMPLE_PDF, "Test")
        out = str(tmp_path / "output.pdf")
        replace(SIMPLE_PDF, match, "\u4f60\u597d", out)

        assert not Path(out).exists()


# ── Dry run ──────────────────────────────────────────────────────────────


@_need_resume
class TestDryRun:
    """Test dry_run mode: full analysis, no file modification."""

    def test_dry_run_returns_editresult(self, tmp_path: Path) -> None:
        """dry_run=True should return a valid EditResult."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "SPIKE", out, dry_run=True)

        assert result.success is True
        assert result.original_text == "Aryan"
        assert result.new_text == "SPIKE"
        assert result.font_action == "kept"

    def test_dry_run_no_output_file(self, tmp_path: Path) -> None:
        """dry_run=True should not create output file."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        replace(RESUME_PDF, match, "SPIKE", out, dry_run=True)

        assert not Path(out).exists()

    def test_dry_run_original_unchanged(self) -> None:
        """dry_run=True should not modify the original PDF."""
        original_bytes = Path(RESUME_PDF).read_bytes()
        match = _first_match(RESUME_PDF, "Aryan")
        replace(RESUME_PDF, match, "SPIKE", "/tmp/nope.pdf", dry_run=True)
        assert Path(RESUME_PDF).read_bytes() == original_bytes

    def test_dry_run_fidelity_report(self, tmp_path: Path) -> None:
        """dry_run should still compute FidelityReport."""
        match = _first_match(SIMPLE_PDF, "Test")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "A" * 200, out, dry_run=True)

        assert result.fidelity_report.overflow_detected is True
        assert not Path(out).exists()


# ── replace_all ──────────────────────────────────────────────────────────


class TestReplaceAll:
    """Test replace_all: find and replace all occurrences."""

    def test_multiple_occurrences(self, tmp_path: Path) -> None:
        """Replace a word appearing on multiple pages."""
        out = str(tmp_path / "output.pdf")
        results = replace_all(MULTIPAGE_PDF, "Content", "Section", out)

        assert len(results) == 2
        assert all(r.success for r in results)
        _validate_output(out, "Section", MULTIPAGE_PDF)
        # Original text should be gone
        text = get_text(out)
        assert "Content" not in text

    def test_not_found_returns_empty(self) -> None:
        """Searching for nonexistent text returns empty list."""
        results = replace_all(SIMPLE_PDF, "ZZZZZZZ", "YYYYYYY", "/tmp/nope.pdf")
        assert results == []

    def test_single_occurrence(self, tmp_path: Path) -> None:
        """replace_all with one match behaves like replace."""
        out = str(tmp_path / "output.pdf")
        results = replace_all(SIMPLE_PDF, "simple", "sample", out)

        assert len(results) == 1
        assert results[0].success is True
        _validate_output(out, "sample", SIMPLE_PDF)


# ── batch_replace ────────────────────────────────────────────────────────


class TestBatchReplace:
    """Test batch_replace: multiple find/replace pairs."""

    def test_two_edits(self, tmp_path: Path) -> None:
        """Apply two different replacements in one call."""
        out = str(tmp_path / "output.pdf")
        edits = [
            Edit(find="Page One", replace="Section A"),
            Edit(find="Page Two", replace="Section B"),
        ]
        results = batch_replace(MULTIPAGE_PDF, edits, out)

        assert len(results) == 2
        assert all(r.success for r in results)
        text = get_text(out)
        assert "Section A" in text
        assert "Section B" in text

    def test_one_result_per_edit(self, tmp_path: Path) -> None:
        """Return list has exactly one result per edit."""
        out = str(tmp_path / "output.pdf")
        edits = [
            Edit(find="Content", replace="Material"),
            Edit(find="NONEXISTENT", replace="NOTHING"),
        ]
        results = batch_replace(MULTIPAGE_PDF, edits, out)

        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False


# ── Error handling ───────────────────────────────────────────────────────


class TestErrorHandling:
    """Test error conditions raise appropriate exceptions."""

    def test_encrypted_pdf_raises(self, tmp_path: Path) -> None:
        """Encrypted PDF should raise PDFEditError."""
        # Create an encrypted PDF
        enc_path = str(tmp_path / "encrypted.pdf")
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        pdf.save(enc_path, encryption=pikepdf.Encryption(owner="secret", user=""))
        pdf.close()

        # Fabricate a minimal TextMatch (won't be used — error raised early)
        dummy_match = _first_match(SIMPLE_PDF, "Test")

        with pytest.raises(PDFEditError, match="encrypted"):
            replace(enc_path, dummy_match, "New", str(tmp_path / "out.pdf"))

    def test_stale_operator_index_raises(self, tmp_path: Path) -> None:
        """TextMatch with out-of-bounds operator_index should raise OperatorError."""
        match = _first_match(SIMPLE_PDF, "Test")
        # Mutate the match to have an invalid operator index
        for ch in match.characters:
            ch.operator_index = 99999
        match.operator_refs = [99999]

        with pytest.raises(OperatorError):
            replace(SIMPLE_PDF, match, "New", str(tmp_path / "out.pdf"))


# ── Output validation integration ────────────────────────────────────────


@_need_resume
class TestOutputValidation:
    """Integration tests verifying complete output PDF quality."""

    def test_resume_spike_full_validation(self, tmp_path: Path) -> None:
        """Full end-to-end: resume Aryan->SPIKE with complete validation."""
        match = _first_match(RESUME_PDF, "Aryan")
        out = str(tmp_path / "output.pdf")
        result = replace(RESUME_PDF, match, "SPIKE", out)

        assert result.success is True

        # PDF opens
        pdf = pikepdf.Pdf.open(out)
        assert len(pdf.pages) == 1
        pdf.close()

        # Text correct
        text = get_text(out)
        assert "SPIKE" in text
        assert "Aryan" not in text

        # Fonts preserved (6 fonts in original)
        orig_fonts = get_fonts(RESUME_PDF)
        out_fonts = get_fonts(out)
        assert len(out_fonts) == len(orig_fonts)
        assert sorted(f.postscript_name for f in orig_fonts) == sorted(
            f.postscript_name for f in out_fonts
        )

    def test_winAnsi_full_validation(self, tmp_path: Path) -> None:
        """Full end-to-end: WinAnsi Test Document -> New Document."""
        match = _first_match(SIMPLE_PDF, "Test Document")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "New Document!", out)

        assert result.success is True
        _validate_output(out, "New Document!", SIMPLE_PDF)


# ── ARY-276: Identity-H CIDFont replacement ──────────────────────────────

from tests._identity_h_fixture import (  # noqa: E402
    _build_identity_h_pdf,
    _no_ttf,
    _title_match,
)


@_no_ttf
class TestCIDFontReplace:
    """ARY-276 regression tests: Identity-H CIDFont replacement fidelity."""

    def test_identity_h_multi_tm_tj_cross_op(self, tmp_path: Path) -> None:
        """Per-glyph Tm+Tj title (Word/Chrome pattern) — F0 gate."""
        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        assert _build_identity_h_pdf(src, title_pattern="per_glyph_tm")

        match = _title_match(str(src), "Acme Corporation")
        # Confirm the match actually spans multiple narrow operators
        # (sanity check for the repro — skip gracefully otherwise).
        assert len({ch.operator_index for ch in match.characters}) >= 4

        result = replace(str(src), match, "Nova Industries", str(out))
        assert result.success is True

        text = get_text(str(out))
        # Title line should read exactly "Nova Industries"
        first_line = text.split("\n", 1)[0]
        assert first_line == "Nova Industries", (
            f"expected clean 'Nova Industries', got {first_line!r}"
        )

    def test_identity_h_per_char_tj_array(self, tmp_path: Path) -> None:
        """Single TJ array with per-character strings (Chrome TJ pattern)."""
        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        assert _build_identity_h_pdf(src, title_pattern="per_char_tj_array")

        match = _title_match(str(src), "Acme Corporation")
        result = replace(str(src), match, "Nova Industries", str(out))
        assert result.success is True
        assert "Nova Industries" in get_text(str(out))

    def test_identity_h_multi_char_tj_array(self, tmp_path: Path) -> None:
        """TJ array with 2-3-char strings and kerning (Word TJ pattern)."""
        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        assert _build_identity_h_pdf(src, title_pattern="multi_char_tj_array")

        match = _title_match(str(src), "Acme Corporation")
        result = replace(str(src), match, "Nova Industries", str(out))
        assert result.success is True
        assert "Nova Industries" in get_text(str(out))

    def test_identity_h_shorter_cross_op(self, tmp_path: Path) -> None:
        """Shorter replacement on a multi-op Identity-H match."""
        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        assert _build_identity_h_pdf(src, title_pattern="per_glyph_tm")

        match = _title_match(str(src), "Acme Corporation")
        result = replace(str(src), match, "Nova", str(out))
        assert result.success is True
        text = get_text(str(out))
        assert text.split("\n", 1)[0] == "Nova"

    def test_identity_h_longer_cross_op(self, tmp_path: Path) -> None:
        """Longer replacement on a multi-op Identity-H match."""
        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        assert _build_identity_h_pdf(
            src,
            title_pattern="per_glyph_tm",
            extra_corpus="Nova Industries Worldwide",
        )

        match = _title_match(str(src), "Acme Corporation")
        result = replace(str(src), match, "Nova Industries Worldwide", str(out), reflow=False)
        assert result.success is True
        # Either clean extraction OR overflow flagged on FidelityReport.
        text = get_text(str(out))
        first_line = text.split("\n", 1)[0]
        if not result.fidelity_report.overflow_detected:
            assert first_line == "Nova Industries Worldwide"

    def test_identity_h_same_length_regression(self, tmp_path: Path) -> None:
        """Same-length replacement on per_glyph_tm must stay clean (splice path guard)."""
        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        assert _build_identity_h_pdf(
            src,
            title_pattern="per_glyph_tm",
            title_text="Acme Corp Glob",
            extra_corpus="Nova Corp GlobAcme Industries",
        )

        match = _title_match(str(src), "Acme Corp Glob")
        result = replace(str(src), match, "Nova Corp Mega", str(out))
        assert result.success is True
        text = get_text(str(out))
        assert text.split("\n", 1)[0] == "Nova Corp Mega"

    def test_winAnsi_regression_guard(self, tmp_path: Path) -> None:
        """F0/F1/F2 must not affect the WinAnsi replacement path."""
        out = str(tmp_path / "out.pdf")
        match = _first_match(SIMPLE_PDF, "Test")
        result = replace(SIMPLE_PDF, match, "Best", out)
        assert result.success is True
        _validate_output(out, "Best", SIMPLE_PDF)

    def test_mixed_winAnsi_and_identity_h(self, tmp_path: Path) -> None:
        """Replacement in body (WinAnsi-like clean Tj) and title (Identity-H multi-op)."""
        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        # Body is a single Tj (Identity-H but simple case), title is per_glyph_tm
        assert _build_identity_h_pdf(src, title_pattern="per_glyph_tm")

        # Replace title via the multi-op path
        title = _title_match(str(src), "Acme Corporation")
        result1 = replace(str(src), title, "Nova Industries", str(out))
        assert result1.success

        # Now replace body text (which is inside a single Tj operator)
        body_in = str(out)
        body_out = str(tmp_path / "out2.pdf")
        body_match = min(
            find(body_in, "body text"),
            key=lambda m: m.characters[0].font_size,
        )
        result2 = replace(body_in, body_match, "body data", body_out)
        assert result2.success
        text = get_text(body_out)
        assert "Nova Industries" in text
        assert "body data" in text

    def test_tier2_narrow_subset_remaps_cleanly(self, tmp_path: Path) -> None:
        """ARY-278: Tier 1.5 extends narrow subsets cleanly.

        Previously (ARY-276) this test accepted either silent corruption
        OR clean failure, because the old Tier 2 subset-and-replace
        strategy silently broke pre-existing CIDs whenever system-font
        GIDs did not match the original embedded subset's CIDs.

        After ARY-278, Tier 1.5 appends missing glyphs to the existing
        embedded font in place, preserving every pre-existing CID. The
        replacement must now SUCCEED with clean text — no silent
        corruption, no loud abort.
        """
        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        # Narrow subset: omit N, I, v, d, u, s, w, W from the embedded
        # font's internal cmap. The replacement text needs some of
        # these, forcing Tier 1.5 to inject from the system font.
        ok = _build_identity_h_pdf(
            src,
            title_pattern="single_tj",
            extra_corpus="",
            omit_chars_from_subset="NIvduswW",
        )
        assert ok

        match = _title_match(str(src), "Acme Corporation")
        result = replace(str(src), match, "Nova Industries", str(out))
        assert result.success is True, (
            f"Tier 1.5 must succeed, got font_action={result.font_action} "
            f"missing={result.fidelity_report.glyphs_missing}"
        )
        text = get_text(str(out))
        assert "Nova Industries" in text, f"Tier 1.5 output corrupted: {text!r}"


@_no_ttf
class TestCrossFontResolverReuse:
    """Regression tests for cross-font resolver pollution in replace_all.

    Discovered during 0.1.1 release verification against a real Chrome PDF
    with four Identity-H fonts on the same page. ``replace_all``'s per-page
    loop was pre-fetching one resolver from the first match and reusing it
    for every subsequent match on that page. When matches used different
    fonts, ``_apply_single_replacement`` validated encodability against the
    stale resolver (``can_encode=True`` because the *wrong* font happened to
    have the chars), skipped extension, and wrote the stale font's CIDs into
    the match's content-stream operator. Symptom: extracted text showed
    ``"ova ndustries"`` for matches rendered in a font that genuinely lacked
    ``N``/``I`` glyphs, because the emitted CIDs only mapped to those letters
    in the *other* font's ToUnicode CMap.

    Fix: ``_apply_single_replacement`` now always calls
    ``_get_font_resolver(page, match.characters[0].font_name)`` at the top of
    the function, discarding the caller-supplied resolver.
    """

    def test_apply_single_replacement_refetches_match_font(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The function must fetch a fresh resolver from the match's font,
        even if the caller passes a resolver for a different font."""
        from unittest.mock import MagicMock

        from pdf_edit_engine import surgeon

        src = tmp_path / "src.pdf"
        assert _build_identity_h_pdf(
            src,
            title_pattern="single_tj",
            title_text="Acme Corporation",
            body_text="body",
            extra_corpus="Nova Industries",
        )
        match = _title_match(str(src), "Acme Corporation")
        match_font = match.characters[0].font_name

        real_get = surgeon._get_font_resolver
        calls: list[str] = []

        def tracking_get(page: pikepdf.Page, font_name: str) -> object:
            calls.append(font_name)
            return real_get(page, font_name)

        monkeypatch.setattr(surgeon, "_get_font_resolver", tracking_get)

        # Deliberately stale resolver: a MagicMock that pretends any text
        # is encodable. Without the fix, _apply_single_replacement would
        # trust it and never refetch.
        wrong_resolver = MagicMock()
        wrong_resolver.can_encode.return_value = (True, [])
        wrong_resolver.byte_width = 2

        pdf = pikepdf.Pdf.open(str(src))
        try:
            page = pdf.pages[match.page_number]
            ops = list(pikepdf.parse_content_stream(page))
            result, _ = surgeon._apply_single_replacement(
                pdf,
                page,
                ops,
                match,
                "Nova Industries",
                wrong_resolver,
                surgeon._width_cache,
                dry_run=True,
            )
        finally:
            pdf.close()

        assert match_font in calls, (
            f"_apply_single_replacement did not refetch the resolver for "
            f"the match's font ({match_font}). Cross-font pollution regression. "
            f"Observed calls: {calls}"
        )
        assert result.success, f"Replacement failed via refetched resolver: {result}"

    def test_replace_all_real_chrome_pdf_if_available(self, tmp_path: Path) -> None:
        """End-to-end guard: real Chrome PDF with 4 Identity-H fonts per page.

        Skipped when the fixture is absent (CI). When present, verifies that
        ``replace_all`` produces no Mode-1 or Mode-2 garble tokens, proving
        the cross-font resolver pollution is fixed on the exact PDF that
        surfaced the bug.
        """
        real_pdf = Path(__file__).parent.parent / ".claude" / "Acme Corporation —Chrome.pdf"
        if not real_pdf.exists():
            pytest.skip("real Chrome PDF not present (see ARY-280 for corpus commit)")

        out = tmp_path / "chrome_out.pdf"
        results = replace_all(str(real_pdf), "Acme Corporation", "Nova Industries", str(out))
        assert len(results) == 6
        assert all(r.success for r in results), [(r.success, r.font_action) for r in results]

        text = get_text(str(out))
        assert text.count("Nova Industries") >= 4, text[:400]
        assert "Acme Corporation" not in text
        for tok in ("ova ndustries", "1ova", "1ndustries", ",ndustries", "$ndustries"):
            assert tok not in text, f"Mode-2 garble token {tok!r} in output"
        for tok in ("N o v a", "No v a", "In d u s"):
            assert tok not in text, f"Mode-1 garble token {tok!r} in output"
