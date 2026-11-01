"""Read a PDF into a `Book`: metadata, print pages, chapters, and paragraphs.

pypdf reads the document catalog (metadata, bookmarks, page labels, encryption) and
pdfplumber reads the text with its positions and sizes. Each file is opened once per
library, and the pages are read in a single pass that every later step reuses.
PyMuPDF is not used because it is AGPL.

The steps, in order: read each page's lines and figures (`pdf_page`); remove page numbers
and running headers and footers (`pdf_furniture`); take footnotes off the foot of each
page (`pdf_notes`); put columns in reading order (`pdf_columns`); link note markers in
the text to their notes; attach captions to figures (`pdf_figures`); find chapters
(`pdf_chapters`); and write sections (`pdf_sections`, with subheadings from
`pdf_headings` and line-end hyphens decided by `pdf_hyphens`).

A scanned PDF, pictures of pages with almost no text, raises ScannedPdfError. When
`extract_book` supplies a progress reporter and pdf.auto_ocr is on, OCR finds its text
(dogear.ocr), and the same steps run on that. In a searchable PDF, only textless pages
with page-sized images are read with OCR; the other pages keep their original text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pdfplumber
from pdfminer.psparser import PSException
from pdfplumber.utils.exceptions import PdfminerException
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pypdf.generic import Destination

from dogear.config import Config
from dogear.document import Book, Metadata
from dogear.errors import ConfigError, ExtractionError, ScannedPdfError, ToolNotFoundError
from dogear.extract.pdf_chapters import (
    Boundary,
    ChapterSource,
    check_methods,
    detect_chapters,
    is_section_label,
    mark_back_matter,
)
from dogear.extract.pdf_columns import ColumnRules, order_columns
from dogear.extract.pdf_figures import attach_captions
from dogear.extract.pdf_furniture import strip_furniture
from dogear.extract.pdf_hyphens import HyphenEvidence
from dogear.extract.pdf_notes import find_notes, link_note_refs, note_lines
from dogear.extract.pdf_page import ReadSettings, read_page
from dogear.extract.pdf_sections import build_sections
from dogear.extract.pdf_text import Layout, Line, Page, measure_layout
from dogear.ocr.cache import discard, text_layer
from dogear.render.progress import Report

log = logging.getLogger(__name__)

OCR_HINT = "Run `dogear ocr <file>` first to add a text layer, then open the new file."
AUTO_OCR_OFF = (
    "Or, to have Dogear stop at scanned PDFs instead, set auto_ocr = false under [pdf] in "
    "your settings file."
)
_DAMAGED_HINT = (
    "Check that the file opens in a PDF reader. If it does, save a new copy from there "
    "and open that copy instead."
)
_LANGUAGE_TAG = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")


@dataclass(frozen=True)
class PdfSettings:
    """The [pdf] config table, checked."""

    chapter_detection: tuple[str, ...]
    page_split_size: int
    heading_size_ratio: float
    subheading_size_ratio: float
    min_chars_per_page: float
    scanned_image_min_page_share: float
    running_line_min_pages: int
    running_line_min_share: float
    running_line_max_lines: int
    paragraph_gap_ratio: float
    paragraph_indent_ratio: float
    hyphen_evidence_min: int
    hyphen_without_evidence: str
    column_gap_ratio: float
    column_min_lines: int
    column_min_width_ratio: float
    image_min_page_share: float
    note_marker_rise_ratio: float

    @classmethod
    def from_config(cls, config: Config) -> PdfSettings:
        table = config["pdf"]
        check_methods(table["chapter_detection"])
        settings = cls(
            chapter_detection=tuple(table["chapter_detection"]),
            page_split_size=table["page_split_size"],
            heading_size_ratio=table["heading_size_ratio"],
            subheading_size_ratio=table["subheading_size_ratio"],
            min_chars_per_page=table["min_chars_per_page"],
            scanned_image_min_page_share=table["scanned_image_min_page_share"],
            running_line_min_pages=table["running_line_min_pages"],
            running_line_min_share=table["running_line_min_share"],
            running_line_max_lines=table["running_line_max_lines"],
            paragraph_gap_ratio=table["paragraph_gap_ratio"],
            paragraph_indent_ratio=table["paragraph_indent_ratio"],
            hyphen_evidence_min=table["hyphen_evidence_min"],
            hyphen_without_evidence=table["hyphen_without_evidence"],
            column_gap_ratio=table["column_gap_ratio"],
            column_min_lines=table["column_min_lines"],
            column_min_width_ratio=table["column_min_width_ratio"],
            image_min_page_share=table["image_min_page_share"],
            note_marker_rise_ratio=table["note_marker_rise_ratio"],
        )
        _require("page_split_size", settings.page_split_size >= 1, "at least 1")
        _require("heading_size_ratio", settings.heading_size_ratio > 1, "more than 1")
        _require("subheading_size_ratio", settings.subheading_size_ratio > 1, "more than 1")
        _require("min_chars_per_page", settings.min_chars_per_page >= 0, "0 or more")
        _require(
            "scanned_image_min_page_share",
            0 < settings.scanned_image_min_page_share <= 1,
            "between 0 and 1",
        )
        _require("running_line_min_pages", settings.running_line_min_pages >= 2, "at least 2")
        _require(
            "running_line_min_share", 0 < settings.running_line_min_share <= 1, "between 0 and 1"
        )
        _require("running_line_max_lines", settings.running_line_max_lines >= 1, "at least 1")
        _require("paragraph_gap_ratio", settings.paragraph_gap_ratio > 1, "more than 1")
        _require("paragraph_indent_ratio", settings.paragraph_indent_ratio > 0, "more than 0")
        _require("hyphen_evidence_min", settings.hyphen_evidence_min >= 1, "at least 1")
        _require(
            "hyphen_without_evidence",
            settings.hyphen_without_evidence in ("join", "keep"),
            '"join" or "keep"',
        )
        _require("column_gap_ratio", settings.column_gap_ratio > 0, "more than 0")
        _require("column_min_lines", settings.column_min_lines >= 1, "at least 1")
        _require("column_min_width_ratio", settings.column_min_width_ratio > 0, "more than 0")
        _require("image_min_page_share", 0 < settings.image_min_page_share <= 1, "between 0 and 1")
        _require("note_marker_rise_ratio", settings.note_marker_rise_ratio > 0, "more than 0")
        return settings


def _require(key: str, ok: bool, what: str) -> None:
    if not ok:
        raise ConfigError(f"Setting 'pdf.{key}' must be {what}.")


@dataclass(frozen=True)
class _Catalog:
    """What pypdf reads from the file, apart from the text."""

    metadata: Metadata
    outline: list[list[tuple[int, str]]]
    labels: list[str]


def extract(
    path: Path,
    config: Config,
    *,
    text_layer: Path | None = None,
    report: Report | None = None,
) -> Book:
    """The book in the PDF at `path`. With `text_layer`, its text is read from that PDF
    instead, page for page: the text OCR found in a scanned `path`. The title, bookmarks,
    and page numbers still come from `path`. With `report` and pdf.auto_ocr on, reads
    scanned pages with OCR automatically, reporting progress as it goes."""
    settings = PdfSettings.from_config(config)
    catalog = _read_catalog(path)
    read = ReadSettings(
        settings.column_gap_ratio, settings.image_min_page_share, settings.note_marker_rise_ratio
    )
    if text_layer is None:
        pages = _read_pages(path, path.name, catalog.labels, read)
    else:
        pages = _read_ocr_pages(text_layer, path.name, catalog.labels, read)
    if text_layer is None:
        try:
            _require_text_layer(path, pages, settings)
        except ScannedPdfError:
            if report is None or not config["pdf"]["auto_ocr"]:
                raise
            pages = _with_ocr(path, config, report, pages, read, settings)
    else:
        _require_ocr_text(path, pages, settings.min_chars_per_page)
    layout = _measure(pages, settings)
    pages = strip_furniture(
        pages,
        layout,
        is_label=is_section_label,
        min_pages=settings.running_line_min_pages,
        min_share=settings.running_line_min_share,
        max_lines=settings.running_line_max_lines,
        keep=lambda page: note_lines(page, layout),
    )
    # Notes are found before columns are ordered, while the foot of the page is still
    # the last lines read; markers are linked after, on the lines in reading order.
    pages = find_notes(pages, layout)
    pages = _order_columns(pages, settings)
    pages = link_note_refs(pages)
    pages = attach_captions(pages, layout)
    source = ChapterSource(pages, catalog.outline, layout, settings.page_split_size)
    found = detect_chapters(settings.chapter_detection, source)
    if found is None:
        log.warning("No chapters found in %s, so it will be one section", path.name)
        boundaries = [Boundary(0, catalog.metadata.title)]
    else:
        boundaries = found[1]
    boundaries = mark_back_matter(boundaries, pages)
    hyphens = HyphenEvidence.from_texts(
        (line.text for page in pages for line in _all_lines(page)),
        min_count=settings.hyphen_evidence_min,
        keep_by_default=settings.hyphen_without_evidence == "keep",
    )
    sections = build_sections(
        pages, boundaries, layout, catalog.metadata.language, keep_hyphen=hyphens.keep
    )
    log.info("Read %d pages and %d sections from %s", len(pages), len(sections), path.name)
    return Book(metadata=catalog.metadata, sections=sections, source_path=path, source_format="pdf")


def _with_ocr(
    path: Path,
    config: Config,
    report: Report,
    pages: list[Page],
    read: ReadSettings,
    settings: PdfSettings,
) -> list[Page]:
    """Read scanned pages only, preserving searchable pages and their layout."""
    scanned = _scanned_pages(pages, settings)
    # A few searchable pages among many scans can still fall below the book-wide
    # text threshold. Preserve those pages as well, however low the average is.
    indices = scanned if scanned and any(page.lines for page in pages) else None
    try:
        layer = text_layer(path, config, report, pages=indices)
    except ToolNotFoundError as e:
        raise ToolNotFoundError(
            f"This PDF is scanned, and reading it needs Tesseract OCR. {e.message}",
            hint=f"{e.hint} {AUTO_OCR_OFF}",
        ) from None
    originals = pages if indices is None else [pages[index] for index in indices]
    found = _read_ocr_pages(layer, path.name, [page.label for page in originals], read)
    if len(found) != len(originals):
        discard(layer)
        raise ExtractionError(
            "The text Dogear kept from OCR has the wrong number of pages.",
            hint="Run the same command again: Dogear reads the scanned pages again.",
        )
    if indices is None:
        _require_ocr_text(path, found, settings.min_chars_per_page)
        return found
    result = list(pages)
    for original, recognized in zip(originals, found, strict=True):
        # A full-page picture may be an illustration, not printed text. Keep its image
        # block when OCR finds nothing; never replace searchable text or other pages.
        if recognized.lines:
            result[original.index] = replace(recognized, index=original.index, label=original.label)
        else:
            report(
                f"OCR found no text on page {original.label} of {path.name}; "
                "Dogear keeps it as an image."
            )
    return result


def _scanned_pages(pages: list[Page], settings: PdfSettings) -> tuple[int, ...]:
    return tuple(
        page.index
        for page in pages
        if not page.lines and page.image_page_share >= settings.scanned_image_min_page_share
    )


def _all_lines(page: Page) -> list[Line]:
    """The page's lines, with those moved into figure captions and footnotes."""
    captions = [line for figure in page.figures for line in figure.caption]
    return [*page.lines, *captions, *(line for note in page.notes for line in note.lines)]


