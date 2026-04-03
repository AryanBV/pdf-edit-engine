# PDF Content Stream Internals

Reference for working with PDF content streams in pdf-edit-engine.

## Content Stream Architecture

PDF pages contain a content stream: a sequence of operators that draw text, images, and paths.
Text is drawn within **BT/ET blocks** (Begin Text / End Text).

Key text operators:
- `Tf` — set font and size: `/F1 12 Tf`
- `Td` — move text position: `72 700 Td`
- `Tm` — set text matrix (absolute position): `1 0 0 1 72 700 Tm`
- `Tj` — show string: `<0048656C6C6F> Tj`
- `TJ` — show string with glyph positioning: `[<0048> -120 <656C6C6F>] TJ`
- `T*` — move to next line
- `'` — move to next line and show string

Content stream order may NOT match visual/reading order. Bullets and list items
are often rendered out of document order. Always use position-based matching.

## Identity-H CIDFont Encoding

Most modern PDFs (including resumes) use CIDFont with Identity-H encoding:
- Text strings contain **2-byte CID glyph indices**, NOT Unicode characters
- `<004800650066>` = CIDs 0x0048, 0x0065, 0x0066 — glyph IDs, not ASCII
- The **ToUnicode CMap** maps CIDs back to Unicode for text extraction
- To render new text, you must map Unicode → CID using the reverse of ToUnicode

## ToUnicode CMap Format

The CMap contains two mapping types:

### bfchar — Single CID Mappings
```
beginbfchar
<0048> <0048>   # CID 0x0048 → U+0048 ('H')
<0065> <0065>   # CID 0x0065 → U+0065 ('e')
endbfchar
```

### bfrange — Range Mappings

**Sequential form** (CID range maps to sequential Unicode):
```
beginbfrange
<0041> <005A> <0041>  # CIDs 0x0041-0x005A → U+0041-U+005A ('A'-'Z')
endbfrange
```

**Array-of-arrays form** (CID range maps to explicit Unicode values):
```
beginbfrange
<00C0> <00C3> [<00C0> <00C1> <00C2> <00C3>]
endbfrange
```

Both forms must be handled. Real PDFs mix bfchar and bfrange in the same CMap.

## Glyph Positioning

The displacement formula for horizontal writing:
```
tx = ((w0 - Tj/1000) * Tfs + Tc + Tw) * Th
```
Where:
- `w0` = glyph width from /W array (in CID units, /1000 for text space)
- `Tj` = positioning value from TJ array (negative = move right)
- `Tfs` = current font size
- `Tc` = character spacing
- `Tw` = word spacing (applied only to space character, CID 0x0003 typically)
- `Th` = horizontal scaling

## Content Stream Manipulation

Use pikepdf's `parse_content_stream()` and `unparse_content_stream()`:
```python
operators = pikepdf.parse_content_stream(page)
# operators is list of (operands, operator) tuples
# Modify operands as needed
new_stream = pikepdf.unparse_content_stream(operators)
page.Contents = pdf.make_stream(new_stream)
```

Do NOT use TokenFilter — it doesn't provide the random access needed for
targeted operator replacement.
