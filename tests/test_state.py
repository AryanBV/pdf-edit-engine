"""Tests for the GraphicsStateTracker module."""

from __future__ import annotations

import pytest

from pdf_edit_engine.models import GraphicsStateSnapshot
from pdf_edit_engine.state import GraphicsStateTracker

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


class TestSaveRestore:
    """Tests for q/Q graphics state stack."""

    def test_q_Q_three_levels(self) -> None:
        t = GraphicsStateTracker()

        # Level 0: set fill color red
        t.process_operator("rg", [1.0, 0.0, 0.0])
        t.process_operator("q", [])

        # Level 1: set fill color green
        t.process_operator("rg", [0.0, 1.0, 0.0])
        t.process_operator("q", [])

        # Level 2: set fill color blue
        t.process_operator("rg", [0.0, 0.0, 1.0])
        t.process_operator("q", [])

        # Level 3: set fill color white
        t.process_operator("rg", [1.0, 1.0, 1.0])
        assert t.fill_color == (1.0, 1.0, 1.0)

        # Pop level 3 → restore level 2 (blue)
        t.process_operator("Q", [])
        assert t.fill_color == (0.0, 0.0, 1.0)

        # Pop level 2 → restore level 1 (green)
        t.process_operator("Q", [])
        assert t.fill_color == (0.0, 1.0, 0.0)

        # Pop level 1 → restore level 0 (red)
        t.process_operator("Q", [])
        assert t.fill_color == (1.0, 0.0, 0.0)

    def test_unbalanced_Q_no_crash(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("rg", [0.5, 0.5, 0.5])

        # Extra Q on empty stack — should not crash
        t.process_operator("Q", [])
        t.process_operator("Q", [])

        # State should remain unchanged
        assert t.fill_color == (0.5, 0.5, 0.5)


class TestCTM:
    """Tests for cm matrix concatenation."""

    def test_cm_identity_times_translation(self) -> None:
        t = GraphicsStateTracker()
        assert t.ctm == IDENTITY

        # Apply translation (100, 200)
        t.process_operator("cm", [1.0, 0.0, 0.0, 1.0, 100.0, 200.0])
        assert t.ctm == (1.0, 0.0, 0.0, 1.0, 100.0, 200.0)

    def test_cm_concatenation(self) -> None:
        t = GraphicsStateTracker()

        # First: scale by 2x
        t.process_operator("cm", [2.0, 0.0, 0.0, 2.0, 0.0, 0.0])
        assert t.ctm == (2.0, 0.0, 0.0, 2.0, 0.0, 0.0)

        # Second: translate by (10, 20) — result should be scale+translate
        # new_ctm = translate_matrix x current_ctm
        t.process_operator("cm", [1.0, 0.0, 0.0, 1.0, 10.0, 20.0])
        # [1 0 0; 0 1 0; 10 20 1] x [2 0 0; 0 2 0; 0 0 1]
        # = [2 0 0; 0 2 0; 20 40 1]
        assert t.ctm == pytest.approx((2.0, 0.0, 0.0, 2.0, 20.0, 40.0))


class TestTextPosition:
    """Tests for Tm, Td, and BT operators."""

    def test_Tm_set_position(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 72.0, 700.0])
        assert t.get_text_position() == pytest.approx((72.0, 700.0))

    def test_Td_relative_move(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 72.0, 700.0])
        t.process_operator("Td", [10.0, -14.0])
        assert t.get_text_position() == pytest.approx((82.0, 686.0))

    def test_BT_resets_matrices(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 72.0, 700.0])
        assert t.get_text_position() != pytest.approx((0.0, 0.0))

        # BT resets text matrices to identity
        t.process_operator("BT", [])
        assert t.get_text_position() == pytest.approx((0.0, 0.0))

    def test_Tm_with_CTM(self) -> None:
        t = GraphicsStateTracker()
        # Scale by 2x
        t.process_operator("cm", [2.0, 0.0, 0.0, 2.0, 0.0, 0.0])
        t.process_operator("BT", [])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 50.0, 100.0])
        # Position = Tm.translation composited with CTM
        # x = 50*2 + 100*0 + 0 = 100, y = 50*0 + 100*2 + 0 = 200
        assert t.get_text_position() == pytest.approx((100.0, 200.0))