def _measure(pages: list[Page], settings: PdfSettings) -> Layout:
    return measure_layout(
        pages,
        heading_ratio=settings.heading_size_ratio,
        subheading_ratio=settings.subheading_size_ratio,
        gap_ratio=settings.paragraph_gap_ratio,
        indent_ratio=settings.paragraph_indent_ratio,
    )


def _order_columns(pages: list[Page], settings: PdfSettings) -> list[Page]:
    """Pages with their lines in reading order."""
    rules = ColumnRules(
        settings.column_gap_ratio, settings.column_min_lines, settings.column_min_width_ratio
    )
    ordered = [order_columns(page, rules) for page in pages]
    columned = sum(
        [line.text for line in before.lines] != [line.text for line in after.lines]
        for before, after in zip(pages, ordered, strict=True)
    )
    if columned:
        log.info("Read %d pages in columns", columned)
    return ordered


def _read_catalog(path: Path) -> _Catalog:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractionError(
                f"{path.name} is locked with a password.",
                hint="Open it with its password in a PDF reader, save a copy without the "
                "password, then open that copy.",
            )
        return _Catalog(
            metadata=_metadata(reader, path),
            outline=_outline(reader),
            labels=_labels(reader),
        )
    except PyPdfError as e:
        raise ExtractionError(
            f"Can't read {path.name}: it is damaged or not a PDF ({e}).", hint=_DAMAGED_HINT
        ) from e
    except OSError as e:
        raise ExtractionError(f"Can't open {path}: {e.strerror or e}.") from e


