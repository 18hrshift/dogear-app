"""Building a talking book's text and syncs from the narration and the rendered audio.

Each passage of the narration becomes a text item. Every spoken utterance becomes a sync
that points at the element showing it, with the clip of audio from its first sound to
the next utterance's, so the pauses between sentences are part of the clips and are
heard when a player plays them back to back.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from dataclasses import replace

from dogear.document import (
    Footnote,
    Heading,
    Image,
    ListBlock,
    PageMarker,
    Paragraph,
    SceneBreak,
    Table,
)
from dogear.narration import Passage, SectionNarration, Utterance
from dogear.output.chapter import ChapterAudio
from dogear.readalong.model import (
    BreakText,
    Clip,
    HeadingText,
    ImageText,
    ListText,
    NoteText,
    PageText,
    ParagraphText,
    RowText,
    SectionText,
    Sentence,
    Sync,
    TableText,
    TextItem,
)
from dogear.readalong.outline import heading_levels, page_kind


def build_readalong(
    narrations: list[SectionNarration], chapters: list[ChapterAudio], language: str | None
) -> list[SectionText]:
    """The talking book's sections, from each narration and the audio rendered from it.

    `chapters` must be in the same order as `narrations`, each with the marks the
    renderer recorded. `language` is the book's.
    """
    if len(narrations) != len(chapters):
        raise ValueError(f"{len(narrations)} narrations but {len(chapters)} chapters of audio")
    sections = [
        _SectionBuilder(narration, f"s{position:03d}").build(audio, language)
        for position, (narration, audio) in enumerate(
            zip(narrations, chapters, strict=True), start=1
        )
    ]
    return _with_book_levels(sections)


class _SectionBuilder:
    def __init__(self, narration: SectionNarration, prefix: str) -> None:
        self.narration = narration
        self.utterances = narration.utterances
        self.prefix = prefix
        self.spoken: list[int] = []  # utterances with audio, set by _clips
        self.targets: dict[int, str] = {}  # utterance index to the id of what shows it
        self.page_utterances: set[int] = set()
        self.page_count = 0
        self.section_lang: str | None = None  # set by build
        self.item_lang: str | None = None  # the language of the item being built

    def build(self, audio: ChapterAudio, language: str | None) -> SectionText:
        clips = self._clips(audio)
        self.section_lang = self.narration.section.lang or language
        items = tuple(self._item(p) for p in self.narration.passages)
        syncs = []
        for k, i in enumerate(self.spoken, start=1):
            if i not in self.targets:
                raise RuntimeError(
                    f"Utterance {i} of section {self.narration.number} belongs to no passage."
                )
            page = i in self.page_utterances
            syncs.append(Sync(f"{self.prefix}-par{k}", self.targets[i], clips[i], page))
        section = self.narration.section
        return SectionText(
            number=self.narration.number,
            title=section.title,
            kind=section.kind,
            lang=section.lang or language,
            items=items,
            syncs=tuple(syncs),
            audio=audio,
        )

    def _clips(self, audio: ChapterAudio) -> dict[int, Clip]:
        marks, rate = audio.marks, audio.sample_rate
        if len(marks) != len(self.utterances) + 1 or rate <= 0:
            raise ValueError(
                f"The audio for section {self.narration.number} has no timings for its "
                f"{len(self.utterances)} utterances."
            )
        said = [i for i, u in enumerate(self.utterances) if u.spoken]
        nexts = [*said[1:], len(self.utterances)]
        # Players can't play an empty clip, so an utterance that came out silent with no
        # pause after it gets none; its text simply isn't highlighted.
        self.spoken = [i for i, n in zip(said, nexts, strict=True) if marks[n] > marks[i]]
        if not self.spoken:
            raise ValueError(f"Section {self.narration.number} came out silent.")
        ends = [*self.spoken[1:], len(self.utterances)]
        return {
            i: Clip(marks[i] / rate, marks[end] / rate)
            for i, end in zip(self.spoken, ends, strict=True)
        }

    def _item(self, passage: Passage) -> TextItem:
        block, parts = passage.block, passage.parts
        self.item_lang = getattr(block, "lang", None) or self.section_lang
        match block:
            case Heading(level=level, text=text, lang=lang):
                [i] = parts[0]
                self.targets[i] = self._id(i)
                sync = self._sync_at(i)
                if sync is None:  # silent, with nothing after it: the section's last words
                    sync = len(self.spoken) - 1
                return HeadingText(self._id(i), level, text, sync, lang)
            case Paragraph(lang=lang):
                return ParagraphText(self._sentences(parts[0]), lang)
            case ListBlock(ordered=ordered, lang=lang):
                return ListText(tuple(self._sentences(p) for p in parts), ordered, lang)
            case Table():
                return self._table(block, parts)
            case Image(lang=lang):
                return ImageText(self._sentences(parts[0]), self._sentences(parts[1]), lang)
            case Footnote(label=label, lang=lang):
                return NoteText(label, self._sentences(parts[0]), lang)
            case PageMarker(label=label):
                self.page_count += 1
                page_id = f"{self.prefix}-page{self.page_count}"
                for i in parts[0]:  # "Page 42.", when pages are announced
                    self.targets[i] = page_id
                    self.page_utterances.add(i)
                return PageText(page_id, label, page_kind(label), self._sync_at(parts[0].start))
            case SceneBreak():
                return BreakText()
        raise TypeError(f"not a block: {block!r}")

    def _table(self, table: Table, parts: tuple[range, ...]) -> TableText:
        header = tuple(RowText(None, row) for row in table.rows[: table.header_rows])
        read = parts[1:]  # one part per body row, or none when tables are skipped
        rows = []
        for k, cells in enumerate(table.rows[table.header_rows :]):
            row_id = None
            if k < len(read) and read[k]:
                row_id = self._id(read[k][0])
                for i in read[k]:
                    self.targets[i] = row_id
            rows.append(RowText(row_id, cells))
        return TableText(self._sentences(parts[0]), header, tuple(rows), table.lang)

    def _sentences(self, part: range) -> tuple[Sentence, ...]:
        for i in part:
            self.targets[i] = self._id(i)
        return tuple(
            Sentence(self._id(i), self.utterances[i].text, self._own_lang(self.utterances[i]))
            for i in part
        )

    def _own_lang(self, utterance: Utterance) -> str | None:
        """The utterance's language, if it isn't the item's around it."""
        lang = utterance.lang
        if not lang or (self.item_lang and lang.lower() == self.item_lang.lower()):
            return None
        return lang

    def _id(self, utterance: int) -> str:
        return f"{self.prefix}-u{utterance}"

    def _sync_at(self, position: int) -> int | None:
        """Index of the first sync at or after an utterance position."""
        k = bisect_left(self.spoken, position)
        return k if k < len(self.spoken) else None


