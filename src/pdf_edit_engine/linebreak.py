"""Reduced UAX#14 line-break-opportunity classifier (stdlib-only leaf).

E.7 (v0.2.0). A spaceless CJK ideographic run has no inter-word spaces, so the
``str.split(" ")`` atomization in :func:`reflow.break_into_lines` treats the
whole run as ONE atom that can never wrap — a CJK paragraph wider than its
column silently overflows. This module exposes a reduced (~8-class) Unicode
Annex #14 break-opportunity classifier so ideographic runs can be split at
ideograph boundaries, while keeping Latin / space-delimited text byte-identical
to ``str.split(" ")`` (the INV-G-10 regression lock).

Pure functions only. Imports stdlib :mod:`unicodedata` ONLY — no fontTools,
pdfminer, pikepdf, or engine imports. This is a leaf in reflow's dependency
zone (reflow imports it, never the reverse).
"""

from __future__ import annotations

import unicodedata
from typing import Literal

CharClass = Literal["BK", "SP", "CM", "OP", "CL", "ID", "AL"]

# CJK / fullwidth OPENING punctuation that must NOT be followed by a break.
# Every member is East-Asian-width W or F (fullwidth/wide). ASCII / narrow
# brackets (``(`` ``[`` ``{`` ``«``) are DELIBERATELY excluded: they are normal
# Latin punctuation (class ``AL``) so Latin-with-bracket text stays Latin-simple
# (the E.7 bracket regression root fix). The ``_is_wide`` gate in
# :func:`_char_class` enforces this even against a stray narrow set member.
_OP_CHARS: frozenset[str] = frozenset("（［｛〈《「『【〔〘〖")

# CLOSE punctuation / close-paren / non-starter — collapsed to one returned
# class ``"CL"`` (the engine only needs "no break BEFORE this"). Includes CJK
# closing brackets, ideographic terminators, small kana, and middle dot, which
# must NOT start a line. Note ``。``(U+3002) and ``、``(U+3001) are W-width but
# CL, so the check order tests CL BEFORE ID. Every member is W/F; ASCII close
# brackets (``)`` ``]`` ``}``) are excluded — they classify as ``AL``.
_CL_NS_CHARS: frozenset[str] = frozenset(
    "）］｝〉》」』】〕〗〙、。，．！？：；ゝゞ々ぁぃぅぇぉっゃゅょゎ・"
)


def _is_wide(ch: str) -> bool:
    """Return ``True`` iff ``ch`` is East-Asian-width W (wide) or F (fullwidth).

    The OP/CL/ID classes (the CJK no-break punctuation + ideograph classes) are
    restricted to W/F characters so that ASCII / narrow (Na/N/A) brackets and
    punctuation classify as ``AL`` — keeping Latin text containing them
    Latin-simple (E.7 root fix). CJK brackets are W/F, so their no-break rules
    are preserved.

    Args:
        ch: A single character (one codepoint).

    Returns:
        Whether the character has East-Asian width W or F.
    """
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _char_class(ch: str) -> CharClass:
    """Classify a single character into a reduced UAX#14 break class.

    The check order is load-bearing (first match wins):
    ``BK -> SP -> CM -> (narrow? AL) -> OP -> CL -> ID``. The narrow-width gate
    runs BEFORE OP/CL/ID so ASCII / narrow brackets and punctuation are ``AL``
    (the E.7 root fix that keeps Latin-with-bracket text Latin-simple). Among
    the remaining W/F characters, CL is tested before ID so the W-width
    ideographic terminators (``。``, ``、``) classify as CL, and OP is tested
    before ID symmetrically.

    Args:
        ch: A single character (one codepoint).

    Returns:
        One of ``"BK"`` (mandatory break), ``"SP"`` (space), ``"CM"``
        (combining mark), ``"OP"`` (open punctuation), ``"CL"`` (close /
        non-starter), ``"ID"`` (ideographic / wide), or ``"AL"`` (everything
        else — alphabetic, narrow, digits, Thai/Lao/Khmer/Myanmar).
    """
    if ch in "\n\r":
        return "BK"
    if ch in " \t":
        return "SP"
    if unicodedata.category(ch) in ("Mn", "Mc", "Me"):
        return "CM"
    if not _is_wide(ch):
        # ASCII / narrow brackets + punctuation are normal Latin content (AL),
        # so Latin-with-bracket text stays Latin-simple (E.7 root fix). The
        # OP/CL/ID classes below are gated on W/F width; only wide chars reach
        # them, so the CJK bracket no-break rules are preserved unchanged.
        return "AL"
    if ch in _OP_CHARS or unicodedata.category(ch) == "Ps":
        return "OP"
    if ch in _CL_NS_CHARS or unicodedata.category(ch) in ("Pe", "Pf"):
        return "CL"
    return "ID"