def _metadata(reader: PdfReader, path: Path) -> Metadata:
    info = reader.metadata
    title = " ".join(str(info.title or "").split()) if info else ""
    author = str(info.author or "") if info else ""
    return Metadata(
        title=title or path.stem,
        authors=[name.strip() for name in author.split(";") if name.strip()],
        language=_language(reader),
    )


def _language(reader: PdfReader) -> str | None:
    lang = reader.root_object.get("/Lang")
    if lang is None:
        return None
    tag = str(_resolve(lang)).strip()
    if _LANGUAGE_TAG.fullmatch(tag):
        return tag
    log.warning("Ignoring the PDF's language %r: it isn't a language tag such as 'en-GB'", tag)
    return None


def _outline(reader: PdfReader) -> list[list[tuple[int, str]]]:
    """Bookmarks that point at a page, grouped by depth: [top level, children, ...].

    pypdf lists an entry's children as a nested list right after the entry.
    """
    try:
        outline = reader.outline
    except PyPdfError as e:
        log.warning("Ignoring the PDF's bookmarks: they are damaged (%s)", e)
        return []
    levels: list[list[tuple[int, str]]] = []
    siblings: list[list[Any]] = [outline]
    while siblings:
        entries: list[tuple[int, str]] = []
        children: list[list[Any]] = []
        for group in siblings:
            for item in group:
                if isinstance(item, list):
                    children.append(item)
                elif isinstance(item, Destination):
                    page = reader.get_destination_page_number(item)
                    title = " ".join(str(item.title or "").split())
                    if page is not None and page >= 0 and title:
                        entries.append((page, title))
        levels.append(entries)
        siblings = children
    return levels


