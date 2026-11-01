"""Turn the <body> of an XHTML content document into document-model blocks.

Blocks come out in document order. Some things sit inside running text but are blocks in
the model. Where they go:

- A print page that begins inside a paragraph, list, or table gets its PageMarker just
  before that block, so jumping to the page never skips its first words.
- Images and notes found inside a block come right after it, so they never split a
  sentence.
- Note ids are qualified by file ("OEBPS/notes.xhtml#n1"), so the same id in two chapters
  can't collide. A note reference keeps its target in `Paragraph.note_refs`; its printed
  marker ("1", "*") is removed from the text. Only paragraphs carry references, so a
  reference in a heading, list item, or table cell loses its target (logged at debug level).
- `<hr>` and non-focusable `role="separator"` elements become SceneBreaks. EPUB's
  structural semantics and DPUB-ARIA have no scene-break term, so no epub:type is used.
  A section never starts or ends with a SceneBreak or has two in a row.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace

from lxml import etree

from dogear.document import (
    Block,
    Footnote,
    Heading,
    Image,
    ListBlock,
    PageMarker,
    Paragraph,
    SceneBreak,
    Table,
)
from dogear.extract.xhtml import collapse, lang_of, local_name, plain_text, resolve_href, semantics

log = logging.getLogger(__name__)

# Elements that start a new block. Anything else is treated as running text.
BLOCK_TAGS = frozenset(
    """
    address article aside blockquote body caption center dd details dialog div dl dt
    fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr legend li
    main menu nav ol p pre section summary table tbody td tfoot th thead tr ul
    """.split()
)
HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
LIST_TAGS = frozenset({"ul", "ol"})
SKIP_TAGS = frozenset({"script", "style", "nav", "template", "head"})
NOTE_TYPES = frozenset({"footnote", "endnote", "rearnote"})
NOTE_CONTAINER_TYPES = frozenset({"footnotes", "endnotes", "rearnotes"})
ARIA_DEFAULT_HEADING_LEVEL = 2  # what ARIA assumes when aria-level is missing


def body_blocks(
    body: etree._Element,
    *,
    doc_path: str,
    section_lang: str | None,
    page_labels: Mapping[str, str],
    note_markers: dict[str, str],
) -> list[Block]:
    """Blocks for `body`, a document at `doc_path` whose section language is `section_lang`.

    `page_labels` maps element ids to print page labels from the book's page list.
    Printed note markers are recorded in `note_markers` as qualified note id -> marker, so
    notes can be labeled once the whole book has been read.
    """
    walker = _Walker(doc_path, section_lang, page_labels, note_markers)
    return _tidy_scene_breaks(walker.container(body, section_lang))


def note_key(path: str, element_id: str) -> str:
    """The book-wide id of the note with `element_id` in the file at `path`."""
    return f"{path}#{element_id}"


@dataclass
class _Around:
    """Blocks that belong just before and just after the block being built."""

    before: list[Block] = field(default_factory=list)
    after: list[Block] = field(default_factory=list)

    def absorb(self, other: _Around) -> None:
        self.before += other.before
        self.after += other.after

    def wrap(self, *blocks: Block) -> list[Block]:
        return [*self.before, *blocks, *self.after]


@dataclass
class _Run(_Around):
    """Running text gathered for one block."""

    preformatted: bool = False
    parts: list[str | None] = field(default_factory=list)  # None marks a line break
    note_refs: list[str] = field(default_factory=list)
    deferred: list[etree._Element] = field(default_factory=list)  # nested lists

    def add(self, text: str | None) -> None:
        if not text:
            return
        if not self.preformatted:
            self.parts.append(text)
            return
        for i, line in enumerate(text.split("\n")):
            if i:
                self.line_break()
            self.parts.append(line)

    def line_break(self) -> None:
        self.parts.append(None)

    def text(self) -> str:
        lines, current = [], []
        for part in self.parts:
            if part is None:
                lines.append("".join(current))
                current = []
            else:
                current.append(part)
        lines.append("".join(current))
        return "\n".join(line for line in map(collapse, lines) if line)


@dataclass
class _ListItems(_Around):
    items: list[str] = field(default_factory=list)


class _Walker:
    def __init__(
        self,
        doc_path: str,
        section_lang: str | None,
        page_labels: Mapping[str, str],
        note_markers: dict[str, str],
    ) -> None:
        self.doc_path = doc_path
        self.section_lang = section_lang
        self.page_labels = page_labels
        self.note_markers = note_markers

    # Block level

    def container(
        self, el: etree._Element, lang: str | None, skip: etree._Element | None = None
    ) -> list[Block]:
        """Blocks for a container's children. Loose text between them becomes paragraphs."""
        out: list[Block] = []
        run = _Run()
        run.add(el.text)
        for child in el:
            if child is not skip and isinstance(child.tag, str):
                child_lang = lang_of(child, lang)
                if local_name(child) in BLOCK_TAGS or _starts_block(child):
                    out += self._paragraph(run, lang)
                    run = _Run()
                    out += self._block(child, child_lang)
                else:
                    self._inline(child, child_lang, run)
            run.add(child.tail)
        return out + self._paragraph(run, lang)

    def _block(self, el: etree._Element, lang: str | None) -> list[Block]:
        tag, sem = local_name(el), semantics(el)
        if tag in SKIP_TAGS:
            return []
        if "pagebreak" in sem:
            return self._page_break(el)
        out = self._page_target(el)
        if tag == "hr" or _is_separator(el):
            return [*out, SceneBreak()]
        if sem & NOTE_TYPES:
            notes = self._note(el, lang)
            if notes is not None:
                return out + notes
        level = heading_level(el)
        if level:
            out += self._heading(el, lang, level)
        elif tag in ("p", "pre"):
            out += self._paragraph(self._collect(el, lang, preformatted=tag == "pre"), lang)
        elif tag in LIST_TAGS:
            out += self._list(el, lang)
        elif tag == "table":
            out += self._table(el, lang)
        elif tag == "figure":
            out += self._figure(el, lang)
        else:
            out += self.container(el, lang)
        return out

    def _paragraph(self, run: _Run, lang: str | None) -> list[Block]:
        text = run.text()
        if not text:
            self._drop_refs(run, "an empty paragraph")
            return run.wrap()
        return run.wrap(Paragraph(text, note_refs=tuple(run.note_refs), lang=self._lang(lang)))

    def _heading(self, el: etree._Element, lang: str | None, level: int) -> list[Block]:
        run = self._collect(el, lang)
        self._drop_refs(run, "a heading")
        text = run.text()
        return run.wrap(Heading(level, text, lang=self._lang(lang))) if text else run.wrap()

    def _note(self, el: etree._Element, lang: str | None, label: str = "") -> list[Block] | None:
        """A Footnote, or None when the note has no id and must be read as plain content."""
        element_id = (el.get("id") or "").strip()
        if not element_id:
            log.warning("A note in %s has no id; reading it as ordinary text.", self.doc_path)
            return None
        note_id = note_key(self.doc_path, element_id)
        run = self._collect(el, lang)
        self._drop_refs(run, f"note {note_id}")
        text = run.text()
        if not text:
            log.debug("Note %s has no text; skipped.", note_id)
            return run.wrap()
        return run.wrap(Footnote(note_id, text, label=label, lang=self._lang(lang)))

    def _list(self, el: etree._Element, lang: str | None) -> list[Block]:
        """A ListBlock with nested lists flattened in, or Footnotes for a list of notes."""
        in_notes = any(semantics(a) & NOTE_CONTAINER_TYPES for a in _self_and_ancestors(el))
        ordered = local_name(el) == "ol"
        out: list[Block] = []
        pending = _ListItems()

        def flush() -> None:
            nonlocal pending
            if pending.items:
                listing = ListBlock(tuple(pending.items), ordered=ordered, lang=self._lang(lang))
                out.extend(pending.wrap(listing))
            else:
                out.extend(pending.wrap())
            pending = _ListItems()

        for number, child in enumerate(_elements(el), start=_list_start(el)):
            child_lang = lang_of(child, lang)
            if local_name(child) == "li" and (in_notes or semantics(child) & NOTE_TYPES):
                notes = self._note(child, child_lang, label=str(number) if ordered else "")
                if notes is not None:
                    flush()
                    out.extend(self._page_target(child) + notes)
                    continue
            self._list_item(child, child_lang, pending)
        flush()
        return out

    def _list_item(self, el: etree._Element, lang: str | None, pending: _ListItems) -> None:
        if local_name(el) in LIST_TAGS:  # a list directly inside a list
            for child in _elements(el):
                self._list_item(child, lang_of(child, lang), pending)
            return
        pending.before += self._page_target(el)
        run = self._collect(el, lang, stop=LIST_TAGS)
        self._drop_refs(run, "a list item")
        pending.absorb(run)
        text = run.text()
        if text:
            pending.items.append(text)
        for nested in run.deferred:
            self._list_item(nested, lang_of(nested, lang), pending)

    def _table(self, el: etree._Element, lang: str | None) -> list[Block]:
        around = _Around()
        caption = ""
        head: list[list[tuple[str, bool]]] = []
        rows: list[list[tuple[str, bool]]] = []
        foot: list[list[tuple[str, bool]]] = []
        for child in _elements(el):
            tag = local_name(child)
            if tag == "caption":
                caption = self._cell_text(child, lang, around)
            elif tag == "thead":
                head += self._rows(_elements(child, "tr"), lang, around)
            elif tag == "tfoot":
                foot += self._rows(_elements(child, "tr"), lang, around)
            elif tag == "tbody":
                rows += self._rows(_elements(child, "tr"), lang, around)
            elif tag == "tr":
                rows += self._rows([child], lang, around)
        all_rows = head + rows + foot
        if not all_rows:
            log.debug("Skipped a table with no cells in %s.", self.doc_path)
            return around.wrap(Paragraph(caption, lang=self._lang(lang))) if caption else []
        header_rows = len(head) or sum(1 for _ in itertools.takewhile(_all_th, all_rows))
        table = Table(
            tuple(tuple(text for text, _ in row) for row in all_rows),
            header_rows=header_rows,
            caption=caption,
            lang=self._lang(lang),
        )
        return around.wrap(table)

    def _rows(
        self, trs: list[etree._Element], lang: str | None, around: _Around
    ) -> list[list[tuple[str, bool]]]:
        """(text, is_header) for each cell of each row that has cells."""
        rows = []
        for tr in trs:
            around.before += self._page_target(tr)
            cells = _elements(tr, "td", "th")
            row = [(self._cell_text(c, lang, around), local_name(c) == "th") for c in cells]
            if row:
                rows.append(row)
        return rows

    def _cell_text(self, el: etree._Element, lang: str | None, around: _Around) -> str:
        around.before += self._page_target(el)
        run = self._collect(el, lang_of(el, lang))
        self._drop_refs(run, "a table")
        around.absorb(run)
        return run.text()

    def _figure(self, el: etree._Element, lang: str | None) -> list[Block]:
        """The figure's blocks, with its figcaption as the caption of its first image."""
        caption_el = next(iter(_elements(el, "figcaption")), None)
        blocks = self.container(el, lang, skip=caption_el)
        if caption_el is None:
            return blocks
        caption_lang = lang_of(caption_el, lang)
        first_image = next((i for i, b in enumerate(blocks) if isinstance(b, Image)), None)
        if first_image is None:
            return blocks + self.container(caption_el, caption_lang)
        run = self._collect(caption_el, caption_lang)
        self._drop_refs(run, "a figure caption")
        blocks[first_image] = replace(blocks[first_image], caption=run.text())
        return run.wrap(*blocks)

    # Running text

    def _collect(
        self,
        el: etree._Element,
        lang: str | None,
        *,
        preformatted: bool = False,
        stop: frozenset[str] = frozenset(),
    ) -> _Run:
        """The running text inside `el`. Elements named in `stop` are deferred, not read."""
        run = _Run(preformatted=preformatted)
        self._inline_children(el, lang, run, stop)
        return run

    def _inline_children(
        self, el: etree._Element, lang: str | None, run: _Run, stop: frozenset[str]
    ) -> None:
        run.add(el.text)
        for child in el:
            if isinstance(child.tag, str):
                self._inline(child, lang_of(child, lang), run, stop)
            run.add(child.tail)

    def _inline(
        self,
        el: etree._Element,
        lang: str | None,
        run: _Run,
        stop: frozenset[str] = frozenset(),
    ) -> None:
        tag, sem = local_name(el), semantics(el)
        if tag in SKIP_TAGS or "backlink" in sem:
            return
        if "pagebreak" in sem:
            run.before += self._page_break(el)
            return
        run.before += self._page_target(el)
        if tag == "br":
            run.line_break()
        elif "noteref" in sem:
            self._noteref(el, run)
        elif tag == "img":
            alt = el.get("alt")
            run.after.append(
                Image(alt=None if alt is None else collapse(alt), lang=self._lang(lang))
            )
        elif tag == "svg":
            run.after.append(Image(alt=_svg_alt(el), lang=self._lang(lang)))
        elif tag in stop:
            run.deferred.append(el)
        elif sem & NOTE_TYPES and (notes := self._note(el, lang)) is not None:
            run.after += notes
        else:
            block_level = tag in BLOCK_TAGS
            if block_level:
                run.line_break()
            self._inline_children(el, lang, run, stop)
            if block_level:
                run.line_break()

    def _noteref(self, el: etree._Element, run: _Run) -> None:
        href = el.get("href")
        target = resolve_href(self.doc_path, href) if href else None
        if target is None or not target[1]:
            log.debug("A note reference in %s points nowhere (href=%r).", self.doc_path, href)
            return
        note_id = note_key(*target)
        if note_id not in run.note_refs:
            run.note_refs.append(note_id)
        marker = plain_text(el)
        if marker:
            self.note_markers.setdefault(note_id, marker)

    # Page markers

    def _page_break(self, el: etree._Element) -> list[Block]:
        """A PageMarker for a page-break element, or nothing if it has no label."""
        label = (
            collapse(el.get("title") or "")
            or collapse(el.get("aria-label") or "")
            or plain_text(el)
            or self.page_labels.get(el.get("id") or "", "")
        )
        if not label:
            log.debug("Skipped a page break with no label in %s.", self.doc_path)
            return []
        return [PageMarker(label)]

    def _page_target(self, el: etree._Element) -> list[Block]:
        """A PageMarker when the book's page list says a page begins at `el`."""
        label = self.page_labels.get(el.get("id") or "")
        return [PageMarker(label)] if label else []

    # Helpers

    def _lang(self, lang: str | None) -> str | None:
        """`lang` for a block: set only when it differs from the section's language."""
        if lang is None or (self.section_lang and lang.lower() == self.section_lang.lower()):
            return None
        return lang

    def _drop_refs(self, run: _Run, where: str) -> None:
        if run.note_refs:
            log.debug(
                "Note references %s in %s in %s can't be kept: only paragraphs carry them.",
                ", ".join(run.note_refs),
                where,
                self.doc_path,
            )


