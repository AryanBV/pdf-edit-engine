# `experiments/` — empirical artifacts for design decisions

Each subdirectory holds the empirical evidence (input PDFs, output PDFs,
comparison images, calibration data, scripts) that informed a specific
design decision. **Preserved for retrospection** — future maintainers can
read these to understand why a design landed where it did.

## Convention

Subdirectories are named after the version or feature they supported:

- `v013_kerning_compare/` — kerning algorithm A (Tz scaling) vs B
  (uncapped proportional kerning) comparison for v0.1.3 (ARY-290).
  Drives the design-doc decision in
  `docs/v0.1.3-implementation-design.md` §1.
- `v013_detector_calibration/` — `_detect_paragraphs_from_index`
  low-confidence signal calibration against four corpora (Chrome,
  Word, reportlab, pikepdf-synthetic) for v0.1.3 (ARY-292 surfacing
  half). Drives the design-doc decision in
  `docs/v0.1.3-implementation-design.md` §2.

## Per-subdirectory layout

Each experiment subdirectory contains:

- `README.md` (optional) — one-line description of the experiment.
- The standalone scripts that produced the artifacts.
- Input PDFs, output PDFs, rendered images.
- A `.venv/` if the experiment needed extra packages
  (`pypdfium2`, `Pillow`, etc.) NOT in the engine's main `pyproject.toml`.

The scripts under `experiments/` are deliberately **standalone** — they do
NOT import from `src/pdf_edit_engine/`. If an experiment needs production
logic, the relevant code is copied in. This keeps production source
untouched (read-only constraint of the lock-in prompt) and makes the
experiments reproducible against any past or future engine version.

## Reproducing an experiment

```powershell
cd experiments/v013_kerning_compare
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install pikepdf pypdfium2 Pillow pdfminer.six
python runner.py
```

Outputs land alongside the scripts.
