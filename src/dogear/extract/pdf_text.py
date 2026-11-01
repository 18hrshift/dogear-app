"""Turn the lines on PDF pages into clean paragraph text.

Works on lines that `dogear.extract.pdf` has already read, so nothing here opens a file.
Cleanup removes print layout only, such as line-end hyphens; `pdf_furniture` removes page
numbers and running headers. Words are never rewritten; speech normalization happens later.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise

log = logging.getLogger(__name__)

# Given the word before a line-end hyphen and the word after it, whether the hyphen is
# part of the word ("well-known") rather than a break in it ("exam-ple").
KeepHyphen = Callable[[str, str], bool]

# Two font sizes within this fraction of each other count as the same size.
_SAME_SIZE = 0.1
# Line sizes are rounded to 0.1 pt when read, so comparisons allow for that.
_SIZE_SLACK = 0.05
# Line spacing to assume when a book has no two consecutive body lines to measure.
_DEFAULT_LEADING = 1.2
# Measuring line spacing, look this many lines ahead for the next line of the same
# column: enough for three columns whose lines don't line up.
_COLUMN_LOOKAHEAD = 3
# A heading set in large type wraps onto at most this many lines.
HEADING_MAX_LINES = 3

# A valid lowercase roman numeral such as "xiv". Lowercase only: "I", "IV" or "MIX" alone
# on a line are more often a chapter number or a word than a page number.
ROMAN = r"(?=[ivxlcdm])m{0,4}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
_SPLIT_WORD = re.compile(r"[^\W\d_]-$")  # a letter, then a hyphen, at the end of a line
# The word before a line-end hyphen, and the word that starts the next line. Words may
# already hold hyphens: "out-of-the-" + "way".
_WORD_BEFORE_HYPHEN = re.compile(r"([^\W\d_]+(?:-[^\W\d_]+)*)-$")
_WORD_AT_START = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
_OPEN_DASH = re.compile(r"\S[—–]$")  # a dash touching the last word, as in "and then—"
_SENTENCE_END = re.compile(r"[.!?…][\"'”’»)\]]*$|[—–-][\"'”’»]+$")
_SOFT_HYPHEN = "\u00ad"
_OPENING_QUOTES = "\"'\u201c\u2018\u00ab"


@dataclass(frozen=True)
class Line:
    """One line of text as laid out, in PDF points measured from the page's top-left corner."""

    text: str
    top: float
    bottom: float
    x0: float
    x1: float
    size: float  # the most common character size in the line
    chars: int  # non-space characters
    bold: bool = False  # most of its characters are in a bold font
    # Where small raised digits or note symbols sit in `text`, as (start, end): possible
    # note references, such as the "1" in "rabbit-hole¹".
    raised: tuple[tuple[int, int], ...] = ()
    note_refs: tuple[str, ...] = ()  # ids of the notes it refers to; see `pdf_notes`
    # The line cut at gaps wide enough to be a column gutter, left to right; empty when
    # there are none. Set when reading and used only by `pdf_columns`.
    parts: tuple[Line, ...] = ()
    seq: int = 0  # position in reading order on its page, set by `pdf_columns`
    # Which run of text on its page the line is in: one column, or the full-width text
    # between blocks of columns. Paragraphs are found within a flow, each with its own
    # margin, and carry on from one flow to the next as they do from page to page.
    flow: int = 0


def merge_lines(pieces: Sequence[Line]) -> Line:
    """One line made of pieces of a row, left to right, joined with spaces."""
    if len(pieces) == 1:
        return pieces[0]
    main = max(pieces, key=lambda piece: piece.chars)
    chars = sum(piece.chars for piece in pieces)
    raised: list[tuple[int, int]] = []
    offset = 0
    for piece in pieces:
        raised += [(start + offset, end + offset) for start, end in piece.raised]
        offset += len(piece.text) + 1  # the space joining it to the next piece
    return Line(
        text=" ".join(piece.text for piece in pieces),
        top=min(piece.top for piece in pieces),
        bottom=max(piece.bottom for piece in pieces),
        x0=min(piece.x0 for piece in pieces),
        x1=max(piece.x1 for piece in pieces),
        size=main.size,
        chars=chars,
        bold=2 * sum(piece.chars for piece in pieces if piece.bold) > chars,
        raised=tuple(raised),
        note_refs=tuple(ref for piece in pieces for ref in piece.note_refs),
    )


