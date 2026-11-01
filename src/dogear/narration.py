"""Spoken structure: what a section sounds like, as a list of utterances.

Structure a sighted reader sees (headings, paragraphs, images, notes, scene breaks) is
turned into words and pauses a listener can hear. Longer pauses mark bigger breaks: a
sentence, a paragraph, a heading, a scene, a chapter. Every choice here comes from the
[narration] settings.

Each utterance knows its language, so the renderer can give it the voice for that
language: the one the book marks on its block or chapter, else the book's. With a
`LanguageGuesser` (from [language_detection]), text the book leaves to the book's own
language is checked too: a block whose language is guessed carries it in its passage,
and a sentence of a long paragraph guessed to be in yet another language carries it on
its utterance. The words narration adds ("Image", "Note") come from that language's
table in [narration.words].
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field, replace

from dogear.config import Config
from dogear.document import (
    Block,
    Book,
    Footnote,
    Heading,
    Image,
    ListBlock,
    PageMarker,
    Paragraph,
    SceneBreak,
    Section,
    Table,
    block_text,
)
from dogear.language_detection import LanguageGuesser
from dogear.language_match import most_specific, same_language
from dogear.speech.normalize import SpeechRules, normalize_for_speech, normalize_heading
from dogear.speech.sentences import SentenceRules, split_sentences

log = logging.getLogger(__name__)

_ENDS_CLAUSE = (".", "!", "?", ":", ";", ",", "…")


@dataclass(frozen=True)
class Utterance:
    text: str  # display text, for read-along output; "" for a pure pause
    spoken: str  # what the voice says; "" for a pure pause
    pause_ms: int  # silence after it
    lang: str | None = None  # its language, a BCP 47 tag; None if the book gives none


@dataclass(frozen=True)
class Passage:
    """Where one block sits among a section's utterances, so read-along text can show it.

    `parts` are ranges of utterance indexes. What each part holds depends on the block:

    - Heading, Paragraph, Footnote: one part, the block's sentences.
    - ListBlock: one part per item.
    - Table: the introduction ("Table: Prices."), then one part per body row read aloud.
      When tables are skipped, only the introduction, which then holds the "not read"
      sentence too.
    - Image: the description, then the caption. Either may be empty.
    - PageMarker: one part, "Page 42." when pages are announced, otherwise empty.
    - SceneBreak: no parts.

    An empty part still has a position: the index of the utterance that follows it.
    Blocks that add nothing to the narration (a note already read, a decorative image)
    have no passage, except page markers and scene breaks, which mark places.
    """

    block: Block
    parts: tuple[range, ...]


@dataclass(frozen=True)
class SectionNarration:
    number: int  # 1-based, as `dogear outline` shows it; 0 for the title track
    section: Section
    utterances: list[Utterance]
    passages: list[Passage] = field(default_factory=list)  # in reading order

    def words(self) -> int:
        return sum(len(u.spoken.split()) for u in self.utterances)


def narrate_book(
    book: Book, config: Config, guesser: LanguageGuesser | None = None
) -> list[SectionNarration]:
    """Every section's narration, leaving out sections that end up with nothing to say.
    With a `guesser`, the language of text the book doesn't mark is guessed."""
    narrator = _Narrator(book, config, guesser)
    narrations = []
    for number, section in enumerate(book.sections, start=1):
        read_earlier = set(narrator.read_notes)
        utterances, passages = narrator.section(section)
        if narrator.has_content(utterances, section, read_earlier):
            narrations.append(SectionNarration(number, section, utterances, passages))
        else:
            log.info(
                "Section %d, %s, was left out of the audio: its notes are read where the "
                "text refers to them.",
                number,
                section.title,
            )
    return narrations


def narrate_title(book: Book, config: Config) -> SectionNarration:
    """A short opening track: the book's title, then who wrote it."""
    metadata = book.metadata
    words = narration_words(config, metadata.language)
    blocks: list[Block] = [Heading(1, metadata.title)]
    if metadata.authors:
        blocks.append(Paragraph(f"{words['by']} {_join(metadata.authors, words['and'])}."))
    section = Section(metadata.title, kind="front", blocks=blocks)
    utterances, passages = _Narrator(book, config).section(section)
    return SectionNarration(0, section, utterances, passages)


