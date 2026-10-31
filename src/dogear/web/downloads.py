"""What a finished book offers for download: each output's file, or a zip of its folder.

Every path is checked to be inside the book's `made` folder before it is read or sent.
Folders (MP3 and DAISY) are zipped on first request without compression (their audio
is compressed already) and the zip is kept for later downloads, in a folder named for
the make it came from, so a zip of an earlier make is never sent for a later one.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from dogear.output.folder_record import RECORD_NAME
from dogear.sizes import describe_size
from dogear.web.books import MADE, ZIPS, BookRecord, Output
from dogear.web.choices import FORMAT_INFO

MEDIA_TYPES = {".m4b": "audio/mp4", ".epub": "application/epub+zip", ".zip": "application/zip"}


class NotAvailable(Exception):
    """The output is missing, or isn't where it should be."""


@dataclass(frozen=True)
class Download:
    number: int  # its place in the book's outputs, used in its link
    format_name: str
    file_name: str  # what the browser saves it as
    size: str  # in words: "116 MB"
    folder: bool  # downloaded as a zip
    available: bool  # False if it has been moved or deleted since it was made


def downloads(record: BookRecord, book_folder: Path) -> list[Download]:
    found = []
    for number, output in enumerate(record.outputs):
        format_name = FORMAT_INFO[output.format].title
        try:
            path = output_path(output, book_folder)
        except NotAvailable:
            name = Path(output.path).name
            found.append(Download(number, format_name, name, "", False, available=False))
            continue
        folder = path.is_dir()
        found.append(
            Download(
                number=number,
                format_name=format_name,
                file_name=path.name + (".zip" if folder else ""),
                size=describe_size(_size(path)),
                folder=folder,
                available=True,
            )
        )
    return found


def output_path(output: Output, book_folder: Path) -> Path:
    """The output's file or folder, if it exists inside the book's `made` folder."""
    made = (book_folder / MADE).resolve()
    path = (book_folder / output.path).resolve()
    if not path.is_relative_to(made) or path == made or not path.exists():
        raise NotAvailable(output.path)
    return path


def zip_folder(folder: Path, book_folder: Path, run: str) -> Path:
    """A zip of `folder` (an output folder of the make `run`), made once and kept in the
    book's `zips`. The caller holds the book's made lock (books.BookStore.made_lock) and
    has checked, holding it, that the book is done."""
    target = book_folder / ZIPS / run / f"{folder.name}.zip"
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, partial = tempfile.mkstemp(dir=target.parent, suffix=".partial")
    made = folder.resolve()
    try:
        with os.fdopen(descriptor, "wb") as file:
            with zipfile.ZipFile(file, "w", compression=zipfile.ZIP_STORED) as archive:
                for path in sorted(folder.rglob("*")):
                    # Only real files inside the folder: links are left out, and so is
                    # Dogear's own record of the folder (output/folder_record.py).
                    if path.is_symlink() or not path.is_file() or path.name == RECORD_NAME:
                        continue
                    if not path.resolve().is_relative_to(made):
                        continue
                    archive.write(path, Path(folder.name) / path.relative_to(folder))
        os.replace(partial, target)
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise
    return target


def media_type(path: Path) -> str:
    return MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())
