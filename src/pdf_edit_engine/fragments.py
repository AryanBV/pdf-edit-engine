"""TJ array fragment reconstruction — join fragmented text with bidirectional mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pikepdf

if TYPE_CHECKING:
    from pdf_edit_engine.encoding import FontResolver


@dataclass
class TJFragment:
    """A single string fragment from a TJ array with mapping metadata.

    Args:
        text: Decoded Unicode text of this fragment.
        raw_bytes: Original bytes from the content stream operand.
        array_index: Position in the TJ array (index of this string element).
        char_offset: Starting character offset in the joined string.
        kerning_before: Numeric adjustment before this fragment (0 for first).
    """

    text: str
    raw_bytes: bytes
    array_index: int
    char_offset: int
    kerning_before: float


@dataclass
class TJReconstructed:
    """Joined text from a TJ array with fragment mapping.

    Args:
        full_text: The complete joined text string.
        fragments: Individual fragments with byte/index mappings.
    """

    full_text: str
    fragments: list[TJFragment]

    def locate_in_fragments(
        self, start: int, end: int,
    ) -> list[tuple[TJFragment, int, int]]:
        """Map a character range in full_text back to specific fragments.

        Args:
            start: Start character index (inclusive) in full_text.
            end: End character index (exclusive) in full_text.

        Returns:
            List of (fragment, start_in_fragment, end_in_fragment) tuples
            for each fragment that overlaps the requested range.
        """
        result: list[tuple[TJFragment, int, int]] = []
        for frag in self.fragments:
            frag_start = frag.char_offset
            frag_end = frag.char_offset + len(frag.text)
            if frag_end <= start or frag_start >= end:
                continue
            overlap_start = max(start, frag_start) - frag_start
            overlap_end = min(end, frag_end) - frag_start
            result.append((frag, overlap_start, overlap_end))
        return result


class TJReconstructor:
    """Reconstructs joined text from TJ array fragments.

    Uses a FontResolver to decode content stream bytes into Unicode,
    maintaining bidirectional mapping between the joined string and
    individual TJ array fragments.

    Args:
        font_resolver: The FontResolver for the current font.
    """

    def __init__(self, font_resolver: FontResolver) -> None:
        self._resolver = font_resolver

    def reconstruct(self, tj_array: list[object]) -> TJReconstructed:
        """Process a TJ array operand into joined text with fragment map.

        Args:
            tj_array: List of items from pikepdf's parsed TJ array,
                alternating between pikepdf.String (text) and int/float
                (kerning adjustments).

        Returns:
            TJReconstructed with joined text and fragment mappings.
        """
        fragments: list[TJFragment] = []
        char_offset = 0
        pending_kerning = 0.0

        for array_index, item in enumerate(tj_array):
            if isinstance(item, pikepdf.String):
                raw = bytes(item)
                if len(raw) == 0:
                    continue
                try:
                    decoded = self._resolver.decode(raw)
                except KeyError:
                    continue
                if not decoded:
                    continue
                fragments.append(TJFragment(
                    text=decoded,
                    raw_bytes=raw,
                    array_index=array_index,
                    char_offset=char_offset,
                    kerning_before=pending_kerning,
                ))
                char_offset += len(decoded)
                pending_kerning = 0.0
            elif isinstance(item, (int, float)):
                pending_kerning += float(item)

        full_text = "".join(f.text for f in fragments)
        return TJReconstructed(full_text=full_text, fragments=fragments)
