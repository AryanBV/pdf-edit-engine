"""Tests for the OperatorSurgeon module — PDF content stream text replacement."""

from __future__ import annotations

import os
from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.errors import OperatorError, PDFEditError
from pdf_edit_engine.locator import find, get_fonts, get_text
from pdf_edit_engine.models import Edit, TextMatch
from pdf_edit_engine.surgeon import (
    _kerning_decision,
    batch_replace,
    replace,
    replace_all,
)

CORPUS_DIR = Path(__file__).parent / "corpus"
RESUME_PDF = str(CORPUS_DIR / "Aryan_BV_Resume_2026.pdf")
SIMPLE_PDF = str(CORPUS_DIR / "reportlab_simple.pdf")
MULTIPAGE_PDF = str(CORPUS_DIR / "reportlab_multipage.pdf")

# M-10: out-of-repo SOW fixture path. CI (and contributors without the
# marketing fixture on disk) override via the M10_SOW_PDF env-var; the
# default points at the original on-disk location so the launch-gate
# author's local runs are unchanged. Mirrors the RESUME_PDF precedent
# above. The skipif at the use-site keeps the test SKIPPED when the
# resolved path is missing, so the default state in CI is unchanged.
_M10_SOW = os.environ.get("M10_SOW_PDF", "C:/New Project/pdf-marketing/m10-launch/sow.pdf")

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

    def test_overflow_emits_typed_degradation(self, tmp_path: Path) -> None:
        """IMP-2: when overflow_detected flips True on the simple-
        replacement path the surgeon emits a typed
        ``overflow_shift_clamped`` Degradation (severity=warning)
        alongside the warnings-list entry. Pre-fix the flag was set
        but no typed Degradation was appended, so callers iterating
        ``fidelity_report.degradations`` (the v0.1.3 surfacing layer)
        could not distinguish "overflowed" from "clean replacement"
        without falling back to the legacy warnings list.

        Uses ``reflow=False`` to force the simple-replacement path.
        With reflow enabled, a wider-than-original replacement routes
        through ``reflow_paragraph`` which already emits its own typed
        overflow Degradations (v0.1.3 Phase 6). The IMP-2 site is the
        simple-replacement horizontal-overflow detection at the tail
        of ``_apply_single_replacement``.
        """
        match = _first_match(SIMPLE_PDF, "Test")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "A" * 200, out, reflow=False)

        assert result.success is True
        assert result.fidelity_report.overflow_detected is True
        assert any(
            d.kind == "overflow_shift_clamped" and d.severity == "warning"
            for d in result.fidelity_report.degradations
        ), (
            "IMP-2: overflow_detected=True must imply at least one "
            "Degradation(kind='overflow_shift_clamped', severity='warning'); "
            f"got {result.fidelity_report.degradations!r}"
        )


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
        """Replacing with characters not in font returns success=False.

        v0.1.3 (Phase 4): also asserts the lying-success-path fix \u2014
        font_preserved must be False (was buggy True pre-v0.1.3) and
        degradations must contain a font_extension_failed entry.

        v0.1.3 Phase 13 note: this test exercises the **non-CID
        extension's no-AGL-name failure path**. SIMPLE_PDF
        (`reportlab_simple.pdf`) is a /TrueType + /WinAnsiEncoding font
        which Phase 13's dispatcher routes to `_extend_simple_tier_one_five`
        rather than rejecting at the `is_cid_font` gate (now dropped).
        The replacement chars are CJK (U+4F60 \u4f60, U+597D \u597d),
        which `_glyph_name_for_codepoint` resolves to `uni4F60` and
        `uni597D` (no AGL entry exists for these codepoints). The
        downstream `_inject_glyph_in_place` then fails to source these
        glyphs from the system font (Carlito/Arial don't carry CJK
        outlines), and the call surfaces as `font_extension_failed`
        per `_FONT_EXTEND_FAIL_EXCS`. This is the v0.1.3 contract:
        extension is *attempted* on simple TrueType fonts, but
        legitimately fails when neither AGL nor system-font cmap covers
        the codepoint.
        """
        match = _first_match(SIMPLE_PDF, "Test")
        out = str(tmp_path / "output.pdf")
        result = replace(SIMPLE_PDF, match, "\u4f60\u597d", out)

        assert result.success is False
        assert result.font_action == "failed"
        assert len(result.fidelity_report.glyphs_missing) > 0

        # v0.1.3 lying-success-path fix (design doc \u00a73 rows 2/3):
        # font_preserved is now a computed @property \u2014 it must return
        # False whenever a FONT_AFFECTING_KINDS Degradation was emitted.
        # font_extension_failed is in FONT_AFFECTING_KINDS.
        assert result.fidelity_report.font_preserved is False, (
            "v0.1.3 INV-J-8 violated: font_preserved should be False on "
            "force-failed extension; was True pre-v0.1.3 (architectural lie)."
        )
        kinds = [d.kind for d in result.fidelity_report.degradations]
        assert "font_extension_failed" in kinds, (
            f"v0.1.3 INV-J-5 violated: expected font_extension_failed "
            f"degradation; got kinds={kinds}"
        )
        # Severity contract: error.
        ext_degs = [
            d for d in result.fidelity_report.degradations if d.kind == "font_extension_failed"
        ]
        assert all(d.severity == "error" for d in ext_degs), (
            "font_extension_failed must have severity=error per design doc \u00a73"
        )

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

    def test_not_found_returns_empty(self, tmp_path: Path) -> None:
        """Searching for nonexistent text returns empty list."""
        # Use the tmp_path fixture (not "/tmp/...") so the test works on
        # Windows runners where "/tmp/nope.pdf" resolves against the
        # current drive — e.g. "D:\tmp\nope.pdf" — and validate_output_path
        # refuses paths whose parent does not exist on the GH Actions image.
        out = str(tmp_path / "nope.pdf")
        results = replace_all(SIMPLE_PDF, "ZZZZZZZ", "YYYYYYY", out)
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

        from pdf_edit_engine.encoding import FontResolverCache
        from pdf_edit_engine.widths import GlyphWidthCache

        real_get = surgeon._get_font_resolver
        calls: list[str] = []

        def tracking_get(
            page: pikepdf.Page, font_name: str, resolver_cache: FontResolverCache
        ) -> object:
            calls.append(font_name)
            return real_get(page, font_name, resolver_cache)

        monkeypatch.setattr(surgeon, "_get_font_resolver", tracking_get)

        # Deliberately stale resolver: a MagicMock that pretends any text
        # is encodable. Without the fix, _apply_single_replacement would
        # trust it and never refetch.
        wrong_resolver = MagicMock()
        wrong_resolver.can_encode.return_value = (True, [])
        wrong_resolver.byte_width = 2

        # Per-call caches, mirroring the public-entrypoint contract (ARY-283).
        resolver_cache = FontResolverCache()
        width_cache = GlyphWidthCache()

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
                width_cache,
                resolver_cache,
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


