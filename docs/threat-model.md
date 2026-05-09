# Threat Model

**Status: stub for v0.1.4.** This document captures the threat model
once formal STRIDE analysis is performed against the v0.1.4 hardened
API surface. v0.1.3 ships with the mitigations enumerated below and
the open items in `LIMITATIONS.md`. The audit evidence backing each
mitigation lives under `experiments/v013_block_3_review/` (gitignored
local artefacts) and the v0.1.3 release notes in `docs/`.

## Tracked threats (carried from v0.1.3 audit)

- **Path traversal via output paths.** Mitigated by
  `_pathutil.validate_output_path` and `_pathutil.validate_output_dir`
  — symlink/junction traversal refused via `realpath != abspath`;
  Windows-specific reserved-name / ADS / extended-prefix / UNC checks
  threaded through `_validate_windows_path`.
- **Information disclosure via raw exception bytes.** Mitigated by
  the `type(exc).__name__` interpolation convention enforced by
  `tests/invariants/test_w0_9_no_exc_bytes_in_user_text.py` and the
  ruff-config commentary in `pyproject.toml`. Forensic detail goes
  to logs only via `logger.error(..., exc_info=True)`.
- **Memory exhaustion via deep composite glyph chains.** Mitigated
  by the `MAX_COMPOSITE_DEPTH=64` cap in `fonts._collect_component_names`.
- **Untrusted XML / XMP via lxml XXE.** Mitigated by the
  `lxml>=6.1.0` floor in `pyproject.toml` (closes CVE-2026-41066).
- **Dependency-supply CVEs in pikepdf / fontTools / pdfminer.six /
  pip.** Mitigated by the dep-floor bumps in v0.1.3 (commit 08):
  `pikepdf>=10`, `fontTools>=4.60.2`, `pdfminer.six>=20251230`,
  `lxml<7`, dev `pip>=25`. `pip-audit` is a release-gate.

## Out of scope for v0.1.3

- Formal denial-of-service profiling (timeouts, fuel limits).
- Multi-process concurrency hardening (file-locking, queue).
- Adversarial PDF fuzzing campaign — planned for v0.1.4.
- Sandboxing / SECCOMP for the font-extraction subprocess.
- A signed-release / supply-chain attestation pipeline (Sigstore /
  PEP 740 attestations).

## Planned for v0.1.4

The full STRIDE table will be expanded here once the v0.1.4 hardening
work lands. This stub exists so downstream contributors have a known
anchor for the document.
