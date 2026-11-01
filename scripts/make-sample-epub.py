"""Make sample EPUB talking books that hold every kind of text Dogear reads, to check with
scripts/check-epub.sh.

    uv run python scripts/make-sample-epub.py OUTPUT_FOLDER

It runs the real `dogear make` pipeline on a small built book with a fake voice that
hums each sentence, so it needs ffmpeg but not the Kokoro voice, and takes seconds. It
saves two books, made with different settings so that between them every choice is
used:

1. pages.epub: print page numbers announced, tables read, notes right after the
   paragraph that refers to them, undescribed images announced, and a title track.
2. quiet.epub: page numbers marked but not announced, tables skipped, notes at the end
   of each section, undescribed images left out, and no title track.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image as Picture

from dogear import make
from dogear.config import Config, default_config
from dogear.document import (
    Book,
    Cover,
    Footnote,
    Heading,
    Image,
    ListBlock,
    Metadata,
    PageMarker,
    Paragraph,
    SceneBreak,
    Section,
    Table,
)
from dogear.make import MakeRequest, make_audiobook
from dogear.voice.engine import SpeechPlan

RATE = 24000

VARIANTS = {
    "pages": {
        "announce_pages": True,
        "tables": "read",
        "footnotes": "after_paragraph",
        "announce_undescribed_images": True,
        "announce_title": True,
    },
    "quiet": {
        "announce_pages": False,
        "tables": "skip",
        "footnotes": "end_of_section",
        "announce_undescribed_images": False,
        "announce_title": False,
    },
}


class HumVoice:
    """A stand-in voice: a tenth of a second of tone per word."""

    sample_rate = RATE

    def plan(self, text: str) -> SpeechPlan:
        return SpeechPlan(key=hashlib.sha256(text.encode()).hexdigest(), payload=text)

    def render(self, plan: SpeechPlan) -> np.ndarray:
        t = np.arange(int(RATE * 0.1 * len(plan.payload.split()))) / RATE
        return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def sample_book(name: str) -> Book:
    """The book for one variant. "pages" has an identifier, so its page numbers name their
    source by it; "quiet" has none, and a table of header cells only, which leaves text
    unread."""
    identifier = "urn:isbn:9780000000002" if name == "pages" else None
    headers_only = [Table(rows=(("North", "South"),), header_rows=1)] if name == "quiet" else []
    preface = Section(
        "Preface",
        kind="front",
        blocks=[
            PageMarker("xi"),
            Heading(2, "Preface"),
            Paragraph("This book is a sample. It uses every kind of text Dogear reads."),
        ],
    )
    chapter = Section(
        "Chapter One",
        blocks=[
            Heading(2, "Chapter One"),
            PageMarker("1"),
            Paragraph("It rained all day. The river rose.", note_refs=("n1",)),
            # A level skipped: level 4 straight under level 2.
            Heading(4, "Things to buy"),
            ListBlock(("Bread.", "Milk. Two bottles."), ordered=True),
            ListBlock(("Rope.", "A lantern."), ordered=False),
            SceneBreak(),
            Heading(3, "The prices"),
            Table(
                rows=(("Item", "Price"), ("Bread", "$2"), ("Milk", "$1.50")),
                header_rows=1,
                caption="What things cost",
            ),
            *headers_only,
            PageMarker("2"),
            Image(alt="A map of the river and the town beside it.", caption="The town."),
            Image(alt=None, caption="A sketch with no description."),
            Image(alt=None),  # no description and no caption
            Image(alt=""),  # decorative
            Paragraph("Il pleut. Le fleuve monte.", lang="fr"),
            Footnote("n1", "The river is the Ouse.", label="1"),
            Paragraph("The end of the chapter.", note_refs=("n2",)),
            Footnote("n2", "Written in 1886.", label="*"),
            PageMarker("3"),  # nothing is said after it in this section
        ],
    )
    appendix = Section(
        "Appendix",
        kind="back",
        blocks=[Heading(2, "Appendix"), Paragraph("A last word.")],
    )
    cover = io.BytesIO()
    Picture.new("RGB", (60, 90), "teal").save(cover, "PNG")
    return Book(
        metadata=Metadata(
            title="A Sample Talking Book",
            authors=["Ann Author", "Bob Writer"],
            language="en",
            identifier=identifier,
            publisher="Dogear Samples",
            cover=Cover(cover.getvalue(), "image/png"),
        ),
        sections=[preface, chapter, appendix],
        source_path=Path("sample.epub"),
        source_format="epub",
    )


def make_sample(name: str, out: Path, cache: Path) -> Path:
    config: Config = default_config()
    config["output"]["formats"] = ["epub"]
    config["render"]["cache_dir"] = str(cache)
    config["narration"].update(VARIANTS[name])
    book = sample_book(name)
    # The two seams the tests use too: the book comes from here, the voice is a hum.
    make.extract_book = lambda path, cfg, report: book
    make.make_voice = lambda cfg, chosen, report: HumVoice()
    folder = out / f".{name}"
    [saved] = make_audiobook(MakeRequest(Path("sample.epub"), folder), config, print)
    target = out / f"{name}.epub"
    shutil.move(saved, target)
    folder.rmdir()
    return target


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("Give one folder to save the sample books in.", file=sys.stderr)
        return 2
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as cache:
        for name in VARIANTS:
            print(f"Saved: {make_sample(name, out, Path(cache))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
