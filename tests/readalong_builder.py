"""Talking-book sections for packager tests: real narration and read-along text, with
made-up timings and a stand-in audio file, so no voice or ffmpeg is needed."""

from __future__ import annotations

from pathlib import Path

from dogear.config import Config
from dogear.document import Book, Metadata, Section
from dogear.language_detection import LanguageGuesser
from dogear.narration import narrate_book, narrate_title
from dogear.output.chapter import ChapterAudio
from dogear.readalong import build_readalong
from dogear.readalong.model import SectionText

# Every utterance lasts 1.337 seconds, so clip times have milliseconds in them.
UTTERANCE_MS = 1337


def metadata(**fields) -> Metadata:
    return Metadata(**{"title": "A Book", "authors": ["Ann Author"], "language": "en", **fields})


def talking_book(
    config: Config,
    folder: Path,
    *sections: Section,
    title_track: bool = True,
    book_metadata: Metadata | None = None,
    guesser: LanguageGuesser | None = None,
) -> tuple[list[SectionText], Metadata]:
    """The read-along sections of a book made of `sections`, and its metadata. Their audio
    is one stand-in .mp3 file in `folder`: the packagers copy it without decoding it."""
    book = Book(book_metadata or metadata(), list(sections), Path("b.epub"), "epub")
    narrations = narrate_book(book, config, guesser)
    if title_track:
        narrations.insert(0, narrate_title(book, config))
    audio = folder / "stand-in.mp3"
    audio.write_bytes(b"stand-in audio")
    chapters = [
        ChapterAudio(
            n.section.title,
            audio,
            tuple(i * UTTERANCE_MS for i in range(len(n.utterances) + 1)),
            1000,
        )
        for n in narrations
    ]
    return build_readalong(narrations, chapters, book.metadata.language), book.metadata
