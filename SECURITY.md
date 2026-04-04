# Security

## Threat Model

pdf-edit-engine processes untrusted PDF inputs, including content streams and embedded font binaries. The library is designed for server-side and local use where PDF files may come from external sources.

## Security Measures

- **Path validation**: All public functions that write files validate output paths before any I/O. This prevents writing to empty paths, existing directories, or paths with nonexistent parent directories.
- **No network calls**: The library makes zero network requests. All operations are local file I/O.
- **No code execution**: No `eval()`, `exec()`, `os.system()`, or `subprocess` with `shell=True`. CMap parsing uses safe text parsing, not PostScript evaluation.
- **No credential storage**: Encryption/decryption passwords are passed directly to pikepdf and never logged, printed, or persisted.

## Dependencies

All dependencies use permissive licenses with no known critical vulnerabilities:

| Package | License | Purpose |
|---------|---------|---------|
| pikepdf | MPL-2.0 | Content stream parsing and PDF manipulation |
| fonttools | MIT | Font extraction, metrics, and subset extension |
| pdfminer.six | MIT | Text extraction with positional data |

## Reporting

If you discover a security vulnerability, please open an issue at [github.com/AryanBV/pdf-edit-engine/issues](https://github.com/AryanBV/pdf-edit-engine/issues).