def _labels(reader: PdfReader) -> list[str]:
    """Printed page numbers from the PDF's page labels, or 1, 2, 3... if it has none."""
    count = len(reader.pages)
    if "/PageLabels" not in reader.root_object:
        return [str(i + 1) for i in range(count)]
    try:
        labels = list(reader.page_labels)
    except PyPdfError as e:
        log.warning("Ignoring the PDF's page labels: they are damaged (%s)", e)
        return [str(i + 1) for i in range(count)]
    # A label range may print nothing; a page marker still needs a name.
    return [label.strip() or str(i + 1) for i, label in enumerate(labels)]


def _resolve(value: Any) -> Any:
    return value.get_object() if hasattr(value, "get_object") else value


def _read_pages(path: Path, name: str, labels: list[str], settings: ReadSettings) -> list[Page]:
    """The pages of the PDF at `path`, the book `name`."""
    try:
        return _pages(path, name, labels, settings)
    except (PdfminerException, PSException) as e:
        raise ExtractionError(
            f"Can't read the text in {name}: it is damaged ({e}).", hint=_DAMAGED_HINT
        ) from e


def _read_ocr_pages(
    layer: Path, name: str, labels: list[str], settings: ReadSettings
) -> list[Page]:
    """The pages of `layer`, the text OCR found in the book `name`. One that can't be read
    was damaged in Dogear's cache folder, not in the book: it is deleted, so the next run
    reads the book with OCR again."""
    try:
        return _pages(layer, name, labels, settings)
    except (PdfminerException, PSException) as e:
        again = (
            "Run the same command again: Dogear reads the PDF with OCR again."
            if discard(layer)
            else "Run the same command again once the other Dogear reading it has finished."
        )
        raise ExtractionError(
            f"The text Dogear read from {name} with OCR was damaged in its cache folder ({e}).",
            hint=f"The damage is in Dogear's own copy of the text, not in {name}. {again}",
        ) from e


def _pages(path: Path, name: str, labels: list[str], settings: ReadSettings) -> list[Page]:
    """The pages of the PDF at `path`, which is the book `name` or its text layer. Raises
    pdfminer's errors for a damaged file."""
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) != len(labels):
            log.warning(
                "pypdf and pdfplumber disagree on the page count of %s (%d vs %d); "
                "page labels may be off",
                name,
                len(labels),
                len(pdf.pages),
            )
        pages = []
        for index, plumber_page in enumerate(pdf.pages):
            label = labels[index] if index < len(labels) else str(index + 1)
            pages.append(read_page(plumber_page, index, label, settings))
            plumber_page.close()  # frees the parsed page; big books stay small in memory
        return pages


def _chars_per_page(path: Path, pages: list[Page]) -> float:
    if not pages:
        raise ExtractionError(f"{path.name} has no pages.", hint=_DAMAGED_HINT)
    return sum(page.char_count for page in pages) / len(pages)


def _require_ocr_text(path: Path, pages: list[Page], min_chars: float) -> None:
    average = _chars_per_page(path, pages)
    if average < min_chars or average == 0:
        raise ExtractionError(
            "Dogear read this scanned PDF with OCR, but found almost no text (about "
            f"{average:.0f} characters a page).",
            hint="Check that its pages are clear pictures of printed text, the right way up.",
        )


def _require_text_layer(path: Path, pages: list[Page], settings: PdfSettings) -> None:
    average = _chars_per_page(path, pages)
    if average < settings.min_chars_per_page or average == 0:
        raise ScannedPdfError(
            f"{path.name} has almost no text (about {average:.0f} characters a page), so it "
            "is probably scanned page images.",
            hint=OCR_HINT,
        )
    scanned = _scanned_pages(pages, settings)
    if scanned:
        raise ScannedPdfError(
            f"{path.name} has {len(scanned):,} pages with page-sized pictures and no text, "
            "so those pages need OCR first.",
            hint=OCR_HINT,
        )
    pictures = [page.label for page in pages if page.has_images and not page.lines]
    if pictures:
        log.warning(
            "Pages %s of %s are pictures with no text. Any words on them will be missing; "
            "if they are scanned pages, run `dogear ocr` on the file first.",
            ", ".join(pictures),
            path.name,
        )
