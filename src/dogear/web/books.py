"""Each book's record: the file that came in, what was chosen, and how making it went.

Every book has its own folder under the web folder's `books`, named by a random id. It
holds `book.json` (the record), the uploaded file (`book.epub` or similar), `made` (what
was made from it), `zips` (folders zipped for download, by make run),
`progress.json` (the lines of its latest read or make, progress.py), and `word-check.json`
(its latest word check, word_check.py). The record is saved on
every change, so a restarted server lists every book. One that was waiting or being
made when the server stopped shows as stopped.

Records are immutable: a change makes a new one, so pages share them without copying.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from dogear.book_summary import BookSummary, summary_from_json, summary_to_json
from dogear.extract import EXTRACTORS
from dogear.make import FORMATS
from dogear.web.atomic import remove_leftovers, write_atomically
from dogear.web.private import PRIVATE_FOLDER, private_folder
from dogear.web.progress import LINES_FILE, Progress, ProgressLog

log = logging.getLogger(__name__)

State = Literal[
    "reading",  # finding its sections with `dogear outline`
    "unreadable",  # Dogear couldn't read it
    "read_stopped",  # reading it was stopped by the user, to carry on with Read again
    "ready",  # read, and waiting for choices
    "waiting",  # in the queue to be made
    "making",
    "done",
    "failed",
    "stopped",  # by the user, or by the server stopping
]
STATES: tuple[State, ...] = (
    "reading",
    "unreadable",
    "read_stopped",
    "ready",
    "waiting",
    "making",
    "done",
    "failed",
    "stopped",
)
RECORD = "book.json"
MADE = "made"
ZIPS = "zips"
TRASH_PREFIX = "deleted-"  # a deleted book's folder, moved aside until its files are gone
MAX_NAME_CHARS = 255  # of the uploaded file's name, as shown
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{16}")
RUN_PATTERN = re.compile(r"[0-9a-f]{0,32}")

CLOSED_ERROR = "Dogear was closed before this book was finished."
CLOSED_HINT = "Choose Start again to carry on. What was already spoken is kept."


@dataclass(frozen=True)
class Choices:
    formats: tuple[str, ...]  # in make.FORMATS order
    voice: str
    speed: float
    sections: tuple[int, ...]  # section numbers, from 1
    as_english: bool = False  # read as English, whatever language the book says it's in


@dataclass(frozen=True)
class Output:
    format: str  # one of make.FORMATS
    path: str  # inside the book's folder, such as "made/The Title.m4b"


@dataclass(frozen=True)
class BookRecord:
    id: str
    original_name: str  # the uploaded file's name: shown, never used as a path
    extension: str  # ".epub", ".pdf", ...
    added: float  # when it was uploaded, in seconds since 1970
    state: State
    summary: BookSummary | None = None
    choices: Choices | None = None
    error: str = ""
    hint: str = ""
    outputs: tuple[Output, ...] = ()
    run: str = ""  # names the latest make, whose outputs these are

    def __post_init__(self) -> None:
        object.__setattr__(self, "outputs", tuple(self.outputs))  # however it was given

    @property
    def title(self) -> str:
        return self.summary.title if self.summary else self.original_name

    @property
    def source_name(self) -> str:
        return "book" + self.extension


class BookStore:
    """Every book's record, kept in memory and saved to its folder. Thread-safe: pages
    and the workers that read and make books all use it."""

    def __init__(self, books_dir: Path) -> None:
        self.dir = books_dir
        self._lock = threading.RLock()
        self._books: dict[str, BookRecord] = {}
        self._progress: dict[str, ProgressLog] = {}
        self._made_locks: dict[str, threading.Lock] = {}

    def load(self) -> list[str]:
        """Read every saved record. Returns the ids of books that were still being read,
        which need reading again; books that were waiting or being made become stopped."""
        private_folder(self.dir)
        unread = []
        with self._lock:
            for folder in sorted(self.dir.iterdir()):
                if folder.name.startswith(TRASH_PREFIX):
                    _remove_trash(folder)  # a deleted book's files, left by a failed delete
                    continue
                record = _read_record(folder)
                if record is None:
                    continue
                remove_leftovers(folder, RECORD)
                self._progress[record.id] = ProgressLog.load(folder)
                if record.state in ("waiting", "making"):
                    record = replace(record, state="stopped", error=CLOSED_ERROR, hint=CLOSED_HINT)
                    self._books[record.id] = record
                    self._save(record)
                else:
                    self._books[record.id] = record
                if record.state == "reading":
                    unread.append(record.id)
        return unread

    def create(self, original_name: str, extension: str, upload: Path) -> BookRecord:
        """A new book, in state "reading", with `upload` moved into its folder. Raises
        OSError, keeping nothing, if it can't be saved."""
        with self._lock:
            book_id = secrets.token_urlsafe(12)
            while book_id in self._books or not ID_PATTERN.fullmatch(book_id):
                book_id = secrets.token_urlsafe(12)
            record = BookRecord(
                id=book_id,
                original_name=original_name[:MAX_NAME_CHARS],
                extension=extension,
                added=time.time(),
                state="reading",
            )
            folder = self.folder(book_id)
            try:
                folder.mkdir(mode=PRIVATE_FOLDER)
                os.replace(upload, folder / record.source_name)
                self._save(record)
            except OSError:
                # A full disk, say: no half-made book is left behind or listed.
                shutil.rmtree(folder, ignore_errors=True)
                raise
            self._books[book_id] = record
            self._progress[book_id] = ProgressLog(folder / LINES_FILE)
            return record

    def get(self, book_id: str) -> BookRecord | None:
        with self._lock:
            return self._books.get(book_id)

    def all(self) -> list[BookRecord]:
        """Every book, newest first."""
        with self._lock:
            records = list(self._books.values())
        return sorted(records, key=lambda r: r.added, reverse=True)

    def update(self, book_id: str, **changes: Any) -> BookRecord | None:
        """Change and save a record. None if the book was deleted in the meantime."""
        with self._lock:
            record = self._books.get(book_id)
            if record is None:
                return None
            record = replace(record, **changes)
            self._books[book_id] = record
            self._save(record)
            return record

    def made_lock(self, book_id: str) -> threading.Lock:
        """Held while the book's `made` folder is zipped, and while a make starts (and
        clears what an earlier make left), so a zip never mixes two makes."""
        with self._lock:
            return self._made_locks.setdefault(book_id, threading.Lock())

    def progress(self, book_id: str) -> Progress:
        """The lines of the book's latest read or make (none for a book that isn't listed)."""
        log = self._progress_log(book_id)
        return log.view() if log else Progress(0, ())

    def add_line(self, book_id: str, line: str) -> None:
        if log := self._progress_log(book_id):
            log.add(line)

    def clear_lines(self, book_id: str) -> None:
        if log := self._progress_log(book_id):
            log.clear()

    def save_lines(self, book_id: str) -> None:
        if log := self._progress_log(book_id):
            log.save()

    def delete(self, book_id: str) -> None:
        """Delete the book's folder and forget the book. The folder is first moved aside
        in one step (a rename), so a delete either works or leaves the book whole and
        listed, to try again: Windows won't rename a folder holding a file another
        program has open. That raises OSError. If deleting what was moved aside fails,
        the next start tries again."""
        with self._lock:
            trash = self.dir / f"{TRASH_PREFIX}{book_id}-{secrets.token_hex(4)}"
            os.rename(self.folder(book_id), trash)
            self._books.pop(book_id, None)
            self._progress.pop(book_id, None)
            self._made_locks.pop(book_id, None)
        _remove_trash(trash)

    def folder(self, book_id: str) -> Path:
        if not ID_PATTERN.fullmatch(book_id):
            raise ValueError(f"not a book id: {book_id!r}")
        return self.dir / book_id

    def _progress_log(self, book_id: str) -> ProgressLog | None:
        with self._lock:
            return self._progress.get(book_id)

    def _save(self, record: BookRecord) -> None:
        data = json.dumps(_to_json(record), indent=1).encode("utf-8")
        write_atomically(self.folder(record.id) / RECORD, data)


