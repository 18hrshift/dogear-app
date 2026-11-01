"""Reading an uploaded book: `dogear outline --json` in a child process, checked here.

The child's JSON is checked field by field before anything uses it, and bounded: a book
can't make the server keep more than MAX_OUTLINE_BYTES of it, or list more than
MAX_SECTIONS sections on a page.

Reading a scanned PDF means reading it with OCR first, which can take many minutes. The
child reports how that goes, and its lines are kept as the book's progress lines
(progress.py), as making keeps them, for the book's page to show.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from dogear.book_summary import BookSummary, summary_from_json
from dogear.web.books import BookStore
from dogear.web.child import Stopper, child_command, interrupted, run_captured
from dogear.web.settings import WebSettings

MAX_OUTLINE_BYTES = 4 * 1024 * 1024
MAX_SECTIONS = 3000
MAX_TITLE_CHARS = 300  # longer titles are cut for the page
INTERRUPTED_ERROR = "Something stopped Dogear while it was reading this book."
READ_AGAIN_HINT = "Choose Read again."
# How the lines start that say why a `dogear` command failed. They are the error the
# page shows, not progress.
PROBLEM_PREFIXES = ("Error: ", "What to do: ")


@dataclass(frozen=True)
class Problem:
    error: str
    hint: str


def read_book(store: BookStore, settings: WebSettings, book_id: str, stopper: Stopper) -> None:
    """Find the book's sections. Leaves it "ready", or "unreadable" with the reason."""
    record = store.get(book_id)
    if record is None:
        return
    source = store.folder(book_id) / record.source_name
    command = child_command(["outline", str(source), "--json"], settings.config_file)

    def heard(line: str) -> None:
        if stopper.requested.is_set():
            return  # what's left is `dogear`'s goodbye, written for a terminal
        if line.strip() and not line.startswith(PROBLEM_PREFIXES):
            store.add_line(book_id, line)  # a full disk is logged, and reading goes on

    store.clear_lines(book_id)
    try:
        result = run_captured(
            command,
            timeout=settings.read_timeout_seconds,
            max_stdout=MAX_OUTLINE_BYTES,
            stopper=stopper,
            on_line=heard,
        )
    finally:
        store.save_lines(book_id)
    if stopper.requested.is_set():
        # Stopped from its page, and Library.stop has said so in its record. Or it's
        # being deleted, or the server is stopping: then it stays "reading", and the
        # server reads it again when it next starts.
        return
    if result.timed_out:
        outcome: BookSummary | Problem = Problem(
            f"Dogear went {settings.read_timeout_seconds:g} seconds without any progress "
            "reading this book, so it stopped.",
            "Choose Read again. If this keeps happening, raise web.read_timeout_seconds in "
            "your config file.",
        )
    elif interrupted(result.returncode):
        # Stopped by something other than the server: not a problem with the book.
        outcome = Problem(INTERRUPTED_ERROR, READ_AGAIN_HINT)
    elif result.returncode != 0:
        outcome = problem_from_output(result.stderr)
    elif result.too_long:
        outcome = _too_big()
    else:
        outcome = parse_outline(result.stdout)
    if isinstance(outcome, Problem):
        store.update(book_id, state="unreadable", error=outcome.error, hint=outcome.hint)
    else:
        store.update(book_id, state="ready", summary=outcome, error="", hint="")


def parse_outline(data: bytes) -> BookSummary | Problem:
    try:
        summary = summary_from_json(json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, ValueError) as e:
        return Problem(
            f"Dogear couldn't understand the list of this book's sections ({e}).",
            "This is a bug in Dogear. Please report it, with the kind of book you used.",
        )
    if len(summary.sections) > MAX_SECTIONS:
        return _too_big()
    if not summary.sections:
        return Problem(
            "Dogear found no sections to read in this book.",
            "Check that the file opens in a reading app and has text, not only pictures.",
        )
    return _shortened(summary)


def problem_from_output(output: str) -> Problem:
    """The `Error:` and `What to do:` lines a failed `dogear` command printed."""
    error = hint = ""
    error_prefix, hint_prefix = PROBLEM_PREFIXES
    for line in output.splitlines():
        if line.startswith(error_prefix):
            error = line.removeprefix(error_prefix)
        elif line.startswith(hint_prefix):
            hint = line.removeprefix(hint_prefix)
    if not error:
        return Problem(
            "Dogear stopped because of a problem it didn't expect.",
            "This is a bug in Dogear. Please report it, with the kind of book you used.",
        )
    return Problem(error, hint)


def _too_big() -> Problem:
    return Problem(
        f"This book has more sections than the web page can list (over {MAX_SECTIONS:,}).",
        "Make it with the dogear make command in a terminal instead.",
    )


def _shortened(summary: BookSummary) -> BookSummary:
    return replace(
        summary,
        title=_cut(summary.title),
        authors=tuple(_cut(a) for a in summary.authors[:20]),
        sections=tuple(replace(s, title=_cut(s.title)) for s in summary.sections),
    )


def _cut(text: str) -> str:
    return text if len(text) <= MAX_TITLE_CHARS else text[: MAX_TITLE_CHARS - 3] + "..."