@_no_ttf
class TestNarrowSubsetReplacement:
    """Regression test for ARY-282: replacing text with characters outside
    the embedded subset on a narrow Identity-H PDF must succeed end-to-end.

    Historical context: ``_calculate_new_width`` raises ``KeyError`` when
    ``resolver.encode`` meets a glyph absent from the embedded cmap, and
    the caller catches it to set ``needs_reflow=False``. The simple-replace
    path then runs, performs font extension in ``_apply_single_replacement``,
    and produces a correct replacement. The ARY-282 concern — that reflow
    was "silently disabled" — is benign for same-width replacements (the
    common case); simple-replace handles both encoding and placement.

    This test asserts the visible-behaviour invariant: the replacement
    succeeds, the output contains the new text, and the font action
    reflects that extension happened (``extended`` or ``kept``).
    """

    def test_narrow_subset_replacement_succeeds_via_simple_replace(self, tmp_path: Path) -> None:
        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        # Build an Identity-H PDF whose embedded cmap OMITS chars needed by
        # the replacement text.
        ok = _build_identity_h_pdf(
            src,
            title_pattern="single_tj",
            title_text="Acme Corp",
            body_text="body",
            extra_corpus="",
            omit_chars_from_subset="NIWLdu",
        )
        assert ok

        match = _title_match(str(src), "Acme Corp")
        # Similar-width replacement needing omitted glyphs.
        result = replace(str(src), match, "Nova Industries", str(out), reflow=True)
        assert result.success, f"ARY-282 regression: {result}"
        assert result.font_action == "extended", (
            f"ARY-282: font extension expected; got font_action={result.font_action}"
        )
        text = get_text(str(out))
        assert "Nova Industries" in text, f"ARY-282: output missing replacement: {text!r}"