def _remove_trash(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)
    if folder.exists():
        log.warning(
            "Couldn't delete everything in %s, which held a deleted book. Dogear tries "
            "again when it next starts.",
            folder,
        )


def _to_json(record: BookRecord) -> dict[str, Any]:
    data = asdict(record)
    data["summary"] = summary_to_json(record.summary) if record.summary else None
    return data


def _read_record(folder: Path) -> BookRecord | None:
    path = folder / RECORD
    if not ID_PATTERN.fullmatch(folder.name) or not path.is_file():
        return None
    try:
        return _from_json(json.loads(path.read_text(encoding="utf-8")), folder.name)
    except (OSError, ValueError, TypeError, KeyError) as e:
        log.warning("Skipping the book in %s: its record can't be read (%s).", folder, e)
        return None


def _from_json(data: dict[str, Any], book_id: str) -> BookRecord:
    if data["id"] != book_id or data["state"] not in STATES:
        raise ValueError("the record doesn't match its folder")
    if data["extension"] not in EXTRACTORS:
        raise ValueError(f"{data['extension']!r} isn't a kind of book Dogear reads")
    run = str(data.get("run", ""))
    if not RUN_PATTERN.fullmatch(run):
        raise ValueError(f"{run!r} isn't a make run Dogear names")
    choices = data.get("choices")
    if choices is not None:
        choices = Choices(
            formats=tuple(f for f in choices["formats"] if f in FORMATS),
            voice=str(choices["voice"]),
            speed=float(choices["speed"]),
            sections=tuple(int(n) for n in choices["sections"]),
            as_english=bool(choices.get("as_english", False)),
        )
    return BookRecord(
        id=book_id,
        original_name=str(data["original_name"])[:MAX_NAME_CHARS],
        extension=str(data["extension"]),
        added=float(data["added"]),
        state=data["state"],
        summary=summary_from_json(data["summary"]) if data.get("summary") else None,
        choices=choices,
        error=str(data.get("error", "")),
        hint=str(data.get("hint", "")),
        outputs=tuple(
            Output(format=str(o["format"]), path=str(o["path"])) for o in data.get("outputs", [])
        ),
        run=run,
    )
