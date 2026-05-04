"""Render full pages and crop the 'Sarah Chen' band by detecting the y position
empirically — render the original at full size, then locate via PDF coords from
content-stream inspection.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pypdfium2 as pdfium

ROOT = Path(__file__).parent
INPUT = ROOT / "input.pdf"

# Find Sarah Chen's text-matrix Y in PDF coords
with pikepdf.open(str(INPUT)) as pdf:
    page = pdf.pages[0]
    page_h = float(page.MediaBox[3])
    print(f"Page height: {page_h}")

    ops = list(pikepdf.parse_content_stream(page))
    cur_tm_y = None
    cur_td_y = 0.0
    cur_y = 0.0
    cur_tm: tuple = (1, 0, 0, 1, 0, 0)
    last_op = ""
    for i, (operands, op) in enumerate(ops):
        op_str = str(op)
        if op_str == "Tm":
            cur_tm = tuple(float(x) for x in operands)  # type: ignore[assignment]
            cur_y = cur_tm[5]
        elif op_str == "Td":
            tx = float(operands[0])
            ty = float(operands[1])
            cur_y += ty
        elif op_str == "TD":
            tx = float(operands[0])
            ty = float(operands[1])
            cur_y += ty
        elif op_str == "T*":
            cur_y -= 12.0  # rough
        if op_str in ("Tj", "TJ"):
            # Decode the string's first 30 bytes
            if op_str == "Tj":
                data = bytes(operands[0])[:40]
            else:
                data = b""
                for item in operands[0]:
                    if isinstance(item, pikepdf.String):
                        data += bytes(item)
                    if len(data) >= 40:
                        break
            try:
                text = data.decode("latin-1", errors="replace")
            except Exception:  # noqa: BLE001
                text = repr(data)
            if "Sarah" in text or "Chen" in text or "Project lead" in text:
                print(f"  op[{i}] y={cur_y:.1f}: {op_str} {text!r}")

# Render full page at 220 DPI for visual reference
doc = pdfium.PdfDocument(str(INPUT))
img = doc[0].render(scale=220 / 72.0).to_pil()
print(f"Rendered: {img.size}")
img.save(ROOT / "_full_render.png")
