# Security Review — v0.1.2

Output of the focused security review against the v0.1.2 diff
(`feat/v0.1.2-architecture` vs `main`, 14 commits including the
Ultimate Audit Charter fixes plus cleanup batches 1 & 2).

## Method

1. Baseline read of `SECURITY.md`'s claimed mitigations.
2. Pinned-version dependency CVE scan with `pip-audit`.
3. Targeted code review of every src/ file changed by v0.1.2 with a
   focus on input-handling primitives (`_pathutil`, `open_pdf`,
   `encoding._init_identity_h`, `system_fonts._fast_lookup`).
4. Empirical verification of the most security-critical change
   (`validate_output_path`) using a Windows directory junction
   (no-admin path) to bypass the documented mitigation.

## Findings

### F1 — Path-traversal: symlink check was logically dead (HIGH, fixed in this review)

**File:** `src/pdf_edit_engine/_pathutil.py` (lines 39-83 on the
v0.1.2 candidate, before this fix)

The first attempt at the v0.1.2 fix called
`Path(path).resolve()` *before* `_has_symlink_in_path()`. By Python
contract, `resolve()` follows every symlink in its argument — so the
walker that came after only ever saw a chain with no symlink
components. Outputs through symlinked or junctioned directories were
silently accepted.

Empirically verified: a Windows directory junction created via
`mklink /J` (which does not require admin or Developer Mode)
slipped past the entire validation chain. The same input on Linux
behaves identically because POSIX symlinks are followed by
`Path.resolve()` too.

Compounding the bug: even if `resolve()` had been deferred,
`Path.is_symlink()` returns `False` for Windows directory
junctions (junctions carry a different reparse-point tag than
NTFS symlinks). The parent-walk approach was incapable of
detecting the most common Windows attack vector.

**Exploit shape (Linux server-side, equally relevant Windows):**

```
attacker can write into output_dir/  (e.g. shared upload area)
attacker:    ln -s /etc/cron.d output_dir/innocuous_subdir
caller:      replace_block(..., output_path="output_dir/innocuous_subdir/0attacker.pdf")
result:      engine writes a PDF to /etc/cron.d/0attacker.pdf
            → cron executes attacker-controlled content
            → privilege escalation to root if engine ran as root
```

**Fix landed in this review:** replaced the
`resolve()`-then-walk approach with a direct
`os.path.realpath(path)` vs `os.path.abspath(path)` comparison
(case-normalized for Windows). The two functions canonicalize
identically *except* for symlink/junction following — when they
differ, a link was traversed.

```python
def _path_traverses_link(path: str) -> bool:
    real = os.path.realpath(path)
    absolute = os.path.abspath(path)
    return os.path.normcase(real) != os.path.normcase(absolute)
```

This catches POSIX symlinks AND Windows junctions on both
platforms. Probe `INV-M-2` was strengthened to actually create a
junction (via `mklink /J`, which works without admin) and assert
rejection on every CI host — no more environment-dependent silent
skip.

Empirical confirmation post-fix:

```
$ python -m pytest tests/invariants/test_m_2_path_traversal.py -v
... 5 passed
```

### F2 — `open_pdf` `**kwargs` passthrough was an open contract (LOW, fixed)

**File:** `src/pdf_edit_engine/_pathutil.py:open_pdf`

Pre-fix signature was `open_pdf(path: str | Path, **kwargs: Any)`,
forwarding everything to `pikepdf.Pdf.open`. Any future pikepdf
release that adds a side-effecting kwarg (e.g. a callback or a
flag that disables a security check) would automatically be
exposed through every public-API entry point of this package
without explicit opt-in.

**Fix landed in this review:** type-narrowed to explicit
`password: str | bytes | None = None,
allow_overwriting_input: bool = False`. Future pikepdf kwargs
must be explicitly added here to be reachable from this package.

### F3 — lxml ≤ 6.0.3 XXE on PDF metadata (MEDIUM, mitigated)

**Source:** CVE-2026-41066. pikepdf is a transitive consumer of
lxml when parsing XMP metadata streams. lxml < 6.1.0's default
parser config has `resolve_entities=True`, allowing crafted XML
entities in PDF metadata to read local files via the XXE
classic primitive.

**Fix landed in this review:** added an explicit
`lxml>=6.1.0` constraint to `pyproject.toml::dependencies`.
Even though pikepdf does not pin it tightly enough, our
package now does. Documented as a security pin with the CVE
ID inline.

### F4 — pytest ≤ 9.0.2 local DoS / privesc (LOW, mitigated)

**Source:** CVE-2025-71176. `pytest` ≤ 9.0.2 uses
predictable `/tmp/pytest-of-{user}` directories. Dev-only —
the runtime library does not depend on pytest.

**Fix landed in this review:** bumped `dev` extra to
`pytest>=9.0.3` in `pyproject.toml`.

### F5 — Hostile ToUnicode CMap consumption (residual, accepted)

**File:** `src/pdf_edit_engine/encoding.py::_init_identity_h`

This function reads `/ToUnicode` bytes from an attacker-supplied
PDF and feeds them to `pdfminer.cmapdb.CMapParser`. A
vulnerability in pdfminer's CMap parser would land in this
process. We mitigate by:

- Pinning `pdfminer.six >= 20231228` (current floor) — recent
  parser revisions, away from any known historical CVE path.
- Running `pip-audit` in CI on every PR (Step 7 of the v0.1.2
  release plan adds this job to `.github/workflows/ci.yml`).

We do not mitigate by sandboxing or forking the parser. This is
documented as residual risk in `SECURITY.md` and accepted.

### F6 — Memory/CPU DoS via giant fonts/streams (residual, accepted)

The library imposes no explicit size or time limits on parsing.
Callers who run this against untrusted PDFs in a multi-tenant
context must impose external resource caps. Documented as
residual risk in `SECURITY.md`. **Out of scope per the
review's exclusion list (DoS / resource exhaustion is excluded
from severity rating).**

## Non-findings (looked, found nothing)

- No SQL or NoSQL paths exist. The library does no database I/O.
- No `eval()`, `exec()`, `compile()`, `os.system()`, or
  `subprocess.run(..., shell=True)` anywhere in `src/`.
- No `pickle`, `marshal`, or `yaml.unsafe_load`.
- No HTTP, socket, or DNS calls.
- No JSON-deserialization of attacker-controlled input.
- All `# type: ignore` markers carry specific error codes; no
  bare ignores that could mask security-relevant type holes.
- `system_fonts._fast_lookup` only walks the platform's standard
  font directories (not user-controlled). No path-traversal
  surface.

## Conclusion

The v0.1.2 candidate had **one HIGH-severity vulnerability**
(F1) introduced by an incorrect implementation of an
otherwise-good design intent. It was discoverable only through
empirical testing — the unit-test probe was conditionally
skipped on the development host. The fix landed in this review
session, with both the implementation correction and a
strengthened probe that runs unconditionally on every CI host
(via Windows directory junctions, which require no admin).

Two LOW-severity dependency CVEs (F3, F4) were mitigated by
pinning version floors. F2 is defense in depth. F5 and F6 are
documented residual risks consistent with the threat model.

**v0.1.2 is now release-eligible from a security standpoint**,
contingent on the rest of the release plan (test coverage
re-measurement, full CI pass on Linux + Windows, build
verification).
