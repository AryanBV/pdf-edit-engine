"""Clean Resume Swap Test — swaps project sections on Aryan's resume."""

from __future__ import annotations

import sys
import io

# Force UTF-8 stdout on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from pdf_edit_engine import (
    batch_replace_block,
    extract_bbox_text,
    get_text,
    get_text_layout,
)

RESUME = "tests/corpus/resume_aryan.pdf"

# ── Step 2a: Inspect layout ─────────────────────────────────────────────

print("=" * 80)
print("STEP 2a: Inspect resume layout")
print("=" * 80)

blocks = get_text_layout(RESUME, page=0)

# Print every block
for b in blocks:
    print(
        f"y={b.y:7.2f}  x={b.x:6.2f}  w={b.width:6.2f}  h={b.height:5.2f}  "
        f"font={b.font_name:<10s}  sz={b.font_size:5.1f}  text={repr(b.text[:70])}"
    )

# Compute page-wide x-extents
all_x0 = min(b.x for b in blocks)
all_x1 = max(b.x + b.width for b in blocks)
print(f"\nPage x-extents: x0={all_x0:.2f}, x1={all_x1:.2f}")

# Identify section title positions
# AJSP Manager title at y=435.88 (F1, 10pt)
ajsp_title_y = 435.88
ajsp_font_size = 10.0

# Lumina Crafts title at y=299.10 (F1, 10pt)
lumina_title_y = 299.10
lumina_font_size = 10.0

# SMART_MED title at y=161.55 (F1, 10pt)
smart_title_y = 161.55
smart_font_size = 10.0

# CERTIFICATIONS heading at y=51.52 (F1, 11pt)
cert_title_y = 51.52
cert_font_size = 11.0

# Bbox computation rules:
# y1 (top) = title_y + font_size + 0.5
# y0 (bottom) = next_section_title_y + next_font_size + 0.5
# x0/x1 = page-wide min/max

bbox_ajsp = (
    all_x0,
    lumina_title_y + lumina_font_size + 0.5,
    all_x1,
    ajsp_title_y + ajsp_font_size + 0.5,
)
bbox_lumina = (
    all_x0,
    smart_title_y + smart_font_size + 0.5,
    all_x1,
    lumina_title_y + lumina_font_size + 0.5,
)
bbox_smart = (
    all_x0,
    cert_title_y + cert_font_size + 0.5,
    all_x1,
    smart_title_y + smart_font_size + 0.5,
)

print(f"\nBbox AJSP Manager (Position 1): {bbox_ajsp}")
print(f"Bbox Lumina Crafts (Position 2): {bbox_lumina}")
print(f"Bbox SMART_MED    (Position 3): {bbox_smart}")

# Sanity check: no overlaps
assert bbox_ajsp[1] == bbox_lumina[3], (
    f"AJSP bottom != Lumina top: {bbox_ajsp[1]} vs {bbox_lumina[3]}"
)
assert bbox_lumina[1] == bbox_smart[3], (
    f"Lumina bottom != SMART top: {bbox_lumina[1]} vs {bbox_smart[3]}"
)
assert bbox_ajsp[1] > bbox_lumina[1] > bbox_smart[1], "Bboxes should be ordered top-to-bottom"
print("Sanity check: no overlaps. OK.")

# ── Step 2b: Extract original text ──────────────────────────────────────

print("\n" + "=" * 80)
print("STEP 2b: Extract original text")
print("=" * 80)

text_ajsp = extract_bbox_text(RESUME, bbox=bbox_ajsp, page=0, tolerance=0)
text_lumina = extract_bbox_text(RESUME, bbox=bbox_lumina, page=0, tolerance=0)
text_smart = extract_bbox_text(RESUME, bbox=bbox_smart, page=0, tolerance=0)

print(f"\n--- AJSP Manager text (Position 1) ---")
print(text_ajsp)
print(f"\n--- Lumina Crafts text (Position 2) ---")
print(text_lumina)
print(f"\n--- SMART_MED text (Position 3) ---")
print(text_smart)

# Check for spurious spaces in extracts
for name, text in [("AJSP", text_ajsp), ("Lumina", text_lumina), ("SMART", text_smart)]:
    for bad in ["month ly", "full - stack", "full -stack"]:
        if bad in text:
            print(f"WARNING: spurious space found in {name}: {repr(bad)}")

# ── Step 2c: Test A — Swap Position 1 <-> Position 3 ────────────────────

print("\n" + "=" * 80)
print("STEP 2c: Test A — Swap Position 1 <-> Position 3")
print("=" * 80)

results_a = batch_replace_block(
    RESUME,
    page_number=0,
    replacements=[
        (bbox_ajsp, text_smart),  # Position 1 gets SMART_MED text
        (bbox_lumina, text_lumina),  # Position 2 re-rendered at uniform spacing
        (bbox_smart, text_ajsp),  # Position 3 gets AJSP Manager text
    ],
    output_path="demo_output/swap_result.pdf",
)

