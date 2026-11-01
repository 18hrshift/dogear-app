"""Read one HTML file (.html, .htm, or .xhtml) into a `dogear.document.Book`.

.html and .htm files are parsed as HTML (see `html_parse`); .xhtml files are XML and go
through `safe_xml`, as an EPUB's documents do. Nothing the file links to is fetched:
an image is its alt text. The <body> becomes blocks as in an EPUB (`xhtml_blocks`), so
notes, page breaks, language tags, and DPUB-ARIA roles work the same way.

Sections. An HTML file has no table of contents, so it is cut before each heading at
its top heading level (`stretches.section_level`). Only headings that sit in the body
itself or in sectioning elements count: not those in lists, tables, figures, asides,
block quotes, or notes, nor the title in the page's own <header>. A heading that opens
its element (the first thing in a <section>) cuts before that element instead, so the
section's own markup, such as role="doc-appendix", goes with it. What comes before the
first cut is front matter.
A stretch the file marks as front, body, or back matter (epub:type or a doc-* role, as
`epub_kind` reads them) is that kind. A file with no headings is one section.

Metadata. The title is the <title>, else the one level-1 heading when there is just one,
else the file name. Authors come from <meta name="author"> (and "dc.creator",
"dcterms.creator", "citation_author"); the language from <html lang>, else a language
<meta>.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from dogear.config import Config
from dogear.document import Book, Metadata
from dogear.errors import ExtractionError
from dogear.extract.epub_kind import marked_kind
from dogear.extract.html_parse import HTML_HINT, decode_html, parse_html
from dogear.extract.limited_read import read_limited
from dogear.extract.note_links import link_notes
from dogear.extract.safe_xml import parse_xml
from dogear.extract.stretches import Stretch, section_level, sections_from
from dogear.extract.xhtml import collapse, lang_of, local_name, plain_text, semantics
from dogear.extract.xhtml_blocks import NOTE_TYPES, body_blocks, heading_level
from dogear.extract.xhtml_split import split_body

XHTML_HINT = (
    "An .xhtml file must be well-formed XML. If it's ordinary HTML, rename it to end in "
    ".html, which Dogear reads more leniently."
)
# Where a heading can start a section: in the body, or in these elements.
SECTIONING = frozenset({"body", "div", "section", "article", "main", "header", "hgroup", "center"})
AUTHOR_META = frozenset({"author", "dc.creator", "dcterms.creator", "citation_author"})
LANGUAGE_META = frozenset({"language", "dc.language", "dcterms.language"})
# Elements that are content with no text in them.
TEXTLESS_CONTENT = frozenset({"img", "svg", "hr"})


def extract(path: Path, config: Config) -> Book:
    """Read the HTML file at `path`."""
    data = read_limited(path, config, "html", "HTML")
    if path.suffix.lower() == ".xhtml":
        root = parse_xml(data, path.name, XHTML_HINT)
    else:
        root = parse_html(decode_html(data, path.name), path.name)
    return read_html(root, path, source_format=path.suffix.lower().lstrip("."))


def read_html(root: etree._Element, path: Path, *, source_format: str) -> Book:
    """A Book from `root`, the parsed document read from `path`."""
    body = _child(root, "body")
    if body is None:
        raise ExtractionError(f"{path.name} has no <body>, so it has no text.", hint=HTML_HINT)
    metadata = _metadata(root, body, fallback_title=path.stem)
    lang = lang_of(body, metadata.language)
    note_markers: dict[str, str] = {}
    starts = _section_starts(body)
    stretches = []
    for number, part in enumerate(split_body(body, starts)):
        blocks = body_blocks(
            part, doc_path=path.name, section_lang=lang, page_labels={}, note_markers=note_markers
        )
        default = "front" if starts and number == 0 else "body"
        source = _source(path.name, starts[number - 1] if number else None)
        stretches.append(Stretch(blocks, marked_kind(part, frozenset()) or default, source))
    sections = sections_from(stretches, lang=lang, lone_title=metadata.title)
    if not sections:
        raise ExtractionError(
            f"{path.name} has no readable text.",
            hint="If it's a page of pictures, it has no text for Dogear to read aloud.",
        )
    link_notes(sections, note_markers)
    return Book(metadata=metadata, sections=sections, source_path=path, source_format=source_format)


# Sections


def _section_starts(body: etree._Element) -> list[etree._Element]:
    """Where sections begin, in document order: see the module's docstring."""
    headings = [el for el in _outline_headings(body) if not _in_page_header(el, body)]
    level = section_level(heading_level(h) for h in headings)
    starts = [_opened(h, body) for h in headings if heading_level(h) == level]
    return list(dict.fromkeys(starts))  # distinct, in order


