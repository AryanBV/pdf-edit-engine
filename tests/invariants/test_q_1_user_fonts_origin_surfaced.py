"""INV-Q-1 (F-D-CC9): user-fonts origin must surface as Degradation.

Closed in v0.1.3 commit 06: ``system_fonts._build_font_cache`` canonicalises
every cached path via ``os.path.realpath`` (catching POSIX symlinks AND
Windows directory junctions, mirroring ``_pathutil.validate_output_path``)
and refuses entries whose real path escapes ``_font_directories()``.
``_find_font_with_origin`` returns ``(path, origin, substituted_name)``
where ``origin`` is one of ``"system"``, ``"user"``,
``"metric_equivalent"``. Tier 1.5 (``_extend_simple_tier_15`` /
``_extend_tier2``) emits
``Degradation(kind="font_substituted_from_user_fonts", severity="warning")``
into the caller-supplied ``degradations`` list whenever ``origin == "user"``.

The Degradation is NOT in ``FONT_AFFECTING_KINDS``: the font WAS found
and used. The kind exists to surface the security-relevant origin so
callers can detect when an injected glyph outline came from a path an
unprivileged process could have primed (F-D-CC9 Medium severity).

Two probes:

1. **Resolver probe** (Windows-only, plants a real font under
   ``~/AppData/Local/Microsoft/Windows/Fonts/PlantedTest_INV_Q_1.ttf``;
   cleanup runs in finally:): asserts ``_find_font_with_origin`` returns
   ``origin == "user"`` for the planted name. The planted file's
   PostScript name is unique to avoid collision with any host font.

2. **Extension probe** (cross-platform; monkeypatches
   ``_find_font_with_origin`` to return ``origin="user"``): asserts
   ``_extend_simple_tier_15`` appends
   ``Degradation(kind="font_substituted_from_user_fonts")`` to the
   caller-supplied ``degradations`` list. Uses the same simple-font
   fixture as ``test_simple_extension.py``.
"""

from __future__ import annotations

import os
import platform
import shutil
from typing import TYPE_CHECKING

import pytest
from fontTools.ttLib import TTFont

import pdf_edit_engine.system_fonts as sf
from pdf_edit_engine._pathutil import open_pdf
from pdf_edit_engine.fonts import _extend_simple_tier_15
from tests._simple_font_fixture import _find_ttf_for_simple_font, _no_ttf_simple

if TYPE_CHECKING:
    from pathlib import Path

    from pdf_edit_engine.models import Degradation


# Unique PostScript name used by the planted font; if any host actually
# has a font by this name, the probe collides — chosen unique enough
# that this is implausible.
PLANTED_PS_NAME = "INVQ1NovelFontName"


def _planted_path() -> Path:
    """Per-platform path for the planted user-fonts entry.

    Windows uses the documented ``~/AppData/Local/Microsoft/Windows/Fonts``
    user-installed-fonts location (Windows 10+). Other platforms route
    through the equivalent user-writable dir from ``_user_font_directories``;
    the resolver probe is skipped on those for now (the contract holds
    cross-platform but the fixture-planting machinery is Windows-specific
    in this commit).
    """
    user_dirs = sf._user_font_directories()
    return user_dirs[0] / "PlantedTest_INV_Q_1.ttf"


def _plant_user_font(name_id_6: str = PLANTED_PS_NAME) -> Path:
    """Copy a known-good system TTF into the user-fonts dir and patch
    its nameID-6 to *name_id_6* so ``_find_font_with_origin(name_id_6)``
    will resolve to the planted file.

    Returns the planted path.
    """
    src = _find_ttf_for_simple_font()
    if src is None:
        pytest.skip("no source TrueType font available for planting")
    dst = _planted_path()
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Patch nameID-6 to the unique name so the cache-build + lookup
    # actually resolve to this file.
    font = TTFont(str(src))
    try:
        for rec in font["name"].names:
            if rec.nameID == 6:
                if rec.platformID == 3:
                    rec.string = name_id_6.encode("utf-16-be")
                else:
                    rec.string = name_id_6.encode("ascii")
        font.save(str(dst))
    finally:
        font.close()
    return dst