def remove_spans(line: Line, spans: Iterable[tuple[int, int]]) -> Line:
    """`line` with the text in `spans` taken out, such as note markers from its `raised`.

    Where taking a span out would leave two spaces in a row, or a space at either end,
    one of them goes too. Raised spans left in the line move with the text.
    """
    text = line.text
    gone: set[int] = set()
    for start, end in spans:
        gone.update(range(start, end))
        if (start == 0 or text[start - 1] == " ") and (end == len(text) or text[end] == " "):
            if end < len(text):
                gone.add(end)  # the space after
            elif start > 0:
                gone.add(start - 1)  # the space before, at the end of the line
    kept = [i for i in range(len(text)) if i not in gone]
    moved = {old: new for new, old in enumerate(kept)}
    raised = tuple(
        (moved[start], moved[end - 1] + 1)
        for start, end in line.raised
        if all(i in moved for i in range(start, end))
    )
    return replace(
        line,
        text="".join(text[i] for i in kept),
        chars=line.chars - sum(not text[i].isspace() for i in gone),
        raised=raised,
    )


@dataclass(frozen=True)
class Figure:
    """An image on a page, big enough to be content rather than decoration."""

    top: float
    bottom: float
    x0: float
    x1: float
    seq: int = 0  # position in reading order on its page, counted with the lines
    caption: tuple[Line, ...] = ()  # its printed caption, taken out of the page's lines


@dataclass(frozen=True)
class PageNote:
    """A footnote printed at the foot of a page, as found by `pdf_notes`."""

    note_id: str  # unique in the book
    label: str  # the marker as printed, such as "1" or "*"
    lines: tuple[Line, ...]  # its text, without the marker; may run onto the next page


@dataclass(frozen=True)
class Page:
    index: int  # 0-based position in the file
    label: str  # the printed page number, such as "xii" or "42"
    lines: tuple[Line, ...]
    has_images: bool = False  # any image at all, even a tiny one
    figures: tuple[Figure, ...] = ()
    notes: tuple[PageNote, ...] = ()  # footnotes printed at its foot
    image_page_share: float = 0  # raster images' combined page share; overlaps count once

    @property
    def char_count(self) -> int:
        return sum(line.chars for line in self.lines)


@dataclass(frozen=True)
class Layout:
    """Measurements of a book's body text, used to tell headings and paragraphs apart."""

    body_size: float
    heading_size: float
    line_pitch: float  # usual distance between the baselines of two body lines
    gap_ratio: float
    indent: float  # how far in, in points, a line must start to count as indented
    # Lines at least this size are subheadings inside a section; infinity turns size-based
    # subheadings off.
    subheading_size: float = math.inf
    body_bold: bool = False  # body text is set in bold, so bold lines are not headings

    def is_heading(self, line: Line) -> bool:
        return line.size >= self.heading_size - _SIZE_SLACK

    def is_subheading_size(self, line: Line) -> bool:
        return line.size >= self.subheading_size - _SIZE_SLACK

    def is_body(self, line: Line) -> bool:
        return same_size(line.size, self.body_size)

    @property
    def paragraph_gap(self) -> float:
        """Baseline-to-baseline distance beyond which two lines are in separate blocks."""
        return self.line_pitch * self.gap_ratio


@dataclass(frozen=True)
class PageText:
    """A page's lines grouped into paragraphs."""

    paragraphs: tuple[tuple[Line, ...], ...]
    indented_start: bool  # the first line is indented, so it can't continue the last page


def same_size(a: float, b: float) -> bool:
    return abs(a - b) <= _SAME_SIZE * max(a, b) + _SIZE_SLACK


