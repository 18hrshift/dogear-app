"""One section's text as an XHTML content document, with every spoken piece in an
element carrying the id its sync points at.

Choices made for listeners and reading apps:

- Each sentence is its own span, so an app can highlight the one being read.
- Notes are plain blocks marked role="note". Some apps hide an aside marked as a
  footnote until a link to it is followed, and nothing links to these: they are read
  where they stand, so they are shown there too.
- Dogear keeps no picture data, so an image is its description and caption, shown as
  text in a figure.
- A page break shows the print page number and is labelled as a page break, so a
  reader can follow a print copy and an announced number is highlighted as it is said.
- A table's header cells are column headers. A header row with no rows under it is
  shown as an ordinary row, since a header needs cells to head.
"""

from __future__ import annotations

from lxml import etree

from dogear.output.epub3_xhtml import EPUB_TYPE, add, new_document, set_lang
from dogear.output.language_tag import language_tag
from dogear.readalong.model import (
    BreakText,
    HeadingText,
    ImageText,
    ListText,
    NoteText,
    PageText,
    ParagraphText,
    SectionText,
    Sentence,
    TableText,
    TextItem,
)

_BODY_TYPES = {"front": "frontmatter", "body": "bodymatter", "back": "backmatter"}


def content_document(section: SectionText, lang: str) -> etree._Element:
    """The section's text as an XHTML document in `lang`, the section's checked language."""
    html, body = new_document(section.title, lang)
    body.set(EPUB_TYPE, _BODY_TYPES[section.kind])
    parent = body
    if section.number == 0:  # the title track opens the book as its title page
        parent = add(body, "section", {EPUB_TYPE: "titlepage", "class": "titlepage"})
    for item in section.items:
        _item(parent, item)
    return html


def _item(parent: etree._Element, item: TextItem) -> None:
    match item:
        case HeadingText(id=id_, level=level, text=text, lang=lang):
            if not 1 <= level <= 6:
                raise ValueError(f"Heading {id_} has level {level}; HTML has levels 1 to 6.")
            _lang(add(parent, f"h{level}", {"id": id_}, text), lang)
        case ParagraphText(sentences=sentences, lang=lang):
            _sentences(_lang(add(parent, "p"), lang), sentences)
        case ListText(items=items, ordered=ordered, lang=lang):
            listing = _lang(add(parent, "ol" if ordered else "ul"), lang)
            for sentences in items:
                _sentences(add(listing, "li"), sentences)
        case TableText():
            _table(parent, item)
        case ImageText(description=description, caption=caption, lang=lang):
            figure = _lang(add(parent, "figure", {"class": "image"}), lang)
            if description:
                _sentences(add(figure, "p"), description)
            if caption:
                _sentences(add(figure, "figcaption"), caption)
        case NoteText(label=label, sentences=sentences, lang=lang):
            note = _lang(add(parent, "div", {"class": "note", "role": "note"}), lang)
            paragraph = add(note, "p")
            if label:
                add(paragraph, "span", {"class": "note-label"}, label).tail = " "
            _sentences(paragraph, sentences)
        case PageText(id=id_, label=label):
            attrs = {EPUB_TYPE: "pagebreak", "role": "doc-pagebreak", "id": id_}
            add(parent, "span", {**attrs, "aria-label": label, "class": "pagebreak"}, label)
        case BreakText():
            add(parent, "hr", {"class": "scene-break"})
        case _:
            raise TypeError(f"not a text item: {item!r}")


def _table(parent: etree._Element, table: TableText) -> None:
    element = _lang(add(parent, "table"), table.lang)
    if table.caption:
        _sentences(add(element, "caption"), table.caption)
    rows = table.rows
    if table.header_rows and table.rows:
        head = add(element, "thead")
        for row in table.header_rows:
            tr = add(head, "tr", {"id": row.id} if row.id else None)
            for cell in row.cells:
                if cell.strip():
                    add(tr, "th", {"scope": "col"}, cell)
                else:  # an empty header cell heads nothing, so it is an ordinary cell
                    add(tr, "td")
    else:
        rows = table.header_rows + table.rows
    body = add(element, "tbody")
    for row in rows:
        tr = add(body, "tr", {"id": row.id} if row.id else None)
        for cell in row.cells:
            add(tr, "td", text=cell)


def _sentences(parent: etree._Element, sentences: tuple[Sentence, ...]) -> None:
    for n, sentence in enumerate(sentences):
        span = _lang(add(parent, "span", {"id": sentence.id}, sentence.text), sentence.lang)
        if n < len(sentences) - 1:
            span.tail = " "


def _lang(element: etree._Element, lang: str | None) -> etree._Element:
    """Mark `element` with a language that differs from its surroundings, if it's usable."""
    set_lang(element, language_tag(lang))
    return element
