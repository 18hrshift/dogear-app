"""Listen: saying a word or a short text with the voice, so a person can hear it.

Each listen runs `dogear say -o <the listen folder>/<id>.wav -- <text>` as a child
process, with the voice and speed chosen for the book when there is one, and the page's
own pronunciation file (page_words.py). The text a person typed comes last, after `--`,
so nothing in it can be read as an option. Listens have their own queue, one at a time,
apart from reading and making, so a listen never waits hours behind a book.

Recordings go in the web folder's `listen` folder, under random names. Only the latest
LISTENS_KEPT are kept, and the folder is emptied when the server starts. What each listen
said is kept in memory only.
"""

from __future__ import annotations

import logging
import re
import secrets
import shutil
import threading
import unicodedata
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from dogear.web.child import Stopper, child_command, interrupted, run_captured
from dogear.web.page_words import words_option
from dogear.web.private import private_folder
from dogear.web.reading import problem_from_output
from dogear.web.settings import WebSettings
from dogear.web.worker import Worker

log = logging.getLogger(__name__)

LISTENS_KEPT = 10  # recordings kept; older ones are deleted
MAX_WAITING = 10  # listens waiting or being said at once; more are refused
MAX_LISTEN_CHARS = 500  # of the text to say
MAX_SAY_OUTPUT = 64 * 1024  # bytes of `dogear say`'s output kept
ID_PATTERN = re.compile(r"[0-9a-f]{32}")
SAID_PREFIX = "Said: "

ListenState = Literal["waiting", "saying", "done", "failed"]


@dataclass(frozen=True)
class Listen:
    id: str
    text: str  # what to say, as typed (tidied)
    back: str  # the page to go back to, such as "/pronunciations"
    options: tuple[str, ...] = ()  # --voice and --speed for `dogear say`, if any
    state: ListenState = "waiting"
    said: tuple[str, ...] = ()  # each sentence the way the voice was given it
    error: str = ""
    hint: str = ""

    @property
    def settled(self) -> bool:
        return self.state in ("done", "failed")


class ListenError(Exception):
    """Something the page can't say, or can't say now. `message` says why."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def clean_text(text: str) -> str:
    """The text to say, on one line with single spaces. Raises ListenError if there's
    nothing to say, it's too long, or it has control characters."""
    tidy = " ".join(text.split())
    if any(unicodedata.category(c) in ("Cc", "Cs") for c in tidy):
        raise ListenError("That has control characters in it. Type only words to say.")
    if not any(c.isalnum() for c in tidy):
        raise ListenError("Type a word or a sentence to say.")
    if len(tidy) > MAX_LISTEN_CHARS:
        raise ListenError(
            f"That's more than {MAX_LISTEN_CHARS} characters. Try a word or a sentence."
        )
    return tidy


# Whatever says a listen: `say_aloud`, or a stand-in in tests. It returns the listen,
# done or failed, or None if it was stopped by the server.
SayFunction = Callable[[WebSettings, Listen, Path, Stopper], Listen | None]


def say_aloud(
    settings: WebSettings, listen: Listen, output: Path, stopper: Stopper
) -> Listen | None:
    """Run `dogear say` for the listen, saving to `output`."""
    command = child_command(
        ["say", "-o", str(output), *listen.options, *words_option(settings.words_file)],
        settings.config_file,
        text=listen.text,
    )
    result = run_captured(
        command,
        timeout=settings.read_timeout_seconds,
        max_stdout=MAX_SAY_OUTPUT,
        stopper=stopper,
    )
    if stopper.requested.is_set():
        return None
    if result.timed_out:
        return _failed(
            listen,
            f"Dogear went {settings.read_timeout_seconds:g} seconds without any progress "
            "saying it, so it stopped.",
            "Try a shorter text. If this was the first time, the voice may still have been "
            "downloading: try again.",
        )
    if interrupted(result.returncode):
        return _failed(
            listen, "Something stopped Dogear while it was saying this.", "Choose Listen again."
        )
    if result.returncode != 0:
        problem = problem_from_output(result.stderr)
        return _failed(listen, problem.error, problem.hint)
    if not output.is_file():
        return _failed(
            listen,
            "Dogear finished, but didn't save the recording.",
            "This is a bug in Dogear. Please report it.",
        )
    lines = result.stdout.decode("utf-8", errors="replace").splitlines()
    said = tuple(line.removeprefix(SAID_PREFIX) for line in lines if line.startswith(SAID_PREFIX))
    return replace(listen, state="done", said=said)


