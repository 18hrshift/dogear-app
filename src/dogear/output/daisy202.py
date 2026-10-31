"""A DAISY 2.02 talking book: a folder with an NCC, SMIL files, the text, and the audio.

Full text and full audio (`audioFullText`): every heading, print page, and sentence of
the read-along model, each tied to the clip of audio that speaks it. Players move by
heading level and by page from the NCC and highlight the text as it's read.

One job per module: `daisy202_plan` names the files and settles the metadata and times,
`daisy202_text`, `daisy202_smil`, and `daisy202_ncc` write the three kinds of document,
and this module puts them in the folder.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from dogear.config import Config
from dogear.document import Metadata
from dogear.errors import OutputFolderError
from dogear.output import daisy202_xhtml as xhtml
from dogear.output.daisy202_ncc import ncc_document
from dogear.output.daisy202_plan import NCC, plan_book
from dogear.output.daisy202_smil import smil_document, write_smil
from dogear.output.daisy202_text import text_document
from dogear.output.folder_record import claim_folder, finish_folder
from dogear.readalong.model import SectionText
from dogear.voice.engines import ChosenVoice

log = logging.getLogger(__name__)


def build_daisy202(
    sections: list[SectionText],
    metadata: Metadata,
    folder: Path,
    config: Config,
    *,
    today: date | None = None,
    voices: Sequence[ChosenVoice] = (),
) -> None:
    """Write the talking book into `folder`, creating it if needed. `voices` read it,
    the main voice first (none: the configured voice alone), and are named as its narrator.

    `sections` come from `readalong.build_readalong`, in reading order, with their audio
    already encoded by `talking_audio` (each `section.audio.path` is the MP3 to put in
    the book) and the syncs that tie their text to it, in seconds. Files Dogear
    didn't make in `folder` are left alone (see `folder_record`). `today` is the
    book's dc:date; it defaults to today.
    """
    book = plan_book(sections, metadata, config, voices)
    names = book.files()
    _make_folder(folder)
    stale = claim_folder(folder, metadata, names)
    with tempfile.TemporaryDirectory(dir=folder, prefix=".dogear-") as tmp:
        # Everything is written into the work folder first, so a run that fails partway
        # leaves the folder as it was.
        work = Path(tmp)
        for plan in book.sections:
            shutil.copyfile(plan.section.audio.path, work / plan.audio)
            write_smil(smil_document(book, plan), work / plan.smil)
            xhtml.write(text_document(book, plan), work / plan.text)
        xhtml.write(ncc_document(book, today or date.today()), work / NCC)
        # The NCC is what makes a folder a DAISY book to a player. The old one goes first
        # and the new one comes last, so the folder never looks like a finished book
        # while its files are being swapped.
        (folder / NCC).unlink(missing_ok=True)
        for name in names:
            os.replace(work / name, folder / name)
    finish_folder(folder, metadata, names, stale)
    log.info("Wrote a DAISY 2.02 book of %d sections to %s", len(book.sections), folder)


def _make_folder(folder: Path) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        raise OutputFolderError(
            f"{folder} is a file, not a folder.",
            hint="Choose a folder for the DAISY book, or a name that doesn't exist yet.",
        ) from None
