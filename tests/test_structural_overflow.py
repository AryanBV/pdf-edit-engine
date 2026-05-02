"""Branch coverage for ``structural.py`` overflow-shift handling.

Targets two private helpers that fire on real-world ``replace_block`` calls
when text grows past its bbox:

* ``_sync_annotations_in_bbox`` (lines 415-432) — annotation rect-shift loop
  and the zero-width ``/BS`` underline restoration.
* ``_shift_content_below_inplace`` (lines 541-558) — bezier curve y-coord
  shifts for ``c`` / ``v`` / ``y`` operators.

The helpers are private but importable; the public ``shift_content_below`` /
``replace_block`` wrappers wrap them with extra logic (orphan removal,
overflow clamping) that obscures the branch under test, so we test the units
directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from pdf_edit_engine.structural import (
    _shift_content_below_inplace,
    _sync_annotations_in_bbox,
)

if TYPE_CHECKING:
    from pathlib import Path


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_link_annot(
    pdf: pikepdf.Pdf,
    rect: tuple[float, float, float, float],
    uri: str,
    bs: pikepdf.Dictionary | None = None,
) -> pikepdf.Object:
    """Build an indirect /Link annotation with optional /BS dict."""
    fields: dict[str, object] = {
        "/Type": pikepdf.Name.Annot,
        "/Subtype": pikepdf.Name.Link,
        "/Rect": pikepdf.Array([pikepdf.Object.parse(str(v).encode()) for v in rect]),
        "/A": pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name.Action,
                "/S": pikepdf.Name.URI,
                "/URI": pikepdf.String(uri),
            }
        ),
    }
    if bs is not None:
        fields["/BS"] = bs
    return pdf.make_indirect(pikepdf.Dictionary(fields))


def _attach_annot(page: pikepdf.Page, annot: pikepdf.Object) -> None:
    """Append *annot* to page's /Annots, creating the array if absent."""
    if "/Annots" not in page:
        page["/Annots"] = pikepdf.Array()
    page["/Annots"].append(annot)


def _new_blank_pdf() -> tuple[pikepdf.Pdf, pikepdf.Page]:
    """Return a fresh single-page PDF, page is the LETTER-size blank page."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612.0, 792.0))
    return pdf, pdf.pages[0]


# ── _sync_annotations_in_bbox: rect-shift branches ───────────────────────


def test_realign_annotation_shifts_link_within_bbox(tmp_path: Path) -> None:
    """Annotation overlapping replaced bbox has its /Rect shifted by delta_y."""
    pdf, page = _new_blank_pdf()
    page.Contents = pdf.make_stream(b"")
    # /Rect overlaps the bbox vertically (rect.y1 < bbox.y3 and rect.y3 > bbox.y0).
    # Use a URI keyword that appears in new_text so orphan-removal keeps it.
    annot = _make_link_annot(
        pdf,
        rect=(72.0, 695.0, 200.0, 720.0),
        uri="https://example.com/keepme",
    )
    _attach_annot(page, annot)

    bbox = (72.0, 690.0, 540.0, 730.0)
    delta_y = -20.0
    _sync_annotations_in_bbox(page, bbox, delta_y, new_text="keepme inline")

    out = tmp_path / "shift.pdf"
    pdf.save(str(out))
    pdf.close()

    with pikepdf.Pdf.open(str(out)) as reopened:
        annots = list(reopened.pages[0]["/Annots"])  # type: ignore[call-overload]
        assert len(annots) == 1
        rect = [float(v) for v in annots[0]["/Rect"]]
        assert rect == [72.0, 695.0 + delta_y, 200.0, 720.0 + delta_y]


def test_realign_annotation_outside_bbox_unchanged(tmp_path: Path) -> None:
    """Annotation that doesn't vertically overlap bbox is not shifted."""
    pdf, page = _new_blank_pdf()
    page.Contents = pdf.make_stream(b"")
    # Rect well below bbox — no vertical overlap.
    rect_in = (72.0, 600.0, 200.0, 620.0)
    annot = _make_link_annot(pdf, rect=rect_in, uri="https://example.com/keepme")
    _attach_annot(page, annot)

    bbox = (72.0, 690.0, 540.0, 730.0)
    _sync_annotations_in_bbox(page, bbox, delta_y=-20.0, new_text="keepme inline")

    out = tmp_path / "outside.pdf"
    pdf.save(str(out))
    pdf.close()

    with pikepdf.Pdf.open(str(out)) as reopened:
        annots = list(reopened.pages[0]["/Annots"])  # type: ignore[call-overload]
        assert len(annots) == 1
        rect = [float(v) for v in annots[0]["/Rect"]]
        assert rect == list(rect_in)