class TestGlyphAdvance:
    """Tests for advance_by_glyph with various spacing parameters."""

    def test_advance_no_spacing(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tf", ["/F1", 12.0])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 100.0, 700.0])

        # Glyph width 0.5 (500/1000), font size 12 → tx = 0.5*12 = 6.0
        t.advance_by_glyph(0.5, 0x0041)
        assert t.get_text_position() == pytest.approx((106.0, 700.0))

    def test_advance_with_Tc(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tf", ["/F1", 10.0])
        t.process_operator("Tc", [2.0])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])

        # w0=0.5, Tfs=10 → 0.5*10=5.0, + Tc=2.0 → 7.0, * Th=1.0 → 7.0
        t.advance_by_glyph(0.5, 0x0041)
        assert t.get_text_position() == pytest.approx((7.0, 0.0))

    def test_advance_with_Tw_space(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tf", ["/F1", 10.0])
        t.process_operator("Tw", [3.0])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])

        # Space char: w0=0.25, Tfs=10 → 2.5, + Tc=0 + Tw=3.0 → 5.5
        t.advance_by_glyph(0.25, 0x0020)
        assert t.get_text_position() == pytest.approx((5.5, 0.0))

    def test_advance_with_Tw_nonspace(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tf", ["/F1", 10.0])
        t.process_operator("Tw", [3.0])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])

        # Non-space: Tw NOT applied → w0=0.25, Tfs=10 → 2.5
        t.advance_by_glyph(0.25, 0x0041)
        assert t.get_text_position() == pytest.approx((2.5, 0.0))

    def test_advance_with_Tz(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tf", ["/F1", 10.0])
        t.process_operator("Tz", [200.0])  # 200% = 2.0
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])

        # w0=0.5, Tfs=10 → 5.0, * Th=2.0 → 10.0
        t.advance_by_glyph(0.5, 0x0041)
        assert t.get_text_position() == pytest.approx((10.0, 0.0))

    def test_advance_with_separate_tj_displacement(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tf", ["/F1", 10.0])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])

        # TJ=100 → displacement = -(100)/1000 * 10 = -1.0
        # Then glyph w0=0.5 → (0.5*10 + 0 + 0)*1.0 = 5.0
        # Total: -1.0 + 5.0 = 4.0 (same result as old combined formula when Tc=0)
        t.apply_tj_displacement(100.0)
        t.advance_by_glyph(0.5, 0x0041)
        assert t.get_text_position() == pytest.approx((4.0, 0.0))


class TestColor:
    """Tests for color state tracking."""

    def test_g_fill_gray(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("g", [0.5])
        assert t.fill_color == (0.5,)

    def test_rg_fill_rgb(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("rg", [1.0, 0.0, 0.0])
        assert t.fill_color == (1.0, 0.0, 0.0)

    # Stroke-state operators (G, RG, K, SCN) are intentionally
    # not tracked — every engine consumer reads only fill_color.
    # Stroke tracking was removed in v0.1.2 cleanup. The tests
    # for "RG_stroke_rgb" / "K_stroke_cmyk" went with it.

    def test_k_fill_cmyk(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("k", [0.0, 0.0, 0.0, 1.0])
        assert t.fill_color == (0.0, 0.0, 0.0, 1.0)


class TestSnapshot:
    """Tests for snapshot() capturing full state."""

    def test_snapshot_captures_state(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("cm", [1.0, 0.0, 0.0, 1.0, 50.0, 100.0])
        t.process_operator("rg", [1.0, 0.0, 0.0])
        t.process_operator("BT", [])
        t.process_operator("Tf", ["/F1", 12.0])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 72.0, 700.0])

        snap = t.snapshot()
        assert isinstance(snap, GraphicsStateSnapshot)
        assert snap.ctm == (1.0, 0.0, 0.0, 1.0, 50.0, 100.0)
        assert snap.fill_color == (1.0, 0.0, 0.0)
        assert snap.font_name == "F1"
        assert snap.font_size == 12.0
        assert snap.text_matrix == (1.0, 0.0, 0.0, 1.0, 72.0, 700.0)

    def test_snapshot_no_font_returns_none_size(self) -> None:
        t = GraphicsStateTracker()
        snap = t.snapshot()
        assert snap.font_name is None
        assert snap.font_size is None


class TestFontState:
    """Tests for Tf operator."""

    def test_Tf_sets_font(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("Tf", ["/F1", 12.0])
        assert t.font_name == "F1"
        assert t.font_size == 12.0

    def test_Tf_strips_slash(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("Tf", ["/MyFont", 16.0])
        assert t.font_name == "MyFont"


class TestTstar:
    """Tests for T* operator using leading."""

    def test_Tstar_uses_leading(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("TL", [14.0])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 72.0, 700.0])

        # T* is equivalent to 0 -TL Td
        t.process_operator("T*", [])
        assert t.get_text_position() == pytest.approx((72.0, 686.0))

    def test_TD_sets_leading_and_moves(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 72.0, 700.0])

        # TD sets TL = -ty and moves
        t.process_operator("TD", [0.0, -14.0])
        assert t.get_text_position() == pytest.approx((72.0, 686.0))

        # Subsequent T* should use the leading set by TD
        t.process_operator("T*", [])
        assert t.get_text_position() == pytest.approx((72.0, 672.0))


class TestTJDisplacement:
    """Tests for apply_tj_displacement — pure TJ kerning translation."""

    def test_negative_value_moves_right(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tf", ["/F1", 10.0])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 100.0, 700.0])

        # TJ value -120 → tx = -(-120)/1000 * 10 = +1.2
        t.apply_tj_displacement(-120.0)
        assert t.get_text_position() == pytest.approx((101.2, 700.0))

    def test_positive_value_moves_left(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("BT", [])
        t.process_operator("Tf", ["/F1", 12.0])
        t.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 50.0, 500.0])

        # TJ value 200 → tx = -(200)/1000 * 12 = -2.4
        t.apply_tj_displacement(200.0)
        assert t.get_text_position() == pytest.approx((47.6, 500.0))

    def test_tj_displacement_independent_of_Tc(self) -> None:
        """TJ displacement must NOT be affected by character spacing (Tc).

        This is the key behavioral difference from the old combined formula.
        """
        t1 = GraphicsStateTracker()
        t1.process_operator("BT", [])
        t1.process_operator("Tf", ["/F1", 10.0])
        t1.process_operator("Tc", [5.0])  # Large Tc
        t1.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
        t1.apply_tj_displacement(-100.0)

        t2 = GraphicsStateTracker()
        t2.process_operator("BT", [])
        t2.process_operator("Tf", ["/F1", 10.0])
        t2.process_operator("Tc", [0.0])  # Zero Tc
        t2.process_operator("Tm", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
        t2.apply_tj_displacement(-100.0)

        # Same displacement regardless of Tc
        assert t1.get_text_position() == pytest.approx(t2.get_text_position())


class TestProperties:
    """Tests for default property values."""

    def test_all_properties_default(self) -> None:
        t = GraphicsStateTracker()
        assert t.ctm == IDENTITY
        assert t.font_name is None
        assert t.font_size == 0.0
        assert t.fill_color is None
        assert t.text_rendering_mode == 0

    def test_text_rendering_mode(self) -> None:
        t = GraphicsStateTracker()
        t.process_operator("Tr", [1])
        assert t.text_rendering_mode == 1

    def test_unknown_operator_ignored(self) -> None:
        t = GraphicsStateTracker()
        # Should not raise
        t.process_operator("Do", ["/Im0"])
        t.process_operator("W", [])
        assert t.ctm == IDENTITY