def break_opportunities(text: str) -> list[int]:
    """Return the interior positions where a line break is ALLOWED.

    Each returned index ``i`` (``1 <= i < len(text)``) means a break is allowed
    BEFORE ``text[i]`` (i.e. between ``text[i-1]`` and ``text[i]``). The end of
    string (``len(text)``) is never an interior opportunity.

    Rules over the adjacent pair ``(prev=text[i-1], cur=text[i])``:

    1. Mandatory after a hard newline (``prev`` is BK).
    2. NO break before a CL / non-starter (a closing bracket / ideographic
       period must not start a line).
    3. NO break after an OP (an opening bracket must not end a line).
    4. NO break before a CM (a combining mark glues to its base).
    5. A space is a break opportunity (preserves ``str.split(" ")`` semantics —
       the Latin-invariance contract).
    6. Between two ID, or after a CL before an ID (CJK breaks freely between
       ideographs).
    7. Otherwise NO break (Latin words stay intact; a glued AL+ID run does not
       break inside).

    Args:
        text: The text to classify.

    Returns:
        The sorted list of interior break positions.
    """
    opportunities: list[int] = []
    for i in range(1, len(text)):
        prev_cls = _char_class(text[i - 1])
        cur_cls = _char_class(text[i])

        if prev_cls == "BK":
            opportunities.append(i)
            continue
        if cur_cls == "CL":
            continue
        if prev_cls == "OP":
            continue
        if cur_cls == "CM":
            continue
        if prev_cls == "SP" or cur_cls == "SP":
            opportunities.append(i)
            continue
        if prev_cls == "ID" and cur_cls == "ID":
            opportunities.append(i)
            continue
        if prev_cls == "CL" and cur_cls == "ID":
            opportunities.append(i)
            continue
        # Default (AL↔AL, AL↔ID, ...): no break.
    return opportunities


def has_break_opportunity(text: str) -> bool:
    """Return ``True`` iff :func:`break_opportunities` yields any position.

    Used by the ``scriptless_reflow_unsupported`` predicate: a spaceless run
    with no break opportunity (Thai / Lao / Khmer / Myanmar) cannot be wrapped
    without a dictionary.

    Args:
        text: The text to test.

    Returns:
        Whether the text has at least one interior break opportunity.
    """
    return len(break_opportunities(text)) > 0


def is_latin_simple(text: str) -> bool:
    """Return ``True`` iff every character is class AL, SP, or BK.

    When ``True`` the caller routes to the existing untouched ``str.split(" ")``
    path so the Latin atomization and ``" ".join`` re-join stay byte-identical
    (INV-G-10). A string containing any ID / OP / CL / CM character is NOT
    Latin-simple and routes through the UAX#14 breaker instead.

    Args:
        text: The text to test.

    Returns:
        Whether the text is purely Latin / space / newline content.
    """
    return all(_char_class(ch) in ("AL", "SP", "BK") for ch in text)


def _is_hangul(ch: str) -> bool:
    """Return ``True`` iff ``ch`` is a Korean Hangul character.

    Covers the Hangul syllables block (U+AC00–U+D7A3) and the Hangul Jamo blocks
    (U+1100–U+11FF compatibility/conjoining, U+3130–U+318F compatibility Jamo,
    U+A960–U+A97F / U+D7B0–U+D7FF extended Jamo). Hangul is East-Asian-width W —
    so :func:`_char_class` returns ``"ID"`` for it — yet Korean is a SPACE-
    DELIMITED script (words are separated by spaces), unlike Han ideographs and
    Kana. :func:`is_space_delimited_script` uses this to treat a Hangul run as
    space-delimited at the paragraph level even when a short fragment carries no
    literal space (the cross-line ``"\\n"`` grouping decision).

    Args:
        ch: A single character (one codepoint).

    Returns:
        Whether the character belongs to a Hangul block.
    """
    cp = ord(ch)
    return (
        0xAC00 <= cp <= 0xD7A3
        or 0x1100 <= cp <= 0x11FF
        or 0x3130 <= cp <= 0x318F
        or 0xA960 <= cp <= 0xA97F
        or 0xD7B0 <= cp <= 0xD7FF
    )