class TestModuleCacheOwnership:
    """Regression tests for ARY-283: module-level cache deletion.

    After the v0.1.2 refactor, neither ``surgeon`` nor ``structural`` is
    allowed to hold a module-level ``FontResolverCache`` or
    ``GlyphWidthCache``.  Each public entrypoint now constructs its own
    caches at entry and threads them through helpers as explicit params,
    eliminating any cross-module staleness hazard.
    """

    def test_surgeon_has_no_module_level_caches(self) -> None:
        from pdf_edit_engine import surgeon

        for attr in ("_resolver_cache", "_width_cache", "_cached_pdf_path"):
            assert not hasattr(surgeon, attr), (
                f"surgeon.{attr} must not exist — Phase 1 (ARY-283) "
                f"removed all module-level cache state"
            )

    def test_structural_has_no_module_level_caches(self) -> None:
        from pdf_edit_engine import structural

        assert not hasattr(structural, "_resolver_cache"), (
            "structural._resolver_cache must not exist — Phase 1 (ARY-283) "
            "removed all module-level cache state"
        )

    def test_sequential_public_calls_share_no_state(self, tmp_path: Path) -> None:
        """Two back-to-back public calls on the same PDF must succeed
        independently.  The original ARY-283 concern was that mutations
        by one module would leave stale caches in another; with per-call
        caches this is structurally impossible to reproduce, and
        sequential calls should round-trip cleanly.
        """
        src = tmp_path / "in.pdf"
        mid = tmp_path / "mid.pdf"
        out = tmp_path / "out.pdf"
        assert _build_identity_h_pdf(
            src,
            title_pattern="single_tj",
            title_text="Acme Corporation",
            body_text="body",
            extra_corpus="Nova Industries Corp",
        )

        # First public call: replace → first output
        m1 = _title_match(str(src), "Acme Corporation")
        r1 = replace(str(src), m1, "Nova Industries", str(mid))
        assert r1.success, f"first call failed: {r1}"

        # Second public call: replace on the first call's output — must
        # open a fresh pdf, build its own caches, and succeed without
        # any stale state from call #1.
        m2 = _title_match(str(mid), "Nova Industries")
        r2 = replace(str(mid), m2, "Acme Co.", str(out))
        assert r2.success, f"second call failed: {r2}"

        # Shorter-than-original replacement keeps the text on one line
        # (no reflow-driven wrap). The invariant under test is simply:
        # both public calls produced a correct output. All three of
        # "Acme Co." present, "Nova Industries" gone, "Acme Corporation"
        # gone — proves no stale-state carry-over between calls.
        text = get_text(str(out))
        assert "Acme Co." in text, text
        assert "Nova Industries" not in text
        assert "Acme Corporation" not in text


# ── Phase 2 (v0.1.3) kerning deadzone boundary tests ────────────────────


