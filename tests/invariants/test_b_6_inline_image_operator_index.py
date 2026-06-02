"""INV-B-6: inline images (BI/ID/EI) collapse to one stable operator slot.

A page content stream may embed an *inline image* — a ``BI`` (begin image) /
``ID`` (image data) / ``EI`` (end image) triple whose raw, unescaped image
bytes sit directly in the stream between ``ID`` and ``EI``.  Two properties of
``pikepdf.parse_content_stream`` are load-bearing for this engine:

1. The locator (``ContentStreamInterpreter.interpret``) assigns
   ``TextCharacter.operator_index`` from ``enumerate(parse_content_stream(page))``.
2. The surgeon re-parses the same stream and writes by indexing ``ops[op_idx]``
   directly.

If pikepdf did *not* tokenize an inline image as a single, stable slot — e.g.
if it leaked the raw image bytes back out as stray operands/operators — then a
text operator *after* the image would land at a different operator index than
the one the locator recorded.  The surgeon would then splice over the WRONG
operator, silently corrupting unrelated text on any page that contains an
inline image.

These probes lock the gate-A1.4 finding (scratch evidence in
``experiments/v014_gate_a14/``) as a permanent regression guard:

(a) A ``BI/ID/EI`` block — including raw image bytes that contain literal
    ``EI``-terminator-lookalike sequences AND a ``/FlateDecode``-filtered
    binary payload — collapses into exactly ONE ``"INLINE IMAGE"`` instruction
    with no leaked operands, and that tokenization is stable across an
    ``unparse_content_stream`` -> reparse round-trip.
(b) ``find()`` + ``replace()`` of a text run AFTER the inline image preserves
    the pre-image text exactly and applies the edit at the correct operator
    index (the post-image text op, not the pre-image one).

The fixture is built in-test (no network).  INV-B-6 is the next collision-free
B-layer slot (INV-B-{1..5} are taken).
"""

from __future__ import annotations

import zlib
from typing import TYPE_CHECKING

import pikepdf

from pdf_edit_engine import find, get_text, replace

if TYPE_CHECKING:
    from pathlib import Path

# Operators that legitimately appear in the fixtures below.  Anything outside
# this set in a parsed op list signals leaked inline-image bytes.
_ALLOWED_OPS = frozenset({"BT", "ET", "Tf", "Td", "Tj", "q", "Q", "cm", "INLINE IMAGE"})


def _font_resource(pdf: pikepdf.Pdf) -> pikepdf.Dictionary:
    """A minimal Helvetica WinAnsi Type1 font resource dict."""
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type1"),
            BaseFont=pikepdf.Name("/Helvetica"),
            Encoding=pikepdf.Name("/WinAnsiEncoding"),
        )
    )
    return pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))