def _failed(listen: Listen, error: str, hint: str) -> Listen:
    return replace(listen, state="failed", error=error, hint=hint)


class Listens:
    """Every listen since the server started, the newest LISTENS_KEPT recordings, and the
    queue that says them. Thread-safe."""

    def __init__(self, settings: WebSettings, say: SayFunction = say_aloud) -> None:
        self.settings = settings
        self.folder = settings.listen_dir
        self._say = say
        self._lock = threading.Lock()
        self._listens: dict[str, Listen] = {}
        self._settled: deque[str] = deque()  # ids of settled listens, oldest first
        # Recordings from an earlier run: nothing links to them any more.
        shutil.rmtree(self.folder, ignore_errors=True)
        private_folder(self.folder)
        self.worker = Worker("listener", self._job)

    def start(self) -> None:
        self.worker.start()

    def close(self, timeout: float) -> None:
        self.worker.close(timeout)

    def add(self, text: str, back: str, options: tuple[str, ...] = ()) -> Listen:
        """A new listen of `text` (from clean_text), in the queue. Raises ListenError if
        too many are waiting already."""
        with self._lock:
            busy = sum(1 for listen in self._listens.values() if not listen.settled)
            if busy >= MAX_WAITING:
                raise ListenError(
                    f"Dogear is still saying {busy} other things. Wait a moment, then try again."
                )
            listen_id = secrets.token_hex(16)
            listen = Listen(listen_id, text, back, options)
            self._listens[listen_id] = listen
        self.worker.add(listen_id)
        return listen

    def get(self, listen_id: str) -> Listen | None:
        with self._lock:
            return self._listens.get(listen_id)

    def ahead(self, listen_id: str) -> int | None:
        return self.worker.ahead(listen_id)

    def recording(self, listen_id: str) -> Path | None:
        """The listen's WAV file, if it's done and its file is still in the listen folder."""
        listen = self.get(listen_id) if ID_PATTERN.fullmatch(listen_id) else None
        if listen is None or listen.state != "done":
            return None
        path = self._path(listen_id)
        folder = self.folder.resolve()
        if path.is_symlink() or not path.is_file() or path.resolve().parent != folder:
            return None
        return path.resolve()

    def _path(self, listen_id: str) -> Path:
        if not ID_PATTERN.fullmatch(listen_id):
            raise ValueError(f"not a listen id: {listen_id!r}")
        return self.folder / f"{listen_id}.wav"

    def _job(self, listen_id: str, stopper: Stopper) -> None:
        with self._lock:
            listen = self._listens.get(listen_id)
            if listen is None:
                return
            listen = self._listens[listen_id] = replace(listen, state="saying")
        output = self._path(listen_id)
        result: Listen | None = None
        try:
            result = self._say(self.settings, listen, output, stopper)
        except Exception:
            result = _failed(
                listen,
                "Dogear stopped because of a problem it didn't expect.",
                "Choose Listen again. If it happens again, please report it.",
            )
            raise  # the worker logs the traceback
        finally:
            self._finish(listen_id, result, output)

    def _finish(self, listen_id: str, result: Listen | None, output: Path) -> None:
        with self._lock:
            if result is None:  # stopped by the server
                self._listens.pop(listen_id, None)
                _delete(output)
                return
            if result.state != "done":
                _delete(output)
            self._listens[listen_id] = result
            self._settled.append(listen_id)
            while len(self._settled) > LISTENS_KEPT:
                old = self._settled.popleft()
                self._listens.pop(old, None)
                _delete(self._path(old))


def _delete(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as e:  # open in a player, on Windows: it goes when the server next starts
        log.warning("Couldn't delete the recording %s: %s", path, e)
