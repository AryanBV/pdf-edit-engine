"""INV-W0-9 (P0): no ``{exc}`` / ``str(exc)`` in user-visible text.

This probe is the load-bearing enforcement of the F-C-03 fix: pikepdf,
fontTools, and OS exception bodies can carry attacker-controlled bytes
(file paths, struct offsets, partial PDF-object snippets, malformed-input
echoes). Surfacing them through a user-visible sink — a ``raise SomeError(...)``
message, an ``EditResult.warnings`` entry, or a ``Degradation(detail=...)``
field — is a passive information-disclosure vector.

The convention closed by ARY-348 commit 07: replace ``f"...{exc}..."`` and
``str(exc)`` with ``f"...{type(exc).__name__}..."`` in every user-visible
sink, and emit a ``logger.error("...", exc_info=True)`` BEFORE the raise/warn
so the forensic traceback survives in logs.

This probe walks every ``.py`` under ``src/pdf_edit_engine/`` via the AST
and asserts that no user-visible-sink call interpolates ``exc``. Forensic
``logger.error/.warning/.debug/.info`` calls (and the comments documenting
the F-C-03 fix) are exempt — they are not user-visible.

A single comprehensive test is intentional: the invariant is one
predicate over the whole src tree. Splitting per-sink-class into multiple
tests would multiply skipped/missing-source noise without adding regression
guard. Accumulating violations into a single failure message keeps the CI
log directly actionable: each offending ``path:lineno`` appears on its own
line.
"""

from __future__ import annotations

import ast
import pathlib

# Resolve src tree once at import. Tests run from the repo root, so
# ``__file__`` lives at ``tests/invariants/test_w0_9_*.py``.
SRC_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent / "src" / "pdf_edit_engine"

# User-visible exception classes — anything raised with these as the call
# target is a caller-facing message and must not interpolate ``exc``.
USER_VISIBLE_EXC_CLASSES = frozenset(
    {
        "PDFEditError",
        "FontNotFoundError",
        "EncodingError",
        "OperatorError",
        "ReflowError",
    }
)

# User-visible dataclass / kwarg sinks. ``Degradation(detail=...)`` and
# ``EditResult(warnings=[...])`` both surface to callers via the public
# return type.
USER_VISIBLE_DATACLASS_NAMES = frozenset({"Degradation", "EditResult", "FidelityReport"})
USER_VISIBLE_FIELD_NAMES = frozenset({"detail", "warnings"})


def _is_exc_fstring(node: ast.AST) -> bool:
    """True iff *node* is an f-string interpolating the bare name ``exc``.

    Catches ``f"...{exc}..."`` and ``f"...{exc!r}..."`` and
    ``f"...{exc:fmt}...".`` Does NOT catch ``f"...{type(exc).__name__}..."``
    (that's the convention we're enforcing) — we only flag when the
    formatted value's expression IS a bare ``Name(id="exc")``.
    """
    if not isinstance(node, ast.JoinedStr):
        return False
    for piece in node.values:
        if (
            isinstance(piece, ast.FormattedValue)
            and isinstance(piece.value, ast.Name)
            and piece.value.id == "exc"
        ):
            return True
    return False


def _is_str_exc_call(node: ast.AST) -> bool:
    """True iff *node* is ``str(exc)`` (optionally subscripted, e.g. ``str(exc)[:80]``).

    Handles the surgeon.py:1243 truncation pattern that the F-C-03 fix
    removed entirely.
    """
    target = node
    if isinstance(target, ast.Subscript):
        target = target.value
    return (
        isinstance(target, ast.Call)
        and isinstance(target.func, ast.Name)
        and target.func.id == "str"
        and len(target.args) == 1
        and isinstance(target.args[0], ast.Name)
        and target.args[0].id == "exc"
    )


def _contains_exc_leak(node: ast.AST) -> bool:
    """Walk *node*'s subtree, return True iff any descendant leaks ``exc``."""
    return any(_is_exc_fstring(child) or _is_str_exc_call(child) for child in ast.walk(node))