def _build(path: Path, stream: bytes) -> None:
    """Write a one-page PDF whose content stream is exactly *stream*."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    page.Resources = _font_resource(pdf)
    page.Contents = pdf.make_stream(stream)
    pdf.save(str(path))
    pdf.close()


def _embedded_ei_stream() -> bytes:
    """Content stream whose raw image bytes contain EI-terminator lookalikes.

    The image payload deliberately splices ``\\nEI\\n``, ``\\nEI `` and
    ``>EI`` into the middle of the data — the exact sequences a naive
    "scan for EI" parser would wrongly treat as the image terminator,
    truncating the image and leaking the trailing bytes as stray operators.
    """
    payload = bytearray(b"\x10" * 48)  # 4x4 RGB = 48 bytes
    payload[10:14] = b"\nEI\n"
    payload[20:24] = b"\nEI "
    payload[30:33] = b">EI"
    raw = bytes(payload)
    return (
        b"BT /F1 12 Tf 72 700 Td (BEFORE) Tj ET\n"
        b"q 1 0 0 1 100 600 cm\n"
        b"BI /W 4 /H 4 /CS /RGB /BPC 8 ID " + raw + b" EI\n"
        b"Q\n"
        b"BT /F1 12 Tf 72 500 Td (AFTER) Tj ET\n"
    )


def _flate_stream() -> bytes:
    """Content stream with a /FlateDecode-filtered inline image.

    The compressed bytes are arbitrary binary, so a correct parser must rely
    on the image dict's filter/length, not byte-scanning, to find the ``EI``.
    """
    raw_pixels = bytes(range(256)) * 3  # 768 bytes of arbitrary binary
    comp = zlib.compress(raw_pixels)
    return (
        b"BT /F1 12 Tf 72 700 Td (BEFORE) Tj ET\n"
        b"q 1 0 0 1 100 400 cm\n"
        b"BI /W 16 /H 16 /CS /RGB /BPC 8 /F /FlateDecode ID " + comp + b" EI\n"
        b"Q\n"
        b"BT /F1 12 Tf 72 500 Td (AFTER) Tj ET\n"
    )


_ParsedOps = list["pikepdf.ContentStreamInstruction | pikepdf.ContentStreamInlineImage"]


def _post_image_tj_index(ops: _ParsedOps) -> int | None:
    """Return the parse-list index of the Tj that shows ``(AFTER)``."""
    for i, ins in enumerate(ops):
        if str(ins.operator) != "Tj":
            continue
        try:
            if bytes(ins.operands[0]) == b"AFTER":
                return i
        except Exception:  # noqa: BLE001 — operand may not be a string
            continue
    return None


def _assert_single_inline_slot_and_stable(path: Path, expected_inline_slots: int) -> None:
    """Core assertions for property (a): one slot, no leaks, round-trip stable."""
    with pikepdf.open(str(path)) as pdf:
        page = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(page))

        inline_slots = [i for i, ins in enumerate(ops) if str(ins.operator) == "INLINE IMAGE"]
        assert len(inline_slots) == expected_inline_slots, (
            f"INV-B-6: expected {expected_inline_slots} 'INLINE IMAGE' slot(s), "
            f"got {len(inline_slots)} at {inline_slots}"
        )

        # Every inline image must be a single self-contained slot: no raw bytes
        # leaked back out as BI/ID/EI sub-operators or stray operands.
        leaked = [
            (i, str(ins.operator))
            for i, ins in enumerate(ops)
            if str(ins.operator) not in _ALLOWED_OPS
        ]
        assert not leaked, f"INV-B-6: leaked inline-image operators/operands: {leaked!r}"

        # The inline-image instruction must carry no leaked operands of its own
        # (the image dict + data are absorbed into the single instruction).
        for slot in inline_slots:
            operands = list(ops[slot].operands)
            assert operands == [] or all(
                not isinstance(o, (bytes, bytearray)) or b"EI" not in bytes(o) for o in operands
            ), f"INV-B-6: inline-image slot {slot} leaked raw operands {operands!r}"

        post_idx_before = _post_image_tj_index(ops)
        assert post_idx_before is not None, (
            "INV-B-6: post-image (AFTER) Tj not locatable — inline image desynced the operator list"
        )

        # Round-trip: unparse -> rebuild -> reparse must preserve the index.
        rt = path.with_name(path.stem + "_rt.pdf")
        page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        pdf.save(str(rt))

    with pikepdf.open(str(rt)) as pdf2:
        ops2 = list(pikepdf.parse_content_stream(pdf2.pages[0]))
        post_idx_after = _post_image_tj_index(ops2)
        inline_slots2 = [i for i, ins in enumerate(ops2) if str(ins.operator) == "INLINE IMAGE"]

    assert len(ops) == len(ops2), (
        f"INV-B-6: op count changed across round-trip ({len(ops)} -> {len(ops2)})"
    )
    assert inline_slots == inline_slots2, (
        f"INV-B-6: inline-image slot positions shifted across round-trip "
        f"({inline_slots} -> {inline_slots2})"
    )
    assert post_idx_before == post_idx_after, (
        f"INV-B-6: post-image Tj index shifted across round-trip "
        f"({post_idx_before} -> {post_idx_after})"
    )


def test_inv_b_6_embedded_ei_collapses_to_single_slot(tmp_path: Path) -> None:
    """(a) Raw image bytes with EI-lookalikes still yield ONE stable slot."""
    path = tmp_path / "embedded_ei.pdf"
    _build(path, _embedded_ei_stream())
    _assert_single_inline_slot_and_stable(path, expected_inline_slots=1)


def test_inv_b_6_flate_filtered_image_collapses_to_single_slot(tmp_path: Path) -> None:
    """(a) A /FlateDecode-filtered binary payload yields ONE stable slot."""
    path = tmp_path / "flate.pdf"
    _build(path, _flate_stream())
    _assert_single_inline_slot_and_stable(path, expected_inline_slots=1)


def test_inv_b_6_replace_after_inline_image_is_correctly_addressed(
    tmp_path: Path,
) -> None:
    """(b) find()+replace() of post-image text edits the right op, leaving pre-image text intact.

    Uses the embedded-EI fixture (the hardest case): if the inline image had
    desynced the operator list, replace() would either raise OperatorError or
    overwrite the pre-image 'BEFORE' run instead of the post-image 'AFTER'.
    """
    src = tmp_path / "embedded_ei.pdf"
    _build(src, _embedded_ei_stream())

    # The locator's operator index for 'AFTER' must equal the raw parse index
    # of the post-image Tj — proving the index the engine USES is unshifted.
    with pikepdf.open(str(src)) as pdf:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
    raw_after_idx = _post_image_tj_index(ops)
    assert raw_after_idx is not None

    matches = find(str(src), "AFTER")
    assert len(matches) == 1, f"INV-B-6: expected exactly one 'AFTER' match, got {len(matches)}"
    after_op_indices = {ch.operator_index for ch in matches[0].characters}
    assert raw_after_idx in after_op_indices, (
        f"INV-B-6: locator op index {sorted(after_op_indices)} does not include the "
        f"raw parse index {raw_after_idx} of the post-image text op"
    )

    # Sanity: both text runs are extractable before the edit.
    pre_text = get_text(str(src))
    assert "BEFORE" in pre_text and "AFTER" in pre_text

    out = tmp_path / "edited.pdf"
    result = replace(str(src), matches[0], "ZAFTERZ", str(out))
    assert result.success, "INV-B-6: replace() of post-image text failed"

    new_text = get_text(str(out))
    assert "ZAFTERZ" in new_text, "INV-B-6: the edit did not land on the post-image text"
    # Pre-image text must be preserved EXACTLY and untouched — the corruption
    # symptom an index shift would produce.
    assert new_text.count("BEFORE") == 1, (
        f"INV-B-6: pre-image 'BEFORE' run corrupted by the post-image edit "
        f"(extracted text: {new_text!r})"
    )
