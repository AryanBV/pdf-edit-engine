"""INV-W0-8 — composite-glyph recursion is depth-bounded.

Closes F-B-03 (security audit, Medium) / n-1 (code-review Nit) / m-4
(code-review Minor) — three lenses on one defect, deduped under R-1.

Pre-fix, ``_collect_component_names`` recursed without a depth cap. A
malicious or pathological font with a composite chain longer than
Python's default recursion limit (typically 1000) raised
``RecursionError`` from the public API, violating INV-L-1's charter
framing that every public-API failure must be a ``PDFEditError``
subclass.

Root fix (NOT a patch): a ``MAX_COMPOSITE_DEPTH = 64`` constant +
explicit depth check in ``_collect_component_names``. When the chain
exceeds the cap, ``FontNotFoundError`` is raised, which IS a member of
``_FONT_EXTEND_FAIL_EXCS`` — so the failure propagates through
``_inject_glyph_in_place`` -> ``_extend_tier2`` -> caller, where it is
translated into ``EditResult.success=False`` plus a
``font_extension_failed`` Degradation per the existing INV-J-5
contract.

Adding ``RecursionError`` to ``_FONT_EXTEND_FAIL_EXCS`` would have been
a patch (surface a stack overflow as an error); the depth cap removes
the failure mode entirely.
"""

from __future__ import annotations

import pytest

from pdf_edit_engine.errors import FontNotFoundError
from pdf_edit_engine.fonts import MAX_COMPOSITE_DEPTH, _collect_component_names


class _FakeComponent:
    """Duck-typed stand-in for fontTools' GlyphComponent.

    ``_collect_component_names`` only reads ``component.glyphName``, so a
    minimal mock is sufficient to exercise the depth-bound branch without
    the cost of constructing a real TrueType glyf table.
    """

    def __init__(self, glyph_name: str) -> None:
        self.glyphName = glyph_name


class _FakeGlyph:
    """Duck-typed stand-in for fontTools' Glyph object.

    ``_collect_component_names`` only reads ``isComposite()`` and
    ``.components`` on the input glyph; for chained recursion it then
    re-enters via the ``font["glyf"][name]`` lookup, which we model with
    ``_FakeFont`` below. Composite chain links carry exactly one
    component pointing at the next link's name.
    """

    def __init__(self, name: str, components: list[_FakeComponent]) -> None:
        self.name = name
        self.components = components

    def isComposite(self) -> bool:
        return bool(self.components)


class _FakeGlyfTable:
    def __init__(self, glyphs: dict[str, _FakeGlyph]) -> None:
        self.glyphs = glyphs

    def __getitem__(self, name: str) -> _FakeGlyph:
        return self.glyphs[name]


class _FakeFont:
    """Duck-typed stand-in for fontTools' TTFont, exposing only the
    ``font["glyf"]`` access path that ``_collect_component_names`` uses.
    """

    def __init__(self, glyphs: dict[str, _FakeGlyph]) -> None:
        self._tables = {"glyf": _FakeGlyfTable(glyphs)}

    def __getitem__(self, key: str) -> _FakeGlyfTable:
        return self._tables[key]


def _build_chain(depth: int) -> tuple[_FakeGlyph, _FakeFont]:
    """Construct a synthetic composite-glyph chain of length ``depth``.

    Each link carries exactly one component pointing at the next link;
    the final link is a simple (non-composite) glyph that terminates the
    walk. Returns the root glyph and a font containing every link.
    """
    glyphs: dict[str, _FakeGlyph] = {}
    # Leaf: simple glyph, no components.
    leaf_name = f"link_{depth}"
    glyphs[leaf_name] = _FakeGlyph(leaf_name, [])
    # Build the chain from leaf back up to the root.
    for i in range(depth - 1, -1, -1):
        name = f"link_{i}"
        next_name = f"link_{i + 1}"
        glyphs[name] = _FakeGlyph(name, [_FakeComponent(next_name)])
    root = glyphs["link_0"]
    return root, _FakeFont(glyphs)


def test_inv_w0_8_max_composite_depth_constant_is_locked() -> None:
    """INV-W0-8: ``MAX_COMPOSITE_DEPTH`` is locked at 64.

    Real Latin composites nest 2-3 deep; the deepest known commercial
    font hits ~16. 64 leaves headroom while clearly bounding adversarial
    inputs. Bumping the cap is a behavior change that requires user
    signoff — guard it as a load-bearing constant.
    """
    assert MAX_COMPOSITE_DEPTH == 64


def test_inv_w0_8_depth_100_chain_raises_font_not_found_not_recursion_error() -> None:
    """INV-W0-8 / F-B-03: a depth-100 composite chain MUST raise
    ``FontNotFoundError``, NOT ``RecursionError``.

    A 100-link chain comfortably exceeds ``MAX_COMPOSITE_DEPTH`` (64)
    while staying well below Python's default recursion limit (~1000).
    Pre-fix, this branch returned silently or raised ``RecursionError``
    on deeper chains; post-fix the depth cap fires first and surfaces
    ``FontNotFoundError`` — a ``PDFEditError`` subclass and a member of
    ``_FONT_EXTEND_FAIL_EXCS`` — which routes through the existing
    fail-translation path at every caller.
    """
    root, font = _build_chain(depth=100)
    with pytest.raises(FontNotFoundError) as excinfo:
        _collect_component_names(root, font)
    # The message must reference the cap so debuggers can identify the
    # exact contract violation, not some unrelated FontNotFoundError.
    assert "composite glyph depth exceeds" in str(excinfo.value)
    assert str(MAX_COMPOSITE_DEPTH) in str(excinfo.value)


def test_inv_w0_8_chain_at_cap_does_not_raise() -> None:
    """INV-W0-8: a chain whose depth equals ``MAX_COMPOSITE_DEPTH`` MUST
    succeed.

    The cap is exclusive (`> MAX_COMPOSITE_DEPTH`, not `>=`), so a
    legitimate composite that nests exactly to the cap renders cleanly.
    This guards against off-by-one regressions in the depth check.
    """
    root, font = _build_chain(depth=MAX_COMPOSITE_DEPTH)
    # Must not raise. The returned list is the chain in injection order
    # (leaves first, roots last); we only assert non-empty here — the
    # exact ordering is covered by ``test_collect_components_recurses_dependencies``.
    names = _collect_component_names(root, font)
    assert len(names) == MAX_COMPOSITE_DEPTH
