"""Tests for TJ array fragment reconstruction."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_edit_engine.encoding import FontResolverCache
from pdf_edit_engine.fragments import TJReconstructed, TJReconstructor

CORPUS_DIR = Path(__file__).parent / "corpus"
RESUME_PDF = CORPUS_DIR / "resume_aryan.pdf"


# ── Helpers ────────────────────────────────────────────────────────────


class _WinAnsiFontResolver:
    """Minimal mock that behaves like a WinAnsi FontResolver for testing."""

    def decode(self, raw_bytes: bytes) -> str:
        return raw_bytes.decode("latin-1")

    @property
    def is_cid_font(self) -> bool:
        return False

    @property
    def byte_width(self) -> int:
        return 1


def _make_string(text: str) -> pikepdf.String:
    """Create a pikepdf.String from Latin-1 encodable text."""
    return pikepdf.String(text.encode("latin-1"))


def _make_reconstructor() -> TJReconstructor:
    """Create a TJReconstructor with a simple WinAnsi-like resolver."""
    return TJReconstructor(_WinAnsiFontResolver())  # type: ignore[arg-type]


# ── TestTJReconstructor ───────────────────────────────────────────────


class TestTJReconstructor:
    """Tests for TJReconstructor.reconstruct()."""

    def test_simple_single_fragment(self) -> None:
        r = _make_reconstructor()
        result = r.reconstruct([_make_string("Hello")])
        assert result.full_text == "Hello"
        assert len(result.fragments) == 1
        assert result.fragments[0].text == "Hello"
        assert result.fragments[0].array_index == 0
        assert result.fragments[0].char_offset == 0
        assert result.fragments[0].kerning_before == 0.0

    def test_kerned_fragments_join(self) -> None:
        r = _make_reconstructor()
        tj: list[object] = [
            _make_string("Do"), -29,
            _make_string("c"), -1,
            _make_string("umen"), 30,
            _make_string("tation"),
        ]
        result = r.reconstruct(tj)
        assert result.full_text == "Documentation"

    def test_kerning_values_preserved(self) -> None:
        r = _make_reconstructor()
        tj: list[object] = [
            _make_string("Do"), -29,
            _make_string("c"), -1,
            _make_string("umen"),
        ]
        result = r.reconstruct(tj)
        assert result.fragments[0].kerning_before == 0.0
        assert result.fragments[1].kerning_before == -29.0
        assert result.fragments[2].kerning_before == -1.0

    def test_char_offsets_sequential(self) -> None:
        r = _make_reconstructor()
        tj: list[object] = [
            _make_string("AB"), -10,
            _make_string("CD"), -5,
            _make_string("EF"),
        ]
        result = r.reconstruct(tj)
        assert result.fragments[0].char_offset == 0
        assert result.fragments[1].char_offset == 2
        assert result.fragments[2].char_offset == 4
        assert result.full_text == "ABCDEF"

    def test_empty_fragment_skipped(self) -> None:
        r = _make_reconstructor()
        tj: list[object] = [
            _make_string(""), -50,
            _make_string("text"),
        ]
        result = r.reconstruct(tj)
        assert result.full_text == "text"
        assert len(result.fragments) == 1
        assert result.fragments[0].kerning_before == -50.0

    def test_numbers_only_returns_empty(self) -> None:
        r = _make_reconstructor()
        result = r.reconstruct([-50, 100, -30])
        assert result.full_text == ""
        assert len(result.fragments) == 0

    def test_single_char_fragments(self) -> None:
        r = _make_reconstructor()
        tj: list[object] = [
            _make_string("H"), -5,
            _make_string("e"), -3,
            _make_string("l"), -2,
            _make_string("l"), -1,
            _make_string("o"),
        ]
        result = r.reconstruct(tj)
        assert result.full_text == "Hello"
        assert len(result.fragments) == 5

    def test_raw_bytes_preserved(self) -> None:
        r = _make_reconstructor()
        tj: list[object] = [_make_string("AB"), -10, _make_string("CD")]
        result = r.reconstruct(tj)
        assert result.fragments[0].raw_bytes == b"AB"
        assert result.fragments[1].raw_bytes == b"CD"

    def test_consecutive_kerning_accumulates(self) -> None:
        r = _make_reconstructor()
        tj: list[object] = [-10, -20, _make_string("X")]
        result = r.reconstruct(tj)
        assert result.fragments[0].kerning_before == -30.0

    def test_empty_array(self) -> None:
        r = _make_reconstructor()
        result = r.reconstruct([])
        assert result.full_text == ""
        assert len(result.fragments) == 0


# ── TestLocateInFragments ─────────────────────────────────────────────


class TestLocateInFragments:
    """Tests for TJReconstructed.locate_in_fragments()."""

    @pytest.fixture()
    def doc_result(self) -> TJReconstructed:
        r = _make_reconstructor()
        tj: list[object] = [
            _make_string("Do"), -29,
            _make_string("c"), -1,
            _make_string("umen"), 30,
            _make_string("tation"),
        ]
        return r.reconstruct(tj)

    def test_within_single_fragment(self, doc_result: TJReconstructed) -> None:
        # "umen" is entirely in fragment index 2 (chars 3-7)
        hits = doc_result.locate_in_fragments(3, 7)
        assert len(hits) == 1
        frag, start, end = hits[0]
        assert frag.text == "umen"
        assert start == 0
        assert end == 4

    def test_spanning_two_fragments(self, doc_result: TJReconstructed) -> None:
        # "Doc" spans "Do" (chars 0-2) and "c" (char 2)
        hits = doc_result.locate_in_fragments(0, 3)
        assert len(hits) == 2
        assert hits[0][0].text == "Do"
        assert hits[0][1:] == (0, 2)
        assert hits[1][0].text == "c"
        assert hits[1][1:] == (0, 1)

    def test_spanning_three_fragments(self, doc_result: TJReconstructed) -> None:
        # "cument" spans "c" (char 2), "umen" (chars 3-7), "tation"[0] (char 7)
        hits = doc_result.locate_in_fragments(2, 8)
        assert len(hits) == 3

    def test_full_range(self, doc_result: TJReconstructed) -> None:
        hits = doc_result.locate_in_fragments(0, len(doc_result.full_text))
        assert len(hits) == 4

    def test_roundtrip_every_character(self, doc_result: TJReconstructed) -> None:
        """Every character in full_text maps to exactly one fragment position."""
        for i in range(len(doc_result.full_text)):
            hits = doc_result.locate_in_fragments(i, i + 1)
            assert len(hits) == 1, f"char {i} ({doc_result.full_text[i]!r}) hit {len(hits)} frags"
            frag, start, end = hits[0]
            assert end - start == 1
            assert frag.text[start] == doc_result.full_text[i]

    def test_empty_range_returns_nothing(self, doc_result: TJReconstructed) -> None:
        hits = doc_result.locate_in_fragments(3, 3)
        assert len(hits) == 0


# ── TestWithResumePDF ─────────────────────────────────────────────────


class TestWithResumePDF:
    """Integration tests using real TJ arrays from resume_aryan.pdf."""

    @pytest.fixture()
    def first_tj(self) -> TJReconstructed:
        """Reconstruct the first TJ array from the resume."""
        pdf = pikepdf.open(str(RESUME_PDF))
        page = pdf.pages[0]
        cache = FontResolverCache()
        ops = pikepdf.parse_content_stream(page)
        current_font: str | None = None
        for operands, operator in ops:
            op = str(operator)
            if op == "Tf":
                current_font = str(operands[0]).lstrip("/")
            elif op == "TJ" and current_font is not None:
                resolver = cache.get_resolver(page, current_font)
                reconstructor = TJReconstructor(resolver)
                tj_array = list(operands[0])
                result = reconstructor.reconstruct(tj_array)
                pdf.close()
                return result
        pdf.close()
        pytest.skip("No TJ operators found in resume PDF")

    def test_real_tj_produces_unicode(self, first_tj: TJReconstructed) -> None:
        assert len(first_tj.full_text) > 0
        # Should be readable text, not raw CID bytes
        assert all(ord(c) < 0xFFFF for c in first_tj.full_text)

    def test_real_tj_has_fragments(self, first_tj: TJReconstructed) -> None:
        assert len(first_tj.fragments) > 0

    def test_real_tj_fragment_offsets_consistent(self, first_tj: TJReconstructed) -> None:
        """Fragment char_offsets should be sequential and cover full_text."""
        total = 0
        for frag in first_tj.fragments:
            assert frag.char_offset == total
            total += len(frag.text)
        assert total == len(first_tj.full_text)

    def test_real_tj_locate_roundtrip(self, first_tj: TJReconstructed) -> None:
        """Every character maps back to a valid fragment."""
        for i in range(len(first_tj.full_text)):
            hits = first_tj.locate_in_fragments(i, i + 1)
            assert len(hits) == 1
            frag, start, end = hits[0]
            assert frag.text[start] == first_tj.full_text[i]
