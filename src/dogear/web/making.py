"""Making a book: `dogear make` in a child process, with its progress lines kept as they
come (progress.py). Its `Saved:` lines say what it made; `Error:` and `What to do:` say
why it failed. It uses the page's own pronunciation file (page_words.py) too."""

from __future__ import annotations

import secrets
import shutil
from pathlib import Path

from dogear.web.books import MADE, ZIPS, BookRecord, BookStore, Output
from dogear.web.child import Stopper, child_command, interrupted, read_lines, start_lines
from dogear.web.page_words import words_option
from dogear.web.reading import PROBLEM_PREFIXES, problem_from_output
from dogear.web.settings import WebSettings


def make_book(store: BookStore, settings: WebSettings, book_id: str, stopper: Stopper) -> None:
    """Make the chosen outputs. Leaves the book "done", "failed", or "stopped"."""
    record = store.get(book_id)
    if record is None or record.choices is None:
        return
    folder = store.folder(book_id)
    with store.made_lock(book_id):
        # Not while a zip of the last make's outputs is being made: they're about to be
        # made again. And no zip is made from now until this make is done (Library.zip).
        store.clear_lines(book_id)
        store.update(
            book_id, state="making", run=secrets.token_hex(8), error="", hint="", outputs=()
        )
        shutil.rmtree(folder / ZIPS, ignore_errors=True)  # zips of an earlier make
    command = child_command(
        [*make_args(record, folder), *words_option(settings.words_file)], settings.config_file
    )
    try:
        child = start_lines(command)
    except OSError as e:
        store.update(
            book_id,
            state="failed",
            error=f"Dogear couldn't start making the book: {e}.",
            hint="Check that Dogear is installed properly, then choose Start again.",
        )
        return
    stopper.attach(child)
    process = child.process
    assert process.stdout is not None
    saved: list[str] = []
    problem: list[str] = []
    try:
        for line in read_lines(process.stdout):
            if stopper.requested.is_set():
                # The server asked it to stop, so what's left is `dogear`'s goodbye,
                # written for a terminal ("Run the same command again to continue").
                # The page says what stopping means in its own words. It's read to the
                # end all the same, so the child never waits on a full pipe.
                continue
            if line.startswith("Saved: "):
                saved.append(line.removeprefix("Saved: "))
            elif line.startswith(PROBLEM_PREFIXES):
                problem.append(line)
            elif line.strip():
                store.add_line(book_id, line)  # a full disk is logged, and the book goes on
        returncode = process.wait()
    except BaseException:
        # A bug here. The child mustn't run on unwatched: Stop couldn't reach it, and
        # Start again would start a second one. It is stopped, and this job ends only
        # once it has ended. (Closing its output first means it can't hang writing.)
        process.stdout.close()
        child.end()
        raise
    finally:
        stopper.detach()
        child.close()
        process.stdout.close()
        store.save_lines(book_id)
    if stopper.requested.is_set() or interrupted(returncode):
        store.update(book_id, state="stopped", error="", hint="")
    elif returncode != 0:
        found = problem_from_output("\n".join(problem))
        store.update(book_id, state="failed", error=found.error, hint=found.hint)
    else:
        outputs = saved_outputs(record, folder, saved)
        if outputs is None:
            store.update(
                book_id,
                state="failed",
                error="Dogear finished, but didn't say where it saved the book.",
                hint="This is a bug in Dogear. Please report it.",
            )
        else:
            store.update(book_id, state="done", outputs=outputs)


def make_args(record: BookRecord, folder: Path) -> list[str]:
    """`dogear make`'s arguments for the book's choices."""
    choices = record.choices
    assert choices is not None
    args = [
        "make",
        str(folder / record.source_name),
        "-o",
        str(folder / MADE),
        "--format",
        ",".join(choices.formats),
        "--voice",
        choices.voice,
        "--speed",
        f"{choices.speed:g}",
    ]
    every = record.summary is not None and len(choices.sections) == len(record.summary.sections)
    if not every:
        args += ["--sections", section_spec(choices.sections)]
    if choices.as_english:
        args += ["--language", "en"]
    return args


def section_spec(numbers: tuple[int, ...]) -> str:
    """(1, 2, 3, 7) as '1-3,7', the way `dogear make --sections` takes them."""
    runs: list[list[int]] = []
    for n in sorted(set(numbers)):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def saved_outputs(record: BookRecord, folder: Path, saved: list[str]) -> list[Output] | None:
    """What `dogear make` saved, paired with the formats it was asked for (it saves them
    in make.FORMATS order). None unless every one is in the book's `made` folder."""
    assert record.choices is not None
    formats = record.choices.formats
    if len(saved) != len(formats):
        return None
    made = (folder / MADE).resolve()
    outputs = []
    for format_name, path_text in zip(formats, saved, strict=True):
        path = Path(path_text).resolve()
        if not path.is_relative_to(made) or not path.exists():
            return None
        outputs.append(
            Output(format=format_name, path=path.relative_to(folder.resolve()).as_posix())
        )
    return outputs