def test_realign_zero_width_border_gets_underline(tmp_path: Path) -> None:
    """Annotation with /BS width 0 inside bbox gains a (W=0.5, S=/U) underline."""
    pdf, page = _new_blank_pdf()
    page.Contents = pdf.make_stream(b"")
    bs = pikepdf.Dictionary({"/W": 0, "/S": pikepdf.Name("/S")})
    annot = _make_link_annot(
        pdf,
        rect=(72.0, 695.0, 200.0, 720.0),
        uri="https://example.com/keepme",
        bs=bs,
    )
    _attach_annot(page, annot)

    bbox = (72.0, 690.0, 540.0, 730.0)
    _sync_annotations_in_bbox(page, bbox, delta_y=-15.0, new_text="keepme inline")

    out = tmp_path / "border.pdf"
    pdf.save(str(out))
    pdf.close()

    with pikepdf.Pdf.open(str(out)) as reopened:
        annot_out = list(reopened.pages[0]["/Annots"])[0]  # type: ignore[call-overload]
        bs_out = annot_out["/BS"]
        assert float(bs_out["/W"]) == 0.5
        # /S is a Name; pikepdf prepends '/' on str().
        assert str(bs_out["/S"]) == "/U"


def test_realign_nonzero_border_unchanged(tmp_path: Path) -> None:
    """Annotation with /BS width != 0 keeps its border style untouched."""
    pdf, page = _new_blank_pdf()
    page.Contents = pdf.make_stream(b"")
    bs = pikepdf.Dictionary({"/W": 2, "/S": pikepdf.Name("/S")})
    annot = _make_link_annot(
        pdf,
        rect=(72.0, 695.0, 200.0, 720.0),
        uri="https://example.com/keepme",
        bs=bs,
    )
    _attach_annot(page, annot)

    bbox = (72.0, 690.0, 540.0, 730.0)
    _sync_annotations_in_bbox(page, bbox, delta_y=-15.0, new_text="keepme inline")

    out = tmp_path / "border_nonzero.pdf"
    pdf.save(str(out))
    pdf.close()

    with pikepdf.Pdf.open(str(out)) as reopened:
        annot_out = list(reopened.pages[0]["/Annots"])[0]  # type: ignore[call-overload]
        bs_out = annot_out["/BS"]
        assert float(bs_out["/W"]) == 2.0
        assert str(bs_out["/S"]) == "/S"


# ── _shift_content_below_inplace: bezier branches ────────────────────────


def test_shift_bezier_curves_below_threshold(tmp_path: Path) -> None:
    """All three y-control-points of a `c` op below threshold shift by -delta_y."""
    pdf, page = _new_blank_pdf()
    # Bezier whose y-control points are all below 700 (threshold).
    # `c` operator: x1 y1 x2 y2 x3 y3 c
    page.Contents = pdf.make_stream(b"100 600 m 110 605 130 615 150 610 c S\n")

    delta_y = 30.0  # positive => shift DOWN; new y = old - 30
    warnings = _shift_content_below_inplace(
        pdf, page, page_num=0, y_threshold=700.0, delta_y=delta_y
    )
    assert isinstance(warnings, list)

    out = tmp_path / "bezier_below.pdf"
    pdf.save(str(out))

    with pikepdf.Pdf.open(str(out)) as reopened:
        ops = list(pikepdf.parse_content_stream(reopened.pages[0]))
    c_ops = [
        op for op in ops if (op.operator if hasattr(op, "operator") else op[1]).__str__() == "c"
    ]
    assert len(c_ops) == 1
    operands = c_ops[0].operands if hasattr(c_ops[0], "operands") else c_ops[0][0]
    ys = [float(operands[j]) for j in (1, 3, 5)]
    # Original ys: 605, 615, 610. After shift -30: 575, 585, 580.
    assert ys == [575.0, 585.0, 580.0]
    pdf.close()


