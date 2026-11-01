"""A section's text document in a DAISY 2.02 book: XHTML 1.0 a player shows and highlights.

The text is the read-along model's, element for element, with the ids its syncs point at:
headings at their book-wide levels, each sentence a span, real lists and tables, print
page numbers as spans of class page-normal, page-front, or page-special, and image
descriptions and notes as the visible text the voice reads.

The text doesn't link back to the SMIL files. The spec doesn't ask for it, and reading
apps such as Thorium Reader find the sentence to highlight by its id. A link around every
sentence would also make a screen reader say "link" before each one, and in a web
browser it would lead to a SMIL file the browser can't show.
"""

from __future__ import annotations

from lxml import etree

from dogear.output import daisy202_xhtml as x
from dogear.output.daisy202_plan import BookPlan, SectionPlan
from dogear.output.xml_text import xml_safe
from dogear.readalong.model import (
    BreakText,
    HeadingText,
    ImageText,
    ListText,
    NoteText,
    PageText,
    ParagraphText,
    RowText,
    Sentence,
    TableText,
    TextItem,
)

PAGE_CLASSES = {"front": "page-front", "normal": "page-normal", "special": "page-special"}


def text_document(book: BookPlan, plan: SectionPlan) -> etree._Element:
    section = plan.section
    html, head, body = x.document(section.title, plan.language)
    x.meta(head, "dc:title", book.title)
    x.meta(head, "dc:identifier", book.identifier)
    x.meta(head, "ncc:generator", book.generator)
    for item in section.items:
        _item(body, item, section.number)
    return html


def _item(body: etree._Element, item: TextItem, number: int) -> None:
    match item:
        case HeadingText(id=id_, level=level, text=words, lang=lang):
            heading = x.element(f"h{level}", body, id=id_)
            heading.text = xml_safe(words)
            _lang(heading, lang, number)
        case ParagraphText(sentences=sentences, lang=lang):
            _lang(_sentences(x.element("p", body), sentences, number), lang, number)
        case ListText(items=entries, ordered=ordered, lang=lang):
            listing = x.element("ol" if ordered else "ul", body)
            for entry in entries:
                _sentences(x.element("li", listing), entry, number)
            _lang(listing, lang, number)
        case TableText():
            _table(body, item, number)
        case ImageText(description=description, caption=caption, lang=lang):
            if description or caption:
                figure = x.element("div", body, **{"class": "image"})
                if description:
                    _sentences(x.element("p", figure), description, number)
                if caption:
                    _sentences(x.element("p", figure, **{"class": "caption"}), caption, number)
                _lang(figure, lang, number)
        case NoteText(label=label, sentences=sentences, lang=lang):
            note = x.element("div", body, **{"class": "note"})
            paragraph = x.element("p", note)
            if label.strip():
                marker = x.element("span", paragraph, **{"class": "label"})
                marker.text = xml_safe(label.strip())
                marker.tail = " "
            _sentences(paragraph, sentences, number)
            _lang(note, lang, number)
        case PageText(id=id_, label=label, kind=kind):
            page = x.element("span", body, id=id_, **{"class": PAGE_CLASSES[kind]})
            page.text = xml_safe(label)
        case BreakText():
            x.element("hr", body, **{"class": "scene-break"})
        case _:
            raise TypeError(f"not a text item: {item!r}")


def _sentences(
    parent: etree._Element, sentences: tuple[Sentence, ...], number: int
) -> etree._Element:
    """Each sentence as a span with its id, a space between them."""
    last = None
    for sentence in sentences:
        if last is not None:
            last.tail = " "
        last = x.element("span", parent, id=sentence.id)
        last.text = xml_safe(sentence.text)
        if sentence.lang:
            _lang(last, sentence.lang, number)
    return parent


def _table(body: etree._Element, table: TableText, number: int) -> None:
    el = x.element("table", body)
    if table.caption:
        _sentences(x.element("caption", el), table.caption, number)
    if table.rows:
        if table.header_rows:
            _rows(x.element("thead", el), table.header_rows, header=True)
        _rows(x.element("tbody", el), table.rows, header=False)
    else:  # header rows alone: XHTML wants a body, so they become it
        _rows(x.element("tbody", el), table.header_rows, header=True)
    _lang(el, table.lang, number)


def _rows(group: etree._Element, rows: tuple[RowText, ...], header: bool) -> None:
    for row in rows:
        tr = x.element("tr", group, **({"id": row.id} if row.id else {}))
        for value in row.cells:
            cell = x.element("th", tr, scope="col") if header else x.element("td", tr)
            cell.text = xml_safe(value)


def _lang(el: etree._Element, lang: str | None, number: int) -> None:
    tag = x.language_tag(lang, f"some text in section {number}")
    if tag:
        x.set_lang(el, tag)
