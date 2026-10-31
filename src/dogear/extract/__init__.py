"""Turn a book file into a `dogear.document.Book`.

Each format module exposes `extract(path, config) -> Book`. Book files come from anywhere,
so extractors treat them as untrusted input: no network access, no external entities,
and nothing is unpacked to disk.

`FORMATS` is the one list of what Dogear reads. The file extensions it accepts, and the
names of the formats in help text and on the web page, all come from it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dogear.config import Config
from dogear.document import Book
from dogear.errors import ScannedPdfError, UnsupportedFormatError
from dogear.render.progress import Report

log = logging.getLogger(__name__)

Extractor = Callable[[Path, Config], Book]


def _epub(path: Path, config: Config) -> Book:
    from dogear.extract import epub

    return epub.extract(path, config)


def _pdf(path: Path, config: Config) -> Book:
    from dogear.extract import pdf

    return pdf.extract(path, config)


def _docx(path: Path, config: Config) -> Book:
    from dogear.extract import docx

    return docx.extract(path, config)


def _html(path: Path, config: Config) -> Book:
    from dogear.extract import html_book

    return html_book.extract(path, config)


def _markdown(path: Path, config: Config) -> Book:
    from dogear.extract import markdown_book

    return markdown_book.extract(path, config)


def _kindle(path: Path, config: Config) -> Book:
    from dogear.extract import calibre

    return calibre.extract(path, config)


@dataclass(frozen=True)
class Format:
    name: str  # as people say it, in sentences such as "an EPUB, PDF, ... file"
    extensions: tuple[str, ...]  # lower case, with the dot
    extractor: Extractor


# In the order they are named to people: the most common books first.
FORMATS: tuple[Format, ...] = (
    Format("EPUB", (".epub",), _epub),
    Format("PDF", (".pdf",), _pdf),
    Format("Word (DOCX)", (".docx",), _docx),
    Format("HTML", (".html", ".htm", ".xhtml"), _html),
    Format("Markdown", (".md", ".markdown"), _markdown),
    Format("MOBI", (".mobi",), _kindle),
    Format("AZW3", (".azw3", ".azw"), _kindle),
)

EXTRACTORS: dict[str, Extractor] = {
    extension: f.extractor for f in FORMATS for extension in f.extensions
}


def format_names(conjunction: str) -> str:
    """The formats Dogear reads, as a phrase: "EPUB, PDF, ..., MOBI, or AZW3"."""
    names = [f.name for f in FORMATS]
    return f"{', '.join(names[:-1])}, {conjunction} {names[-1]}"


def extract_book(path: Path, config: Config, report: Report | None = None) -> Book:
    """The book in `path`. A scanned PDF is read with OCR first if pdf.auto_ocr is on,
    which can take many minutes: `report` hears how that goes (else it is logged)."""
    if not path.is_file():
        raise UnsupportedFormatError(
            f"No such file: {path}", hint="Check the file name and the folder it is in."
        )
    extractor = EXTRACTORS.get(path.suffix.lower())
    if extractor is None:
        supported = ", ".join(sorted(EXTRACTORS))
        raise UnsupportedFormatError(
            f"Dogear can't read {path.suffix or 'files without an extension'} files yet.",
            hint=f"Supported formats: {supported}.",
        )
    try:
        return extractor(path, config)
    except ScannedPdfError:
        if not config["pdf"]["auto_ocr"]:
            raise
    from dogear.extract import pdf

    return pdf.extract_scanned(path, config, report or log.info)
