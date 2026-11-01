"""DAISY books made by the real `dogear make` pipeline from a book that has every kind of block.

The unit tests use these, and so does DAISY validation, which runs outside pytest:

    uv run python tests/daisy_samples.py OUT_DIR
    scripts/check-daisy.sh OUT_DIR/*/*DAISY

The voice is a fake that hums (`fake_voice`), so no voice model is needed; ffmpeg is.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fake_voice import HumEngine

from dogear import make
from dogear.config import Config, default_config
from dogear.document import (
    Book,
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

# Each sample sets [narration] its own way, so between them every setting that shapes a
# talking book is tried both ways.
VARIANTS: dict[str, dict[str, Any]] = {
    "title-track": {
        "announce_title": True,
        "announce_pages": False,
        "tables": "read",
        "footnotes": "after_paragraph",
        "announce_undescribed_images": False,
    },
    "no-title-track": {
        "announce_title": False,
        "announce_pages": True,
        "tables": "skip",
        "footnotes": "end_of_section",
        "announce_undescribed_images": True,
    },
}


def everything_book() -> Book:
    """Headings at several levels (one skipping a level), pages of every kind, lists, a
    table, images with and without descriptions, notes, a scene break, and French."""
    preface = Section(
        "Preface",
        kind="front",
        blocks=[
            PageMarker("iii"),
            Heading(2, "Preface"),
            Paragraph("This book has one of everything. It is short."),
            PageMarker("iv"),
            Paragraph("The preface ends here."),
        ],
    )
    one = Section(
        "Chapter One",
        blocks=[
            PageMarker("1"),
            Heading(2, "Chapter One"),
            Paragraph("It rained all day. The roads flooded.", note_refs=("n1",)),
            Footnote("n1", "The flood of 1903.", label="1"),
            Heading(4, "A Deeper Part"),  # skips level 3; the book's levels close the gap
            Paragraph("We made a list."),
            ListBlock(("Bread.", "Candles."), ordered=True),
            ListBlock(("A map.", "A lamp."), ordered=False),
            PageMarker("2"),
            SceneBreak(),
            Paragraph("Il pleuvait toujours.", lang="fr"),
            Table(
                rows=(("Name", "Age"), ("Ann", "30"), ("Bob", "41")),
                header_rows=1,
                caption="Ages",
            ),
            Image(alt="A flooded street.", caption="Main Street, 1903."),
            Image(alt=None),  # no description given
            Image(alt=""),  # decorative
            Image(alt=None, caption="A caption with no description."),
            Heading(3, "Level Three"),
            Paragraph("Back at level three."),
            PageMarker("2a"),
            Paragraph("A special page.", note_refs=("n2",)),
            Footnote("n2", "A second note, at the end of the chapter.", label="*"),
        ],
    )
    two = Section(
        "Chapter Two",
        blocks=[
            Heading(2, "Chapter Two"),
            Paragraph("A short chapter."),
            PageMarker("3"),  # nothing is said after it in this section
        ],
    )
    appendix = Section(
        "Appendix",
        kind="back",
        blocks=[
            Heading(2, "Appendix"),
            Paragraph("Some last words."),
            PageMarker("4"),
            Paragraph("The very end."),
        ],
    )
    return Book(
        metadata=Metadata(
            title="Every Kind of Block",
            authors=["Ann Author", "Bob Writer"],
            language="en",
            identifier="urn:isbn:9780000000002",
            publisher="Sample Press",
        ),
        sections=[preface, one, two, appendix],
        source_path=Path("everything.epub"),
        source_format="epub",
    )


def sample_config(variant: str, cache: Path) -> Config:
    config = default_config()
    config["output"]["formats"] = ["daisy"]
    config["render"]["cache_dir"] = str(cache)
    config["narration"].update(VARIANTS[variant])
    return config


def make_daisy(
    book: Book, config: Config, out_dir: Path, report: Callable[[str], None] = lambda line: None
) -> Path:
    """The DAISY folder `dogear make` writes for `book`, spoken by the humming voice."""
    with (
        patch.object(make, "extract_book", lambda path, cfg, report: book),
        patch.object(make, "make_voice", lambda cfg, chosen, rep: HumEngine()),
    ):
        request = MakeRequest(book=Path(book.source_path.name), output_dir=out_dir)
        [folder] = make_audiobook(request, config, report)
    return folder


def main(out_dir: Path) -> None:
    with tempfile.TemporaryDirectory() as cache:
        for variant in VARIANTS:
            config = sample_config(variant, Path(cache))
            folder = make_daisy(everything_book(), config, out_dir / variant)
            print(f"Wrote {folder}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: uv run python tests/daisy_samples.py OUT_DIR")
    main(Path(sys.argv[1]))
