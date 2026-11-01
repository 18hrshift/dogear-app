"""Read a Word document (.docx) into a `dogear.document.Book`.

The archive is read in memory (`docx_package`), with every part parsed by `safe_xml`,
so a document can't make Dogear touch the network or other files, and no part is read
past `docx.max_member_mb`. The body becomes blocks (`docx_blocks`), which are cut into
sections at the top heading level (`stretches.section_level`); what comes before the
first cut is front matter. Endnotes, and any notes the text never refers to, end the
last section.

Metadata comes from the document's properties (docProps/core.xml): the title, else the
first paragraph in the Title style, else the file name; the authors (dc:creator, split
at semicolons); and the language, else the document's default (w:lang in its styles).
Headers, footers, and comments are not part of the book's text, so they aren't read.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from dogear.config import Config
from dogear.document import Book, Metadata
from dogear.errors import ConfigError, ExtractionError
from dogear.extract.docx_blocks import BlockReader
from dogear.extract.docx_notes import NoteCounter, number_formats, read_note_part
from dogear.extract.docx_numbering import Numbering
from dogear.extract.docx_package import DocxArchive, check_document, find_parts
from dogear.extract.docx_styles import NO_LANGUAGE, Styles
from dogear.extract.note_links import link_notes
from dogear.extract.stretches import Stretch, section_level, sections_from
from dogear.extract.xhtml import plain_text

DC = "{http://purl.org/dc/elements/1.1/}"


def extract(path: Path, config: Config) -> Book:
    """Read the Word document at `path`."""
    with DocxArchive(path, max_member_bytes=_max_member_bytes(config)) as archive:
        parts = find_parts(archive)

        def parse(part: str | None) -> etree._Element | None:
            return archive.parse(part) if part is not None else None

        body = check_document(archive.parse(parts.document), archive, parts.document)
        styles = Styles(parse(parts.styles))
        numbering = Numbering(parse(parts.numbering), styles)
        notes = {
            kind: read_note_part(parse(part), kind, part or f"{kind}s")
            for kind, part in (("footnote", parts.footnotes), ("endnote", parts.endnotes))
        }
        formats = number_formats(parse(parts.settings), body)
        core = parse(parts.core)
    title, authors, language = _properties(core)
    language = language or styles.default_lang
    reader = BlockReader(styles, numbering, notes, NoteCounter(formats), language)
    content = reader.body(body)
    blocks = content.blocks + reader.endnotes + reader.unreferenced_notes()
    title = title or reader.title or path.stem
    metadata = Metadata(title=title, authors=authors, language=language)
    level = section_level(content.blocks[i].level for i in content.outline)
    cuts = [i for i in content.outline if content.blocks[i].level == level]
    stretches = [
        Stretch(blocks[start:end], "front" if cuts and n == 0 else "body", parts.document)
        for n, (start, end) in enumerate(zip([0, *cuts], [*cuts, len(blocks)], strict=True))
    ]
    sections = sections_from(stretches, lang=language, lone_title=metadata.title)
    if not sections:
        raise ExtractionError(
            f"{path.name} has no readable text.",
            hint="If it holds only pictures or scanned pages, it has no text to read aloud.",
        )
    link_notes(sections, {})
    return Book(metadata=metadata, sections=sections, source_path=path, source_format="docx")


def _properties(core: etree._Element | None) -> tuple[str, list[str], str | None]:
    """(title, authors, language) from docProps/core.xml; empty where it says nothing."""
    if core is None:
        return "", [], None

    def first(name: str) -> str:
        el = core.find(f"{DC}{name}")
        return plain_text(el) if el is not None else ""

    authors = [name.strip() for name in first("creator").split(";")]
    language = first("language")
    if language.lower() == NO_LANGUAGE:
        language = ""
    return first("title"), list(dict.fromkeys(a for a in authors if a)), language or None


def _max_member_bytes(config: Config) -> int:
    limit_mb = config["docx"]["max_member_mb"]
    if limit_mb <= 0:
        raise ConfigError(f"docx.max_member_mb must be more than 0, got {limit_mb}.")
    return limit_mb * 1024 * 1024
