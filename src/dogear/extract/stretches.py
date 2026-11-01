"""Turn a book cut into stretches (runs of consecutive blocks) into sections.

EPUB cuts its files where the table of contents points. Formats with no table of
contents (HTML, Markdown, Word) cut at their top-level headings instead: `section_level`
says which level that is, and `sections_from` turns the stretches into Sections.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from dogear.document import Block, Heading, PageMarker, Section, SectionKind


@dataclass
class Stretch:
    blocks: list[Block]
    kind: SectionKind
    source: str  # where it came from, for Section.source


def section_level(levels: Iterable[int]) -> int | None:
    """The heading level that starts sections, or None when there are no headings.

    It is the highest level (the smallest number) used more than once, else the highest
    level used. So a book whose one level-1 heading is its title, over level-2 chapters,
    gets a section per chapter, with the title in a section of its own before them.
    """
    used: dict[int, int] = {}
    for level in levels:
        used[level] = used.get(level, 0) + 1
    if not used:
        return None
    repeated = [level for level, count in used.items() if count > 1]
    return min(repeated or used)


def sections_from(stretches: list[Stretch], *, lang: str | None, lone_title: str) -> list[Section]:
    """A Section for each stretch with content, titled by its first heading.

    A stretch with no heading is titled `lone_title` when it is the book's only section,
    else "Section N". Page markers that end a stretch move to the start of the next.
    """
    carry_page_markers([s.blocks for s in stretches])
    kept = [s for s in stretches if s.blocks]
    sections = []
    for number, stretch in enumerate(kept, start=1):
        heading = next((b.text for b in stretch.blocks if isinstance(b, Heading)), "")
        fallback = lone_title if len(kept) == 1 else f"Section {number}"
        sections.append(
            Section(
                title=" ".join(heading.split()) or fallback,
                kind=stretch.kind,
                blocks=stretch.blocks,
                lang=lang,
                source=stretch.source,
            )
        )
    return sections


def carry_page_markers(stretches: Sequence[list[Block]]) -> None:
    """Move page markers that end a stretch to the start of the next one, in place.

    A page that begins just before a cut begins with the next stretch's first words.
    """
    for before, after in itertools.pairwise(stretches):
        if all(isinstance(b, PageMarker) for b in after):
            continue  # nothing there for the page to begin with
        count = 0
        while count < len(before) and isinstance(before[-1 - count], PageMarker):
            count += 1
        if count:
            after[:0] = before[-count:]
            del before[-count:]