def opens_with_title(narration: SectionNarration, title: str) -> bool:
    """Whether a section starts by saying the book's title, as most title pages do."""
    first = narration.passages[0].block if narration.passages else None
    return isinstance(first, Heading) and _words(first.text) == _words(title)


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", "", text).casefold().split()


def narration_words(config: Config, lang: str | None) -> dict[str, str]:
    """The words narration adds, in `lang`: the most specific table for it under
    [narration.words], or the English words when there is none."""
    return _language_words(config, lang) or _english_words(config)


def has_narration_words(config: Config, lang: str | None) -> bool:
    """Whether [narration.words] has words in `lang`, English included."""
    return same_language(lang, None) or _language_words(config, lang) is not None


def _english_words(config: Config) -> dict[str, str]:
    return {k: v for k, v in config["narration"]["words"].items() if isinstance(v, str)}


def _language_words(config: Config, lang: str | None) -> dict[str, str] | None:
    words = config["narration"]["words"]
    return most_specific({k.lower(): v for k, v in words.items() if isinstance(v, dict)}, lang)


class _Narrator:
    def __init__(self, book: Book, config: Config, guesser: LanguageGuesser | None = None) -> None:
        self.config = config
        self.settings = config["narration"]
        self.pauses = config["narration"]["pauses"]
        self.guesser = guesser
        self.speech = SpeechRules.from_config(config)
        self.sentences = SentenceRules.from_config(config)
        self.language = book.metadata.language
        self.notes = {
            b.note_id: b for s in book.sections for b in s.blocks if isinstance(b, Footnote)
        }
        self.read_notes: set[str] = set()
        self.title_level = _title_level(book)
        self._passages: list[Passage] = []
        self._words: dict[str | None, dict[str, str]] = {}
        self._section_marked = False  # a chapter marked in a language not the book's

    def has_content(
        self, utterances: list[Utterance], section: Section, read_earlier: set[str]
    ) -> bool:
        """False for a section whose only content was notes already read in earlier sections.

        `read_earlier` must be taken before narrating the section: narrating it marks its
        own notes as read, and those were read here, not elsewhere.
        """
        spoken_blocks = [b for b in section.blocks if not isinstance(b, PageMarker | SceneBreak)]
        all_notes_read = all(
            isinstance(b, Heading) or (isinstance(b, Footnote) and b.note_id in read_earlier)
            for b in spoken_blocks
        )
        has_notes = any(isinstance(b, Footnote) for b in spoken_blocks)
        return not (has_notes and all_notes_read) and any(u.spoken for u in utterances)

    def section(self, section: Section) -> tuple[list[Utterance], list[Passage]]:
        lang = section.lang or self.language
        out = [Utterance("", "", self.pauses["section_start_ms"], lang)]
        self._passages = []
        self._section_marked = bool(section.lang) and not same_language(section.lang, self.language)
        top = _first_heading_level(section)
        if top is None:
            top = self.title_level
            self._add_heading(out, Heading(top, section.title), lang)
        end_notes: list[Footnote] = []
        for block in _heading_first(section.blocks):
            self._block(out, block, lang, end_notes)
        if end_notes:
            self._add_heading(out, Heading(min(top + 1, 6), self.words(lang)["notes"]), lang)
            for note in end_notes:
                self._note(out, note, lang)
        _extend_pause(out, self.pauses["section_end_ms"])
        return out, self._passages

    def words(self, lang: str | None) -> dict[str, str]:
        if lang not in self._words:
            self._words[lang] = narration_words(self.config, lang)
        return self._words[lang]

    def _guessing(self, block: Block) -> bool:
        """Whether the language of `block` is open to guessing: there is a guesser, and
        the book marks no language on the block or its chapter."""
        return (
            self.guesser is not None
            and not self._section_marked
            and not isinstance(block, PageMarker | SceneBreak)
            and not block.lang
        )

    def _guessed(self, block: Block, section_lang: str | None) -> tuple[Block, bool]:
        """`block`, carrying the language its text is guessed to be in if that isn't the
        section's; and whether its sentences are to be guessed one by one as well."""
        if not self._guessing(block) or self.guesser is None:
            return block, False
        text = block_text(block)
        sentences = isinstance(block, Paragraph | Footnote) and self.guesser.checks_sentences(text)
        guess = self.guesser.guess(text)
        if guess is None or same_language(guess, section_lang):
            return block, sentences
        log.info("Guessed that a passage is in %s: %s", guess, _preview(text))
        return replace(block, lang=guess), sentences

    def _block(
        self, out: list[Utterance], block, section_lang: str | None, end_notes: list
    ) -> None:
        if isinstance(block, Footnote):
            mode = self.settings["footnotes"]
            if mode == "end_of_section":
                end_notes.append(block)
            elif mode != "skip" and block.note_id not in self.read_notes:
                self._note(out, block, section_lang)
            return
        block, check = self._guessed(block, section_lang)
        lang = block_lang(block, section_lang)
        match block:
            case Heading():
                self._add_heading(out, block, lang)
            case Paragraph(text=text, note_refs=refs):
                part = self._sentences(out, text, lang, self.pauses["paragraph_ms"], check=check)
                self._passages.append(Passage(block, (part,)))
                if self.settings["footnotes"] == "after_paragraph":
                    for note_id in refs:
                        if note_id in self.notes and note_id not in self.read_notes:
                            self._note(out, self.notes[note_id], section_lang)
            case ListBlock(items=items, ordered=ordered):
                parts = []
                for n, item in enumerate(items, start=1):
                    # The number is said ("1: Buy milk.", which stays one utterance where
                    # "1. Buy milk." would split in two) but not shown: a list shows its own.
                    prefix = f"{n}: " if ordered else ""
                    parts.append(self._sentences(out, _sentence(item), lang, 0, prefix))
                    _extend_pause(out, self.pauses["list_item_ms"])
                _extend_pause(out, self.pauses["paragraph_ms"])
                self._passages.append(Passage(block, tuple(parts)))
            case Table():
                self._table(out, block, lang)
            case Image():
                self._image(out, block, lang)
            case PageMarker(label=label):
                part = range(len(out), len(out))
                if self.settings["announce_pages"]:
                    part = self._sentences(out, f"{self.words(lang)['page']} {label}.", lang, 0)
                    _extend_pause(out, self.pauses["sentence_ms"])
                self._passages.append(Passage(block, (part,)))
            case SceneBreak():
                _extend_pause(out, self.pauses["scene_break_ms"])
                self._passages.append(Passage(block, ()))
            case _:
                raise TypeError(f"not a block: {block!r}")

    def _add_heading(self, out: list[Utterance], heading: Heading, lang: str | None) -> None:
        start = len(out)
        self._heading(out, heading.text, lang)
        self._passages.append(Passage(heading, (range(start, len(out)),)))

    def _heading(self, out: list[Utterance], text: str, lang: str | None) -> None:
        if out[-1].spoken:
            _extend_pause(out, self.pauses["paragraph_ms"])  # a breath before it, too
        spoken = normalize_heading(text, self.speech, lang)
        if not spoken.endswith(_ENDS_CLAUSE):
            spoken += "."  # so the voice closes the heading instead of trailing off
        out.append(Utterance(text, spoken, self.pauses["heading_ms"], lang))

    def _sentences(
        self,
        out: list[Utterance],
        text: str,
        lang: str | None,
        end_ms: int,
        prefix: str = "",
        check: bool = False,
    ) -> range:
        """Add `text` sentence by sentence. `prefix` is said before the first, not shown.
        With `check`, each sentence's language is guessed too."""
        start = len(out)
        for n, sentence in enumerate(split_sentences(text, self.sentences)):
            said = prefix + sentence if n == 0 else sentence
            said_in = self._sentence_lang(sentence, lang) if check else lang
            spoken = normalize_for_speech(said, self.speech, said_in)
            out.append(Utterance(sentence, spoken, self.pauses["sentence_ms"], said_in))
        _extend_pause(out, end_ms)
        return range(start, len(out))

    def _sentence_lang(self, sentence: str, lang: str | None) -> str | None:
        """The sentence's own language, if it is guessed to be another than `lang`."""
        guess = self.guesser.guess(sentence) if self.guesser else None
        if guess is None or same_language(guess, lang):
            return lang
        log.info("Guessed that a sentence is in %s: %s", guess, _preview(sentence))
        return guess

    def _note(self, out: list[Utterance], note: Footnote, section_lang: str | None) -> None:
        self.read_notes.add(note.note_id)
        note, check = self._guessed(note, section_lang)
        lang = block_lang(note, section_lang)
        text = f"{self.words(lang)['note']}: {note.text}"
        part = self._sentences(out, text, lang, self.pauses["paragraph_ms"], check=check)
        self._passages.append(Passage(note, (part,)))

    def _image(self, out: list[Utterance], image: Image, lang: str | None) -> None:
        label = self.words(lang)["image"]
        start = len(out)
        description = caption = range(start, start)
        if image.alt:
            description = self._sentences(out, _sentence(f"{label}: {image.alt}"), lang, 0)
        elif image.caption or (image.alt is None and self.settings["announce_undescribed_images"]):
            description = self._sentences(out, f"{label}.", lang, 0)
        if image.caption:
            caption = self._sentences(out, _sentence(image.caption), lang, 0)
        else:
            caption = range(len(out), len(out))
        _extend_pause(out, self.pauses["paragraph_ms"])
        if len(out) > start:
            self._passages.append(Passage(image, (description, caption)))

    def _table(self, out: list[Utterance], table: Table, lang: str | None) -> None:
        words = self.words(lang)
        intro = f"{words['table']}: {table.caption}" if table.caption else f"{words['table']}."
        start = len(out)
        self._sentences(out, _sentence(intro), lang, self.pauses["sentence_ms"])
        if self.settings["tables"] == "skip":
            self._sentences(out, words["table_skipped"], lang, self.pauses["paragraph_ms"])
            self._passages.append(Passage(table, (range(start, len(out)),)))
            return
        parts = [range(start, len(out))]
        headers = table.rows[0] if table.header_rows else ()
        for n, row in enumerate(table.rows[table.header_rows :], start=1):
            parts.append(self._sentences(out, _row_text(words["row"], n, headers, row), lang, 0))
            _extend_pause(out, self.pauses["list_item_ms"])
        _extend_pause(out, self.pauses["paragraph_ms"])
        self._passages.append(Passage(table, tuple(parts)))


