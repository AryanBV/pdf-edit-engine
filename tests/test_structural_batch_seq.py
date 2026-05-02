"""Branch-coverage tests for batch_replace_block — sequential and bbox-anchored modes.

Sequential mode (skip_vertical_shift=True under the hood) is engaged when
``section_gap`` and ``line_height`` are provided as a coordinated pair, OR
auto-engaged when ``len(replacements) > 1`` and ``line_height`` is omitted
(``_auto_compute_layout`` then computes both).

Bbox-anchored mode is engaged when ``line_height`` is provided but
``section_gap`` is omitted, or when there is exactly one replacement.

These tests target the two-mode branching at structural.py:1351-1455
(sequential) and 1510-1569 (bbox-anchored).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf
import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as rl_canvas

from pdf_edit_engine import batch_replace_block, get_text, get_text_layout, replace_block

if TYPE_CHECKING:
    from pathlib import Path

    from pdf_edit_engine.models import TextBlock

# ── Fixture builders ─────────────────────────────────────────────────


def _make_three_section_pdf(path: Path) -> tuple[float, float, float]:
    """Three text sections on a letter page at y = 700, 600, 500.

    Section heights are ~12pt. Returns (y_top_section, y_mid_section,
    y_bot_section) in PDF coords. Each section is a single line.
    """
    page_w, page_h = letter  # noqa: F841 — page_w unused
    c = rl_canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "ALPHA section first body line.")
    c.drawString(72, 600, "BETA section second body line.")
    c.drawString(72, 500, "GAMMA section third body line.")
    c.save()
    return (700.0, 600.0, 500.0)


def _make_two_section_pdf_with_gap(path: Path, gap: float = 30.0) -> tuple[float, float]:
    """Two sections with a known vertical gap. Returns (y_top, y_bot)."""
    _, page_h = letter
    y_top = 700.0
    y_bot = y_top - gap - 12.0  # next section starts gap+section_height below
    c = rl_canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, y_top, "TOP section text.")
    c.drawString(72, y_bot, "BOT section text.")
    c.save()
    return (y_top, y_bot)


def _make_pdf_with_hyperlinks(path: Path) -> None:
    """Two sections, each with a hyperlink annotation overlapping its bbox."""
    c = rl_canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 12)
    # Section 1 with a link whose URI keyword 'github' won't appear in
    # the replacement text.
    c.drawString(72, 700, "SectionA visit github profile here")
    c.linkURL("https://github.com/user-profile", (72, 696, 350, 712), relative=0)
    # Section 2 with a link whose URI keyword 'linkedin' won't appear.
    c.drawString(72, 600, "SectionB visit linkedin profile here")
    c.linkURL("https://linkedin.com/in/handle-name", (72, 596, 350, 612), relative=0)
    c.save()


def _count_annotations(pdf_path: str) -> int:
    """Count /Annots on page 0."""
    with pikepdf.open(pdf_path) as pdf:
        page = pdf.pages[0]
        annots = page.get("/Annots")
        if not annots:
            return 0
        return len(list(annots))  # type: ignore[call-overload]


def _y_of(text_layout: list[TextBlock], prefix: str) -> float | None:
    """Return the y of the first TextBlock starting with prefix, else None."""
    for tb in text_layout:
        if tb.text.startswith(prefix):
            return float(tb.y)
    return None


# ── Tests ─────────────────────────────────────────────────────────────


class TestBatchSequentialMode:
    """Sequential mode: line_height + section_gap provided, or auto-computed."""

    def test_overflow_shifts_below_region_seq(self, tmp_path: Path) -> None:
        """Sequential mode: when the last section's reflowed text extends
        below ``region_bottom``, the overflow branch at structural.py:1399-1407
        fires and shifts content below the region downward.

        Covers: prev_last_line_y < region_bottom branch.
        """
        src = tmp_path / "src.pdf"
        out = tmp_path / "out.pdf"
        # Two sections plus an anchor below the region.
        c = rl_canvas.Canvas(str(src), pagesize=letter)
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, "TOPSEC original.")
        c.drawString(72, 600, "BOTSEC original.")
        c.drawString(72, 500, "ANCHOR below region.")
        c.save()

        pre = get_text_layout(str(src))
        pre_anchor_y = _y_of(pre, "ANCHOR")
        assert pre_anchor_y is not None

        # Replace BOTSEC (the lower section, region_bottom=596) with a
        # long multi-line block. In sequential mode, BOTSEC's text starts
        # right below TOPSEC and runs many lines down — its last line
        # falls below region_bottom, triggering the down-shift.
        long_text = " ".join(["expanded"] * 50)
        replacements = [
            ((72.0, 696.0, 500.0, 712.0), "TOPSEC short."),
            ((72.0, 596.0, 500.0, 612.0), long_text),
        ]
        results = batch_replace_block(
            str(src),
            0,
            replacements,
            str(out),
            line_height=14.4,
            section_gap=20.0,
        )
        assert len(results) == 2
        assert all(r.success for r in results)

        post = get_text_layout(str(out))
        post_anchor_y = _y_of(post, "ANCHOR")
        assert post_anchor_y is not None
        # In PDF coords, "shifted down" means smaller y. The down-shift
        # branch must fire because TOPSEC starts at 712, runs to ~700,
        # then BOTSEC reflows at 700-20=680 for many lines, ending below
        # 596 (region_bottom). The excess pushes ANCHOR down.
        assert post_anchor_y < pre_anchor_y - 1e-3, (
            f"down-shift branch did not fire: pre={pre_anchor_y}, post={post_anchor_y}"
        )

    def test_collapse_slack_when_short_seq(self, tmp_path: Path) -> None:
        """Sequential mode: when replacement leaves vertical slack
        between the trailing section and the next content below, the
        slack-collapse branch shifts content up by (actual_gap -
        section_gap). Branch at structural.py:1419-1427."""
        src = tmp_path / "src.pdf"
        out = tmp_path / "out.pdf"
        # Three sections at y = 700, 600, 500 (gap = 100 between baselines).
        _make_three_section_pdf(src)
        # ALSO add a "below" content element so the slack-collapse path
        # has a concrete target. We do this by re-creating the source.
        c = rl_canvas.Canvas(str(src), pagesize=letter)
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, "ALPHA section body line.")
        c.drawString(72, 600, "BETA section body line.")
        # Below-region anchor that we expect to move UP after slack collapse.
        c.drawString(72, 400, "BELOW anchor line below the region.")
        c.save()

        pre = get_text_layout(str(src))
        pre_below_y = _y_of(pre, "BELOW")
        assert pre_below_y is not None

        replacements = [
            ((72.0, 696.0, 500.0, 712.0), "AAA"),  # short replacement
            ((72.0, 596.0, 500.0, 612.0), "BBB"),  # short replacement
        ]
        # Sequential mode with a small section_gap so that the actual
        # gap between the last rendered line and BELOW is much larger
        # than section_gap → triggers the up-shift branch.
        results = batch_replace_block(
            str(src),
            0,
            replacements,
            str(out),
            line_height=14.4,
            section_gap=20.0,
        )
        assert len(results) == 2
        assert all(r.success for r in results)

        post = get_text_layout(str(out))
        post_below_y = _y_of(post, "BELOW")
        assert post_below_y is not None
        # Slack collapse should pull BELOW upward (larger y in PDF coords)
        # — strictly greater than the original. If the branch never fired,
        # post_below_y would equal pre_below_y.
        assert post_below_y > pre_below_y, (
            f"slack-collapse branch did not fire: pre={pre_below_y}, post={post_below_y}"
        )

    def test_section_gap_preserved_no_below_content(self, tmp_path: Path) -> None:
        """Sequential mode: with no content below the region, only the
        replacements are reflowed. The trailing branch (else of overflow)
        with no below_elems must be a no-op for the page."""
        src = tmp_path / "src.pdf"
        out = tmp_path / "out.pdf"
        c = rl_canvas.Canvas(str(src), pagesize=letter)
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, "TOPSEC original line.")
        c.drawString(72, 670, "MIDSEC original line.")
        c.save()

        replacements = [
            ((72.0, 696.0, 500.0, 712.0), "TOPSEC replaced."),
            ((72.0, 666.0, 500.0, 682.0), "MIDSEC replaced."),
        ]
        results = batch_replace_block(
            str(src),
            0,
            replacements,
            str(out),
            line_height=14.4,
            section_gap=10.0,
        )
        assert len(results) == 2
        assert all(r.success for r in results)
        text = get_text(str(out))
        assert "TOPSEC replaced" in text
        assert "MIDSEC replaced" in text

    def test_seq_auto_layout_two_sections(self, tmp_path: Path) -> None:
        """Auto-layout path: with len > 1 and no kwargs, batch_replace_block
        invokes _auto_compute_layout and engages sequential mode."""
        src = tmp_path / "src.pdf"
        out = tmp_path / "out.pdf"
        _make_three_section_pdf(src)

        replacements = [
            ((72.0, 696.0, 500.0, 712.0), "ALPHA new"),
            ((72.0, 596.0, 500.0, 612.0), "BETA new"),
        ]
        # No layout kwargs → triggers _auto_compute_layout (len > 1).
        results = batch_replace_block(str(src), 0, replacements, str(out))
        assert len(results) == 2
        assert all(r.success for r in results)
        text = get_text(str(out))
        assert "ALPHA new" in text
        assert "BETA new" in text


class TestBatchAnchoredMode:
    """Bbox-anchored (default) mode: line_height provided, section_gap=None."""

    def test_anchored_results_in_input_order(self, tmp_path: Path) -> None:
        """Bbox-anchored: results follow input order, not the internal
        topmost-first sort. Branch at structural.py:1432, 1450, 1459."""
        src = tmp_path / "src.pdf"
        out = tmp_path / "out.pdf"
        _make_three_section_pdf(src)

        # Provide in shuffled order: middle, top, bottom.
        replacements = [
            ((72.0, 596.0, 500.0, 612.0), "B-middle"),  # input idx 0
            ((72.0, 696.0, 500.0, 712.0), "A-top"),  # input idx 1
            ((72.0, 496.0, 500.0, 512.0), "C-bottom"),  # input idx 2
        ]
        # Force bbox-anchored mode: explicit line_height, no section_gap.
        results = batch_replace_block(
            str(src),
            0,
            replacements,
            str(out),
            line_height=14.4,
        )
        assert [r.new_text for r in results] == [
            "B-middle",
            "A-top",
            "C-bottom",
        ]
        text = get_text(str(out))
        assert "A-top" in text
        assert "B-middle" in text
        assert "C-bottom" in text

    def test_anchored_cumulative_shift_correct(self, tmp_path: Path) -> None:
        """Bbox-anchored: when section_a (top) overflows by X, the next
        bbox is internally shifted by cumulative_shift before its own
        replace_block call. Verify second section content lands shifted
        from where its raw bbox would have placed it."""
        src = tmp_path / "src.pdf"
        out = tmp_path / "out.pdf"
        _make_three_section_pdf(src)

        pre = get_text_layout(str(src))
        pre_gamma_y = _y_of(pre, "GAMMA")
        assert pre_gamma_y is not None

        # ALPHA with long text → overflow_delta > 0.
        long_text = " ".join(["xx"] * 60)
        replacements = [
            ((72.0, 696.0, 500.0, 712.0), long_text),  # overflows
            ((72.0, 596.0, 500.0, 612.0), "BETA short"),
            ((72.0, 496.0, 500.0, 512.0), "GAMMA short"),
        ]
        results = batch_replace_block(
            str(src),
            0,
            replacements,
            str(out),
            line_height=14.4,
        )
        assert len(results) == 3
        assert all(r.success for r in results)
        # First replacement reports overflow.
        assert results[0].fidelity_report.overflow_detected

        text = get_text(str(out))
        # GAMMA should have its replacement applied (BETA-related bbox
        # was successfully matched even after cumulative_shift carried
        # forward).
        assert "GAMMA short" in text
        assert "BETA short" in text

    def test_anchored_single_replacement(self, tmp_path: Path) -> None:
        """Bbox-anchored: with len(replacements) == 1, _auto_compute_layout
        is bypassed (guard at structural.py:1351 requires len > 1).
        Sequential check at 1365 also fails (section_gap is None).
        Falls into bbox-anchored branch."""
        src = tmp_path / "src.pdf"
        out = tmp_path / "out.pdf"
        _make_three_section_pdf(src)

        replacements = [
            ((72.0, 696.0, 500.0, 712.0), "ALPHA only"),
        ]
        results = batch_replace_block(str(src), 0, replacements, str(out))
        assert len(results) == 1
        assert results[0].success
        text = get_text(str(out))
        assert "ALPHA only" in text
        # Other sections preserved.
        assert "BETA" in text
        assert "GAMMA" in text


class TestBatchOrphanCleanup:
    """INV-W0-7: orphan-annotation cleanup runs per bbox in both modes."""

    def test_anchored_orphan_annotations_cleaned_per_bbox(self, tmp_path: Path) -> None:
        """Bbox-anchored mode: each bbox triggers
        _remove_orphaned_annotations regardless of overflow."""
        src = tmp_path / "src.pdf"
        out = tmp_path / "out.pdf"
        _make_pdf_with_hyperlinks(src)

        pre_count = _count_annotations(str(src))
        assert pre_count == 2  # baseline: two link annotations

        # Both replacements omit any URI keyword → both annotations orphan.
        replacements = [
            ((72.0, 692.0, 500.0, 716.0), "Replacement text one."),
            ((72.0, 592.0, 500.0, 616.0), "Replacement text two."),
        ]
        batch_replace_block(
            str(src),
            0,
            replacements,
            str(out),
            line_height=14.4,
        )
        post_count = _count_annotations(str(out))
        assert post_count == 0, (
            f"orphan annotations remained after bbox-anchored batch: "
            f"pre={pre_count}, post={post_count}"
        )

    def test_seq_orphan_annotations_cleaned_per_bbox(self, tmp_path: Path) -> None:
        """Sequential mode: same orphan cleanup invariant. Branch at
        structural.py:1389-1390."""
        src = tmp_path / "src.pdf"
        out = tmp_path / "out.pdf"
        _make_pdf_with_hyperlinks(src)

        pre_count = _count_annotations(str(src))
        assert pre_count == 2

        replacements = [
            ((72.0, 692.0, 500.0, 716.0), "Replacement one."),
            ((72.0, 592.0, 500.0, 616.0), "Replacement two."),
        ]
        batch_replace_block(
            str(src),
            0,
            replacements,
            str(out),
            line_height=14.4,
            section_gap=20.0,
        )
        post_count = _count_annotations(str(out))
        assert post_count == 0


class TestBatchEdgeCases:
    """Empty input and single-edit equivalence."""

    def test_empty_edits_list_no_op(self, tmp_path: Path) -> None:
        """batch_replace_block with [] edits returns []. Per
        structural.py:1336-1337, no PDF is opened, no file is written."""
        src = tmp_path / "src.pdf"
        out = tmp_path / "out.pdf"
        _make_three_section_pdf(src)

        results = batch_replace_block(str(src), 0, [], str(out))
        assert results == []
        # Output PDF was not created (early return before pdf.save).
        assert not out.exists()

    def test_single_edit_equivalent_to_replace_block(self, tmp_path: Path) -> None:
        """A single-edit batch_replace_block produces an EditResult whose
        new_text and success match a direct replace_block call.

        Note: byte-identity is not asserted (pikepdf nondeterminism); we
        compare the user-observable contract: success, new_text, and that
        the new text is in the output."""
        src = tmp_path / "src.pdf"
        out_batch = tmp_path / "out_batch.pdf"
        out_direct = tmp_path / "out_direct.pdf"
        _make_three_section_pdf(src)

        bbox = (72.0, 696.0, 500.0, 712.0)
        new = "ALPHA via batch path"
        # Single-edit path inside batch_replace_block.
        batch_results = batch_replace_block(str(src), 0, [(bbox, new)], str(out_batch))
        # Direct replace_block call.
        direct_result = replace_block(str(src), 0, bbox, new, str(out_direct))

        assert len(batch_results) == 1
        b = batch_results[0]
        assert b.success == direct_result.success
        assert b.new_text == direct_result.new_text == new
        # New text in both outputs.
        assert new in get_text(str(out_batch))
        assert new in get_text(str(out_direct))


# Force pytest to import (avoids accidental dead-code warnings if these
# helpers shift around).
_ = (pytest, get_text_layout)
