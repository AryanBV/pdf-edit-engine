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


def _find_ttf_for_cidfont() -> Path | None:
    """Find a TrueType font for inline Identity-H PDF construction."""
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/ARIAL.TTF"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _build_identity_h_pdf(
    out_path: Path,
    *,
    title_text: str = "Acme Corporation",
    body_text: str = "This is body text with Acme Corporation in it.",
    extra_corpus: str = "Nova Industries Worldwide",
    title_pattern: str = "per_glyph_tm",
    title_font_size: float = 24.0,
    body_font_size: float = 12.0,
    omit_chars_from_subset: str = "",
) -> bool:
    """Construct an Identity-H PDF with a configurable title emission pattern.

    Args:
        out_path: Output path.
        title_text: Text for the 24pt title line.
        body_text: Text for the 12pt body line (always single Tj).
        extra_corpus: Additional characters to include in the font subset so
            that replacements can succeed without triggering Tier 2 extension.
        title_pattern: One of "single_tj", "per_glyph_tm", "per_char_tj_array",
            "multi_char_tj_array".
        title_font_size: Font size for the title line.
        body_font_size: Font size for the body line.
        omit_chars_from_subset: Characters to deliberately exclude from the
            font subset (used to test Tier 2 extension triggering).

    Returns:
        True if the PDF was built, False if no TTF font is available.
    """
    ttf_path = _find_ttf_for_cidfont()
    if ttf_path is None:
        return False

    from fontTools import ttLib
    from fontTools.subset import Subsetter

    full = ttLib.TTFont(str(ttf_path))
    cmap_table = full["cmap"]
    cp_map: dict[int, str] = {}
    for table in cmap_table.tables:
        if table.platformID == 3 and table.platEncID == 1:
            cp_map = table.cmap
            break
    glyph_order = full.getGlyphOrder()
    name_to_gid = {n: i for i, n in enumerate(glyph_order)}
    hmtx = full["hmtx"]
    units_per_em = full["head"].unitsPerEm

    corpus = set(title_text + body_text + extra_corpus) - set(omit_chars_from_subset)
    cp_to_gid: dict[int, int] = {}
    used_gids: set[int] = set()
    for ch in sorted(corpus):
        cp = ord(ch)
        gname = cp_map.get(cp)
        if gname and gname in name_to_gid:
            gid = name_to_gid[gname]
            cp_to_gid[cp] = gid
            used_gids.add(gid)

    # Subset the font
    import io as _io

    sub_font = ttLib.TTFont(str(ttf_path))
    Subsetter().populate(
        glyphs=[glyph_order[gid] for gid in sorted(used_gids) if gid < len(glyph_order)]
    )
    subsetter = Subsetter()
    subsetter.populate(
        glyphs=[glyph_order[gid] for gid in sorted(used_gids) if gid < len(glyph_order)]
    )
    subsetter.subset(sub_font)
    buf = _io.BytesIO()
    sub_font.save(buf)
    font_bytes = buf.getvalue()

    # Build /W array
    w_flat: list[object] = []
    for gid in sorted(used_gids):
        gname = glyph_order[gid] if gid < len(glyph_order) else ".notdef"
        try:
            advance = float(hmtx[gname][0])
        except (KeyError, IndexError):
            advance = 500.0
        w_1000 = round(advance * 1000 / units_per_em)
        w_flat.append(gid)
        w_flat.append(pikepdf.Array([w_1000]))

    # ToUnicode CMap
    bfchar_lines = [f"<{cp_to_gid[cp]:04X}> <{cp:04X}>" for cp in sorted(cp_to_gid)]
    tounicode_str = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\nbegincmap\n"
        "/CIDSystemInfo\n"
        "<< /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n"
        "/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
    )
    for j in range(0, len(bfchar_lines), 100):
        chunk = bfchar_lines[j : j + 100]
        tounicode_str += f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar\n"
    tounicode_str += "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"

    def encode_text(text: str) -> bytes:
        return bytes(
            b
            for cp in (ord(c) for c in text)
            for b in [
                (cp_to_gid.get(cp, 0) >> 8) & 0xFF,
                cp_to_gid.get(cp, 0) & 0xFF,
            ]
        )

    def text_advance(text: str, size: float) -> float:
        total = 0.0
        for ch in text:
            gid = cp_to_gid.get(ord(ch), 0)
            gname = glyph_order[gid] if gid < len(glyph_order) else ".notdef"
            try:
                raw = float(hmtx[gname][0])
            except (KeyError, IndexError):
                raw = 500.0
            total += raw * size / units_per_em
        return total

    # Build content stream
    lines: list[str] = ["BT"]

    lines.append(f"/F1 {title_font_size} Tf")
    title_x = 72.0
    title_y = 730.0

    if title_pattern == "single_tj":
        lines.append(f"1 0 0 1 {title_x} {title_y} Tm")
        lines.append(f"<{encode_text(title_text).hex().upper()}> Tj")
    elif title_pattern == "per_glyph_tm":
        cursor_x = title_x
        i = 0
        while i < len(title_text):
            cluster = title_text[i : i + 2]
            lines.append(f"1 0 0 1 {cursor_x:.4f} {title_y} Tm")
            lines.append(f"<{encode_text(cluster).hex().upper()}> Tj")
            cursor_x += text_advance(cluster, title_font_size)
            i += 2
    elif title_pattern == "per_char_tj_array":
        lines.append(f"1 0 0 1 {title_x} {title_y} Tm")
        parts = ["["]
        for ch in title_text:
            parts.append(f"<{encode_text(ch).hex().upper()}>")
        parts.append("] TJ")
        lines.append("".join(parts))
    elif title_pattern == "multi_char_tj_array":
        lines.append(f"1 0 0 1 {title_x} {title_y} Tm")
        chunks = ["Acm", "e Co", "rpo", "ration"]
        parts = ["["]
        for j, chunk in enumerate(chunks):
            parts.append(f"<{encode_text(chunk).hex().upper()}>")
            if j < len(chunks) - 1:
                parts.append(" -15 ")
        parts.append("] TJ")
        lines.append("".join(parts))
    else:
        raise ValueError(f"Unknown title_pattern: {title_pattern}")

    lines.append(f"/F1 {body_font_size} Tf")
    lines.append("1 0 0 1 72 680 Tm")
    lines.append(f"<{encode_text(body_text).hex().upper()}> Tj")
    lines.append("ET")
    content_stream = "\n".join(lines).encode("latin-1")

    # Assemble PDF
    pdf = pikepdf.Pdf.new()
    font_stream = pikepdf.Stream(pdf, font_bytes)
    font_stream["/Length1"] = len(font_bytes)

    raw_ps = full.get("name").getDebugName(6) or "ArialMT"
    ps_name = str(raw_ps)
    bbox = [
        full["head"].xMin,
        full["head"].yMin,
        full["head"].xMax,
        full["head"].yMax,
    ]
    bbox_1000 = [round(v * 1000 / units_per_em) for v in bbox]

    font_descriptor = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/" + ps_name),
            "/Flags": 4,
            "/FontBBox": pikepdf.Array(bbox_1000),
            "/ItalicAngle": 0,
            "/Ascent": round(full["OS/2"].sTypoAscender * 1000 / units_per_em),
            "/Descent": round(full["OS/2"].sTypoDescender * 1000 / units_per_em),
            "/CapHeight": round(full["OS/2"].sCapHeight * 1000 / units_per_em),
            "/StemV": 80,
            "/FontFile2": font_stream,
        }
    )
    cid_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/BaseFont": pikepdf.Name("/" + ps_name),
            "/CIDSystemInfo": pikepdf.Dictionary(
                {
                    "/Registry": pikepdf.String("Adobe"),
                    "/Ordering": pikepdf.String("Identity"),
                    "/Supplement": 0,
                }
            ),
            "/FontDescriptor": pdf.make_indirect(font_descriptor),
            "/DW": 1000,
            "/W": pikepdf.Array(w_flat),
            "/CIDToGIDMap": pikepdf.Name("/Identity"),
        }
    )
    tounicode_stream = pikepdf.Stream(pdf, tounicode_str.encode("latin-1"))
    type0_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/BaseFont": pikepdf.Name("/" + ps_name),
            "/Encoding": pikepdf.Name("/Identity-H"),
            "/DescendantFonts": pikepdf.Array([pdf.make_indirect(cid_font)]),
            "/ToUnicode": tounicode_stream,
        }
    )
    page_dict = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
            "/Resources": pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(type0_font)}),
                }
            ),
            "/Contents": pikepdf.Stream(pdf, content_stream),
        }
    )
    pdf.pages.append(pikepdf.Page(page_dict))
    pdf.save(str(out_path))
    pdf.close()
    full.close()
    return True


