"""Turn the body of a Word document into document-model blocks.

- A paragraph is a Heading when its style chain or its own properties give it an
  outline level (see `docx_styles`); levels 7 to 9 are read as level 6. A paragraph in
  the Title style is a level-1 heading too, but it doesn't start a section.
- A paragraph with list numbering (w:numPr) is a list item. Consecutive items of one
  list (one w:numId) make one ListBlock, nested levels included, numbered or bulleted
  as its first item is. A level whose format is "none" shows no number or bullet, so
  its paragraphs are ordinary paragraphs.
- A table is a Table; rows marked "Repeat as header row" (w:tblHeader) at its top are
  its header rows. What a cell holds is read as its text, a line per paragraph.
- Images, text boxes, and footnotes come right after the block they sit in, so they
  never split a sentence. Endnotes are kept for the end of the book, where Word prints
  them. Only paragraphs carry note references (as in EPUB), so a reference in a
  heading, list item, or table cell loses its link; its note is still read.
- A table of contents (a content control Word marks as one, or paragraphs in the "toc"
  styles) is skipped: the sections are its entries, and its page numbers aren't book
  text.
- Page breaks aren't print pages, so no page markers come out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

from lxml import etree

from dogear.document import Block, Footnote, Heading, Image, ListBlock, Paragraph, Table, block_text
from dogear.extract.docx_notes import NoteCounter, NotePart
from dogear.extract.docx_numbering import Numbering
from dogear.extract.docx_package import MC, W
from dogear.extract.docx_runs import Inline, InlineReader, NoteRef, alternative, showing_placeholder
from dogear.extract.docx_styles import (
    Styles,
    heading_for_outline,
    on,
    outline_level,
    paragraph_numbering,
)

log = logging.getLogger(__name__)

MAX_HEADING_LEVEL = 6  # the document model's deepest; Word has nine
# Elements that only wrap block content (tracked insertions, custom XML), read through.
WRAPPERS = frozenset({f"{W}customXml", f"{W}ins", f"{W}moveTo"})
TEXT_BLOCKS = (Heading, Paragraph, ListBlock, Table)
CONTENTS_GALLERY = "table of contents"


@dataclass
class Body:
    blocks: list[Block]
    outline: list[int]  # indices in `blocks` of the headings that can start sections


@dataclass(frozen=True)
class _Listed:
    """What a list paragraph's numbering says."""

    list_id: str  # w:numId
    level: int  # w:ilvl, 0 for the top level
    ordered: bool


@dataclass
class _Out:
    """Blocks being written, with the list being gathered, if any."""

    blocks: list[Block] = field(default_factory=list)
    outline: list[int] = field(default_factory=list)
    list_id: str | None = None
    ordered: bool = False
    items: list[str] = field(default_factory=list)
    list_lang: str | None = None
    after_list: list[Block] = field(default_factory=list)

    def add(self, *blocks: Block, outline: bool = False) -> None:
        self.flush()
        if outline:
            self.outline.append(len(self.blocks))
        self.blocks.extend(blocks)

    def item(self, listed: _Listed, text: str, lang: str | None, after: list[Block]) -> None:
        # A deeper level continues the list it is in, whichever numbering it uses.
        if listed.list_id != self.list_id and not (self.list_id and listed.level > 0):
            self.flush()
            self.list_id, self.ordered, self.list_lang = listed.list_id, listed.ordered, lang
        if text:
            self.items.append(text)
        self.after_list += after

    def flush(self) -> None:
        if self.items:
            listing = ListBlock(tuple(self.items), ordered=self.ordered, lang=self.list_lang)
            self.blocks.append(listing)
        self.blocks.extend(self.after_list)
        self.list_id, self.items, self.after_list = None, [], []