def measure_layout(
    pages: Sequence[Page],
    *,
    heading_ratio: float,
    subheading_ratio: float,
    gap_ratio: float,
    indent_ratio: float,
) -> Layout:
    body = body_size(pages)
    return Layout(
        body_size=body,
        heading_size=body * heading_ratio,
        line_pitch=_line_pitch(pages, body),
        gap_ratio=gap_ratio,
        indent=body * indent_ratio,
        subheading_size=body * subheading_ratio,
        body_bold=_mostly_bold(pages, body),
    )


def _mostly_bold(pages: Sequence[Page], body: float) -> bool:
    lines = [line for page in pages for line in page.lines if same_size(line.size, body)]
    return 2 * sum(line.chars for line in lines if line.bold) > sum(line.chars for line in lines)


def body_size(pages: Sequence[Page]) -> float:
    """The font size that most characters are set in."""
    return common_size(line for page in pages for line in page.lines)


def common_size(lines: Iterable[Line]) -> float:
    """The font size that most characters in `lines` are set in."""
    weights: Counter[float] = Counter()
    for line in lines:
        weights[line.size] += line.chars
    if not weights:
        raise ValueError("no text to measure")
    return weights.most_common(1)[0][0]


def _line_pitch(pages: Sequence[Page], body: float) -> float:
    # The most common baseline-to-baseline distance between body lines. The mode, not the
    # median, because books of short paragraphs have nearly as many gaps as plain lines.
    pitches: Counter[float] = Counter()
    for page in pages:
        for a, b in _line_pairs(page.lines):
            if same_size(a.size, body) and same_size(b.size, body) and b.bottom > a.bottom:
                pitches[round((b.bottom - a.bottom) * 2) / 2] += 1
    if not pitches:
        return body * _DEFAULT_LEADING
    # Ties go to the smaller distance: plain line spacing is never wider than a gap.
    return min(pitches.items(), key=lambda item: (-item[1], item[0]))[0]


def _line_pairs(lines: Sequence[Line]) -> Iterable[tuple[Line, Line]]:
    """Each line with the next one below it in the same column: the first of the next few
    that overlaps it across the page. In one column that is simply the next line; where
    columns' lines don't line up, pdfplumber reads them interleaved, and the line just
    after is the other column's."""
    for i, line in enumerate(lines):
        below = lines[i + 1 : i + 1 + _COLUMN_LOOKAHEAD]
        found = next((b for b in below if b.x0 < line.x1 and line.x0 < b.x1), None)
        if found is not None:
            yield line, found


def merge_drop_caps(lines: Sequence[Line]) -> tuple[Line, ...]:
    """Put a drop cap, a large first letter set beside the opening lines, back on its word.

    pdfplumber reads the letter as a line of its own, after the line it starts. The lines
    beside it are indented to make room; they are moved back to the letter's margin so
    the paragraph doesn't look like it starts over. A line beside the letter starts within
    a letter's width of it: text further right, such as a second column, is left alone.
    """
    result = list(lines)
    for cap in lines:
        if len(cap.text.lstrip(_OPENING_QUOTES)) != 1 or not cap.text[-1].isalpha():
            continue
        beside = [
            line
            for line in result
            if line is not cap
            and cap.x1 - _SIZE_SLACK <= line.x0 <= cap.x1 + (cap.x1 - cap.x0)
            and line.bottom > cap.top
            and line.top < cap.bottom
            and line.size < cap.size
            and not same_size(line.size, cap.size)
        ]
        if not beside:
            continue  # nothing beside it: a heading such as "I", not a drop cap
        first = min(beside, key=lambda line: line.top)
        for line in beside:
            result[result.index(line)] = _after_cap(line, cap, line is first)
        result.remove(cap)
    return tuple(result)


def _after_cap(line: Line, cap: Line, first: bool) -> Line:
    """`line` moved to the drop cap's margin, with the letter put back if it starts the
    paragraph. A line in parts gets the same change to its first part."""
    merged = replace(line, x0=cap.x0)
    if first:
        shift = len(cap.text)
        merged = replace(
            merged,
            text=cap.text + line.text,
            chars=line.chars + cap.chars,
            raised=tuple((start + shift, end + shift) for start, end in line.raised),
        )
    if line.parts:
        merged = replace(merged, parts=(_after_cap(line.parts[0], cap, first), *line.parts[1:]))
    return merged


