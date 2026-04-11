"""Validation harness — compare interpreter output against pdfminer.six."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import TypedDict

import pikepdf
import pytest
from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTChar

from pdf_edit_engine.locator import ContentStreamInterpreter

CORPUS_DIR = Path(__file__).parent / "corpus"
RESUME_PDF = str(CORPUS_DIR / "Aryan_BV_Resume_2026.pdf")

_NEW_CORPUS = [
    ("chrome_webpage.pdf", 90.0),
    ("word_document.pdf", 90.0),
    ("gdocs_document.pdf", 90.0),
]

_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


class CharInfo(TypedDict):
    char: str
    x: float
    y: float
    width: float
    height: float
    fontname: str
    fontsize: float


# ── Extraction helpers ────────────────────────────────────────────────


def _extract_with_pdfminer(pdf_path: str) -> list[CharInfo]:
    """Extract characters with positions using pdfminer.six.

    Walks the layout tree recursively to reach LTChar objects nested
    inside LTTextBox → LTTextLine → LTChar.
    """
    chars: list[CharInfo] = []

    def _walk(element: object) -> None:
        if isinstance(element, LTChar):
            text = element.get_text()
            x0 = element.bbox[0]
            total_w = element.bbox[2] - element.bbox[0]
            h = element.bbox[3] - element.bbox[1]
            y = element.bbox[1]
            # Split ligatures (e.g. "ti", "fi") into individual chars
            n = len(text)
            char_w = total_w / n if n > 0 else total_w
            for ci, ch in enumerate(text):
                chars.append(
                    CharInfo(
                        char=ch,
                        x=x0 + ci * char_w,
                        y=y,
                        width=char_w,
                        height=h,
                        fontname=element.fontname,
                        fontsize=element.size,
                    )
                )
        elif hasattr(element, "__iter__"):
            for child in element:  # type: ignore[union-attr]
                _walk(child)

    for page_layout in extract_pages(pdf_path, laparams=LAParams()):
        for element in page_layout:
            _walk(element)

    return chars


def _extract_with_interpreter(pdf_path: str) -> list[CharInfo]:
    """Extract characters using our ContentStreamInterpreter.

    Adjusts y-coordinate from baseline to approximate bbox-bottom so it
    is comparable to pdfminer's ``LTChar.bbox[1]``.  Resolves font
    resource names (e.g. "F1") to PostScript names with subset prefix
    so font comparison is meaningful.
    """
    chars: list[CharInfo] = []
    with pikepdf.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            # Build resource-name → BaseFont mapping for this page
            font_name_map: dict[str, str] = {}
            try:
                font_res = page["/Resources"]["/Font"]
                for key in list(font_res.keys()):
                    name = str(key).lstrip("/")
                    bf = font_res[key].get("/BaseFont")
                    if bf is not None:
                        font_name_map[name] = str(bf).lstrip("/")
            except (KeyError, TypeError):
                pass

            interp = ContentStreamInterpreter(page, page_num)
            elements = interp.interpret()
            for elem in elements:
                if elem.type != "text" or not elem.characters:
                    continue
                for ch in elem.characters:
                    # Our page_y is the text baseline; pdfminer uses
                    # bbox bottom ≈ baseline − descent (≈ font_size×0.25).
                    adjusted_y = ch.page_y - ch.font_size * 0.25
                    ps_name = font_name_map.get(ch.font_name, ch.font_name)
                    chars.append(
                        CharInfo(
                            char=ch.unicode_char,
                            x=ch.page_x,
                            y=adjusted_y,
                            width=ch.width,
                            height=ch.height,
                            fontname=ps_name,
                            fontsize=ch.font_size,
                        )
                    )
    return chars


# ── Comparison engine ─────────────────────────────────────────────────


class _ComparisonResult:
    """Aggregated comparison statistics."""

    def __init__(self) -> None:
        self.char_matched: int = 0  # same char, any distance
        self.char_mismatch: int = 0  # different char at closest position
        self.unmatched: int = 0  # no counterpart found
        self.position_close: int = 0  # matched char within 1pt
        self.position_total: int = 0  # total matched chars checked
        self.font_matched: int = 0
        self.font_total: int = 0

    @property
    def char_agreement(self) -> float:
        total = self.char_matched + self.char_mismatch + self.unmatched
        return self.char_matched / total * 100.0 if total > 0 else 0.0

    @property
    def position_agreement(self) -> float:
        return self.position_close / self.position_total * 100.0 if self.position_total > 0 else 0.0

    @property
    def font_agreement(self) -> float:
        return self.font_matched / self.font_total * 100.0 if self.font_total > 0 else 0.0


def _compare(
    ours: list[CharInfo],
    pdfminer: list[CharInfo],
) -> _ComparisonResult:
    """Compare interpreter output against pdfminer via proximity matching.

    For each of our characters, find the nearest unused pdfminer
    character **with the same Unicode value** (no distance limit).
    If found, count as ``char_matched`` and measure position accuracy
    separately.  If no same-char counterpart remains, count as
    ``char_mismatch`` or ``unmatched``.
    """
    result = _ComparisonResult()

    # Index pdfminer chars by Unicode char for fast lookup
    from collections import defaultdict

    pm_by_char: dict[str, list[int]] = defaultdict(list)
    for pi, pc in enumerate(pdfminer):
        pm_by_char[pc["char"]].append(pi)

    pm_used = [False] * len(pdfminer)

    for oc in ours:
        # Find nearest pdfminer char with same Unicode
        best_idx = -1
        best_dist = float("inf")
        for pi in pm_by_char.get(oc["char"], []):
            if pm_used[pi]:
                continue
            pc = pdfminer[pi]
            dist = math.hypot(oc["x"] - pc["x"], oc["y"] - pc["y"])
            if dist < best_dist:
                best_dist = dist
                best_idx = pi

        if best_idx != -1:
            pm_used[best_idx] = True
            result.char_matched += 1

            # Position agreement (within ±1.0pt)
            result.position_total += 1
            if best_dist <= 1.0:
                result.position_close += 1

            # Font agreement (strip subset prefix)
            pc = pdfminer[best_idx]
            result.font_total += 1
            our_font = _SUBSET_PREFIX.sub("", oc["fontname"])
            pm_font = _SUBSET_PREFIX.sub("", pc["fontname"])
            if our_font == pm_font:
                result.font_matched += 1
        else:
            # No same-char counterpart — count by surplus type
            result.unmatched += 1

    return result


# ── Tests ─────────────────────────────────────────────────────────────


@pytest.mark.skipif(not Path(RESUME_PDF).exists(), reason="Aryan_BV_Resume_2026.pdf not in corpus")
class TestPdfminerValidation:
    """Compare our interpreter's text extraction against pdfminer.six."""

    @pytest.fixture(scope="class")
    def comparison(self) -> _ComparisonResult:
        ours = _extract_with_interpreter(RESUME_PDF)
        pdfminer_chars = _extract_with_pdfminer(RESUME_PDF)
        return _compare(ours, pdfminer_chars)

    def test_character_agreement(self, comparison: _ComparisonResult) -> None:
        pct = comparison.char_agreement
        print(f"\nCharacter agreement: {pct:.1f}%")
        print(
            f"  char_matched={comparison.char_matched}, "
            f"char_mismatch={comparison.char_mismatch}, "
            f"unmatched={comparison.unmatched}"
        )
        assert pct > 99.0, f"Character agreement {pct:.1f}% < 99% target"

    def test_position_agreement(self, comparison: _ComparisonResult) -> None:
        pct = comparison.position_agreement
        far = comparison.position_total - comparison.position_close
        print(f"\nPosition agreement (±1.0pt): {pct:.1f}%")
        print(f"  close={comparison.position_close}, far={far}")
        assert pct > 95.0, f"Position agreement {pct:.1f}% < 95% target"

    def test_font_agreement(self, comparison: _ComparisonResult) -> None:
        pct = comparison.font_agreement
        print(f"\nFont agreement: {pct:.1f}%")
        print(f"  matched={comparison.font_matched}, total={comparison.font_total}")
        assert pct > 98.0, f"Font agreement {pct:.1f}% < 98% target"

    def test_no_garbled_output(self) -> None:
        """Interpreter should produce zero non-printable characters."""
        ours = _extract_with_interpreter(RESUME_PDF)
        bad_chars: list[str] = []
        for ci in ours:
            ch = ci["char"]
            if not ch.isprintable() and ch not in ("\n", "\t", " "):
                bad_chars.append(repr(ch))
        assert bad_chars == [], f"Non-printable characters found: {bad_chars[:20]}"


class TestNewCorpusValidation:
    """Validation against Chrome, Word, and Google Docs PDFs (relaxed thresholds)."""

    @pytest.mark.parametrize(
        ("filename", "threshold"),
        _NEW_CORPUS,
        ids=lambda x: x if isinstance(x, str) else "",
    )
    def test_character_agreement(
        self,
        filename: str,
        threshold: float,
    ) -> None:
        pdf_path = str(CORPUS_DIR / filename)
        if not Path(pdf_path).exists():
            pytest.skip(f"{filename} not in corpus")
        ours = _extract_with_interpreter(pdf_path)
        pm = _extract_with_pdfminer(pdf_path)
        result = _compare(ours, pm)
        pct = result.char_agreement
        print(
            f"\n{filename} character agreement: {pct:.1f}%"
            f" (matched={result.char_matched}, unmatched={result.unmatched})",
        )
        assert pct > threshold, f"{filename} character agreement {pct:.1f}% < {threshold}%"
