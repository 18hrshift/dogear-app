"""The text of a talking book, with every spoken piece tied to the audio that speaks it.

EPUB 3 Media Overlays and DAISY 2.02 both pair text documents with SMIL files that say
which stretch of audio speaks which element. This model is that pairing, built once by
`readalong.build`, so every talking-book packager shows the same text with the same ids
and differs only in the markup it writes.

Ids are unique across the whole book and safe as XML ids. Text is display text, as the
reader sees it; for a table row the voice says more ("Row 1: Name: Ann") than the row
shows, so a row is synced as a whole.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dogear.document import SectionKind
from dogear.output.chapter import ChapterAudio

PageKind = Literal["front", "normal", "special"]


@dataclass(frozen=True)
class Clip:
    """A stretch of a section's audio file, in seconds from its start."""

    begin: float
    end: float


@dataclass(frozen=True)
class Sync:
    """One spoken utterance: the element that shows it and the audio that speaks it.

    A section's syncs, in order, cover its audio from the first words to the end with no
    gaps, so playing their clips back to back sounds like the whole track, pauses included.
    No clip is empty: an utterance that came out silent has no sync.
    """

    id: str  # for the SMIL element that pairs the two
    target: str  # id of the element in the section's text
    clip: Clip
    page: bool = False  # an announced page number ("Page 42."), which players may skip


@dataclass(frozen=True)
class Sentence:
    id: str
    text: str
    # Set only when it differs from the language of the item it's in: a sentence found
    # to be in another language than the rest of its paragraph.
    lang: str | None = None


@dataclass(frozen=True)
class HeadingText:
    id: str
    level: int  # 1 to 6, never more than one deeper than the heading before it in the book
    text: str
    # Index of its sync in the section: its own, or the next one if it came out silent.
    sync: int
    lang: str | None = None  # set only when it differs from the section's language


@dataclass(frozen=True)
class ParagraphText:
    sentences: tuple[Sentence, ...]
    lang: str | None = None


@dataclass(frozen=True)
class ListText:
    items: tuple[tuple[Sentence, ...], ...]
    ordered: bool
    lang: str | None = None


@dataclass(frozen=True)
class RowText:
    id: str | None  # None when the row wasn't read aloud (tables skipped, or a header row)
    cells: tuple[str, ...]


@dataclass(frozen=True)
class TableText:
    caption: tuple[Sentence, ...]  # what was said to introduce it: "Table: Prices."
    header_rows: tuple[RowText, ...]
    rows: tuple[RowText, ...]
    lang: str | None = None


@dataclass(frozen=True)
class ImageText:
    """An image's description and caption. Dogear keeps no image data, only these words."""

    description: tuple[Sentence, ...]
    caption: tuple[Sentence, ...]
    lang: str | None = None


@dataclass(frozen=True)
class NoteText:
    label: str  # the marker as printed, such as "1" or "*"; may be empty
    sentences: tuple[Sentence, ...]  # the first starts with the word for "Note"
    lang: str | None = None


@dataclass(frozen=True)
class PageText:
    """Where a print page begins."""

    id: str
    label: str  # as printed: "42", "xii"
    kind: PageKind  # front (roman numerals), normal (arabic numbers), or special
    # The first sync at or after the page break in this section; None if nothing in the
    # section is said after it.
    sync: int | None


@dataclass(frozen=True)
class BreakText:
    """A scene break."""


TextItem = (
    HeadingText | ParagraphText | ListText | TableText | ImageText | NoteText | PageText | BreakText
)


@dataclass(frozen=True)
class SectionText:
    """One section of the talking book: its text, its syncs, and its audio."""

    number: int  # as `dogear outline` shows it; 0 for the title track
    title: str
    kind: SectionKind
    lang: str | None  # the section's language, or the book's
    items: tuple[TextItem, ...]
    syncs: tuple[Sync, ...]
    audio: ChapterAudio

    @property
    def duration(self) -> float:
        """Seconds of audio the syncs cover: from the first words to the end of the file."""
        return self.syncs[-1].clip.end - self.syncs[0].clip.begin if self.syncs else 0.0

    def headings(self) -> list[HeadingText]:
        return [item for item in self.items if isinstance(item, HeadingText)]

    def pages(self) -> list[PageText]:
        return [item for item in self.items if isinstance(item, PageText)]