def _with_book_levels(sections: list[SectionText]) -> list[SectionText]:
    """Heading levels settled across the whole book. The title track's heading stays 1.

    Front and back matter never open at a shallower level than the book's chapters do.
    Many books mark a title page as level 1 and every chapter as level 2; taken as they
    are, every chapter would sit under the title page in the contents. Body sections
    keep their levels, so a book's parts still hold its chapters.
    """
    book = [s for s in sections if s.number != 0]
    chapter_level = _usual_opening_level(book)
    raw = []
    for section in book:
        headings = section.headings()
        shift = 0
        if section.kind != "body" and headings and headings[0].level < chapter_level:
            shift = chapter_level - headings[0].level
        raw += [min(h.level + shift, 6) for h in headings]
    levels = iter(heading_levels(raw))
    result = []
    for section in sections:
        items = tuple(
            replace(item, level=1 if section.number == 0 else next(levels))
            if isinstance(item, HeadingText)
            else item
            for item in section.items
        )
        result.append(replace(section, items=items))
    return result


def _usual_opening_level(sections: list[SectionText]) -> int:
    """The level most body sections open with: the book's chapter level."""
    openings = Counter(s.headings()[0].level for s in sections if s.kind == "body" and s.headings())
    return openings.most_common(1)[0][0] if openings else 1
