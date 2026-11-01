"""Read the running text of one Word paragraph (w:p): its words, note references,
images, and text boxes.

What a paragraph shows is read; what it doesn't is not:
- Runs (w:r) are joined. Tabs and line breaks of every kind (w:br, w:cr) are spaces.
- Tracked changes read as if accepted: insertions (w:ins) are read, deletions (w:del,
  w:delText) and the old place of moved text (w:moveFrom) are not.
- A field's result is read, its code (w:instrText) is not. Hidden text (w:vanish),
  content-control placeholder text, comments, and bookmarks are not read.
- Equations (m:oMath) are read as their characters, in order.
- A footnote or endnote reference becomes a NoteRef, and its custom mark, if any, is
  taken out of the text.
- A picture, chart, or other drawing becomes an Image with the alt text Word keeps for
  it (wp:docPr descr, else its title), or none; "Mark as decorative" gives alt "".
- A text box's contents (w:txbxContent) are paragraphs of their own, read after this one.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

from lxml import etree

from dogear.document import Image
from dogear.extract.docx_package import MATH, MC, OFFICE, VML, W
from dogear.extract.docx_styles import Styles, on, run_lang
from dogear.extract.xhtml import collapse

log = logging.getLogger(__name__)

NOTE_REFERENCES = {f"{W}footnoteReference": "footnote", f"{W}endnoteReference": "endnote"}
SPACES = frozenset({f"{W}tab", f"{W}ptab", f"{W}br", f"{W}cr"})
# Paragraph content that holds nothing to read. Anything else is searched for runs.
NOT_READ = frozenset(
    {
        f"{W}pPr",
        f"{W}rPr",
        f"{W}del",
        f"{W}moveFrom",
        f"{W}sdtPr",
        f"{W}sdtEndPr",
        f"{W}customXmlPr",
        f"{W}smartTagPr",
    }
)
# The Office 2010 extension Word writes for "Mark as decorative" on an image.
DECORATIVE = "{http://schemas.microsoft.com/office/drawing/2017/decorative}decorative"


@dataclass(frozen=True)
class NoteRef:
    kind: str  # "footnote" or "endnote"
    ref_id: str  # the note's w:id
    custom_mark: str | None  # the mark printed instead of a number, if any


@dataclass
class Inline:
    """What one paragraph holds."""

    parts: list[str] = field(default_factory=list)
    refs: list[NoteRef] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)  # with no language: the block sets it
    boxes: list[etree._Element] = field(default_factory=list)  # w:txbxContent
    run_langs: list[str | None] = field(default_factory=list)  # each run with words

    def text(self) -> str:
        return collapse("".join(self.parts))


class InlineReader:
    def __init__(self, styles: Styles) -> None:
        self.styles = styles

    def read(self, paragraph: etree._Element, lang: str | None) -> Inline:
        """The content of `paragraph`, whose own language (from its style) is `lang`."""
        inline = Inline()
        self._children(paragraph, inline, lang)
        return inline

    def _children(self, el: etree._Element, inline: Inline, lang: str | None) -> None:
        for child in el:
            if not isinstance(child.tag, str) or child.tag in NOT_READ:
                continue
            if child.tag == f"{W}r":
                self._run(child, inline, lang)
            elif child.tag == f"{MC}AlternateContent":
                chosen = alternative(child)
                if chosen is not None:
                    self._children(chosen, inline, lang)
            elif child.tag in (f"{MATH}oMath", f"{MATH}oMathPara"):
                inline.parts.append(f" {_math_text(child)} ")
            elif child.tag == f"{W}sdt" and showing_placeholder(child):
                continue
            else:  # hyperlinks, fields, insertions, content controls, smart tags, ...
                self._children(child, inline, lang)

    def _run(self, run: etree._Element, inline: Inline, paragraph_lang: str | None) -> None:
        rpr = run.find(f"{W}rPr")
        if rpr is not None and (hidden := rpr.find(f"{W}vanish")) is not None and on(hidden):
            log.debug("Skipped hidden text.")
            return
        lang = self._run_lang(rpr) or paragraph_lang
        texts: list[str] = []
        custom: list[str] | None = None  # the custom mark being read, after its reference
        for child in self._run_content(run):
            tag = child.tag
            if tag == f"{W}t":
                (texts if custom is None else custom).append(child.text or "")
            elif tag in SPACES:
                texts.append(" ")
            elif tag == f"{W}noBreakHyphen":
                texts.append("-")
            elif tag in NOTE_REFERENCES:
                custom = self._note_ref(child, inline, custom)
            elif tag == f"{W}drawing":
                self._drawing(child, inline)
            elif tag in (f"{W}pict", f"{W}object"):
                self._vml(child, inline)
            # Not read: w:delText, w:instrText, w:fldChar, w:sym, w:softHyphen, the marks
            # inside notes (w:footnoteRef), page-break hints, and run properties.
        if custom is not None:
            self._end_custom_mark(inline, custom)
        text = "".join(texts)
        inline.parts.append(text)
        if text.strip():
            inline.run_langs.append(lang)

    def _run_content(self, run: etree._Element) -> Iterator[etree._Element]:
        for child in run:
            if child.tag == f"{MC}AlternateContent":
                chosen = alternative(child)
                if chosen is not None:
                    yield from (c for c in chosen if isinstance(c.tag, str))
            elif isinstance(child.tag, str):
                yield child

    def _run_lang(self, rpr: etree._Element | None) -> str | None:
        if rpr is None:
            return None
        style = rpr.find(f"{W}rStyle")
        own = run_lang(rpr)
        return own or (self.styles.lang(style.get(f"{W}val")) if style is not None else None)

    def _note_ref(
        self, el: etree._Element, inline: Inline, custom: list[str] | None
    ) -> list[str] | None:
        if custom is not None:
            self._end_custom_mark(inline, custom)
        ref_id = el.get(f"{W}id")
        if ref_id is None:
            log.debug("Skipped a note reference with no id.")
            return None
        has_custom = on(el, f"{W}customMarkFollows", default=False)
        inline.refs.append(NoteRef(NOTE_REFERENCES[el.tag], ref_id, "" if has_custom else None))
        return [] if has_custom else None

    def _end_custom_mark(self, inline: Inline, custom: list[str]) -> None:
        last = inline.refs[-1]
        inline.refs[-1] = NoteRef(last.kind, last.ref_id, collapse("".join(custom)))

    def _drawing(self, el: etree._Element, inline: Inline) -> None:
        """A text box's contents, or an image (a picture also when a text box is with it)."""
        boxes = outermost_boxes(el)
        inline.boxes += boxes
        has_picture = next(el.iter("{*}pic"), None) is not None
        if boxes and not has_picture:
            return
        doc_pr = next(el.iter("{*}docPr"), None)
        inline.images.append(Image(alt=_drawing_alt(doc_pr)))

    def _vml(self, el: etree._Element, inline: Inline) -> None:
        """An older (VML) picture or text box, as Word 2003 and some converters write."""
        inline.boxes += outermost_boxes(el)
        image = next(el.iter(f"{VML}imagedata"), None)
        if image is None:
            return
        shape = image.getparent()
        alt = (shape.get("alt") if shape is not None else None) or image.get(f"{OFFICE}title")
        inline.images.append(Image(alt=collapse(alt) if alt and alt.strip() else None))


