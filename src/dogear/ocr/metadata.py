"""Keep a book's passive metadata when OCR replaces its pages.

Copy the information dictionary as text, the language, printed page labels, and internal
bookmarks. Rebuild labels and bookmarks against the new pages instead of cloning the
original catalog, which may contain scripts, attachments, or external actions.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from pypdf.generic import (
    ArrayObject,
    Destination,
    DictionaryObject,
    Fit,
    NameObject,
    NumberObject,
    TextStringObject,
)

log = logging.getLogger(__name__)
_DAMAGED = (PyPdfError, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError)


def copy_book_metadata(source: Path, writer: PdfWriter) -> None:
    """Keep each usable part of the original book's metadata, reporting damaged parts
    individually so a broken label tree doesn't also lose the language or bookmarks."""
    try:
        reader = PdfReader(source)
        if reader.is_encrypted:
            reader.decrypt("")
    except _DAMAGED as error:
        log.warning("Couldn't keep book metadata from %s: %s", source.name, error)
        return
    for part, copy in (
        ("title and author", _copy_info),
        ("language", _copy_language),
        ("page labels", _copy_labels),
        ("bookmarks", _copy_bookmarks),
    ):
        try:
            copy(reader, writer)
        except _DAMAGED as error:
            log.warning("Couldn't keep %s from %s: %s", part, source.name, error)


def _copy_info(reader: PdfReader, writer: PdfWriter) -> None:
    metadata = reader.metadata
    if metadata:
        writer.add_metadata({key: str(value) for key, value in metadata.items()})


def _copy_language(reader: PdfReader, writer: PdfWriter) -> None:
    if "/Lang" in reader.root_object:
        language = reader.root_object["/Lang"]
        if not isinstance(language, str):
            raise ValueError("the PDF's language is not text")
        writer.root_object[NameObject("/Lang")] = TextStringObject(language)


def _copy_labels(reader: PdfReader, writer: PdfWriter) -> None:
    if "/PageLabels" not in reader.root_object:
        return
    labels = reader.page_labels
    if len(labels) != len(writer.pages):
        raise ValueError("the original and the OCR copy have different page counts")
    # Each label is a literal prefix, without automatic numbering. A flat number tree
    # keeps even custom or empty labels exactly, and is built in one pass.
    numbers = ArrayObject()
    for index, label in enumerate(labels):
        numbers.extend(
            [NumberObject(index), DictionaryObject({NameObject("/P"): TextStringObject(label)})]
        )
    writer.root_object[NameObject("/PageLabels")] = DictionaryObject({NameObject("/Nums"): numbers})


def _copy_bookmarks(reader: PdfReader, writer: PdfWriter) -> None:
    # pypdf bounds outline depth and entries while reading. Walk its result without
    # adding Python recursion, retaining the parent before each nested list.
    pending = [(reader.outline, None)]
    while pending:
        entries, parent = pending.pop()
        previous = parent
        for entry in entries:
            if isinstance(entry, list):
                pending.append((entry, previous))
            elif isinstance(entry, Destination):
                page = reader.get_destination_page_number(entry)
                if page is None or not 0 <= page < len(writer.pages):
                    log.warning("Couldn't keep a bookmark: its destination is not a book page")
                    previous = parent
                    continue
                previous = writer.add_outline_item(
                    str(entry.title),
                    page,
                    parent=parent,
                    fit=Fit(str(entry.typ), entry.dest_array[2:]),
                )
