"""What a DAISY 2.02 book will hold, settled before any file is written.

Each section of the talking book becomes three files with one name: its SMIL file, its
text document, and its MP3 (`s001.smil`, `s001.html`, `s001.mp3`), next to one
`ncc.html`. Names are short, lowercase ASCII, and in reading order, the safest choice for
hardware players and the file systems they read.

The book also gets its identity here: the metadata the NCC declares, with an honest value
for each field the spec makes mandatory even when the source book doesn't say.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dogear import __version__
from dogear.config import Config
from dogear.document import Metadata
from dogear.output.book_identity import book_uid, narrator
from dogear.output.daisy202_clock import clip_bounds
from dogear.output.daisy202_xhtml import language_tag
from dogear.readalong.model import HeadingText, SectionText
from dogear.voice.engines import ChosenVoice

NCC = "ncc.html"

# DAISY 2.02 makes dc:creator and dc:publisher mandatory; when the book names none, the
# NCC says so rather than inventing one.
UNKNOWN = "Unknown"
# A book with no language tag is read as English, unless the main voice speaks another
# language, and then `make.settle_language` gives the book that voice's language.
SPOKEN_LANGUAGE = "en"


@dataclass(frozen=True)
class SectionPlan:
    section: SectionText
    smil: str
    text: str
    audio: str
    bounds: tuple[int, ...]  # clip boundaries in ms, from `clip_bounds`
    elapsed: int  # ms of the book's audio before this section
    language: str
    opening: HeadingText  # the heading the section opens with, where its SMIL starts

    @property
    def duration(self) -> int:
        """Milliseconds the section's clips add up to."""
        return self.bounds[-1] - self.bounds[0]


@dataclass(frozen=True)
class BookPlan:
    identifier: str
    title: str
    creators: tuple[str, ...]
    publisher: str
    source: str | None  # the source book's own identifier, when it has one
    language: str
    narrator: str
    generator: str
    sections: tuple[SectionPlan, ...]

    @property
    def total(self) -> int:
        """Milliseconds of audio in the whole book."""
        return sum(s.duration for s in self.sections)

    def files(self) -> list[str]:
        """Every file the book is made of, the NCC last."""
        return [name for s in self.sections for name in (s.smil, s.text, s.audio)] + [NCC]


def plan_book(
    sections: list[SectionText],
    metadata: Metadata,
    config: Config,
    voices: Sequence[ChosenVoice] = (),
) -> BookPlan:
    if not sections:
        raise ValueError("A talking book needs at least one section.")
    language = language_tag(metadata.language, "the book") or SPOKEN_LANGUAGE
    width = max(3, len(str(len(sections))))
    plans, elapsed = [], 0
    for position, section in enumerate(sections, start=1):
        stem = f"s{position:0{width}d}"
        bounds = clip_bounds(section)
        plans.append(
            SectionPlan(
                section=section,
                smil=f"{stem}.smil",
                text=f"{stem}.html",
                audio=f"{stem}.mp3",
                bounds=bounds,
                elapsed=elapsed,
                language=language_tag(section.lang, f"section {section.number}") or language,
                opening=_opening(section),
            )
        )
        elapsed += bounds[-1] - bounds[0]
    if plans[0].opening.level != 1:
        raise ValueError("A DAISY book's first heading is its title, so it must be level 1.")
    return BookPlan(
        identifier=book_uid(metadata),
        title=metadata.title,
        creators=tuple(a for a in metadata.authors if a.strip()) or (UNKNOWN,),
        publisher=(metadata.publisher or "").strip() or UNKNOWN,
        source=(metadata.identifier or "").strip() or None,
        language=language,
        narrator=narrator(config, voices),
        generator=f"Dogear {__version__}",
        sections=tuple(plans),
    )


def _opening(section: SectionText) -> HeadingText:
    """The heading a section opens with. Each SMIL file must start on a heading, and the
    read-along model always ties a section's first words to its opening heading."""
    first = section.items[0] if section.items else None
    if not isinstance(first, HeadingText) or first.sync != 0:
        raise ValueError(
            f"Section {section.number} doesn't open with a heading tied to its first words."
        )
    return first
