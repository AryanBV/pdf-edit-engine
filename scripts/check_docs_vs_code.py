"""Guard against documentation drift.

Asserts a handful of invariants that must hold between the code and the
documentation. Run in CI after test + lint + typecheck.

Invariants checked:
  1. If ``fonts.py`` defines ``_inject_glyph_in_place``, then
     ``docs/font-pipeline.md`` must mention "Tier 1.5".
  2. ``CHANGELOG.md`` must have a top-level ``## [x.y.z]`` entry whose
     version matches ``__version__`` in ``pdf_edit_engine/__init__.py``.
  3. ``docs/font-pipeline.md`` must NOT still claim the pre-ARY-278
     Tier 2 subset-and-replace strategy (guard against re-regressing
     the doc rewrite).

Exits 0 when all invariants hold, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _assert(cond: bool, message: str, failures: list[str]) -> None:
    if not cond:
        failures.append(message)


def main() -> int:
    failures: list[str] = []

    fonts_src = (ROOT / "src" / "pdf_edit_engine" / "fonts.py").read_text(encoding="utf-8")
    pipeline_doc = (ROOT / "docs" / "font-pipeline.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    init_src = (ROOT / "src" / "pdf_edit_engine" / "__init__.py").read_text(encoding="utf-8")

    # Invariant 1
    if "def _inject_glyph_in_place" in fonts_src:
        _assert(
            "Tier 1.5" in pipeline_doc,
            "docs/font-pipeline.md must describe Tier 1.5 because "
            "fonts.py:_inject_glyph_in_place exists",
            failures,
        )

    # Invariant 2
    m = re.search(r'__version__\s*=\s*["\']([0-9]+\.[0-9]+\.[0-9]+)["\']', init_src)
    if m is None:
        failures.append("Could not parse __version__ from __init__.py")
    else:
        version = m.group(1)
        _assert(
            f"## [{version}]" in changelog,
            f"CHANGELOG.md has no top-level '## [{version}]' entry for the "
            f"current __version__ = {version!r}",
            failures,
        )

    # Invariant 3: guard against re-regressing to the pre-ARY-278 Tier 2
    # "subset fresh system font via retain-gids" strategy. The original
    # pre-PR text had backticks between the tokens ("fonttools
    # `pyftsubset` with `--retain-gids`"), so the pattern here must match
    # the backticked form that actually appeared in that doc — the
    # unbacktick'd space-joined substring never did (ultrareview bug_001).
    _assert(
        "`--retain-gids`" not in pipeline_doc,
        "docs/font-pipeline.md mentions the pre-ARY-278 retain-gids "
        "strategy — the doc rewrite regressed",
        failures,
    )
    # NOTE: we do NOT forbid "Liberation"/"Carlito" in the doc. The
    # engine DOES use a metric-equivalent fallback cascade through
    # `system_fonts._METRIC_EQUIVALENTS` (see docs/font-pipeline.md
    # system-font-matching section). Forbidding the words prevents
    # honestly documenting that behavior — exactly the bug
    # ultrareview bug_006 flagged.

    if failures:
        print("docs-vs-code drift detected:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("docs-vs-code invariants OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
