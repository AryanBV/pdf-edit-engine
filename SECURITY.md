# Security

## Threat model

`pdf-edit-engine` is a Python library that parses, edits, and writes
PDF files on the local filesystem. It is intended for two consumer
profiles:

1. **Server-side processing of untrusted PDFs.** The PDF input may
   come from external sources — uploads, third-party APIs, document
   ingestion pipelines. The library must not crash, exfiltrate data,
   or write files outside the caller-specified output path.
2. **Local-application use.** The PDF is trusted, but the library is
   embedded in a host application that may pass user-controlled
   `output_path` strings (drag-and-drop, save-as dialogs, scripts).

Threats considered in scope:

- **Hostile PDF content** — malformed structure, attacker-crafted
  ToUnicode CMap streams, oversized embedded fonts, malicious
  metadata.
- **Hostile output paths** — `..`-traversal, symlink redirection,
  pointing at sensitive files.
- **Information leak through error messages** — exception text
  exposing absolute filesystem paths or other host details.
- **Dependency CVEs** in pikepdf, fonttools, pdfminer.six.

Out of scope:

- Network attacks. The library makes zero network requests.
- Cryptographic attacks on PDF encryption itself. Encryption and
  decryption are delegated entirely to pikepdf/qpdf.
- Defenses against the host process being compromised.

## Mitigations

- **Path validation** (`_pathutil.validate_output_path`,
  `validate_output_dir`): output paths are resolved with
  `Path.resolve()` and refused if (a) empty, (b) the resolved
  target is an existing directory (write would shadow it), (c) the
  parent directory does not exist, or (d) **any component of the
  resolved path traverses a symlink**. The symlink check walks
  every parent to the filesystem root — `Path.is_symlink()` only
  inspects the leaf and was insufficient. Enforced as of v0.1.2
  (this contract was previously documented but only partially
  implemented).
- **Single canonical PDF-open entry point**
  (`_pathutil.open_pdf`): every public-API entry point routes
  through this one function. It catches `pikepdf.PasswordError`,
  `pikepdf.PdfError`, `FileNotFoundError`, `IsADirectoryError`,
  and `PermissionError`, translating each to a `PDFEditError`
  subclass with a sanitized message (only the file basename leaks,
  never the absolute path). Adopted v0.1.2 as the INV-L-1 root
  fix.
- **Type-narrowed kwargs**: `open_pdf` accepts only `password=`
  and `allow_overwriting_input=` — no `**kwargs` passthrough.
  Future pikepdf releases that add side-effecting kwargs cannot
  leak through this package implicitly.
- **No shell, no eval, no subprocess**. There is no `eval()`,
  `exec()`, `os.system()`, `subprocess.run(..., shell=True)`, or
  dynamic-import of attacker-controlled names. CMap parsing uses
  pdfminer.six's text parser, not PostScript evaluation.
- **No credential persistence**. Encryption/decryption passwords
  pass directly to pikepdf and are never logged, printed, written
  to disk by this library, or held in module-level state.
- **No network calls**. The library is a pure local-filesystem
  library — no HTTP clients, no socket I/O, no DNS resolution.
- **Supply-chain hygiene**. `pip-audit` runs in CI on every PR
  against the resolved dependency closure (pikepdf, fonttools,
  pdfminer.six and their transitive deps). All direct dependencies
  use permissive licenses with no known critical CVEs at v0.1.2:

  | Package | License | Min version |
  |---------|---------|-------------|
  | pikepdf | MPL-2.0 | 9.0.0 |
  | fonttools | MIT | 4.50.0 |
  | pdfminer.six | MIT | 20231228 |

## Residual risk

- **Hostile ToUnicode CMap on Type0 fonts**.
  `encoding.FontResolver._init_identity_h` reads `/ToUnicode` bytes
  and feeds them to `pdfminer.cmapdb.CMapParser`. A vulnerability
  in pdfminer's CMap parser would land in our process. We mitigate
  by pinning a recent floor on pdfminer.six and running pip-audit
  in CI; we cannot eliminate this attack surface without either
  forking the parser or sandboxing.
- **Memory/CPU DoS via giant fonts or content streams**. As of
  v0.2.0 the engine bounds the two most exploitable read paths: a
  Flate decompression-bomb guard on every embedded-font / CMap /
  ToUnicode read (32 MiB font, 8 MiB ToUnicode *decoded*-size caps;
  raises `FontStreamTooLargeError` → a `font_stream_too_large`
  Degradation) and a graphics-state stack (`q`/`Q`) depth cap of 128
  (raises `OperatorError`). These close the decompression-bomb and
  unbounded-nesting classes. pikepdf and fontTools may still allocate
  large buffers for other oversized structures, and there is no
  engine-side wall-clock timeout, so callers running this library
  against untrusted PDFs should still impose an external resource
  limit (memory cap, wall-clock timeout, container isolation).
- **CFF / OpenType fonts**. As of v0.2.0, CID-keyed (Type0)
  CFF / Type1C fonts ARE extended in place (C.3). The remaining CFF
  shapes the injector does not cover — simple-font (non-CID) CFF,
  CFF2, name-keyed CFF, multi-FD CID, and composite donors — refuse
  honestly (`FontNotFoundError` → a `font_extension_failed`
  Degradation, `success=False`). Either way the failure mode is a
  caller-visible signal, never silent corruption.

## Reporting a vulnerability

Please **do not** file public GitHub issues for security
disclosures. Email the maintainer privately at
**aryansalian5678@gmail.com** with `[pdf-edit-engine SECURITY]`
in the subject line.

We aim to acknowledge within 7 days and ship a fix or mitigation
within 30 days for any confirmed vulnerability rated medium or
higher. Public disclosure will follow a 90-day coordinated window
unless circumstances require sooner action.