def _title_match(pdf_path: str, text: str) -> TextMatch:
    """Return the match for `text` with the largest font size (the title)."""
    matches = find(pdf_path, text)
    assert matches, f"No match for {text!r} in {pdf_path}"
    return max(matches, key=lambda m: m.characters[0].font_size)


_no_ttf = pytest.mark.skipif(
    _find_ttf_for_cidfont() is None,
    reason="no TrueType font available for inline Identity-H PDF construction",
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

    def test_tier2_misalignment_aborts_cleanly(self, tmp_path: Path) -> None:
        """ARY-276 F2: Tier 2 extension must fail loudly, not silently corrupt."""
        src = tmp_path / "in.pdf"
        out = tmp_path / "out.pdf"
        # Deliberately omit 'N' from the subset AND from extra_corpus so
        # the embedded font's cmap does not cover 'N'.  Replacement
        # with "Nova" will need to trigger Tier 2 extension.
        ok = _build_identity_h_pdf(
            src,
            title_pattern="single_tj",
            extra_corpus="",
            omit_chars_from_subset="NIvduswW",
        )
        assert ok

        match = _title_match(str(src), "Acme Corporation")
        result = replace(str(src), match, "Nova Industries", str(out))
        # The guard should cause a clean failure — either:
        #   - resolver raised KeyError during encode (before extension)
        #     and the fallback path returned success=False, OR
        #   - Tier 2 alignment guard raised FontNotFoundError,
        #     handled at surgeon.py:454 → success=False, font_action="failed"
        # In any case, NO silent corruption, and the output either does
        # not exist or does not contain silently-wrong glyphs.
        if result.success:
            text = get_text(str(out))
            assert "Nova Industries" in text, f"silent corruption: got {text!r}"
        else:
            assert result.font_action == "failed"
