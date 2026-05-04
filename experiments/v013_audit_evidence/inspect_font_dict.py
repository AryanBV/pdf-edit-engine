"""Diagnose the font-extension bug for the M10 case.

Surfaces the evidence behind the design doc's Section 1 verification gate (a)
claim: that for `Sarah Chen` -> `Søren Müller` on
`experiments/v013_kerning_compare/input.pdf`, the engine returns
`font_action="kept"` even though the embedded Calibri subset cannot render
ø (U+00F8) or ü (U+00FC).

Read-only: opens input.pdf, runs `replace()` to a tempfile we discard,
inspects the font dictionary directly, and calls FontResolver.can_encode
to show what the engine sees.

Run:
    cd experiments/v013_kerning_compare
    .venv/Scripts/python.exe ../v013_audit_evidence/inspect_font_dict.py
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

# Force UTF-8 stdout so ø/ü render in the captured trace on Windows consoles.
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

ROOT = Path(__file__).parent  # experiments/v013_audit_evidence/
ENGINE_SRC = ROOT.parent.parent / "src"
INPUT = ROOT.parent / "v013_kerning_compare" / "input.pdf"
sys.path.insert(0, str(ENGINE_SRC))

import pikepdf  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

from pdf_edit_engine import find, replace  # noqa: E402
from pdf_edit_engine.encoding import FontResolver  # noqa: E402

TARGET = "Sarah Chen"
REPLACEMENT = "Søren Müller"


def hr(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def fmt_obj(o: object) -> str:
    s = str(o)
    return s if len(s) < 80 else s[:77] + "..."


def inspect_page_fonts(page: pikepdf.Page) -> list[tuple[str, pikepdf.Dictionary]]:
    """Return a list of (resource_name, font_dict) pairs for the page."""
    fonts = page.get("/Resources", {}).get("/Font", {}) or {}
    return [(str(name), pikepdf.Dictionary(fdict)) for name, fdict in fonts.items()]


def report_widths_range(name: str, fdict: pikepdf.Dictionary) -> None:
    """Print /FirstChar, /LastChar, /Widths length, and coverage for 0xF8 / 0xFC."""
    first = fdict.get("/FirstChar")
    last = fdict.get("/LastChar")
    widths = fdict.get("/Widths")
    fc = int(first) if first is not None else None
    lc = int(last) if last is not None else None
    wl = len(widths) if widths is not None else None
    print(f"  /FirstChar={fc!r}  /LastChar={lc!r}  len(/Widths)={wl!r}")

    for ch_name, ch_byte in (("ø (0xF8)", 0xF8), ("ü (0xFC)", 0xFC)):
        in_range = fc is not None and lc is not None and fc <= ch_byte <= lc
        in_widths = False
        width_val = None
        if in_range and widths is not None:
            idx = ch_byte - fc
            if 0 <= idx < len(widths):
                in_widths = True
                width_val = float(widths[idx])
        print(
            f"    {ch_name}: in /FirstChar..LastChar range = {in_range}, "
            f"width entry present = {in_widths}, width = {width_val}"
        )


def report_fontfile2_coverage(name: str, fdict: pikepdf.Dictionary) -> None:
    """Open /FontDescriptor /FontFile2 with fontTools and check cmap for ø, ü."""
    fd = fdict.get("/FontDescriptor")
    if fd is None:
        print("  no /FontDescriptor")
        return
    ff2 = fd.get("/FontFile2")
    if ff2 is None:
        ff = fd.get("/FontFile")
        ff3 = fd.get("/FontFile3")
        which = "FontFile" if ff is not None else ("FontFile3" if ff3 is not None else "none")
        print(f"  no /FontFile2 (other: {which})")
        return
    try:
        raw = bytes(ff2.read_bytes())
    except Exception as e:
        print(f"  /FontFile2 unreadable: {e!r}")
        return
    print(f"  /FontFile2 raw size = {len(raw)} bytes")
    try:
        tt = TTFont(io.BytesIO(raw))
    except Exception as e:
        print(f"  fontTools failed to load /FontFile2: {e!r}")
        return
    glyph_order = tt.getGlyphOrder()
    print(f"  glyphOrder length = {len(glyph_order)} glyphs")
    try:
        cmap = tt.getBestCmap()
    except Exception as e:
        print(f"  cmap unreadable: {e!r}")
        return
    print(f"  cmap entries = {len(cmap)}")
    for ch_name, codepoint in (("ø U+00F8", 0x00F8), ("ü U+00FC", 0x00FC)):
        gname = cmap.get(codepoint)
        if gname is None:
            print(f"    {ch_name}: NOT in cmap (no glyph)")
        else:
            in_order = gname in glyph_order
            print(f"    {ch_name}: cmap -> glyph {gname!r}, in glyphOrder = {in_order}")


def find_target_font(page: pikepdf.Page, pdf_path: Path) -> tuple[str, pikepdf.Dictionary] | None:
    """Use the engine's locator to find which page-1 font hosts 'Sarah Chen'.

    The engine's TextCharacter.font_name is the resource name without a
    leading slash (e.g. "F1"), while pikepdf surfaces page resource keys
    with the slash (e.g. "/F1"). Compare both stripped.
    """
    matches = find(str(pdf_path), TARGET)
    if not matches:
        return None
    target_font = str(matches[0].characters[0].font_name).lstrip("/")
    fonts = inspect_page_fonts(page)
    for name, fdict in fonts:
        if str(name).lstrip("/") == target_font:
            return name, fdict
    return None


def main() -> None:
    print(f"Input PDF: {INPUT}  (exists={INPUT.exists()}, size={INPUT.stat().st_size if INPUT.exists() else 'n/a'})")
    print(f"Target:      {TARGET!r}")
    print(f"Replacement: {REPLACEMENT!r}")

    # ── 1. List all page-1 fonts ────────────────────────────────────────
    hr("1. All page-1 fonts in input.pdf")
    with pikepdf.open(str(INPUT)) as pdf:
        page = pdf.pages[0]
        for name, fdict in inspect_page_fonts(page):
            base = fmt_obj(fdict.get("/BaseFont", "?"))
            sub = fmt_obj(fdict.get("/Subtype", "?"))
            enc = fmt_obj(fdict.get("/Encoding", "?"))
            print(f"  {name}: BaseFont={base}  Subtype={sub}  Encoding={enc}")

    # ── 2. Identify the font that hosts 'Sarah Chen' ────────────────────
    hr("2. Locate the font that renders 'Sarah Chen'")
    with pikepdf.open(str(INPUT)) as pdf:
        page = pdf.pages[0]
        result = find_target_font(page, INPUT)
        if result is None:
            print(f"  ERROR: 'Sarah Chen' not found in {INPUT}")
            return
        target_name, target_fdict = result
        base = fmt_obj(target_fdict.get("/BaseFont", "?"))
        sub = fmt_obj(target_fdict.get("/Subtype", "?"))
        enc = fmt_obj(target_fdict.get("/Encoding", "?"))
        print(f"  Target font resource = {target_name}")
        print(f"  /BaseFont = {base}")
        print(f"  /Subtype  = {sub}")
        print(f"  /Encoding = {enc}")

        # ── 3. /Widths range coverage for the target font ───────────────
        hr("3. Target font /Widths range and coverage of ø (0xF8) and ü (0xFC)")
        report_widths_range(target_name, target_fdict)

        # ── 4. /FontFile2 cmap coverage ─────────────────────────────────
        hr("4. Target font /FontFile2 cmap coverage of U+00F8 and U+00FC")
        report_fontfile2_coverage(target_name, target_fdict)

        # ── 5. FontResolver.can_encode direct call ──────────────────────
        hr("5. Direct call: FontResolver.can_encode(REPLACEMENT)")
        resolver = FontResolver(target_fdict, target_name)
        can_enc, missing = resolver.can_encode(REPLACEMENT)
        print(f"  encoding_type = {resolver.encoding_type}")
        print(f"  is_cid_font   = {resolver.is_cid_font}")
        print(f"  can_encode({REPLACEMENT!r}) = (can_enc={can_enc}, missing={missing!r})")

        # Per-character probe
        for ch in REPLACEMENT:
            ce, miss = resolver.can_encode(ch)
            print(f"    char {ch!r} (U+{ord(ch):04X}): can_encode = {ce}, missing = {miss!r}")

    # ── 6. Engine replace() — full edit, observe font_action ────────────
    hr("6. Engine replace() — observe EditResult.font_action")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.pdf"
        matches = find(str(INPUT), TARGET)
        result = replace(str(INPUT), matches[0], REPLACEMENT, str(out), reflow=False)
        print(f"  result.success            = {result.success}")
        print(f"  result.font_action        = {result.font_action!r}")
        print(f"  result.warnings           = {result.warnings!r}")
        fr = result.fidelity_report
        print(f"  fidelity.font_preserved   = {fr.font_preserved}")
        print(f"  fidelity.font_substituted = {fr.font_substituted!r}")
        print(f"  fidelity.glyphs_missing   = {fr.glyphs_missing!r}")
        print(f"  fidelity.overflow_detected= {fr.overflow_detected}")

    hr("Diagnosis summary")
    print("  See font_extension_bug.md for the file:line citations and scope estimate.")


if __name__ == "__main__":
    main()
