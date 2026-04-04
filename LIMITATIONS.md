# Known Limitations

## Text editing

- Cross-paragraph reflow is not supported — text reflows within a single paragraph only
- Mixed-font paragraphs (e.g., bold words within regular text) lose inline formatting during reflow
- Text inside Form XObjects (reusable content streams) is not found or edited
- Justified text may lose justification after reflow (replaced with left-aligned)
- Empty string replacement removes the text visually but leaves the operator structure intact

## Transformed text

- Rotated text (non-zero CTM rotation) is extracted correctly and `find()` returns matches for small angles (tested up to 5 degrees). Replacement positioning under rotation has not been extensively tested.
- Horizontally scaled text (CTM Tz) is found and replaceable — the engine correctly handles width changes.
- Very small text (6pt) is found and replaceable.
- Character spacing (`Tc` operator) is handled correctly — text is stored as a single string in the content stream, not as individual characters with spaces.

## Cross-tool compatibility

- PDFs previously edited by PyMuPDF (redact + re-insert) can be read, searched, and edited by pdf-edit-engine. The mixed font origins (original + PyMuPDF-added) do not cause issues.

## Font handling

- Tier 2 font extension (full re-embed) requires the matching system font to be installed
- CJK fonts with 30,000+ glyphs have not been tested
- Type 3 fonts (bitmap/procedural) are not supported for extension
- Emoji and other multi-codepoint characters cannot be rendered if the font lacks those glyphs (reported via FidelityReport)

## Encoding

- Identity-H CIDFont is the primary encoding path; WinAnsi is fully supported
- Complex color spaces (ICC profiles, Separation, DeviceN) are tracked but not fully resolved
- Custom encodings with unusual /Differences arrays may fail
- MacRoman and other legacy encodings are less tested

## Performance

Benchmarks on a typical developer machine (Windows 11, Python 3.12, WinAnsi PDFs):

| Operation | Input | Time |
|-----------|-------|------|
| `get_text()` | 100-page PDF | ~0.3s |
| `find()` | 100-page PDF (900 matches) | ~0.3s |
| `replace()` | Single page | ~0.03s |
| `batch_replace()` | 50 edits | ~0.1s |

Identity-H CIDFont PDFs (Chrome, Google Docs, Word) may be slower due to CMap parsing and width lookups. Performance scales linearly with page count.

## PDF compatibility

- Encrypted PDFs cannot be edited without providing the password
- PDF/A compliance is not maintained after editing
- Digital signatures are invalidated by any edit (inherent to how PDF signatures work)
- Linearization (fast web view) is removed after editing
- Right-to-left text is not corrupted but reflow does not handle RTL properly
- XFA forms are not supported
- Content streams with non-UTF-8 byte sequences in operators are rejected with a clear error