def leading_heading_lines(lines: Sequence[Line], layout: Layout) -> int:
    """How many lines at the start of `lines` are set in heading type."""
    count = 0
    while count < min(HEADING_MAX_LINES, len(lines)) and layout.is_heading(lines[count]):
        count += 1
    text = "".join(line.text for line in lines[:count])
    # One large letter is a drop cap or an ornament, not a heading.
    return count if sum(c.isalnum() for c in text) >= 2 else 0


def join_lines(lines: Iterable[str], keep_hyphen: KeepHyphen | None = None) -> str:
    """Join a paragraph's lines into one string, undoing line-end hyphenation.

    "beauti-" + "ful" becomes "beautiful". A hyphen before a lowercase letter is dropped
    unless `keep_hyphen(left, right)` says the two parts make a hyphenated compound, as
    "well-" + "known" does; see `pdf_hyphens`. A hyphen before a capital or a digit is kept
    without a space ("Anglo-" + "Saxon" is "Anglo-Saxon"). Other lines join with a space.
    """
    text = ""
    for raw in lines:
        line = " ".join(raw.split())
        if not line:
            continue
        if not text:
            text = line
        elif text.endswith(_SOFT_HYPHEN):
            text = text[:-1] + line
        elif _SPLIT_WORD.search(text):
            soft = line[0].islower() and not (keep_hyphen and _compound(text, line, keep_hyphen))
            text = (text[:-1] if soft else text) + line
        elif _OPEN_DASH.search(text):
            text += line
        else:
            text += " " + line
    return text.replace(_SOFT_HYPHEN, "")


def _compound(text: str, line: str, keep_hyphen: KeepHyphen) -> bool:
    left = _WORD_BEFORE_HYPHEN.search(text)
    right = _WORD_AT_START.match(line)
    return bool(left and right) and keep_hyphen(left.group(1), right.group(0))


def split_paragraphs(lines: Sequence[Line], layout: Layout) -> PageText:
    """Group a page's lines into paragraphs by vertical gaps, indents, and size changes."""
    if not lines:
        return PageText((), False)
    body = [line for line in lines if layout.is_body(line)] or list(lines)
    # The leftmost body line marks the margin. Not the most common start: on a page of
    # one-line dialogue most lines are indented.
    margin = min(line.x0 for line in body)
    right = max(line.x1 for line in body)
    groups: list[list[Line]] = [[lines[0]]]
    for prev, line in pairwise(lines):
        if _starts_paragraph(prev, line, margin, right, layout):
            groups.append([line])
        else:
            groups[-1].append(line)
    return PageText(
        paragraphs=tuple(tuple(g) for g in groups),
        indented_start=_indented(lines[0], margin, layout),
    )


def _indented(line: Line, margin: float, layout: Layout) -> bool:
    return line.x0 - margin >= layout.indent


def _starts_paragraph(prev: Line, line: Line, margin: float, right: float, layout: Layout) -> bool:
    if not same_size(prev.size, line.size):
        return True
    if line.bottom - prev.bottom > layout.paragraph_gap:
        return True
    if not _indented(line, margin, layout):
        return False
    # An indented line after a line at the margin is a first-line indent. After another
    # indented line it starts a paragraph only if that one ended a sentence short of the
    # margin, as one-line dialogue does; otherwise both belong to an indented block such
    # as a quotation.
    if line.x0 - prev.x0 >= layout.indent:
        return True
    return prev.x1 <= right - layout.indent and _SENTENCE_END.search(prev.text) is not None


def ends_sentence(text: str) -> bool:
    return _SENTENCE_END.search(text) is not None


def continues(previous: Sequence[Line], page: PageText, layout: Layout) -> bool:
    """Whether a page's first paragraph carries on `previous`, the last page's last one."""
    if not previous or not page.paragraphs or page.indented_start:
        return False
    last, first = previous[-1], page.paragraphs[0][0]
    if not same_size(last.size, first.size):
        return False
    return not _SENTENCE_END.search(last.text) or first.text[:1].islower()
