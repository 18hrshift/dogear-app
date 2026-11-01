"""The books, and the queues that work on them: one reads uploads, one makes books, and
one checks books' words (word_check.py).

Reading has its own queue so a new upload is read straight away, even while a long
book is being made. Each queue runs one child process at a time.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dogear.errors import DogearError
from dogear.web.books import BookRecord, BookStore, Choices
from dogear.web.child import STOP_GRACE_SECONDS, Stopper
from dogear.web.downloads import NotAvailable, zip_folder
from dogear.web.making import make_book
from dogear.web.progress import Progress
from dogear.web.reading import read_book
from dogear.web.settings import WebSettings
from dogear.web.word_check import CheckFunction, WordChecks, check_words
from dogear.web.worker import Worker

# Whatever runs a job: `read_book` and `make_book`, or a stand-in in tests.
JobFunction = Callable[[BookStore, WebSettings, str, Stopper], None]

CAN_CHOOSE = ("ready", "done", "failed", "stopped")
CAN_START_AGAIN = ("failed", "stopped")
CAN_READ_AGAIN = ("unreadable", "read_stopped")
UNEXPECTED = "Dogear stopped because of a problem it didn't expect."
UNEXPECTED_HINTS = {
    "unreadable": "Choose Read again. If it happens again, please report it.",
    "failed": "Choose Start again. If it happens again, please report it.",
}


class Library:
    def __init__(
        self,
        settings: WebSettings,
        read: JobFunction = read_book,
        make: JobFunction = make_book,
        check: CheckFunction = check_words,
    ) -> None:
        self.settings = settings
        self.store = BookStore(settings.books_dir)
        unread = self.store.load()
        self.reader = Worker("reader", self._job(read, "unreadable"))
        self.maker = Worker("maker", self._job(make, "failed"))
        self.words = WordChecks(self.store, settings, check)  # with the saved ones, so after load
        for book_id in unread:
            self._read(book_id)

    def start(self) -> None:
        self.reader.start()
        self.maker.start()
        self.words.start()

    def close(self) -> None:
        """Stop every job and child process. Books being made become "stopped"."""
        timeout = STOP_GRACE_SECONDS + 5
        self.reader.close(timeout)
        self.maker.close(timeout)
        self.words.close(timeout)
        for record in self.store.all():
            if record.state in ("waiting", "making"):
                self.store.update(record.id, state="stopped", error="", hint="")

    def books(self) -> list[BookRecord]:
        return self.store.all()

    def get(self, book_id: str) -> BookRecord | None:
        return self.store.get(book_id)

    def add(self, original_name: str, extension: str, upload: Path) -> BookRecord:
        record = self.store.create(original_name, extension, upload)
        self.reader.add(record.id)  # a new book, with no lines from before
        return record

    def choose(self, book_id: str, choices: Choices) -> None:
        record = self._existing(book_id)
        if record.state not in CAN_CHOOSE:
            raise DogearError("This book is already waiting or being made.")
        self.store.update(book_id, choices=choices, state="waiting", error="", hint="")
        self.maker.add(book_id)

    def start_again(self, book_id: str) -> None:
        record = self._existing(book_id)
        if record.state not in CAN_START_AGAIN or record.choices is None:
            raise DogearError("This book can't be started again now.")
        self.store.update(book_id, state="waiting", error="", hint="")
        self.maker.add(book_id)

    def read_again(self, book_id: str) -> None:
        """Read a book Dogear couldn't read, as after something has been fixed, such as
        installing Calibre for a Kindle book, or after reading it was interrupted or
        stopped. A scanned PDF carries on with the pages it hadn't read with OCR."""
        record = self._existing(book_id)
        if record.state not in CAN_READ_AGAIN:
            raise DogearError("This book can't be read again now.")
        # A read that was just stopped may still be ending: the queue takes the book
        # again only once it has.
        if not self.reader.wait_until_not_running(book_id, STOP_GRACE_SECONDS + 5):
            raise DogearError("Dogear is still stopping the last read of this book.")
        self.store.update(book_id, state="reading", error="", hint="")
        self._read(book_id)

    def stop(self, book_id: str) -> None:
        record = self._existing(book_id)
        if record.state == "reading":
            # Marked first: the job, finding it was asked to stop, leaves the record be.
            self.store.update(book_id, state="read_stopped", error="", hint="")
            self.reader.stop(book_id)
        elif record.state == "waiting" and self.maker.stop(book_id):
            self.store.update(book_id, state="stopped", error="", hint="")
        elif record.state == "making":
            self.maker.stop(book_id)  # the job marks it stopped when the child ends

    def delete(self, book_id: str) -> None:
        """Stop anything working on the book, then remove its folder. If that fails,
        nothing is deleted and the book stays listed."""
        self._existing(book_id)
        for worker in (self.reader, self.maker):
            if worker.stop(book_id):
                worker.wait_until_not_running(book_id, STOP_GRACE_SECONDS + 5)
        self.words.forget(book_id, STOP_GRACE_SECONDS + 5)
        try:
            with self.store.made_lock(book_id):  # not while it's being zipped
                self.store.delete(book_id)
        except OSError as e:
            raise DogearError(
                f"Dogear couldn't delete the book's folder ({e.strerror or e}), so nothing "
                "was deleted.",
                hint="If a file from this book is open in another program, such as a player, "
                "close that program. Then try again.",
            ) from None

    def zip(self, book_id: str, output: Path) -> Path:
        """A zip of an output folder of a finished book. Raises NotAvailable if the book
        isn't finished, which is checked holding the made lock, so a make that started
        meanwhile is never zipped half-way."""
        with self.store.made_lock(book_id):
            record = self.store.get(book_id)
            if record is None or record.state != "done":
                raise NotAvailable(str(output))
            return zip_folder(output, self.folder(book_id), record.run)

    def progress(self, book_id: str) -> Progress:
        return self.store.progress(book_id)

    def ahead(self, book_id: str) -> int | None:
        return self.maker.ahead(book_id)

    def folder(self, book_id: str) -> Path:
        return self.store.folder(book_id)

    def _read(self, book_id: str) -> None:
        """Queue the book to be read. Lines from before are cleared now, so a book waiting
        its turn never shows how an earlier read went."""
        self.store.clear_lines(book_id)
        self.reader.add(book_id)

    def _existing(self, book_id: str) -> BookRecord:
        record = self.store.get(book_id)
        if record is None:
            raise DogearError("There's no such book. It may have been deleted.")
        return record

    def _job(self, run: JobFunction, failed_state: str) -> Callable[[str, Stopper], None]:
        def job(book_id: str, stopper: Stopper) -> None:
            try:
                run(self.store, self.settings, book_id, stopper)
            except Exception:
                # A bug. The book says so, and the worker logs the traceback.
                self.store.update(
                    book_id,
                    state=failed_state,
                    error=UNEXPECTED,
                    hint=UNEXPECTED_HINTS[failed_state],
                )
                raise

        return job