def _elements(el: etree._Element, *tags: str) -> list[etree._Element]:
    """Child elements of `el`, optionally only those with the given local names."""
    children = [c for c in el if isinstance(c.tag, str)]
    return [c for c in children if local_name(c) in tags] if tags else children


def _self_and_ancestors(el: etree._Element) -> Iterator[etree._Element]:
    return itertools.chain([el], el.iterancestors())


def _is_aria_heading(el: etree._Element) -> bool:
    return "heading" in (el.get("role") or "").split()


def _is_separator(el: etree._Element) -> bool:
    """ARIA's thematic-break role. A focusable separator is a resize handle instead."""
    return "separator" in (el.get("role") or "").split() and el.get("tabindex") is None


def _starts_block(el: etree._Element) -> bool:
    """True for elements that aren't block tags but still start a block."""
    return _is_aria_heading(el) or _is_separator(el)


def _tidy_scene_breaks(blocks: list[Block]) -> list[Block]:
    """`blocks` without SceneBreaks at the start or end or right after another one.

    Page markers don't count as content: a break between two of them is still the first.
    """
    out: list[Block] = []
    seen_content = False
    break_at: int | None = None  # where a kept break goes once more content follows
    for block in blocks:
        if isinstance(block, SceneBreak):
            if seen_content and break_at is None:
                break_at = len(out)
            continue
        if not isinstance(block, PageMarker):
            if break_at is not None:
                out.insert(break_at, SceneBreak())
                break_at = None
            seen_content = True
        out.append(block)
    return out


def heading_level(el: etree._Element) -> int:
    """1-6 for a heading element (h1-h6 or role="heading"), else 0."""
    if _is_aria_heading(el):
        try:
            level = int(el.get("aria-level") or ARIA_DEFAULT_HEADING_LEVEL)
        except ValueError:
            level = ARIA_DEFAULT_HEADING_LEVEL
        return min(max(level, 1), 6)
    return HEADING_LEVELS.get(local_name(el), 0)


def _list_start(el: etree._Element) -> int:
    try:
        return int(el.get("start") or 1)
    except ValueError:
        return 1


def _all_th(row: list[tuple[str, bool]]) -> bool:
    return all(is_header for _, is_header in row)


def _svg_alt(el: etree._Element) -> str | None:
    """An inline SVG's description: aria-label, else its <title>, else None."""
    label = el.get("aria-label")
    if label is not None:
        return collapse(label)
    title = el.find("{*}title")
    return plain_text(title) if title is not None else None