def alternative(el: etree._Element) -> etree._Element | None:
    """The one choice of an mc:AlternateContent to read: the first mc:Choice, else the
    mc:Fallback. They hold the same content in different forms, so only one is read."""
    choice = el.find(f"{MC}Choice")
    return choice if choice is not None else el.find(f"{MC}Fallback")


def showing_placeholder(sdt: etree._Element) -> bool:
    """Whether a content control shows its placeholder ("Click here to enter text")."""
    return sdt.find(f"{W}sdtPr/{W}showingPlcHdr") is not None


def outermost_boxes(el: etree._Element) -> list[etree._Element]:
    """The text boxes in `el` that aren't inside another text box in it."""
    boxes = []
    for box in el.iter(f"{W}txbxContent"):
        ancestor = box.getparent()
        while ancestor is not None and ancestor is not el and ancestor.tag != f"{W}txbxContent":
            ancestor = ancestor.getparent()
        if ancestor is None or ancestor is el:
            boxes.append(box)
    return boxes


def _drawing_alt(doc_pr: etree._Element | None) -> str | None:
    if doc_pr is None:
        return None
    decorative = next(doc_pr.iter(DECORATIVE), None)
    if decorative is not None and on(decorative, "val", default=False):
        return ""
    for attribute in ("descr", "title"):
        text = collapse(doc_pr.get(attribute) or "")
        if text:
            return text
    return None


def _math_text(el: etree._Element) -> str:
    return " ".join(t.text for t in el.iter(f"{MATH}t") if t.text and t.text.strip())
