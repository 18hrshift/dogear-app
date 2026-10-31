"""The facts `dogear outline` gives about a book, as plain data and as sentences.

The text outline and `outline --json` are both made from one summary, and the web page
reads the JSON back into the same summary, so every surface describes a section in the
same words.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from dogear.document import SECTION_KINDS, Book, Footnote, Image, SectionKind, Table

KIND_NAMES: dict[SectionKind, str] = {
    "front": "front matter",
    "body": "main text",
    "back": "back matter",
}


@dataclass(frozen=True)
class SectionSummary:
    number: int  # as `dogear make --sections` counts them, from 1
    title: str
    kind: SectionKind
    words: int
    first_page: str | None  # print page labels, None when the section has none
    last_page: str | None
    images: int
    notes: int
    tables: int


@dataclass(frozen=True)
class BookSummary:
    title: str
    authors: tuple[str, ...]
    language: str | None
    source_format: str  # the file's extension without the dot: "epub", "docx", "md"...
    words: int
    sections: tuple[SectionSummary, ...]


def summarize(book: Book) -> BookSummary:
    sections = []
    for number, section in enumerate(book.sections, start=1):
        pages = section.page_labels()
        sections.append(
            SectionSummary(
                number=number,
                title=section.title,
                kind=section.kind,
                words=section.word_count(),
                first_page=pages[0] if pages else None,
                last_page=pages[-1] if pages else None,
                images=sum(isinstance(b, Image) for b in section.blocks),
                notes=sum(isinstance(b, Footnote) for b in section.blocks),
                tables=sum(isinstance(b, Table) for b in section.blocks),
            )
        )
    return BookSummary(
        title=book.metadata.title,
        authors=tuple(book.metadata.authors),
        language=book.metadata.language or None,
        source_format=book.source_format,
        words=book.word_count(),
        sections=tuple(sections),
    )


def summary_to_json(summary: BookSummary) -> dict[str, Any]:
    return asdict(summary)


_BOOK_FIELDS: dict[str, Any] = {
    "title": str,
    "authors": list,
    "language": (str, type(None)),
    "source_format": str,
    "words": int,
    "sections": list,
}
_SECTION_FIELDS: dict[str, Any] = {
    "number": int,
    "title": str,
    "kind": str,
    "words": int,
    "first_page": (str, type(None)),
    "last_page": (str, type(None)),
    "images": int,
    "notes": int,
    "tables": int,
}


def summary_from_json(data: Any) -> BookSummary:
    """A summary in `summary_to_json`'s shape, checked field by field. Raises ValueError
    naming the first field that is missing or wrong."""
    book = _fields(data, "the book", _BOOK_FIELDS)
    if not all(isinstance(author, str) for author in book["authors"]):
        raise ValueError("an author isn't text")
    sections = []
    for number, item in enumerate(book["sections"], start=1):
        where = f"section {number}"
        fields = _fields(item, where, _SECTION_FIELDS)
        if fields["kind"] not in SECTION_KINDS:
            raise ValueError(f"{where} has an unknown kind, {fields['kind']!r}")
        if fields["number"] != number:
            raise ValueError(f"{where} is numbered {fields['number']}")
        sections.append(SectionSummary(**fields))
    return BookSummary(
        title=book["title"],
        authors=tuple(book["authors"]),
        language=book["language"],
        source_format=book["source_format"],
        words=book["words"],
        sections=tuple(sections),
    )


def _fields(data: Any, where: str, types: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{where} isn't a JSON object")
    for name, kind in types.items():
        value = data.get(name)
        if name not in data or isinstance(value, bool) or not isinstance(value, kind):
            raise ValueError(f"{where} has no {name}, or it isn't the right type")
        if isinstance(value, int) and value < 0:
            raise ValueError(f"{where} has a negative {name}")
    return {name: data[name] for name in types}


def describe_section(section: SectionSummary) -> str:
    """'Chapter One: main text, 15 words, print pages 1 to 2, 1 note.'"""
    parts = [f"{section.title}: {KIND_NAMES[section.kind]}", count(section.words, "word")]
    if section.first_page and section.first_page == section.last_page:
        parts.append(f"print page {section.first_page}")
    elif section.first_page:
        parts.append(f"print pages {section.first_page} to {section.last_page}")
    for number, name in [
        (section.images, "image"),
        (section.notes, "note"),
        (section.tables, "table"),
    ]:
        if number:
            parts.append(count(number, name))
    return ", ".join(parts) + "."


def count(n: int, noun: str) -> str:
    """'1 word', '1,204 words'."""
    return f"{n:,} {noun}{'' if n == 1 else 's'}"
