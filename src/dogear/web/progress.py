"""A book's progress lines while it is read or made, kept apart from its record.

`dogear make` prints a line or two per section, `dogear outline` a line every few pages
while it reads a scanned PDF with OCR, and some books add a warning per element (a note
without an id, a word with no pronunciation): thousands of lines. So:

- a line that repeats the one before it adds to that line's count ("... (412 times)"),
- only the latest MAX_KEPT lines are kept, and the page says how many came before them,
- the lines are saved in the book's folder (`progress.json`) at most every
  SAVE_EVERY_SECONDS and when reading or making ends, not with every line. A line that
  arrives between saves is lost only if the server itself dies, and a save that fails (a
  full disk) is logged and doesn't stop the book.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dogear.web.atomic import remove_leftovers, write_atomically
from dogear.web.child import MAX_LINE_CHARS

log = logging.getLogger(__name__)

LINES_FILE = "progress.json"
MAX_KEPT = 100  # lines kept and shown on the book's page
SAVE_EVERY_SECONDS = 2.0
# How `dogear`'s log messages start (cli.py's logging format). They aren't progress, so
# the status sentence that a screen reader announces never repeats them.
LOG_PREFIXES = ("DEBUG: ", "INFO: ", "WARNING: ", "ERROR: ", "CRITICAL: ")


@dataclass(frozen=True)
class Line:
    text: str
    times: int = 1  # how many times in a row it came

    @property
    def shown(self) -> str:
        return self.text if self.times == 1 else f"{self.text} ({self.times:,} times)"


@dataclass(frozen=True)
class Progress:
    left_out: int  # lines that came before the first one here
    lines: tuple[Line, ...]
    latest: str = ""  # the latest line that isn't a log message: "Section 3 of 9 done..."

    def last(self, count: int) -> Progress:
        """Only the last `count` lines."""
        kept = self.lines[-count:] if count else ()
        return Progress(self.left_out + len(self.lines) - len(kept), kept, self.latest)


class ProgressLog:
    """One book's lines. Thread-safe: the job making the book adds them, pages read them."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._lines: deque[Line] = deque()
        self._left_out = 0
        self._latest = ""
        self._changed = False
        self._saved_at = -math.inf
        self._failed = False  # a save failed; said once in the log

    @classmethod
    def load(cls, folder: Path, clock: Callable[[], float] = time.monotonic) -> ProgressLog:
        """The lines saved in a book's folder, if any."""
        progress = cls(folder / LINES_FILE, clock)
        remove_leftovers(folder, LINES_FILE)
        try:
            data = json.loads(progress._path.read_text(encoding="utf-8"))
            lines = [
                Line(str(text)[:MAX_LINE_CHARS], max(1, int(times)))
                for text, times in data["lines"]
            ]
            progress._left_out = max(0, int(data["left_out"])) + max(0, len(lines) - MAX_KEPT)
            progress._lines.extend(lines[-MAX_KEPT:])
            progress._latest = str(data["latest"])[:MAX_LINE_CHARS]
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError) as e:
            log.warning("Ignoring the saved progress in %s: it can't be read (%s).", folder, e)
        return progress

    def add(self, text: str) -> None:
        with self._lock:
            if self._lines and self._lines[-1].text == text:
                self._lines[-1] = Line(text, self._lines[-1].times + 1)
            else:
                self._lines.append(Line(text))
                if len(self._lines) > MAX_KEPT:
                    self._lines.popleft()
                    self._left_out += 1
            if not text.startswith(LOG_PREFIXES):
                self._latest = text
            self._changed = True
            due = self._clock() - self._saved_at >= SAVE_EVERY_SECONDS
        if due:
            self.save()

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()
            self._left_out = 0
            self._latest = ""
            self._changed = True
        self.save()

    def view(self) -> Progress:
        with self._lock:
            return Progress(self._left_out, tuple(self._lines), self._latest)

    def save(self) -> None:
        """Save the lines if they changed since the last save."""
        with self._save_lock:  # one save at a time, so an older one never lands last
            with self._lock:
                if not self._changed:
                    return
                self._changed = False
                self._saved_at = self._clock()
                data = {
                    "left_out": self._left_out,
                    "lines": [[line.text, line.times] for line in self._lines],
                    "latest": self._latest,
                }
            try:
                write_atomically(self._path, json.dumps(data, indent=1).encode("utf-8"))
            except OSError as e:
                with self._lock:
                    self._changed = True  # tried again with the next save
                if not self._failed:
                    self._failed = True
                    log.warning("Couldn't save the progress lines in %s: %s", self._path.parent, e)


# What the page says about the lines it doesn't show. progress.js fills in {n} too.
LEFT_OUT_ONE = "1 earlier line isn't shown."
LEFT_OUT_MANY = "{n} earlier lines aren't shown."


def left_out_sentence(count: int) -> str:
    return LEFT_OUT_ONE if count == 1 else LEFT_OUT_MANY.replace("{n}", f"{count:,}")
