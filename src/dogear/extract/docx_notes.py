"""Read a Word document's footnotes and endnotes, and the marks Word prints for them.

Each note is a w:footnote or w:endnote in its own part, holding paragraphs, and is
referenced from the text by id. Word numbers notes itself, in the order they are
referenced: footnotes 1, 2, 3 and endnotes i, ii, iii unless the document chooses
another format (w:footnotePr/w:numFmt in the settings or the last section's
properties). A reference with a custom mark (w:customMarkFollows) prints that mark
instead and takes no number.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from lxml import etree

from dogear.extract.docx_package import W

NOTE_KINDS = ("footnote", "endnote")
DEFAULT_FORMATS = {"footnote": "decimal", "endnote": "lowerRoman"}
# w:type values for the lines Word draws above notes, which hold no text of their own.
SEPARATOR_TYPES = frozenset({"separator", "continuationSeparator", "continuationNotice"})
CHICAGO_MARKS = ("*", "†", "‡", "§")  # * dagger, double dagger, section


@dataclass(frozen=True)
class NotePart:
    kind: str  # "footnote" or "endnote"
    path: str  # archive path, such as "word/footnotes.xml"
    notes: dict[str, etree._Element]  # w:id -> the w:footnote or w:endnote element

    def note_id(self, ref_id: str) -> str:
        """The book-wide id of the note `ref_id` names."""
        return f"{self.path}#{ref_id}"


def read_note_part(root: etree._Element | None, kind: str, path: str) -> NotePart:
    notes: dict[str, etree._Element] = {}
    if root is not None:
        for el in root.iter(f"{W}{kind}"):
            note_id = el.get(f"{W}id")
            if note_id is not None and el.get(f"{W}type") not in SEPARATOR_TYPES:
                notes.setdefault(note_id, el)
    return NotePart(kind, path, notes)


def number_formats(settings: etree._Element | None, body: etree._Element) -> dict[str, str]:
    """The number format of footnotes and of endnotes: the last section's, else the
    settings', else Word's default."""
    formats = dict(DEFAULT_FORMATS)
    last_section = body.find(f"{W}sectPr")
    for source in (settings, last_section):
        if source is None:
            continue
        for kind in NOTE_KINDS:
            fmt = source.find(f"{W}{kind}Pr/{W}numFmt")
            if fmt is not None and fmt.get(f"{W}val"):
                formats[kind] = fmt.get(f"{W}val")
    return formats


def mark(number: int, fmt: str) -> str:
    """The mark Word prints for the `number`th note (from 1) in the format `fmt`."""
    if fmt in ("lowerRoman", "upperRoman"):
        roman = _roman(number)
        return roman.lower() if fmt == "lowerRoman" else roman
    if fmt in ("lowerLetter", "upperLetter"):
        # a, b, ... z, then aa, bb, ... zz, as Word counts.
        letter = chr(ord("a") + (number - 1) % 26) * ((number - 1) // 26 + 1)
        return letter if fmt == "lowerLetter" else letter.upper()
    if fmt == "chicago":
        return CHICAGO_MARKS[(number - 1) % 4] * ((number - 1) // 4 + 1)
    return str(number)


def _roman(number: int) -> str:
    values = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
        (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )  # fmt: skip
    out = []
    for value, letters in values:
        count, number = divmod(number, value)
        out.append(letters * count)
    return "".join(out)


class NoteCounter:
    """Hands out the next mark for each kind of note, as Word numbers them."""

    def __init__(self, formats: dict[str, str]) -> None:
        self._formats = formats
        self._counts = {kind: itertools.count(1) for kind in NOTE_KINDS}

    def next_mark(self, kind: str) -> str:
        return mark(next(self._counts[kind]), self._formats[kind])
