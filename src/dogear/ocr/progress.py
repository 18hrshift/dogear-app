"""Progress while a PDF's pages are read with OCR, as occasional plain sentences: after
the first page this run reads (with an estimate of the time left), every
PROGRESS_EVERY_PAGES pages, and at the end. `dogear ocr` and reading a scanned PDF
automatically report the same way."""

from __future__ import annotations

import time
from collections.abc import Callable

from dogear.book_summary import count
from dogear.render.progress import Report, describe_duration

PROGRESS_EVERY_PAGES = 10
# What the first sentence says about how long OCR takes.
TAKES_A_WHILE = "which takes a while: often a few seconds a page"


class OcrProgress:
    def __init__(
        self,
        total: int,
        done: int,
        report: Report,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.total = total
        self.done = done  # pages read so far, counting any read before this run
        self.report = report
        self._clock = clock
        self._started = clock()
        self._new = 0  # pages read in this run

    def page_done(self) -> None:
        self.done += 1
        self._new += 1
        if self.done >= self.total:
            self.report(f"Finished reading {count(self.total, 'page')} with OCR.")
        elif self._new == 1 or self.done % PROGRESS_EVERY_PAGES == 0:
            self.report(f"Read {self.done:,} of {self.total:,} pages with OCR.{self._time_left()}")

    def _time_left(self) -> str:
        per_page = (self._clock() - self._started) / self._new
        seconds = per_page * (self.total - self.done)
        if seconds < 60:
            return " Less than a minute left."
        return f" About {describe_duration(seconds)} left."