def block_lang(block, section_lang: str | None) -> str | None:
    return getattr(block, "lang", None) or section_lang


def _heading_first(blocks: list[Block]) -> list[Block]:
    """Blocks in reading order, except that page markers before a section's opening heading
    come right after it: a talking book's sections start with their heading."""
    leading = 0
    while leading < len(blocks) and isinstance(blocks[leading], PageMarker):
        leading += 1
    if 0 < leading < len(blocks) and isinstance(blocks[leading], Heading):
        return [blocks[leading], *blocks[:leading], *blocks[leading + 1 :]]
    return blocks


def _first_heading_level(section: Section) -> int | None:
    """The level of the heading a section opens with, or None if it opens with something else."""
    for block in section.blocks:
        if not isinstance(block, PageMarker):
            return block.level if isinstance(block, Heading) else None
    return None


def _title_level(book: Book) -> int:
    """The heading level for a section title Dogear adds: the level most sections open with."""
    levels = Counter(_first_heading_level(s) for s in book.sections)
    levels.pop(None, None)
    return levels.most_common(1)[0][0] if levels else 1


def _join(names: list[str], and_word: str) -> str:
    """'A', 'A and B', or 'A, B and C'."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} {and_word} {names[-1]}"


def _row_text(row_word: str, n: int, headers: tuple[str, ...], row: tuple[str, ...]) -> str:
    cells = []
    for i, cell in enumerate(row):
        if not cell.strip():
            continue
        header = headers[i].strip() if i < len(headers) else ""
        cells.append(f"{header}: {cell}" if header and header != cell else cell)
    return _sentence(f"{row_word} {n}: " + "; ".join(cells))


def _preview(text: str, length: int = 60) -> str:
    """The start of `text`, for the log."""
    text = " ".join(text.split())
    return text if len(text) <= length else text[: length - 3] + "..."


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith(_ENDS_CLAUSE) else text + "."


def _extend_pause(out: list[Utterance], ms: int) -> None:
    """Make the silence after the last utterance at least `ms` long."""
    if out and ms > out[-1].pause_ms:
        out[-1] = replace(out[-1], pause_ms=ms)