def is_space_delimited_script(text: str) -> bool:
    """Return ``True`` iff ``text`` is a space-delimited paragraph.

    The PARAGRAPH-level decision input for :func:`grouping_boundary_separator`
    (the cross-line ``"\\n"`` grouping-boundary join). A paragraph is treated as
    space-delimited when it contains ANY ASCII space / tab (Latin, mixed,
    CJK-with-spaces) OR ANY Hangul character (Korean is a space-delimited script
    whose syllables are East-Asian-width W and so otherwise indistinguishable from
    Han ideographs at the character class level — a short Korean fragment such as
    ``"관리자"`` carries no literal space yet a ``"\\n"`` boundary beside it is a
    word boundary). A PURE Han / Kana ideograph run with no space returns
    ``False`` so its ``"\\n"`` grouping boundaries stay glued.

    Args:
        text: The combined paragraph source text.

    Returns:
        Whether the paragraph should re-join its ``"\\n"`` grouping artifacts with
        a space.
    """
    if " " in text or "\t" in text:
        return True
    return any(_is_hangul(ch) for ch in text)


def grouping_boundary_separator(
    left_piece: str, right_piece: str, space_delimited: bool = True
) -> str:
    """Return the separator for an element-GROUPING boundary (a ``"\\n"`` artifact).

    Distinct from :func:`recorded_separators`. The recorded model recovers the
    ACTUAL separator from the original text and is the source of truth for every
    gap WITHIN a segment (real spaces, including Korean word spaces and real
    inter-ideograph spaces, survive verbatim). This helper governs only the
    element-grouping ``"\\n"`` artifacts that :func:`reflow._rejoin_newline_artifacts`
    must resolve — boundaries the locator INSERTED between separately-grouped
    content elements, for which there is NO original-text provenance to record
    (the original inter-element text is genuinely lost as the ``"\\n"``).

    Because the per-boundary "was there a space here" provenance was discarded at
    extraction (recovering it is a READ-PATH change carved out to 0.3.0), the
    decision is made at the PARAGRAPH level and threaded in via ``space_delimited``:

    * ``space_delimited=True`` (the default, and the case for any Latin / Korean /
      mixed / CJK-with-spaces paragraph — i.e. its combined source text contains
      ANY ASCII space) re-joins EVERY grouping boundary with a single space ``" "``.
      This matches the pre-E.7 blanket ``"\\n"->" "`` and is the load-bearing fix
      for the Korean regression: Hangul is East-Asian-width W (so ``_char_class``
      returns ``"ID"``) yet Korean is space-delimited, so a word space falling at a
      source-line ``"\\n"`` boundary must be preserved — class inference on the
      adjacent ID|ID glyphs cannot see this and wrongly glued the words.
    * ``space_delimited=False`` (a PURE no-space ideograph paragraph — no space
      anywhere) re-joins a no-width ideographic boundary with EMPTY ``""`` so two
      ideographs the generator merely split onto separate elements stay adjacent
      (no spurious inter-line space — an improvement over the pre-E.7 blanket
      space). A non-ideographic boundary in such a paragraph still yields a space.

    The default is ``True`` so the 2-argument call surface (and any caller without
    paragraph context) inherits the pre-E.7-faithful space, never silently gluing.
    An empty piece (no neighbour on one side) always yields a space.

    Real spaces are NOT affected by this helper: they live INSIDE a piece (they
    are not ``"\\n"`` characters) and flow untouched into :func:`break_into_lines`,
    where the recorded model preserves them.

    RESIDUAL (heuristic, documented in ``docs/decisions.md``): a CJK paragraph that
    DOES contain internal spaces (``space_delimited=True``) but whose source line
    happened to break EXACTLY at an ideograph-ideograph boundary gets a spurious
    ``" "`` at that ``"\\n"``. This matches pre-E.7 behaviour (which inserted a
    space at every ``"\\n"``), so it is NOT a regression; the perfect per-boundary
    fix needs the carved-out read-path provenance (0.3.0).

    Args:
        left_piece: The text immediately before the grouping boundary.
        right_piece: The text immediately after the grouping boundary.
        space_delimited: Whether the enclosing paragraph is space-delimited (its
            combined source text contains any space). Defaults to ``True`` (the
            pre-E.7-faithful, never-glue default).

    Returns:
        ``""`` for a no-width ideographic boundary in a pure no-space ideograph
        paragraph, else ``" "``.
    """
    if not left_piece or not right_piece:
        return " "
    if space_delimited:
        return " "
    left_cls = _char_class(left_piece[-1])
    right_cls = _char_class(right_piece[0])
    if right_cls == "ID" and left_cls in ("ID", "CL"):
        return ""
    return " "