def _outline_headings(body: etree._Element) -> list[etree._Element]:
    """The headings with text in the body itself or in sectioning elements, in order."""
    return [el for el in body.iter(etree.Element) if el is not body and _in_outline(el, body)]


def _in_outline(el: etree._Element, body: etree._Element) -> bool:
    if not heading_level(el) or not plain_text(el):
        return False
    for ancestor in el.iterancestors():
        if local_name(ancestor) not in SECTIONING or semantics(ancestor) & NOTE_TYPES:
            return False
        if ancestor is body:
            return True
    return False


def _in_page_header(el: etree._Element, body: etree._Element) -> bool:
    """Whether `el` is in the page's own <header>, one directly in the body. Its heading
    titles the page, so it stays in the front matter instead of starting a section."""
    return any(local_name(a) == "header" and a.getparent() is body for a in el.iterancestors())


def _opened(heading: etree._Element, body: etree._Element) -> etree._Element:
    """The outermost element `heading` opens (or the heading itself), below the body."""
    el = heading
    while (parent := el.getparent()) is not None and parent is not body and _opens(el, parent):
        el = parent
    return el


def _opens(child: etree._Element, parent: etree._Element) -> bool:
    """Whether nothing a listener would hear comes before `child` in `parent`."""
    if (parent.text or "").strip():
        return False
    for sibling in child.itersiblings(preceding=True):
        if (sibling.tail or "").strip() or not _silent(sibling):
            return False
    return True


def _silent(el: etree._Element) -> bool:
    if not isinstance(el.tag, str):
        return True
    if plain_text(el):
        return False
    return not any(local_name(d) in TEXTLESS_CONTENT for d in el.iter(etree.Element))


def _source(name: str, start: etree._Element | None) -> str:
    if start is None:
        return name
    element_id = (start.get("id") or "").strip()
    if not element_id:
        element_id = next((el.get("id") for el in start.iter(etree.Element) if el.get("id")), "")
    return f"{name}#{element_id}" if element_id else name


# Metadata


def _metadata(root: etree._Element, body: etree._Element, *, fallback_title: str) -> Metadata:
    head = _child(root, "head")
    metas = [] if head is None else [m for m in head.iter(etree.Element) if local_name(m) == "meta"]
    title_el = (
        None
        if head is None
        else next((el for el in head.iter(etree.Element) if local_name(el) == "title"), None)
    )
    title = plain_text(title_el) if title_el is not None else ""
    authors = list(dict.fromkeys(_named(metas, AUTHOR_META)))  # each once, in order
    language = lang_of(root, None) or next(iter(_named(metas, LANGUAGE_META)), None)
    if language is None:
        language = _content_language(metas)
    return Metadata(
        title=title or _lone_top_heading(body) or fallback_title,
        authors=authors,
        language=language,
    )


def _named(metas: list[etree._Element], names: frozenset[str]) -> list[str]:
    """The content of each <meta> whose name is one of `names`, in document order."""
    found = []
    for meta in metas:
        if (meta.get("name") or "").strip().lower() in names:
            content = collapse(meta.get("content") or "")
            if content:
                found.append(content)
    return found


def _content_language(metas: list[etree._Element]) -> str | None:
    for meta in metas:
        if (meta.get("http-equiv") or "").strip().lower() == "content-language":
            first = (meta.get("content") or "").split(",")[0].strip()
            if first:
                return first
    return None


def _lone_top_heading(body: etree._Element) -> str:
    """The text of the document's level-1 heading, when it has exactly one."""
    tops = [el for el in _outline_headings(body) if heading_level(el) == 1]
    return plain_text(tops[0]) if len(tops) == 1 else ""


def _child(el: etree._Element, name: str) -> etree._Element | None:
    return next((c for c in el if isinstance(c.tag, str) and local_name(c) == name), None)
