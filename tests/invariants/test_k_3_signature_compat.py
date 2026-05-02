"""INV-K-3 (P1): public API signatures are a superset of the v0.1.1 baseline.

Rather than installing an old version side-by-side (heavy in this
session), the probe locks the v0.1.1 signatures into a frozen baseline
table inline. New parameters MUST have defaults; existing positional
parameters MUST not be removed or reordered.
"""

from __future__ import annotations

import inspect

import pdf_edit_engine as engine

# Baseline parameter names (positional or kw-or-positional) for the
# functions exported as of v0.1.1. Keep this list updated only when
# breaking changes are intentional and documented.
# Verified against the v0.1.1 release commit (4396ec9 "chore(ARY-281): prep v0.1.1 release").
V011_SIGNATURES: dict[str, list[str]] = {
    "find": ["pdf_path", "search_text"],
    "get_text": ["pdf_path"],
    "replace": ["pdf_path", "match", "new_text", "output_path"],
    "replace_all": ["pdf_path", "search", "replacement", "output_path"],
    "batch_replace": ["pdf_path", "edits", "output_path"],
    "merge_pdfs": ["pdf_paths", "output_path"],
    "split_pdf": ["pdf_path", "output_dir"],
    "rotate_pages": ["pdf_path", "pages", "angle", "output_path"],
    "delete_pages": ["pdf_path", "pages", "output_path"],
    "encrypt_pdf": ["pdf_path", "owner_pass", "user_pass", "output_path"],
    "decrypt_pdf": ["pdf_path", "password", "output_path"],
}


def test_inv_k_3_signature_superset() -> None:
    """For each baseline-known function, the current signature must
    include all baseline-named parameters in the same order at the
    front, and any added parameters must have defaults."""
    failures: list[str] = []
    for fn_name, baseline_params in V011_SIGNATURES.items():
        fn = getattr(engine, fn_name, None)
        if fn is None:
            failures.append(f"{fn_name}: removed from public API")
            continue
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        # Compare leading positional/kw-or-pos params
        leading = [
            p
            for p in params
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.POSITIONAL_ONLY,
            )
        ]
        for i, baseline_name in enumerate(baseline_params):
            if i >= len(leading):
                failures.append(f"{fn_name}: baseline param '{baseline_name}' missing at index {i}")
                break
            if leading[i].name != baseline_name:
                failures.append(
                    f"{fn_name}: param[{i}] is {leading[i].name!r} "
                    f"but baseline expects {baseline_name!r}"
                )
                break
        # Any positional param past the baseline length must have a default
        for p in leading[len(baseline_params) :]:
            if p.default is inspect.Parameter.empty:
                failures.append(f"{fn_name}: new positional param {p.name!r} has no default")
    if failures:
        raise AssertionError("v0.1.1 signature compatibility broken:\n  " + "\n  ".join(failures))