def recorded_separators(segment: str, atoms: list[str]) -> list[str]:
    """Return the BYTE-FAITHFUL separator consumed at each inter-atom gap.

    The recorded-separator model (E.7 recorded-separator root fix). Class
    inference (``separator_between``) could not distinguish a former-WHITESPACE
    gap between two ideographs (``"報告 売上"`` — keep the space) from a
    genuinely-adjacent-ideograph gap (``"報告売上"`` — empty), because both are
    ID↔ID. Korean compounds the problem: Hangul is East-Asian-width W (ID
    class) yet uses spaces between words, so class inference deleted every
    Korean word space.

    This function recovers the gap provenance from the ORIGINAL ``segment``,
    not the atom classes. ``atoms`` are the (whitespace-stripped) substrings
    that :func:`segment_by_opportunities` produced in order; this walks
    ``segment`` left to right, re-locating each atom at its original offset,
    and for each gap between consecutive atoms inspects the slice of original
    text that was consumed there:

    * the consumed slice contains any whitespace (a former word boundary) ->
      a single space ``" "`` (a run is normalised to one space, matching the
      Latin single-space convention);
    * the consumed slice has no whitespace (a no-width ID↔ID / CL→ID break) ->
      EMPTY ``""``.

    So ``"報告 売上"`` records ``[" "]`` (the inter-ideograph space survives) and
    ``"報告売上"`` records ``["", ""]`` (every gap empty). ``"安녕하세요 세계 평화"``
    records a space at each Korean word boundary while still breaking freely at
    the no-width syllable opportunities.

    The walk tolerates an atom that re-locates ahead of the cursor (a stripped
    leading-space atom, or a defensive resync) by treating any skipped text as
    part of the preceding gap. If an atom cannot be re-located (a malformed
    atom list), the remaining gaps default to a space so two words are never
    silently glued.

    Args:
        segment: The original (un-stripped) source text the atoms came from.
        atoms: The atoms in order, as produced by
            :func:`segment_by_opportunities` (whitespace already stripped).

    Returns:
        One separator per inter-atom gap; length is ``max(len(atoms) - 1, 0)``.
    """
    if len(atoms) < 2:
        return []
    seps: list[str] = []
    cursor = 0
    # Offset (in ``segment``) one past the end of the previous atom.
    prev_end = 0
    found_first = False
    for index, atom in enumerate(atoms):
        if not atom:
            # Defensive: an empty atom carries no offset; treat its gap as a
            # space so a malformed list never glues neighbours.
            if found_first:
                seps.append(" ")
            continue
        start = segment.find(atom, cursor)
        if start < 0:
            # Atom not locatable from the cursor (malformed input). Fall back
            # to a space for this and every remaining gap.
            if found_first:
                seps.append(" ")
            cursor = len(segment)
            prev_end = len(segment)
            continue
        if found_first:
            gap = segment[prev_end:start]
            seps.append(" " if any(ch in " \t\r\n" for ch in gap) else "")
        found_first = True
        prev_end = start + len(atom)
        cursor = prev_end
        _ = index  # index retained for readability; no positional use.
    return seps


def segment_by_opportunities(text: str, opportunities: list[int]) -> list[str]:
    """Split ``text`` into atoms at the given break positions.

    Returns the substrings between consecutive break positions. Whitespace at a
    space-driven boundary is dropped so that, for a Latin / space-delimited
    string, the result matches ``str.split(" ")`` (the INV-G-10 contract); for
    an ID↔ID boundary nothing is dropped (the atoms re-join with an empty
    separator).

    Args:
        text: The text to segment.
        opportunities: The break positions from :func:`break_opportunities`.

    Returns:
        The atoms in order. Empty / whitespace-only atoms may be present and are
        the caller's responsibility to filter.
    """
    if not text:
        return [text]
    cuts = [0, *opportunities, len(text)]
    atoms: list[str] = []
    for start, end in zip(cuts, cuts[1:], strict=False):
        atom = text[start:end]
        # Strip a leading/trailing run of plain spaces at a space-driven cut so
        # the Latin path equals ``str.split(" ")``. ID-boundary atoms have no
        # spaces, so this is a no-op for CJK.
        atoms.append(atom.strip(" "))
    return atoms