def test_shift_bezier_curves_above_threshold_unchanged(tmp_path: Path) -> None:
    """A `c` op whose every y-control-point is above threshold is NOT shifted."""
    pdf, page = _new_blank_pdf()
    # All y values above 700 — none satisfy y < 700.
    page.Contents = pdf.make_stream(b"100 750 m 110 755 130 765 150 760 c S\n")

    _shift_content_below_inplace(pdf, page, page_num=0, y_threshold=700.0, delta_y=30.0)

    out = tmp_path / "bezier_above.pdf"
    pdf.save(str(out))

    with pikepdf.Pdf.open(str(out)) as reopened:
        ops = list(pikepdf.parse_content_stream(reopened.pages[0]))
    c_ops = [
        op for op in ops if (op.operator if hasattr(op, "operator") else op[1]).__str__() == "c"
    ]
    assert len(c_ops) == 1
    operands = c_ops[0].operands if hasattr(c_ops[0], "operands") else c_ops[0][0]
    ys = [float(operands[j]) for j in (1, 3, 5)]
    assert ys == [755.0, 765.0, 760.0]
    pdf.close()


def test_shift_v_y_path_operators_below_threshold(tmp_path: Path) -> None:
    """`v` and `y` bezier variants (4-operand) shift their two y-coords below threshold."""
    pdf, page = _new_blank_pdf()
    # `v`: x2 y2 x3 y3 v   (y components at indices 1, 3)
    # `y`: x1 y1 x3 y3 y   (y components at indices 1, 3)
    page.Contents = pdf.make_stream(b"100 600 m 110 610 130 605 v 140 615 160 612 y S\n")

    delta_y = 25.0
    _shift_content_below_inplace(pdf, page, page_num=0, y_threshold=700.0, delta_y=delta_y)

    out = tmp_path / "vy_below.pdf"
    pdf.save(str(out))

    with pikepdf.Pdf.open(str(out)) as reopened:
        ops = list(pikepdf.parse_content_stream(reopened.pages[0]))
    v_ops = [
        op for op in ops if (op.operator if hasattr(op, "operator") else op[1]).__str__() == "v"
    ]
    y_ops = [
        op for op in ops if (op.operator if hasattr(op, "operator") else op[1]).__str__() == "y"
    ]
    assert len(v_ops) == 1 and len(y_ops) == 1

    v_operands = v_ops[0].operands if hasattr(v_ops[0], "operands") else v_ops[0][0]
    v_ys = [float(v_operands[j]) for j in (1, 3)]
    # Original v ys: 610, 605. After -25: 585, 580.
    assert v_ys == [585.0, 580.0]

    y_operands = y_ops[0].operands if hasattr(y_ops[0], "operands") else y_ops[0][0]
    y_ys = [float(y_operands[j]) for j in (1, 3)]
    # Original y ys: 615, 612. After -25: 590, 587.
    assert y_ys == [590.0, 587.0]
    pdf.close()