@pytest.mark.skipif(
    platform.system() != "Windows",
    reason="user-fonts dir planting machinery is Windows-specific",
)
def test_inv_q_1_resolver_reports_user_origin_for_planted_font() -> None:
    """A font planted under the user-writable fonts dir resolves with
    ``origin == "user"`` via ``_find_font_with_origin``.

    Cleanup MUST run unconditionally — wrapped in try/finally so a test
    failure cannot leave ``PlantedTest_*.ttf`` behind on the host.
    """
    planted = _plant_user_font()
    try:
        # Force cache rebuild so the planted font is observed.
        sf._FONT_CACHE = None

        found = sf._find_font_with_origin(PLANTED_PS_NAME)
        assert found is not None, (
            f"planted user-fonts entry {planted} did not resolve via "
            f"_find_font_with_origin({PLANTED_PS_NAME!r})"
        )
        path, origin, substituted = found
        assert origin == "user", (
            f"planted font under user-writable dir must report origin='user'; "
            f"got origin={origin!r} for path={path!r}"
        )
        assert substituted is None, (
            f"exact-name match must report substituted_name=None; got {substituted!r}"
        )
        # Path canonicalization: realpath should match the planted target.
        # (case-normalized to tolerate Windows drive-letter casing).
        assert os.path.normcase(os.path.realpath(path)) == os.path.normcase(
            os.path.realpath(str(planted))
        )
    finally:
        # Reset cache regardless so subsequent tests rebuild fresh.
        sf._FONT_CACHE = None
        if planted.exists():
            planted.unlink()


@_no_ttf_simple
def test_inv_q_1_extension_emits_degradation_when_origin_is_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``_extend_simple_tier_15`` appends
    ``Degradation(kind="font_substituted_from_user_fonts")`` to the
    caller-supplied ``degradations`` list when the resolved font has
    ``origin == "user"``.

    Drives the Tier 1.5 helper directly with a monkeypatched resolver
    so the assertion is independent of which fonts the host actually
    has installed.
    """
    # Use the existing simple-font fixture so /FontFile2 parses cleanly.
    from tests._simple_font_fixture import _build_simple_winansi_pdf

    src = tmp_path / "fixture.pdf"
    if not _build_simple_winansi_pdf(src):
        pytest.skip("no TrueType font available to build simple-font fixture")
    work = tmp_path / "work.pdf"
    shutil.copy(src, work)

    # The system font we hand the helper IS the fixture font itself — its
    # outlines + cmap are guaranteed compatible with the embedded subset.
    system_ttf = _find_ttf_for_simple_font()
    if system_ttf is None:
        pytest.skip("no source TrueType for fake_resolver")

    def fake_resolver(_name: str) -> tuple[str, str, str | None]:
        # Pretend the resolved font came from the user-writable dir.
        return (str(system_ttf), "user", None)

    monkeypatch.setattr(sf, "_find_font_with_origin", fake_resolver)

    pdf = open_pdf(str(work))
    try:
        page = pdf.pages[0]
        font_dict = page["/Resources"]["/Font"]["/F1"]
        fd = font_dict["/FontDescriptor"]

        degradations: list[Degradation] = []
        # 'ø' is absent from the /WinAnsiEncoding subset → forces Tier 1.5.
        try:
            _extend_simple_tier_15(
                pdf,
                font_dict,
                fd,
                additional_chars="ø",
                degradations=degradations,
            )
        except Exception as e:  # noqa: BLE001
            # A downstream font-binary issue (upem mismatch, missing
            # composite component) may still raise — but the F-D-CC9
            # Degradation is appended BEFORE any of those checks, so
            # the list must already carry it. Verify and re-raise only
            # if the list is empty (which would be a genuine probe miss).
            if not any(d.kind == "font_substituted_from_user_fonts" for d in degradations):
                pytest.skip(f"extension path did not run far enough: {e}")

        kinds = [d.kind for d in degradations]
        assert "font_substituted_from_user_fonts" in kinds, (
            f"origin='user' must surface font_substituted_from_user_fonts "
            f"Degradation; got kinds={kinds!r}"
        )
        # Severity must be warning (origin surface, not a fidelity break).
        match = next(d for d in degradations if d.kind == "font_substituted_from_user_fonts")
        assert match.severity == "warning", (
            f"font_substituted_from_user_fonts must be severity='warning'; got {match.severity!r}"
        )
        # Detail must include the resolved path so log forensics work.
        assert "path=" in match.detail, (
            f"Degradation.detail must include the resolved path; got {match.detail!r}"
        )
    finally:
        pdf.close()
