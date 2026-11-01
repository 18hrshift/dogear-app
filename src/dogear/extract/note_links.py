"""Label each note with the marker printed where it is referenced, and report broken links.

Extractors record printed markers ("1", "*") as note id -> marker while they read, and
call `link_notes` once the whole book is read, since a note may come before or after
the text that refers to it.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from dogear.document import Footnote, Paragraph, Section

log = logging.getLogger(__name__)


def link_notes(sections: list[Section], note_markers: dict[str, str]) -> None:
    """Label notes with the marker printed where they're referenced, and report problems."""
    found: set[str] = set()
    for section in sections:
        for i, block in enumerate(section.blocks):
            if not isinstance(block, Footnote):
                continue
            if block.note_id in found:
                log.warning(
                    "Two notes share the id %r, so references to it are ambiguous.", block.note_id
                )
            found.add(block.note_id)
            marker = note_markers.get(block.note_id)
            if marker:
                section.blocks[i] = replace(block, label=marker)
    missing = {
        ref
        for section in sections
        for block in section.blocks
        if isinstance(block, Paragraph)
        for ref in block.note_refs
        if ref not in found
    }
    if missing:
        log.warning(
            "%d note references point to notes Dogear didn't find (for example %r), so those "
            "notes won't be read.",
            len(missing),
            sorted(missing)[0],
        )
