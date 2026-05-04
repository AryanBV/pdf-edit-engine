"""Render kerning experiment outputs to PNG and compose the comparison grid.

Renders each output PDF at 220 DPI and crops the "Project lead: Sarah Chen"
region (page-anchored band around y=380, the line where the replacement
lands). Composes:

  comparison.png — 3x2 grid (rows = deltas d15/d25/d40, cols = algo A/B)
  m10_verification.png — 1x2 (cols = algo A/B), Sarah Chen → Søren Müller
  identity_h_regression.png — emitted by a separate Identity-H run
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent

DPI = 220
SCALE = DPI / 72.0  # pdfium scale factor

# Crop region for the "Project lead: Sarah Chen" line.
# SOW MediaBox: 595.25 x 842 (A4). Phase 1 inspect_sow.py confirmed the line
# sits in the lower-middle of the page. We crop a horizontal band ≈ 50pt
# tall around y=400 (PDF coords, origin bottom-left). PIL coords are
# top-left origin so y_top_pil = (page_height_pt - y_top_pdf) * SCALE.
#
# After visual confirmation we may tune; for now use a generous band.
CROP_PDF_X = 50
CROP_PDF_Y_TOP = 245  # PDF-up coords, top edge of band (Sarah Chen at y=222)
CROP_PDF_Y_BOTTOM = 200  # bottom edge of band
CROP_PDF_WIDTH = 500


def render_pdf_to_image(pdf_path: Path) -> Image.Image:
    """Render the FIRST page of the PDF at DPI."""
    doc = pdfium.PdfDocument(str(pdf_path))
    page = doc[0]
    pil_img = page.render(scale=SCALE).to_pil()
    page.close()
    doc.close()
    return pil_img


def crop_band(img: Image.Image, page_height_pt: float = 842.0) -> Image.Image:
    """Crop the 'Project lead' region by PDF coords."""
    px_per_pt = img.height / page_height_pt
    left = int(CROP_PDF_X * px_per_pt)
    right = int((CROP_PDF_X + CROP_PDF_WIDTH) * px_per_pt)
    top = int((page_height_pt - CROP_PDF_Y_TOP) * px_per_pt)
    bottom = int((page_height_pt - CROP_PDF_Y_BOTTOM) * px_per_pt)
    return img.crop((left, top, right, bottom))


def add_label(img: Image.Image, text: str) -> Image.Image:
    """Stamp `text` at top-left."""
    canvas = img.copy()
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    pad = 4
    box = (
        bbox[0] - pad,
        bbox[1] - pad,
        bbox[2] + pad,
        bbox[3] + pad,
    )
    draw.rectangle(box, fill="white", outline="black")
    draw.text((0, 0), text, fill="black", font=font)
    return canvas


def compose_grid(rows: list[list[Image.Image]], gap: int = 12) -> Image.Image:
    """Stack rows of images vertically; each row's images horizontally."""
    row_w = max(sum(im.width for im in row) + gap * (len(row) - 1) for row in rows)
    total_h = sum(max(im.height for im in row) for row in rows) + gap * (len(rows) - 1)
    canvas = Image.new("RGB", (row_w, total_h), "white")
    y = 0
    for row in rows:
        x = 0
        max_h = max(im.height for im in row)
        for im in row:
            canvas.paste(im, (x, y))
            x += im.width + gap
        y += max_h + gap
    return canvas


def main() -> None:
    cases_grid = ["d15", "d25", "d40"]
    # 3x2 comparison grid
    rows = []
    for label in cases_grid:
        row = []
        for algo in ("a", "b"):
            pdf = ROOT / f"out_{algo}_{label}.pdf"
            if not pdf.exists():
                print(f"missing: {pdf}")
                continue
            img = render_pdf_to_image(pdf)
            band = crop_band(img)
            band = add_label(band, f"Algo {algo.upper()} / {label}")
            row.append(band)
        rows.append(row)
    grid = compose_grid(rows)
    grid_path = ROOT / "comparison.png"
    grid.save(grid_path)
    print(f"wrote {grid_path} ({grid.size})")

    # M10 verification — 3 cells: baseline (v0.1.2), algo A, algo B
    m10_specs = [
        ("out_engine_baseline_m10.pdf", "BASELINE (v0.1.2: 0.5× cap → flat fallback)"),
        ("out_a_m10_with_extend.pdf", "Algo A (Tz scaling)"),
        ("out_b_m10_with_extend.pdf", "Algo B (uncapped kerning)"),
    ]
    m10_row = []
    for fname, label in m10_specs:
        pdf = ROOT / fname
        if not pdf.exists():
            print(f"missing M10 PDF: {pdf}")
            continue
        img = render_pdf_to_image(pdf)
        band = crop_band(img)
        band = add_label(band, label)
        m10_row.append(band)
    m10 = compose_grid([m10_row])
    m10_path = ROOT / "m10_verification.png"
    m10.save(m10_path)
    print(f"wrote {m10_path} ({m10.size})")


if __name__ == "__main__":
    main()
