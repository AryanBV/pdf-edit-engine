"""Render the Identity-H gate (b) PDFs and compose the regression image."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
DPI = 220
SCALE = DPI / 72.0


def render_full(pdf_path: Path) -> Image.Image:
    doc = pdfium.PdfDocument(str(pdf_path))
    img = doc[0].render(scale=SCALE).to_pil()
    doc.close()
    return img


def add_label(img: Image.Image, text: str) -> Image.Image:
    canvas = img.copy()
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    pad = 4
    box = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
    draw.rectangle(box, fill="white", outline="black")
    draw.text((0, 0), text, fill="black", font=font)
    return canvas


def find_replacement_y(pdf_path: Path) -> float:
    """Return PDF-coords y of the line containing 'Software Engineer'."""
    with pikepdf.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        cur_y = 0.0
        for operands, op in ops:
            os_ = str(op)
            if os_ == "Tm":
                cur_y = float(operands[5])
            elif os_ in ("Td", "TD"):
                cur_y += float(operands[1])
            elif os_ == "Tj":
                # Could decode CID bytes here, but coarse: just return mid y
                pass
        # Fallback: page midpoint
        return float(page.MediaBox[3]) / 2


def main() -> None:
    baseline = ROOT / "out_identity_h_baseline.pdf"
    algo_a = ROOT / "out_identity_h_algo_a.pdf"
    if not baseline.exists() or not algo_a.exists():
        print("Identity-H output PDFs missing")
        return

    # Crop the upper portion of the page (Software Engineer is on line 2)
    bands = []
    for fname, label in [
        (baseline, "BASELINE (v0.1.2)"),
        (algo_a, "Algo A (Tz, monkey-patched flat)"),
    ]:
        img = render_full(fname)
        # Crop top quarter of page
        top_band = img.crop((0, 0, img.width, img.height // 4))
        labeled = add_label(top_band, label)
        bands.append(labeled)

    # Compose horizontally
    gap = 12
    w = sum(b.width for b in bands) + gap
    h = max(b.height for b in bands)
    canvas = Image.new("RGB", (w, h), "white")
    x = 0
    for b in bands:
        canvas.paste(b, (x, 0))
        x += b.width + gap
    out = ROOT / "identity_h_regression.png"
    canvas.save(out)
    print(f"wrote {out} ({canvas.size})")


if __name__ == "__main__":
    main()
