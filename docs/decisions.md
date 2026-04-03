# Decision Log

Architectural and technical decisions for pdf-edit-engine.

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-03 | Python engine + TS MCP wrapper (two repos) | fonttools irreplaceable; MCP SDK is TypeScript |
| 2026-04-03 | pikepdf + fonttools + pdfminer.six | Converged from two independent research tracks |
| 2026-04-03 | parse_content_stream + unparse_content_stream | Validated in spike; TokenFilter not suitable |
| 2026-04-03 | Identity-H as primary encoding path | Spike confirmed resume uses CIDFont/Identity-H |
| 2026-04-03 | CMap-only font extension as fast path | Spike found "subsets" contain full glyph data |
| 2026-04-03 | No AGPL dependencies | pikepdf MPL, fonttools MIT, pdfminer.six MIT |
| 2026-04-03 | FidelityReport on every edit | Key differentiator vs silent degradation (PyMuPDF) |
| 2026-04-03 | dry_run parameter on all edit methods | Enables AI agents to preview before committing |

## Decision Details

### Python engine + TS MCP wrapper
The PDF editing engine lives in this repo (Python). A separate TypeScript repo will wrap it
as an MCP server. fonttools has no viable JS/TS equivalent, making Python mandatory for the
core engine. The MCP SDK is TypeScript-native, so the wrapper lives separately.

### parse_content_stream over TokenFilter
pikepdf's TokenFilter processes tokens in a streaming fashion but doesn't support random
access to specific operators. Our approach requires locating specific text operators by index
and modifying them in place. parse_content_stream returns a list of (operands, operator)
tuples that can be indexed, modified, and re-serialized via unparse_content_stream.

### Identity-H as primary encoding
Real-world testing (resumes, business documents) showed that modern PDFs overwhelmingly
use CIDFont with Identity-H encoding. WinAnsi and other encodings exist but are secondary.
The engine handles Identity-H first and falls back to simpler encodings.

### FidelityReport as differentiator
Competing approaches (PyMuPDF redact-and-replace) silently degrade formatting. Our engine
returns a FidelityReport with every edit, documenting exactly what changed: whether fonts
were preserved or substituted, whether overflow occurred, and which glyphs (if any) could
not be rendered. This is critical for AI agent consumers that need to verify edit quality.
