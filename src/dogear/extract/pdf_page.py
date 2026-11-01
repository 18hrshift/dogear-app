"""Read one pdfplumber page into a `Page`: its lines and its figures.

Lines come from pdfplumber's text map, the same one its `extract_text_lines` uses, so a
line's text and box are exactly what that method gives. Building them here keeps what it
drops: the character behind every letter, which shows where a line has a gap wide enough
to be a column gutter, which letters are bold, and which are small raised digits or
symbols that may be note references.

Figures are the page's images that cover at least a set share of the page. Smaller ones
are decoration, such as ornaments and rules. An image with text on top of it is a page
background, or the picture of a scanned page under its text layer, so it is not a figure
either. Vector drawings are not images and are not found.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from dogear.extract.pdf_images import Box, image_boxes, image_page_share
from dogear.extract.pdf_text import (
    Figure,
    Line,
    Page,
    common_size,
    merge_drop_caps,
    same_size,
)

log = logging.getLogger(__name__)

Char = dict[str, Any]
# One letter of a line's text and the character it came from. pdfplumber puts a space
# between words without a character behind it; one character can give several letters
# ("ﬁ" gives "f" and "i").
Cell = tuple[str, Char | None]

# Font names such as "Times-Bold", "ABCDEF+Minion-Semibold", or "Arial-BlackItalic".
_BOLD_FONT = re.compile(r"bold|black|heavy|demi", re.IGNORECASE)
# Note markers: digits and the traditional symbols, raised small above the line...
_MARKERS = frozenset("0123456789*†‡§¶‖")
# ...or digits that are superscript characters in their own right.
SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"


@dataclass(frozen=True)
class ReadSettings:
    """The [pdf] settings that apply while reading a page."""

    column_gap_ratio: float  # a gutter is this many times the page's main font size
    image_min_page_share: float  # a figure covers at least this share of the page
    # A marker sits at least this many times the line's font size above the line's letters.
    marker_rise_ratio: float


def read_page(plumber_page: Any, index: int, label: str, settings: ReadSettings) -> Page:
    rise = settings.marker_rise_ratio
    tuples = plumber_page.get_textmap().tuples
    rows = [row for cells in _rows(tuples) if (row := _row(cells, rise))]
    lines = [line for line, _ in rows]
    if lines:
        gap = settings.column_gap_ratio * common_size(lines)
        lines = [replace(line, parts=_parts(cells, gap, rise)) for line, cells in rows]
    lines = merge_drop_caps(lines)
    return Page(
        index=index,
        label=label,
        lines=lines,
        has_images=bool(plumber_page.images),
        figures=_figures(plumber_page, lines, settings.image_min_page_share),
        image_page_share=image_page_share(plumber_page),
    )


def _figures(plumber_page: Any, lines: Sequence[Line], min_share: float) -> tuple[Figure, ...]:
    x0, top, x1, bottom = plumber_page.bbox
    page_area = (x1 - x0) * (bottom - top)
    figures = []
    for box in _merge_overlapping(image_boxes(plumber_page)):
        if _area(box) < min_share * page_area:
            log.debug(
                "Page %d: skipped a small image, taken as decoration", plumber_page.page_number
            )
        elif any(_inside(line, box) for line in lines):
            log.debug("Page %d: skipped an image with text on it", plumber_page.page_number)
        else:
            figures.append(Figure(top=box[1], bottom=box[3], x0=box[0], x1=box[2]))
    return tuple(figures)


def _merge_overlapping(boxes: list[Box]) -> list[Box]:
    """Overlapping or touching boxes made one, as when a picture is drawn in tiles."""
    merged: list[Box] = []
    for box in boxes:
        while overlap := next((m for m in merged if _touch(m, box)), None):
            merged.remove(overlap)
            box = (
                min(box[0], overlap[0]),
                min(box[1], overlap[1]),
                max(box[2], overlap[2]),
                max(box[3], overlap[3]),
            )
        merged.append(box)
    return merged


def _touch(a: Box, b: Box) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _area(box: Box) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def _inside(line: Line, box: Box) -> bool:
    """Whether the middle of `line` is inside `box`."""
    x, y = (line.x0 + line.x1) / 2, (line.top + line.bottom) / 2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _rows(tuples: Sequence[Cell]) -> list[list[Cell]]:
    """The text map split into rows, as `extract_text_lines` splits it."""
    rows: list[list[Cell]] = [[]]
    for letter, char in tuples:
        if letter == "\n" and char is None:
            rows.append([])
        else:
            rows[-1].append((letter, char))
    return rows


def _row(cells: Sequence[Cell], rise: float) -> tuple[Line, list[Cell]] | None:
    """The line `extract_text_lines` makes of a row, with whitespace collapsed, and the
    cells behind that text. None for a row with no text."""
    marks = [i for i, (letter, _) in enumerate(cells) if letter != " "]
    if not marks:
        return None
    span = cells[marks[0] : marks[-1] + 1]
    text = _collapse(span)
    box = [char for _, char in span if char is not None]
    if not text or not box:
        return None
    return _line(text, box, rise), text


def _collapse(cells: Sequence[Cell]) -> list[Cell]:
    """Cells with each run of whitespace made one plain space, and none at either end."""
    out: list[Cell] = []
    for letter, char in cells:
        if not letter.isspace():
            out.append((letter, char))
        elif out and out[-1] != (" ", None):
            out.append((" ", None))
    if out and out[-1] == (" ", None):
        out.pop()
    return out


def _line(cells: Sequence[Cell], box: Sequence[Char], rise: float) -> Line:
    """A line of the text in `cells`, measured over the characters in `box`."""
    chars = [char for _, char in cells if char is not None]
    sizes = Counter(round(char["size"], 1) for char in chars)
    size = sizes.most_common(1)[0][0]
    return Line(
        text="".join(letter for letter, _ in cells),
        top=min(char["top"] for char in box),
        bottom=max(char["bottom"] for char in box),
        x0=min(char["x0"] for char in box),
        x1=max(char["x1"] for char in box),
        size=size,
        chars=len(chars),
        bold=2 * sum(bool(_BOLD_FONT.search(char["fontname"])) for char in chars) > len(chars),
        raised=_raised(cells, size, rise),
    )


def _raised(cells: Sequence[Cell], size: float, rise: float) -> tuple[tuple[int, int], ...]:
    """Where runs of small digits or note symbols sit above the line, as (start, end)."""
    main = sorted(c["bottom"] for _, c in cells if c is not None and round(c["size"], 1) == size)
    base = main[len(main) // 2]  # where the bottoms of the line's own letters are

    def marker(letter: str, char: Char | None) -> bool:
        if letter in SUPERSCRIPT_DIGITS:
            return True
        return (
            char is not None
            and letter in _MARKERS
            and char["size"] < size
            and not same_size(char["size"], size)
            and char["bottom"] <= base - rise * size
        )

    spans: list[tuple[int, int]] = []
    for i, (letter, char) in enumerate(cells):
        if not marker(letter, char):
            continue
        if spans and spans[-1][1] == i:
            spans[-1] = (spans[-1][0], i + 1)
        else:
            spans.append((i, i + 1))
    return tuple(spans)


def _parts(cells: Sequence[Cell], gap: float, rise: float) -> tuple[Line, ...]:
    """The row cut where the space between two words is at least `gap` wide, or ()."""
    pieces: list[list[Cell]] = [[]]
    previous: Char | None = None
    for letter, char in cells:
        if char is not None:
            between_words = pieces[-1][-1:] == [(" ", None)]
            if between_words and previous and char["x0"] - previous["x1"] >= gap:
                pieces[-1].pop()  # the space before the gap
                pieces.append([])
            previous = char
        pieces[-1].append((letter, char))
    if len(pieces) < 2:
        return ()
    return tuple(
        _line(piece, [char for _, char in piece if char is not None], rise) for piece in pieces
    )