for i, r in enumerate(results_a):
    print(f"\nEditResult [{i}]: success={r.success}, font_action={r.font_action}")
    if r.warnings:
        print(f"  warnings: {r.warnings}")
    if not r.success:
        print(f"  FAILED — original: {repr(r.original_text[:50])}")
        print(f"           new:      {repr(r.new_text[:50])}")

# ── Step 2d: Test B — Custom text in Position 1 ─────────────────────────

print("\n" + "=" * 80)
print("STEP 2d: Test B — Custom text in Position 1")
print("=" * 80)

custom_text = (
    "PDF Edit Engine \u2014 Format-Preserving PDF Text Editing Library\n"
    "Python 3.12+, pikepdf, fonttools, pdfminer.six, CIDFont/Identity-H\n"
    "\u2022 Built a Python library that edits text in existing PDFs by modifying content "
    "stream operators in-place, preserving original fonts, sizes, positions, and full "
    "page layout\n"
    "\u2022 Implemented two-tier font subset extension \u2014 CMap table fast path for "
    "existing CIDFont subsets, plus full font re-embedding via fonttools when characters "
    "fall outside the original subset\n"
    "\u2022 Built a FidelityReport system that validates every edit by comparing pre and "
    "post font metrics and character mappings, producing per-replacement confidence scores"
)

results_b = batch_replace_block(
    RESUME,
    page_number=0,
    replacements=[
        (bbox_ajsp, custom_text),  # Position 1 gets custom text
        (bbox_lumina, text_lumina),  # Position 2 re-rendered at uniform spacing
        (bbox_smart, text_ajsp),  # Position 3 gets AJSP Manager text
    ],
    output_path="demo_output/swap_custom.pdf",
)

for i, r in enumerate(results_b):
    print(f"\nEditResult [{i}]: success={r.success}, font_action={r.font_action}")
    if r.warnings:
        print(f"  warnings: {r.warnings}")
    if not r.success:
        print(f"  FAILED — original: {repr(r.original_text[:50])}")
        print(f"           new:      {repr(r.new_text[:50])}")

# ── Step 2e: Validate both outputs ──────────────────────────────────────

print("\n" + "=" * 80)
print("STEP 2e: Validate outputs")
print("=" * 80)


def validate(pdf_path: str, label: str, expected_pos1: str, expected_pos3: str) -> str:
    """Validate a swap output PDF. Returns verdict string."""
    problems: list[str] = []

    print(f"\n--- {label}: Full extracted text ---")
    full_text = get_text(pdf_path, page=0)
    print(full_text)

    # Check 1: Aryan B V count
    aryan_count = full_text.count("Aryan B V")
    if aryan_count != 1:
        problems.append(f"'Aryan B V' appears {aryan_count} times (expected 1)")
    print(f"\n'Aryan B V' count: {aryan_count}")

    # Check 2: Spurious spaces
    for bad in ["month ly", "full - stack", "full -stack"]:
        if bad in full_text:
            problems.append(f"spurious space: {repr(bad)}")
            print(f"  FOUND spurious: {repr(bad)}")

    # Check 3: Position 1 contains expected text
    if expected_pos1 not in full_text:
        problems.append(f"Position 1 missing expected text: {repr(expected_pos1[:40])}")
        print(f"  Position 1 MISSING: {repr(expected_pos1[:60])}")
    else:
        print(f"  Position 1 contains: {repr(expected_pos1[:60])}... OK")

    # Check 4: Position 3 contains expected text
    if expected_pos3 not in full_text:
        problems.append(f"Position 3 missing expected text: {repr(expected_pos3[:40])}")
        print(f"  Position 3 MISSING: {repr(expected_pos3[:60])}")
    else:
        print(f"  Position 3 contains: {repr(expected_pos3[:60])}... OK")

    # Check 5: Lumina Crafts present
    if "Lumina Crafts" not in full_text:
        problems.append("'Lumina Crafts' not found")
        print("  Lumina Crafts: MISSING")
    else:
        print("  Lumina Crafts: present OK")

    if problems:
        verdict = f"{label}: GARBLED \u2014 {'; '.join(problems)}"
    else:
        verdict = f"{label}: CLEAN"
    return verdict


verdict_a = validate(
    "demo_output/swap_result.pdf",
    "TEST A",
    expected_pos1="SMART_MED",
    expected_pos3="AJSP Manager",
)

verdict_b = validate(
    "demo_output/swap_custom.pdf",
    "TEST B",
    expected_pos1="PDF Edit Engine",
    expected_pos3="AJSP Manager",
)

print("\n" + "=" * 80)
print("FINAL VERDICTS")
print("=" * 80)
print(verdict_a)
print(verdict_b)
