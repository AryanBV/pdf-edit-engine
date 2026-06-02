"""RED tests for the DX honesty-taxonomy + safe-polish unit (v0.2.0).

These tests are written BEFORE the implementation (TDD). Every test in this
file is expected to FAIL for the "API not implemented yet" reason until the
honesty-taxonomy unit lands:

- B1: ``Degradation`` / ``DegradationKind`` / ``FONT_AFFECTING_KINDS`` /
  ``DEGRADATION_KINDS`` exported from the package root and present in
  ``__all__`` (currently NOT exported → ``ImportError`` / ``AttributeError``).
- B2: ``FidelityReport.summary()`` one-line human-readable rendering
  (method does not exist yet → ``AttributeError``).
- B3: ``FidelityReport.is_clean`` / ``.max_severity`` / ``.warnings()``
  accessors (do not exist yet → ``AttributeError``).
- B4: ``EditResult.to_dict()`` whose nested report dict carries the computed
  ``font_preserved`` (method does not exist yet → ``AttributeError``).
- B6: ``models.DEGRADATION_KINDS`` machine-enumerable tuple (== 30,
  == ``get_args(DegradationKind)``) (does not exist yet → ``ImportError`` /
  ``AttributeError``).
- B8: ``find()`` on an out-of-range page raises a ``PDFEditError`` subclass,
  NOT a bare ``IndexError`` (currently raises ``IndexError`` → RED).
- B9: ``FontNotFoundError`` message carries the documented remedy text
  ``pass full_font_path=<path>`` (current wording differs → RED).
- B10: the CFF-refusal message drops the internal ``slice-1`` jargon
  (current message contains ``slice-1`` → RED).

Run::

    .venv/Scripts/python.exe -m pytest tests/test_dx_taxonomy.py -q
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import tempfile
import typing

import pikepdf
import pytest

from pdf_edit_engine.errors import FontNotFoundError, PDFEditError
from pdf_edit_engine.fonts import extend_subset
from pdf_edit_engine.locator import find
from pdf_edit_engine.models import (
    Degradation as _Degradation,
)
from pdf_edit_engine.models import (
    DegradationKind,
    EditResult,
    FidelityReport,
)

# corpus_builders is the ``tests.corpus_builders`` package (the repo root is
# the import root; ``tests`` is a package — same convention as
# tests/invariants/test_c_11_cff_injection.py).
from tests.corpus_builders.cff_cid_injection import (  # noqa: E402
    build_cff_donor_bytes,
    build_namekeyed_otf_cff_pdf,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _clean_report() -> FidelityReport:
    """A report with no degradations, no substitution, no overflow."""
    return FidelityReport(
        font_substituted=None,
        overflow_detected=False,
        reflow_applied=False,
        glyphs_missing=[],
        degradations=[],
    )


def _report_with(
    *,
    degradations: list[_Degradation] | None = None,
    font_substituted: str | None = None,
    overflow_detected: bool = False,
) -> FidelityReport:
    return FidelityReport(
        font_substituted=font_substituted,
        overflow_detected=overflow_detected,
        reflow_applied=False,
        glyphs_missing=[],
        degradations=degradations or [],
    )


def _save_temp_pdf(pdf: pikepdf.Pdf) -> str:
    """Save a pikepdf into a fresh temp dir and return the path.

    Uses ``mkdtemp`` + an explicit path so pikepdf's atomic-overwrite can
    rename onto the target on Windows (a held-open ``NamedTemporaryFile``
    handle triggers ``PermissionError`` there).
    """
    d = tempfile.mkdtemp(prefix="dx_taxonomy_")
    path = os.path.join(d, "doc.pdf")
    pdf.save(path)
    return path


def _build_one_page_pdf() -> str:
    """A minimal 1-page PDF with a Base-14 Helvetica text run 'Hello x World'."""
    pdf = pikepdf.Pdf.new()
    content = b"BT /F1 12 Tf 72 700 Td (Hello x World) Tj ET"
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    page = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
            "/Resources": pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})}),
            "/Contents": pikepdf.Stream(pdf, content),
        }
    )
    pdf.pages.append(pikepdf.Page(page))
    path = _save_temp_pdf(pdf)
    pdf.close()
    return path


def _build_cid_glyf_pdf_synthetic_name() -> str:
    """A 1-page Identity-H CID *glyf* font with a name no system font matches.

    The ``/BaseFont`` ``ZZZSynthCIDFont-Regular`` resolves to no installed
    font and has no metric equivalent, so editing it to add a glyph not in
    the embedded subset (``C``) forces the CID glyf Tier 1.5 path to call the
    system-font lookup, which returns None and raises ``FontNotFoundError``
    (``fonts.py`` ``_inject_glyph_in_place`` sourcing). Deterministic and
    host-font-free (synthesised entirely via fontTools).
    """
    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]
    from fontTools.pens.ttGlyphPen import TTGlyphPen  # type: ignore[import-untyped]

    glyph_names = [".notdef", "cid00001", "cid00002"]
    cmap = {ord("A"): "cid00001", ord("B"): "cid00002"}
    glyf: dict[str, object] = {}
    for gn in glyph_names:
        pen = TTGlyphPen(None)
        if gn != ".notdef":
            pen.moveTo((50, 0))
            pen.lineTo((50, 700))
            pen.lineTo((450, 700))
            pen.lineTo((450, 0))
            pen.closePath()
        glyf[gn] = pen.glyph()

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(glyph_names)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyf)
    fb.setupHorizontalMetrics({gn: (500, 0) for gn in glyph_names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable(
        {
            "familyName": "ZZZSynthCID",
            "styleName": "Regular",
            "psName": "ZZZSynthCIDFont-Regular",
        }
    )
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, sCapHeight=700)
    fb.setupPost()
    fb.setupMaxp()
    buf = io.BytesIO()
    fb.font.save(buf)
    fb.font.close()
    font_bytes = buf.getvalue()

    pdf = pikepdf.Pdf.new()
    fs = pikepdf.Stream(pdf, font_bytes)
    fs["/Length1"] = len(font_bytes)
    fdsc = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/ZZZSynthCIDFont-Regular"),
                "/Flags": 4,
                "/FontBBox": pikepdf.Array([0, -200, 600, 800]),
                "/ItalicAngle": 0,
                "/Ascent": 800,
                "/Descent": -200,
                "/CapHeight": 700,
                "/StemV": 80,
                "/FontFile2": fs,
            }
        )
    )
    cid_font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/BaseFont": pikepdf.Name("/ZZZSynthCIDFont-Regular"),
                "/CIDSystemInfo": pikepdf.Dictionary(
                    {
                        "/Registry": pikepdf.String("Adobe"),
                        "/Ordering": pikepdf.String("Identity"),
                        "/Supplement": 0,
                    }
                ),
                "/FontDescriptor": fdsc,
                "/DW": 1000,
                "/W": pikepdf.Array([1, pikepdf.Array([500]), 2, pikepdf.Array([500])]),
                "/CIDToGIDMap": pikepdf.Name("/Identity"),
            }
        )
    )
    tounicode = (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap 1 "
        "begincodespacerange <0000> <FFFF> endcodespacerange 2 beginbfchar "
        "<0001> <0041> <0002> <0042> endbfchar endcmap CMapName currentdict "
        "/CMap defineresource pop end end"
    )
    type0 = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/BaseFont": pikepdf.Name("/ZZZSynthCIDFont-Regular"),
            "/Encoding": pikepdf.Name("/Identity-H"),
            "/DescendantFonts": pikepdf.Array([cid_font]),
            "/ToUnicode": pikepdf.Stream(pdf, tounicode.encode("latin-1")),
        }
    )
    content = b"BT\n/F1 24 Tf\n1 0 0 1 72 720 Tm\n<00010002> Tj\nET"
    page = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
            "/Resources": pikepdf.Dictionary(
                {"/Font": pikepdf.Dictionary({"/F1": pdf.make_indirect(type0)})}
            ),
            "/Contents": pikepdf.Stream(pdf, content),
        }
    )
    pdf.pages.append(pikepdf.Page(page))
    path = _save_temp_pdf(pdf)
    pdf.close()
    return path


# ── B1 — honesty taxonomy exported from the package root ──────────────────


class TestB1HonestyTaxonomyExports:
    """B1: Degradation, DegradationKind, FONT_AFFECTING_KINDS, DEGRADATION_KINDS
    are importable from the package root and present in ``__all__``."""

    def test_importable_from_root(self) -> None:
        # RED until B1 lands: these names are not exported from the package root.
        from pdf_edit_engine import (  # noqa: F401
            DEGRADATION_KINDS,
            FONT_AFFECTING_KINDS,
            Degradation,
            DegradationKind,
        )

    def test_present_as_module_attributes(self) -> None:
        import pdf_edit_engine

        for name in (
            "Degradation",
            "DegradationKind",
            "FONT_AFFECTING_KINDS",
            "DEGRADATION_KINDS",
        ):
            assert hasattr(pdf_edit_engine, name), f"{name} not a package attribute"

    def test_present_in_all(self) -> None:
        import pdf_edit_engine

        for name in (
            "Degradation",
            "DegradationKind",
            "FONT_AFFECTING_KINDS",
            "DEGRADATION_KINDS",
        ):
            assert name in pdf_edit_engine.__all__, f"{name} missing from __all__"

    def test_root_degradation_is_models_degradation(self) -> None:
        import pdf_edit_engine
        import pdf_edit_engine.models as models

        assert pdf_edit_engine.Degradation is models.Degradation


# ── B2 — FidelityReport.summary() ─────────────────────────────────────────


class TestB2FidelityReportSummary:
    """B2: one-line human-readable rendering of the report."""

    def test_summary_clean(self) -> None:
        assert _clean_report().summary() == "clean"

    def test_summary_single_warning(self) -> None:
        r = _report_with(
            degradations=[
                _Degradation(kind="kerning_compressed", detail="Tz 88%", severity="warning")
            ]
        )
        assert r.summary() == "saved with 1 warning: kerning compressed"

    def test_summary_multiple_warnings_plural(self) -> None:
        r = _report_with(
            degradations=[
                _Degradation(kind="kerning_compressed", detail="", severity="warning"),
                _Degradation(kind="overflow_shift_clamped", detail="", severity="warning"),
            ],
            overflow_detected=True,
        )
        assert r.summary() == ("saved with 2 warnings: kerning compressed; overflow shift clamped")

    def test_summary_failed_on_error(self) -> None:
        r = _report_with(
            degradations=[_Degradation(kind="font_extension_failed", detail="", severity="error")]
        )
        s = r.summary()
        assert s.startswith("FAILED:")
        assert "font extension failed" in s

    def test_summary_substitution_not_double_counted(self) -> None:
        r = _report_with(
            degradations=[
                _Degradation(kind="font_coverage_substituted", detail="", severity="warning")
            ],
            font_substituted="Carlito-Regular",
        )
        s = r.summary()
        # The substitution is conveyed by the degradation kind; the synthetic
        # "font substituted (...)" phrase must NOT also appear.
        assert "font coverage substituted" in s
        assert "font substituted (" not in s

    def test_summary_synthetic_substitution_phrase(self) -> None:
        # Substitution recorded with NO degradation kind conveying it → the
        # synthetic phrase must be present.
        r = _report_with(font_substituted="Carlito-Regular")
        s = r.summary()
        assert "font substituted (Carlito-Regular)" in s


# ── B3 — is_clean / max_severity / warnings() accessors ───────────────────


class TestB3FidelityReportAccessors:
    """B3: is_clean, max_severity, warnings() accessors on FidelityReport."""

    def test_is_clean_true(self) -> None:
        assert _clean_report().is_clean is True

    def test_is_clean_false_on_degradation(self) -> None:
        r = _report_with(
            degradations=[
                _Degradation(kind="line_break_quality_degraded", detail="", severity="info")
            ]
        )
        assert r.is_clean is False

    def test_is_clean_false_on_substitution(self) -> None:
        assert _report_with(font_substituted="Carlito-Regular").is_clean is False

    def test_is_clean_false_on_overflow(self) -> None:
        assert _report_with(overflow_detected=True).is_clean is False

    def test_is_clean_consistent_with_font_preserved(self) -> None:
        # INV-J-8 cross-check: is_clean True ⇒ no degradations ⇒ font_preserved True.
        r = _clean_report()
        assert r.is_clean is True
        assert r.font_preserved is True

    def test_max_severity_none_when_empty(self) -> None:
        assert _clean_report().max_severity is None

    def test_max_severity_info(self) -> None:
        r = _report_with(degradations=[_Degradation(kind="indent_flattened", severity="info")])
        assert r.max_severity == "info"

    def test_max_severity_picks_warning_over_info(self) -> None:
        r = _report_with(
            degradations=[
                _Degradation(kind="indent_flattened", severity="info"),
                _Degradation(kind="kerning_compressed", severity="warning"),
            ]
        )
        assert r.max_severity == "warning"

    def test_max_severity_picks_error_over_warning(self) -> None:
        r = _report_with(
            degradations=[
                _Degradation(kind="kerning_compressed", severity="warning"),
                _Degradation(kind="font_extension_failed", severity="error"),
            ]
        )
        assert r.max_severity == "error"

    def test_warnings_excludes_info(self) -> None:
        info = _Degradation(kind="indent_flattened", severity="info")
        warn = _Degradation(kind="kerning_compressed", severity="warning")
        err = _Degradation(kind="font_extension_failed", severity="error")
        r = _report_with(degradations=[info, warn, err])
        result = r.warnings()
        # Returns the Degradation objects (warning + error), order preserved.
        assert result == [warn, err]
        assert all(isinstance(d, _Degradation) for d in result)
        assert info not in result


# ── B4 — EditResult.to_dict() ─────────────────────────────────────────────


class TestB4EditResultToDict:
    """B4: EditResult.to_dict() nests the report dict with computed
    font_preserved (the asdict trap, reopened one level up)."""

    def test_to_dict_nests_report_with_font_preserved_true(self) -> None:
        er = EditResult(
            success=True,
            original_text="a",
            new_text="b",
            font_action="kept",
            fidelity_report=_clean_report(),
        )
        d = er.to_dict()
        assert d["fidelity_report"]["font_preserved"] is True
        # JSON-serializable end-to-end.
        json.dumps(d)

    def test_to_dict_font_preserved_false_survives(self) -> None:
        # font_action="failed" requires an INV-J-9 font-affecting degradation.
        fr = _report_with(
            degradations=[_Degradation(kind="font_extension_failed", detail="x", severity="error")]
        )
        er = EditResult(
            success=False,
            original_text="a",
            new_text="b",
            font_action="failed",
            fidelity_report=fr,
        )
        d = er.to_dict()
        assert d["fidelity_report"]["font_preserved"] is False

    def test_to_dict_top_level_fields(self) -> None:
        er = EditResult(
            success=True,
            original_text="orig",
            new_text="new",
            font_action="kept",
            warnings=["w1"],
            fidelity_report=_clean_report(),
        )
        d = er.to_dict()
        assert set(d.keys()) == {
            "success",
            "original_text",
            "new_text",
            "font_action",
            "warnings",
            "fidelity_report",
        }
        assert d["success"] is True
        assert d["original_text"] == "orig"
        assert d["new_text"] == "new"
        assert d["font_action"] == "kept"
        assert d["warnings"] == ["w1"]
        assert isinstance(d["fidelity_report"], dict)

    def test_to_dict_drops_nothing_vs_asdict(self) -> None:
        # Pins the trap closure: plain asdict loses the computed property,
        # to_dict() restores it.
        er = EditResult(
            success=True,
            original_text="a",
            new_text="b",
            font_action="kept",
            fidelity_report=_clean_report(),
        )
        asdict_nested = dataclasses.asdict(er)["fidelity_report"]
        assert "font_preserved" not in asdict_nested  # the trap
        assert "font_preserved" in er.to_dict()["fidelity_report"]  # closed


# ── B6 — machine-enumerable DEGRADATION_KINDS ─────────────────────────────


class TestB6DegradationKinds:
    """B6: DEGRADATION_KINDS is a tuple, len == 30, == get_args(DegradationKind)."""

    def test_is_tuple(self) -> None:
        from pdf_edit_engine.models import DEGRADATION_KINDS

        assert isinstance(DEGRADATION_KINDS, tuple)

    def test_length_30(self) -> None:
        from pdf_edit_engine.models import DEGRADATION_KINDS

        assert len(DEGRADATION_KINDS) == 30

    def test_equals_literal_args(self) -> None:
        from pdf_edit_engine.models import DEGRADATION_KINDS

        assert typing.get_args(DegradationKind) == DEGRADATION_KINDS

    def test_all_str(self) -> None:
        from pdf_edit_engine.models import DEGRADATION_KINDS

        assert all(isinstance(k, str) for k in DEGRADATION_KINDS)

    def test_exported_from_root_is_same_object(self) -> None:
        import pdf_edit_engine
        import pdf_edit_engine.models as models

        assert pdf_edit_engine.DEGRADATION_KINDS is models.DEGRADATION_KINDS


# ── B8 — out-of-range page raises PDFEditError, not bare IndexError ───────


class TestB8OutOfRangePage:
    """B8: find() on an out-of-range page raises a PDFEditError subclass."""

    def test_find_invalid_page_raises_pdfediterror(self) -> None:
        path = _build_one_page_pdf()
        # Sanity: page 0 is found.
        assert [m.matched_text for m in find(path, "x", page=0)] == ["x"]
        # RED until B8: this currently raises a BARE IndexError (not a
        # PDFEditError subclass), so pytest.raises(PDFEditError) does NOT catch
        # it and the test errors/fails.
        with pytest.raises(PDFEditError):
            find(path, "x", page=99)

    def test_find_invalid_page_not_bare_indexerror(self) -> None:
        path = _build_one_page_pdf()
        try:
            find(path, "x", page=99)
        except PDFEditError:
            pass  # GREEN after B8.
        except IndexError as exc:  # pragma: no cover - the RED state
            pytest.fail(
                "find() raised a bare IndexError on an out-of-range page; "
                f"expected a PDFEditError subclass. Got: {exc!r}"
            )


# ── B9 — FontNotFoundError message carries the documented remedy ──────────


class TestB9FontNotFoundRemedyText:
    """B9: the system-font-missing message includes the documented remedy
    ``pass full_font_path=<path>``."""

    def test_remedy_text_present(self) -> None:
        path = _build_cid_glyf_pdf_synthetic_name()
        pdf = pikepdf.open(path)
        try:
            with pytest.raises(FontNotFoundError) as exc_info:
                # No full_font_path; the synthetic /BaseFont resolves to no
                # system font (and no metric equivalent) → the engine raises
                # the "system font not found" FontNotFoundError.
                extend_subset(pdf, pdf.pages[0], "F1", "C")
            msg = str(exc_info.value)
            # RED until B9 normalizes the wording: the current message says
            # "Install the font or provide full_font_path." and lacks the
            # documented remedy form below.
            assert "pass full_font_path=<path>" in msg, (
                f"FontNotFoundError message missing documented remedy text; got: {msg!r}"
            )
        finally:
            pdf.close()


# ── B10 — CFF-refusal message drops internal "slice-1" jargon ─────────────


class TestB10NoSliceJargon:
    """B10: user-facing CFF-refusal messages do not leak the internal
    ``slice-1`` scope jargon."""

    def test_cff_refusal_message_has_no_slice_jargon(self) -> None:
        # A name-keyed (non-ROS) embedded CFF with a valid CFF donor reaches
        # the CID-keyed (ROS) gate, which raises FontNotFoundError. Today the
        # message contains "slice-1"; B10 removes it.
        donor = build_cff_donor_bytes(("C",))
        donor_dir = tempfile.mkdtemp(prefix="dx_b10_")
        donor_path = os.path.join(donor_dir, "donor.otf")
        with open(donor_path, "wb") as fh:
            fh.write(donor)
        pdf_bytes = build_namekeyed_otf_cff_pdf()
        pdf = pikepdf.open(io.BytesIO(pdf_bytes))
        try:
            with pytest.raises(FontNotFoundError) as exc_info:
                extend_subset(pdf, pdf.pages[0], "F1", "C", full_font_path=donor_path)
            msg = str(exc_info.value)
            # RED until B10 strips it: the message currently reads
            # "CFF injection slice-1 requires a CID-keyed (ROS) embedded font".
            assert "slice-1" not in msg, (
                f"user-facing CFF-refusal message still leaks 'slice-1' jargon; got: {msg!r}"
            )
        finally:
            pdf.close()
            os.unlink(donor_path)
            os.rmdir(donor_dir)