def test_shift_v_y_path_operators_above_threshold_unchanged(tmp_path: Path) -> None:
    """`v` and `y` bezier ops whose y-coords are all above threshold are unchanged."""
    pdf, page = _new_blank_pdf()
    page.Contents = pdf.make_stream(b"100 750 m 110 760 130 755 v 140 765 160 762 y S\n")

    _shift_content_below_inplace(pdf, page, page_num=0, y_threshold=700.0, delta_y=25.0)

    out = tmp_path / "vy_above.pdf"
    pdf.save(str(out))

    with pikepdf.Pdf.open(str(out)) as reopened:
        ops = list(pikepdf.parse_content_stream(reopened.pages[0]))
    v_ops = [
        op for op in ops if (op.operator if hasattr(op, "operator") else op[1]).__str__() == "v"
    ]
    y_ops = [
        op for op in ops if (op.operator if hasattr(op, "operator") else op[1]).__str__() == "y"
    ]
    assert len(v_ops) == 1 and len(y_ops) == 1

    v_operands = v_ops[0].operands if hasattr(v_ops[0], "operands") else v_ops[0][0]
    assert [float(v_operands[j]) for j in (1, 3)] == [760.0, 755.0]

    y_operands = y_ops[0].operands if hasattr(y_ops[0], "operands") else y_ops[0][0]
    assert [float(y_operands[j]) for j in (1, 3)] == [765.0, 762.0]
    pdf.close()


def test_shift_bezier_curves_partial_only_below_y_shifted(tmp_path: Path) -> None:
    """`c` op with mixed above/below threshold y-coords: only below-threshold get shifted.

    Exercises the inner-loop branch where ``val < y_threshold`` is False for some
    indices in (1, 3, 5) — the ``new_operands[yi] = ...`` write is skipped for
    those indices while the others move. Without this test the
    ``546->544`` partial branch in structural.py stays uncovered.
    """
    pdf, page = _new_blank_pdf()
    # y1=605 (below 700, shifts), y2=720 (above, unchanged), y3=610 (below, shifts).
    page.Contents = pdf.make_stream(b"100 600 m 110 605 130 720 150 610 c S\n")

    delta_y = 30.0
    _shift_content_below_inplace(pdf, page, page_num=0, y_threshold=700.0, delta_y=delta_y)

    out = tmp_path / "bezier_partial.pdf"
    pdf.save(str(out))

    with pikepdf.Pdf.open(str(out)) as reopened:
        ops = list(pikepdf.parse_content_stream(reopened.pages[0]))
    c_ops = [
        op for op in ops if (op.operator if hasattr(op, "operator") else op[1]).__str__() == "c"
    ]
    assert len(c_ops) == 1
    operands = c_ops[0].operands if hasattr(c_ops[0], "operands") else c_ops[0][0]
    ys = [float(operands[j]) for j in (1, 3, 5)]
    # 605 -> 575, 720 -> 720 (untouched), 610 -> 580.
    assert ys == [575.0, 720.0, 580.0]
    pdf.close()


def test_shift_v_y_partial_only_below_y_shifted(tmp_path: Path) -> None:
    """`v` op with mixed above/below threshold: only the below-threshold y shifts.

    Same rationale as the `c` partial test but for the 4-operand `v` / `y`
    branch (covers the ``556->554`` partial branch).
    """
    pdf, page = _new_blank_pdf()
    # `v`: y1=720 (above 700, unchanged), y2=605 (below, shifts).
    page.Contents = pdf.make_stream(b"100 600 m 110 720 130 605 v S\n")

    delta_y = 25.0
    _shift_content_below_inplace(pdf, page, page_num=0, y_threshold=700.0, delta_y=delta_y)

    out = tmp_path / "v_partial.pdf"
    pdf.save(str(out))

    with pikepdf.Pdf.open(str(out)) as reopened:
        ops = list(pikepdf.parse_content_stream(reopened.pages[0]))
    v_ops = [
        op for op in ops if (op.operator if hasattr(op, "operator") else op[1]).__str__() == "v"
    ]
    assert len(v_ops) == 1
    operands = v_ops[0].operands if hasattr(v_ops[0], "operands") else v_ops[0][0]
    ys = [float(operands[j]) for j in (1, 3)]
    # 720 stays, 605 -> 580.
    assert ys == [720.0, 580.0]
    pdf.close()