def _collect_violations_in_module(module: ast.Module, path: pathlib.Path) -> list[str]:
    """Collect all user-visible-sink leaks in *module*.

    Returns a list of human-readable ``path:lineno: kind`` strings, one
    per violation. Empty list = no violations.
    """
    violations: list[str] = []
    rel = path.relative_to(SRC_ROOT.parent.parent)

    for node in ast.walk(module):
        # Case 1: ``raise SomeError(...)`` where SomeError is in our
        # user-visible set. Inspect every argument of the constructor
        # call; the message is typically arg[0] but kwargs can carry it
        # too (defensive).
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            func = node.exc.func
            cls_name: str | None = None
            if isinstance(func, ast.Name):
                cls_name = func.id
            elif isinstance(func, ast.Attribute):
                cls_name = func.attr
            if cls_name in USER_VISIBLE_EXC_CLASSES:
                for arg in node.exc.args:
                    if _contains_exc_leak(arg):
                        violations.append(
                            f"{rel}:{arg.lineno}: raise {cls_name}(...) interpolates exc"
                        )
                for kw in node.exc.keywords:
                    if _contains_exc_leak(kw.value):
                        violations.append(
                            f"{rel}:{kw.value.lineno}: raise {cls_name}"
                            f"(..., {kw.arg}=...) interpolates exc"
                        )

        # Case 2 + 3: ``Degradation(detail=...)``, ``EditResult(warnings=[...])``,
        # ``FidelityReport(...)`` keyword arguments. Inspect the value of every
        # keyword whose name is in USER_VISIBLE_FIELD_NAMES, plus any nested
        # list/JoinedStr inside.
        if isinstance(node, ast.Call):
            func = node.func
            cls_name = None
            if isinstance(func, ast.Name):
                cls_name = func.id
            elif isinstance(func, ast.Attribute):
                cls_name = func.attr
            if cls_name in USER_VISIBLE_DATACLASS_NAMES:
                for kw in node.keywords:
                    if kw.arg in USER_VISIBLE_FIELD_NAMES and _contains_exc_leak(kw.value):
                        violations.append(
                            f"{rel}:{kw.value.lineno}: {cls_name}({kw.arg}=...) interpolates exc"
                        )

    return violations


def test_inv_w0_9_no_exc_bytes_in_user_visible_sinks() -> None:
    """No source file under ``src/pdf_edit_engine/`` may interpolate ``exc``
    or ``str(exc)`` into a user-visible sink (raise message, Degradation
    detail, EditResult warnings)."""
    assert SRC_ROOT.exists(), f"src tree not found at {SRC_ROOT}"

    all_violations: list[str] = []
    for py_path in SRC_ROOT.rglob("*.py"):
        source = py_path.read_text(encoding="utf-8")
        module = ast.parse(source, filename=str(py_path))
        all_violations.extend(_collect_violations_in_module(module, py_path))

    assert not all_violations, (
        "INV-W0-9 violated — F-C-03 leak re-introduced. User-visible sinks "
        "must use ``f'...{type(exc).__name__}'`` or ``logger.error(..., "
        "exc_info=True)`` instead of interpolating ``exc`` or ``str(exc)``. "
        "Offending sites:\n  " + "\n  ".join(all_violations)
    )


def test_inv_w0_9_probe_self_test_synthetic_violation() -> None:
    """Probe self-test: confirm the AST helpers actually fire on a
    synthetic violation. Without this, a refactor that breaks
    ``_is_exc_fstring`` would silently disarm the probe.

    Three synthetic snippets exercise the three patterns the main probe
    must catch: f-string in raise, f-string in Degradation.detail, and
    str(exc) call.
    """
    # Pattern 1: f-string with bare ``exc`` in a raise of a user-visible
    # class. Use ``compile``+``ast.parse`` rather than executing the code
    # so we don't actually need ``exc`` to be in scope.
    snippet_1 = ast.parse('raise PDFEditError(f"oops: {exc}")')
    # Pattern 2: ``Degradation(detail=f"...{exc}")``.
    snippet_2 = ast.parse('Degradation(kind="x", detail=f"why: {exc}")')
    # Pattern 3: ``str(exc)[:80]`` truncation.
    snippet_3 = ast.parse("msg = str(exc)[:80]")

    fake_path = SRC_ROOT / "_synthetic_violation.py"

    v1 = _collect_violations_in_module(snippet_1, fake_path)
    v2 = _collect_violations_in_module(snippet_2, fake_path)

    assert v1, "probe failed to detect raise PDFEditError(f'...{exc}')"
    assert v2, "probe failed to detect Degradation(detail=f'...{exc}')"

    # _is_str_exc_call is exercised at the helper level (it's used in
    # _contains_exc_leak which the violations collector walks). The
    # assertion below confirms the helper recognizes the truncation form.
    assign = snippet_3.body[0]
    assert isinstance(assign, ast.Assign)
    assert _is_str_exc_call(assign.value), (
        "probe helper _is_str_exc_call failed to detect str(exc)[:80]"
    )
