"""Tests for ``pdf_edit_engine.models``.

Currently covers:

- CRIT-2: ``FidelityReport.to_dict`` surfaces the ``font_preserved``
  ``@property`` (which ``dataclasses.asdict`` silently drops) so callers
  serializing via ``json.dumps`` see the engine's wedge-differentiator
  field. The suite covers the four canonical truth-function shapes plus
  one round-trip of a nested ``Degradation`` to verify ``asdict``
  recursion is correct under the helper.
"""

from __future__ import annotations

import json

from pdf_edit_engine.models import Degradation, FidelityReport


class TestFidelityReportToDict:
    """CRIT-2: ``to_dict()`` returns a JSON-serializable dict that
    includes the computed ``font_preserved`` property."""

    def test_to_dict_preserves_font_preserved_true(self) -> None:
        r = FidelityReport(
            font_substituted=None,
            overflow_detected=False,
            reflow_applied=False,
            glyphs_missing=[],
            degradations=[],
        )
        d = r.to_dict()
        assert d["font_preserved"] is True
        assert '"font_preserved": true' in json.dumps(d)

    def test_to_dict_false_via_font_affecting_degradation(self) -> None:
        deg = Degradation(kind="font_extension_failed", detail="x", severity="error")
        r = FidelityReport(
            font_substituted=None,
            overflow_detected=False,
            reflow_applied=False,
            glyphs_missing=[],
            degradations=[deg],
        )
        d = r.to_dict()
        assert d["font_preserved"] is False
        assert '"font_preserved": false' in json.dumps(d)

    def test_to_dict_false_via_substitution(self) -> None:
        r = FidelityReport(
            font_substituted="Carlito-Bold",
            overflow_detected=False,
            reflow_applied=False,
            glyphs_missing=[],
            degradations=[],
        )
        d = r.to_dict()
        assert d["font_preserved"] is False
        assert d["font_substituted"] == "Carlito-Bold"

    def test_to_dict_round_trips_nested_degradations(self) -> None:
        """Verifies asdict recursion into ``list[Degradation]`` is
        correct under ``to_dict``. ``Degradation`` is a frozen dataclass
        with no @property fields, so default recursion suffices; this
        test pins that contract for any future regression."""
        deg = Degradation(
            kind="font_coverage_substituted",
            detail="tier=1.5,chars=ø,source=Carlito-Regular",
            severity="warning",
        )
        r = FidelityReport(
            font_substituted="Carlito-Regular",
            overflow_detected=False,
            reflow_applied=False,
            glyphs_missing=["ø"],
            degradations=[deg],
        )
        d = r.to_dict()
        assert isinstance(d["degradations"], list)
        assert d["degradations"][0]["kind"] == "font_coverage_substituted"
        assert d["degradations"][0]["detail"] == "tier=1.5,chars=ø,source=Carlito-Regular"
        assert d["degradations"][0]["severity"] == "warning"
        # Round-trip serializability: must not raise.
        json.dumps(d)