class BlockReader:
    """Reads blocks from the body, table cells, text boxes, and notes of one document."""

    def __init__(
        self,
        styles: Styles,
        numbering: Numbering,
        notes: dict[str, NotePart],
        counter: NoteCounter,
        section_lang: str | None,
    ) -> None:
        self.styles = styles
        self.numbering = numbering
        self.notes = notes
        self.counter = counter
        self.section_lang = section_lang
        self.inline = InlineReader(styles)
        self.title = ""  # the first paragraph in the Title style
        self.endnotes: list[Footnote] = []
        self._placed: set[str] = set()  # ids of the notes that have a block
        self._in_note = False
        self._skipped_contents = False

    def body(self, body: etree._Element) -> Body:
        out = _Out()
        self._container(body, out, top=True)
        out.flush()
        return Body(out.blocks, out.outline)

    def blocks(self, container: etree._Element) -> list[Block]:
        """The blocks in a table cell, text box, or note."""
        out = _Out()
        self._container(container, out, top=False)
        out.flush()
        return out.blocks

    def unreferenced_notes(self) -> list[Footnote]:
        """Notes the text never refers to, which would otherwise never be read."""
        found = []
        for part in self.notes.values():
            for ref_id in part.notes:
                note = self._note(NoteRef(part.kind, ref_id, ""))
                if note is not None:
                    found.append(note)
        if found:
            log.info("%d notes aren't referred to in the text; they come last.", len(found))
        return found

    # Block level

    def _container(self, el: etree._Element, out: _Out, *, top: bool) -> None:
        for child in el:
            tag = child.tag
            if tag == f"{W}p":
                self._paragraph(child, out, top=top)
            elif tag == f"{W}tbl":
                out.add(*self._table(child))
            elif tag == f"{W}sdt":
                self._content_control(child, out, top=top)
            elif tag in WRAPPERS:
                self._container(child, out, top=top)
            elif tag == f"{MC}AlternateContent":
                chosen = alternative(child)
                if chosen is not None:
                    self._container(chosen, out, top=top)
            elif tag == f"{W}altChunk":
                log.warning(
                    "Skipped part of the document that was pasted in from another file "
                    "(an altChunk): Dogear can't read it. Save the document again in Word "
                    "to make it ordinary text."
                )
            # Not read: section properties, bookmarks, and deleted content.

    def _content_control(self, sdt: etree._Element, out: _Out, *, top: bool) -> None:
        gallery = sdt.find(f"{W}sdtPr/{W}docPartObj/{W}docPartGallery")
        if gallery is not None and (gallery.get(f"{W}val") or "").lower() == CONTENTS_GALLERY:
            self._note_contents_skipped()
            return
        content = sdt.find(f"{W}sdtContent")
        if content is not None and not showing_placeholder(sdt):
            self._container(content, out, top=top)

    def _paragraph(self, p: etree._Element, out: _Out, *, top: bool) -> None:
        ppr = p.find(f"{W}pPr")
        style_el = ppr.find(f"{W}pStyle") if ppr is not None else None
        style = style_el.get(f"{W}val") if style_el is not None else self.styles.default_paragraph
        if self.styles.is_contents(style):
            self._note_contents_skipped()
            return
        style_lang = self.styles.lang(style) or self.section_lang
        inline = self.inline.read(p, style_lang)
        text = inline.text()
        lang = self._lang(_common(inline.run_langs, style_lang))
        after = self._after(inline, lang)
        own_level = outline_level(ppr)
        level = (
            heading_for_outline(own_level)
            if own_level is not None
            else self.styles.heading_level(style)
        )
        if level is not None:
            _drop_refs(inline, "a heading")
            heading = [Heading(min(level, MAX_HEADING_LEVEL), text, lang=lang)] if text else []
            out.add(*heading, *after, outline=top and bool(heading))
        elif self.styles.is_title(style):
            _drop_refs(inline, "a title")
            self.title = self.title or text
            out.add(*([Heading(1, text, lang=lang)] if text else []), *after)
        elif (listed := self._list(ppr, style)) is not None:
            _drop_refs(inline, "a list item")
            out.item(listed, text, lang, after)
        else:
            refs = tuple(self._note_id(r) for r in inline.refs)
            out.add(*([Paragraph(text, note_refs=refs, lang=lang)] if text else []), *after)

    def _list(self, ppr: etree._Element | None, style: str | None) -> _Listed | None:
        """What the numbering of a list paragraph says, or None if it isn't one."""
        own = paragraph_numbering(ppr)
        styled = self.styles.numbering(style)
        num_id = (own[0] if own and own[0] else None) or (styled[0] if styled else None)
        if not num_id:
            return None
        level = next((n[1] for n in (own, styled) if n and n[1] is not None), 0)
        ordered = self.numbering.ordered(num_id, level)
        return None if ordered is None else _Listed(num_id, level, ordered)

    def _table(self, tbl: etree._Element) -> list[Block]:
        rows: list[tuple[str, ...]] = []
        header_rows = 0
        after: list[Block] = []
        for tr in _children(tbl, f"{W}tr"):
            trpr = tr.find(f"{W}trPr")
            if trpr is not None and trpr.find(f"{W}del") is not None:
                continue  # a row deleted with Track Changes on
            cells = []
            for tc in _children(tr, f"{W}tc"):
                blocks = self.blocks(tc)
                lines = [block_text(b) for b in blocks if isinstance(b, TEXT_BLOCKS)]
                cells.append("\n".join(line for line in lines if line))
                after += [b for b in blocks if not isinstance(b, TEXT_BLOCKS)]
            if not cells:
                continue
            header = trpr is not None and any(on(h) for h in trpr.iter(f"{W}tblHeader"))
            if header and header_rows == len(rows):
                header_rows += 1
            rows.append(tuple(cells))
        if not rows or not any(any(row) for row in rows):
            return after
        return [Table(tuple(rows), header_rows=header_rows), *after]

    # What follows a block

    def _after(self, inline: Inline, lang: str | None) -> list[Block]:
        """Images, text boxes, and footnotes in a paragraph, in the order of each kind."""
        after: list[Block] = [replace(image, lang=lang) for image in inline.images]
        for box in inline.boxes:
            after += self.blocks(box)
        if self._in_note:
            return after  # a note within a note is read where its own reference is
        for ref in inline.refs:
            note = self._note(ref)
            if note is None:
                continue
            if ref.kind == "endnote":
                self.endnotes.append(note)
            else:
                after.append(note)
        return after

    def _note(self, ref: NoteRef) -> Footnote | None:
        """The block for the note `ref` refers to, the first time it is referred to."""
        part = self.notes[ref.kind]
        note_id = self._note_id(ref)
        el = part.notes.get(ref.ref_id)
        label = self.counter.next_mark(ref.kind) if ref.custom_mark is None else ref.custom_mark
        if el is None or note_id in self._placed:
            return None
        self._placed.add(note_id)
        self._in_note = True
        try:
            blocks = self.blocks(el)
        finally:
            self._in_note = False
        lines = [block_text(b) for b in blocks if isinstance(b, TEXT_BLOCKS)]
        text = "\n".join(line for line in lines if line)
        if any(isinstance(b, Image) for b in blocks):
            log.info("An image in note %s isn't read: notes are read as text.", note_id)
        if not text:
            return None
        lang = next((b.lang for b in blocks if isinstance(b, TEXT_BLOCKS)), None)
        return Footnote(note_id, text, label=label, lang=lang)

    def _note_id(self, ref: NoteRef) -> str:
        return self.notes[ref.kind].note_id(ref.ref_id)

    # Helpers

    def _lang(self, lang: str | None) -> str | None:
        """`lang` for a block: set only when it differs from the section's language."""
        if lang is None or (self.section_lang or "").lower() == lang.lower():
            return None
        return lang

    def _note_contents_skipped(self) -> None:
        if not self._skipped_contents:
            log.info("Skipped the table of contents: each of its entries is a section.")
            self._skipped_contents = True


def _common(langs: list[str | None], fallback: str | None) -> str | None:
    """The language every run with words shares, else `fallback`."""
    distinct = {(lang or "").lower() for lang in langs}
    return langs[0] if len(distinct) == 1 and langs[0] else fallback


def _children(el: etree._Element, tag: str) -> list[etree._Element]:
    """`el`'s children named `tag`, including those inside content controls and the
    other elements that only wrap content."""
    found = []
    for child in el:
        if child.tag == tag:
            found.append(child)
        elif child.tag == f"{W}sdt":
            content = child.find(f"{W}sdtContent")
            if content is not None:
                found += _children(content, tag)
        elif child.tag in WRAPPERS:
            found += _children(child, tag)
    return found


def _drop_refs(inline: Inline, where: str) -> None:
    if inline.refs:
        log.debug(
            "%d note references in %s can't be kept: only paragraphs carry them.",
            len(inline.refs),
            where,
        )