class TestKerningDecisionDeadzone:
    """Boundary tests for ``surgeon._kerning_decision`` (Algo A, design doc §1).

    Symmetric 95-105 deadzone for Degradation emission. ±0.05 deadzone
    around 100 for Tz operator emission. Both deadzones are deterministic
    boundaries — these tests pin them exactly.
    """

    def test_factor_exactly_100_emits_no_tz_no_degradation(self) -> None:
        """factor == 100.0 → no Tz operator, no Degradation."""
        tz, deg = _kerning_decision(100.0)
        assert tz is None
        assert deg is None

    def test_factor_exactly_95_no_degradation(self) -> None:
        """Edge of compression deadzone: factor == 95.0 → Tz emitted, no Degradation."""
        tz, deg = _kerning_decision(95.0)
        assert tz == 95.0
        assert deg is None

    def test_factor_94_9_emits_kerning_compressed(self) -> None:
        """Just below compression deadzone: factor == 94.9 → kerning_compressed warning."""
        tz, deg = _kerning_decision(94.9)
        assert tz == 94.9
        assert deg is not None
        assert deg.kind == "kerning_compressed"
        assert deg.severity == "warning"
        assert "95" in deg.detail or "Tz" in deg.detail

    def test_factor_exactly_105_no_degradation(self) -> None:
        """Edge of widening deadzone: factor == 105.0 → Tz emitted, no Degradation."""
        tz, deg = _kerning_decision(105.0)
        assert tz == 105.0
        assert deg is None

    def test_factor_105_1_emits_kerning_widened(self) -> None:
        """Just above widening deadzone: factor == 105.1 → kerning_widened info."""
        tz, deg = _kerning_decision(105.1)
        assert tz == 105.1
        assert deg is not None
        assert deg.kind == "kerning_widened"
        assert deg.severity == "info"

    def test_factor_within_tz_deadzone_no_tz_emit(self) -> None:
        """factor within ±0.05 of 100 → no Tz operator emitted (clean op stack)."""
        for factor in (99.96, 100.0, 100.04):
            tz, deg = _kerning_decision(factor)
            assert tz is None, f"factor={factor} should be in Tz deadzone"
            # All within [95, 105] → no degradation either
            assert deg is None

    def test_factor_just_outside_tz_deadzone_emits_tz(self) -> None:
        """factor outside ±0.05 of 100 (but within 95-105) → Tz emitted, no Degradation."""
        for factor in (99.94, 100.06, 99.0, 101.0):
            tz, deg = _kerning_decision(factor)
            assert tz == factor, f"factor={factor} should emit Tz"
            assert deg is None, f"factor={factor} in 95-105 should not degrade"

    def test_factor_extreme_compression_no_refusal(self) -> None:
        """No refusal threshold (design doc §1) — extreme factors still produce Tz + Degradation."""
        tz, deg = _kerning_decision(50.0)
        assert tz == 50.0
        assert deg is not None
        assert deg.kind == "kerning_compressed"

    def test_factor_extreme_widening_no_refusal(self) -> None:
        """Symmetric to extreme compression — extreme widening also produces Tz."""
        tz, deg = _kerning_decision(150.0)
        assert tz == 150.0
        assert deg is not None
        assert deg.kind == "kerning_widened"


# ── Phase 13.5: M10 SOW e2e (the launch gate) ────────────────────────────


_need_m10_sow = pytest.mark.skipif(
    not Path(_M10_SOW).exists(),
    reason=f"M10 SOW not present at {_M10_SOW} (set M10_SOW_PDF to override)",
)


@_need_m10_sow
def test_simple_font_extension_via_m10_sow(tmp_path: Path) -> None:
    """Sarah Chen → Søren Müller on the M10 SOW (the actual launch gate).

    The M10 SOW's title block uses a /TrueType + /WinAnsiEncoding +
    /FontFile2 font (BCDEEE+Calibri-Bold). Pre-Phase-13, the
    ``is_cid_font`` gate at structural.py:864-865 short-circuited this
    case to ``return False`` and replace() emitted a
    font_extension_failed Degradation.

    Phase 13 wires in the non-CID Tier 1.5 path. This e2e is the
    contract-level proof that the launch gate now passes:

    - replace() returns success=True
    - font_action == "extended"
    - glyphs_missing == {ø, ü}  (the two accented glyphs in
      "Søren Müller" not in F1's embedded subset)
    - degradations contain font_coverage_substituted (severity=warning)
      since the outline source is a system font (Calibri-Bold or a
      metric-equivalent like Carlito-Bold)
    - degradations do NOT contain font_extension_failed
    - get_text(out) contains the new string

    Phase 13.4.2 verification (per Defect 5 in
    experiments/v013_block_3_execution/prompt-defects.md): the assertion
    ``"font_coverage_substituted" in kinds`` IS present below — Phase
    13.4.2 is satisfied by this assertion's existence, not by a separate
    addition.
    """
    match = _first_match(_M10_SOW, "Sarah Chen")
    out = str(tmp_path / "sow_edited.pdf")

    result = replace(_M10_SOW, match, "Søren Müller", out, reflow=False)

    assert result.success is True, (
        f"M10 SOW launch gate failed: {result.fidelity_report.degradations}"
    )
    assert result.font_action == "extended"
    assert set(result.fidelity_report.glyphs_missing) == {"ø", "ü"}
    kinds = [d.kind for d in result.fidelity_report.degradations]
    assert "font_coverage_substituted" in kinds, (
        f"expected font_coverage_substituted in degradations; got {kinds}"
    )
    assert "font_extension_failed" not in kinds, (
        f"font_extension_failed must not be present; got {kinds}"
    )

    # Rendering verification — output text must contain the new string.
    out_text = get_text(out)
    assert "Søren Müller" in out_text, (
        f"output PDF text does not contain 'Søren Müller'; first 200 chars: {out_text[:200]!r}"
    )
