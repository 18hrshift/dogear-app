"""Build an EPUB's sections from its table of contents and reading order.

Each Section becomes one audio track, so it should match what a listener thinks of as a
chapter. The table of contents (an EPUB 3 nav document or an EPUB 2 NCX) says where
chapters begin; the reading order (the spine) says what to read. The rules:

- Each table-of-contents entry at most `epub.toc_depth` levels deep starts a section where
  it points: the top of a file, or an element inside one (`book.xhtml#chapter-3`), where
  the file is cut. Deeper entries don't start sections; their text stays in the section
  around them. When several entries point to the same place, the deepest one names the
  section (a Chapter rather than the Part it opens), then the first one listed.
- With the default depth of 2, a Part > Chapter contents gives one track per chapter, and
  a Part's own pages (its title, an epigraph) become a short track of their own. That keeps
  the Part's title in the track list, where a listener can jump to it, and keeps a Part's
  title page from being classified with the chapter after it.
- Content no entry covers is never dropped. Each file before the first entry becomes its
  own section, front matter unless the book marks it otherwise. Content after an entry
  continues that entry's section, which joins a chapter split across files
  (`ch1_split_000.xhtml`, `ch1_split_001.xhtml`). It starts a section of its own instead
  when the book marks it as a different kind of matter (a colophon after the last
  chapter), or when no entry points into its file and it opens with a heading as
  prominent as the section's own (a chapter the contents leave out).
- Files the book marks non-linear come after the main text, one section each.
- If no entry points into the reading order (the book has no table of contents, an empty
  one, or one that points elsewhere), or `epub.toc_depth` is 0, each file becomes one
  section.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace

from lxml import etree

from dogear.document import Block, Heading, Image, PageMarker, SceneBreak, Section, SectionKind
from dogear.errors import ExtractionError
from dogear.extract.epub_kind import marked_kind
from dogear.extract.epub_nav import Navigation, TocEntry
from dogear.extract.epub_package import DAMAGED_HINT, XHTML_MEDIA_TYPE, EpubArchive, Package
from dogear.extract.stretches import carry_page_markers
from dogear.extract.xhtml import lang_of
from dogear.extract.xhtml_blocks import body_blocks
from dogear.extract.xhtml_split import split_body

log = logging.getLogger(__name__)


def read_sections(
    archive: EpubArchive,
    package: Package,
    nav: Navigation,
    toc_depth: int,
    note_markers: dict[str, str],
) -> list[Section]:
    """Sections in reading order, then the documents the book marks non-linear.

    Printed note markers are recorded in `note_markers`, as `body_blocks` describes.
    """
    reader = _FileReader(archive, package, nav, note_markers)
    linear, non_linear = _reading_order(archive, package)
    entries = _section_entries(archive.name, nav, linear, toc_depth)
    if entries:
        drafts = _by_toc(reader, linear, entries)
    else:
        drafts = [_whole_file(reader, path, "body") for path in linear]
    for path in non_linear:
        draft = _whole_file(reader, path, "back")
        if draft.blocks:
            log.info(
                'Added %s after the main text: the book marks it non-linear (linear="no").', path
            )
        drafts.append(draft)
    sections: list[Section] = []
    for draft in drafts:
        section = draft.section(len(sections) + 1)
        if section is not None:
            sections.append(section)
    return sections


# Reading order


def _reading_order(archive: EpubArchive, package: Package) -> tuple[list[str], list[str]]:
    """Archive paths of the content documents: (linear, non-linear), each in spine order."""
    linear: list[str] = []
    non_linear: list[str] = []
    for ref in package.spine:
        path = _content_path(archive, package, ref.idref)
        if path is not None:
            (linear if ref.linear else non_linear).append(path)
    return linear, non_linear


def _content_path(archive: EpubArchive, package: Package, idref: str) -> str | None:
    """Archive path of the XHTML document a spine entry names, or None to skip it."""
    item = package.manifest.get(idref)
    if item is None:
        raise ExtractionError(
            f"The reading order of {archive.name} names '{idref}', "
            "which isn't in the book's list of files.",
            hint=DAMAGED_HINT,
        )
    if "nav" in item.properties:
        log.info("Skipped %s as content: it is the navigation document.", item.href)
        return None
    if item.media_type != XHTML_MEDIA_TYPE:
        log.warning(
            "Skipped %s in %s: Dogear can't read %s documents.",
            item.href,
            archive.name,
            item.media_type or "untyped",
        )
        return None
    if item.path is None or not archive.has(item.path):
        raise ExtractionError(
            f"{archive.name} lists {item.href} in its reading order, but the file is missing.",
            hint=DAMAGED_HINT,
        )
    return item.path


def _section_entries(
    name: str, nav: Navigation, linear: list[str], toc_depth: int
) -> list[TocEntry]:
    """The table-of-contents entries that start sections, or [] to use one per file."""
    if toc_depth == 0:
        log.info("epub.toc_depth is 0, so each file in %s becomes one section.", name)
        return []
    in_order = set(linear)
    shallow = [e for e in nav.toc if e.depth <= toc_depth]
    entries = [e for e in shallow if e.path in in_order]
    if not entries:
        problem = (
            "a table of contents that doesn't point into its reading order"
            if nav.toc
            else "no table of contents entries"
        )
        log.info("%s has %s, so each file becomes one section.", name, problem)
    elif len(entries) < len(shallow):
        log.debug(
            "%d table of contents entries point outside the main reading order; "
            "they don't start sections.",
            len(shallow) - len(entries),
        )
    return entries


# Assembling sections


@dataclass
class _Stretch:
    """Part of one content file: the whole file, or from one cut to the next."""

    path: str
    fragment: str  # the id where it starts, or "" for the top of the file
    entry: TocEntry | None  # the table-of-contents entry that starts a section here
    blocks: list[Block]
    kind: SectionKind | None  # what the book marks it as, if anything
    lang: str | None  # the file's language

    @property
    def source(self) -> str:
        return f"{self.path}#{self.fragment}" if self.fragment else self.path


@dataclass
class _Draft:
    """A section being put together from one or more stretches."""

    title: str  # "" until a heading or number is chosen
    kind: SectionKind
    lang: str | None
    blocks: list[Block] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def add(self, stretch: _Stretch) -> None:
        self.blocks += _relabel_lang(stretch.blocks, stretch.lang, self.lang)
        self.sources.append(stretch.source)

    def section(self, number: int) -> Section | None:
        """The finished Section, or None if there is nothing in it."""
        first, last = self.sources[0], self.sources[-1]
        source = first if len(self.sources) == 1 else f"{first} to {last}"
        if not self.blocks:
            log.debug("Skipped %s: it has no content.", source)
            return None
        heading = next((b.text for b in self.blocks if isinstance(b, Heading)), "")
        return Section(
            title=self.title or " ".join(heading.split()) or f"Section {number}",
            kind=self.kind,
            blocks=self.blocks,
            lang=self.lang,
            source=source,
        )


def _by_toc(reader: _FileReader, linear: list[str], entries: list[TocEntry]) -> list[_Draft]:
    by_path: dict[str, list[TocEntry]] = defaultdict(list)
    for entry in entries:
        by_path[entry.path].append(entry)
    listed = {e.path for e in reader.nav.toc}  # files any entry points into, at any depth
    drafts: list[_Draft] = []
    current: _Draft | None = None  # the section that uncovered content continues
    for path in linear:
        for stretch in reader.stretches(path, by_path.get(path, [])):
            if stretch.entry is not None:
                current = target = _Draft(stretch.entry.title, stretch.kind or "body", stretch.lang)
            elif not stretch.blocks:
                continue
            elif current is None:  # before the first entry
                target = _Draft("", stretch.kind or "front", stretch.lang)
            elif _continues(current, stretch, listed=path in listed):
                current.add(stretch)
                continue
            else:
                current = target = _Draft("", stretch.kind or current.kind, stretch.lang)
            drafts.append(target)
            target.add(stretch)
    return drafts


def _continues(draft: _Draft, stretch: _Stretch, *, listed: bool) -> bool:
    """Whether content that no entry starts belongs to the section before it.

    It doesn't when the book marks it as another kind of matter (a colophon after the
    last chapter), or when no entry points into its file and it opens with a heading as
    prominent as the section's own: that's a chapter the table of contents leaves out.
    A split chapter's later files open with running text or a lesser heading.
    """
    if stretch.kind not in (None, draft.kind):
        return False
    if listed:
        return True
    level, own = _opening_level(stretch.blocks), _opening_level(draft.blocks)
    return level is None or (own is not None and level > own)


def _opening_level(blocks: list[Block]) -> int | None:
    """The level of the heading `blocks` open with, if any.

    Page markers and images before it don't count.
    """
    for block in blocks:
        if isinstance(block, Heading):
            return block.level
        if not isinstance(block, PageMarker | Image | SceneBreak):
            return None
    return None


def _whole_file(reader: _FileReader, path: str, default_kind: SectionKind) -> _Draft:
    [stretch] = reader.stretches(path, [])
    title = next((e.title for e in reader.nav.toc if e.path == path and e.title), "")
    draft = _Draft(title, stretch.kind or default_kind, stretch.lang)
    draft.add(stretch)
    return draft


def _relabel_lang(blocks: list[Block], file_lang: str | None, lang: str | None) -> list[Block]:
    """`blocks`, whose language tags are relative to `file_lang`, made relative to `lang`.

    A block's `lang` is set only when it differs from its section's language, so a file
    that joins a section in another language needs its blocks retagged.
    """
    if _same_lang(file_lang, lang):
        return blocks
    out: list[Block] = []
    for block in blocks:
        if isinstance(block, PageMarker | SceneBreak):
            out.append(block)
            continue
        own = block.lang or file_lang
        relative = None if _same_lang(own, lang) else own
        out.append(block if relative == block.lang else replace(block, lang=relative))
    return out


def _same_lang(a: str | None, b: str | None) -> bool:
    return (a or "").lower() == (b or "").lower()


# Reading files


class _FileReader:
    def __init__(
        self,
        archive: EpubArchive,
        package: Package,
        nav: Navigation,
        note_markers: dict[str, str],
    ) -> None:
        self.archive = archive
        self.package = package
        self.nav = nav
        self.note_markers = note_markers

    def stretches(self, path: str, entries: list[TocEntry]) -> list[_Stretch]:
        """The file at `path`, cut where `entries` point into it.

        The first stretch runs from the top of the file to the first cut; its `entry` is
        set only when an entry points to the top of the file.
        """
        root = self.archive.parse(path)
        body = root.find("{*}body")
        if body is None:
            raise ExtractionError(
                f"{path} in {self.archive.name} has no <body>.", hint=DAMAGED_HINT
            )
        lang = lang_of(body, lang_of(root, self.package.metadata.language))
        top, cuts = _cut_points(body, entries, path)
        bodies = split_body(body, [el for el, _ in cuts])
        starts = [("", top), *((entry.fragment, entry) for _, entry in cuts)]
        landmarks = self.nav.landmarks.get(path, frozenset())
        stretches = [
            _Stretch(
                path=path,
                fragment=fragment,
                entry=entry,
                blocks=body_blocks(
                    part,
                    doc_path=path,
                    section_lang=lang,
                    page_labels=self.nav.page_labels.get(path, {}),
                    note_markers=self.note_markers,
                ),
                kind=marked_kind(part, landmarks),
                lang=lang,
            )
            for part, (fragment, entry) in zip(bodies, starts, strict=True)
        ]
        carry_page_markers([s.blocks for s in stretches])
        return stretches


def _cut_points(
    body: etree._Element, entries: list[TocEntry], path: str
) -> tuple[TocEntry | None, list[tuple[etree._Element, TocEntry]]]:
    """(entry for the top of the file, [(element, entry) for each cut in document order]).

    An entry whose id isn't in the file starts at the top of the file instead. Where
    several entries meet, the deepest wins, then the first listed.
    """
    wanted = {e.fragment for e in entries if e.fragment}
    found: dict[str, tuple[int, etree._Element]] = {}
    if wanted:
        for position, el in enumerate(body.iter(etree.Element)):  # body itself is 0
            element_id = el.get("id")
            if element_id in wanted and element_id not in found:
                found[element_id] = (position, el)
    chosen: dict[int, tuple[etree._Element, TocEntry]] = {}
    for entry in entries:
        position, el = found.get(entry.fragment, (0, body))
        if entry.fragment and entry.fragment not in found:
            log.info(
                "The table of contents points to #%s in %s, which isn't there, so %r "
                "starts at the top of that file.",
                entry.fragment,
                path,
                entry.title,
            )
        held = chosen.get(position)
        if held is None or entry.depth > held[1].depth:
            chosen[position] = (el, entry)
    top = chosen.pop(0, (body, None))[1]
    return top, [chosen[p] for p in sorted(chosen)]
