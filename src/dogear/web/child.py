"""Running the `dogear` command as a child process: starting it, reading it, stopping it.

The server runs `dogear outline --json`, `make`, `words --json`, and `say` this way, so
a book file that crashes or hangs the reader takes down only the child, and the server
never loads the voice. Each child and whatever it starts
make one process tree (process_tree.py) that only the server stops, and a child stops
by itself if the server ends first (server_watch.py). Children write UTF-8.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from dogear.web.process_tree import INTERRUPTED, ProcessTree
from dogear.web.process_tree import popen_options as tree_options
from dogear.web.server_watch import STOP_WITH_SERVER_ENV

# Set for every child: `dogear` then writes its output as UTF-8 (see cli.py).
UTF8_OUTPUT_ENV = "DOGEAR_UTF8_OUTPUT"
# A child asked to stop gets this long to tidy up before it is killed.
STOP_GRACE_SECONDS = 10.0
# Longest output line kept. Longer lines are cut (a traceback can hold a whole sentence).
MAX_LINE_CHARS = 2000
# The last lines of a captured child's stderr that are kept, for its `Error:` lines.
MAX_STDERR_LINES = 200
WAIT_POLL_SECONDS = 1.0  # how often a captured child's silence is checked


def dogear_command() -> list[str]:
    """How to run `dogear`: this Python with `-m dogear`, or this program itself when it
    is a frozen build (an installer's PyInstaller executable), which is `dogear` already."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "dogear"]


def child_command(
    args: list[str], config_file: Path | None, *, text: str | None = None
) -> list[str]:
    """The full command for `dogear ARGS`, with the server's own --config if it has one.
    `text`, which a person typed, comes last, after `--`, so that nothing in it can be
    read as an option."""
    command = [*dogear_command(), *args]
    if config_file is not None:
        command += ["--config", str(config_file)]
    if text is not None:
        command += ["--", text]
    return command


def popen_options() -> dict:
    """What every child is started with: UTF-8 text, its own process tree, a stdin the
    server never writes to (its end says the server has gone), files only this user can
    read (private.py) on Linux and macOS, and no console window on Windows."""
    options: dict = {
        "env": {
            **os.environ,
            UTF8_OUTPUT_ENV: "1",
            STOP_WITH_SERVER_ENV: "1",
            "PYTHONIOENCODING": "utf-8",
        },
        "stdin": subprocess.PIPE,
        **tree_options(),
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["umask"] = 0o077
    return options


def interrupted(returncode: int) -> bool:
    """Whether a child ended because something stopped it: Ctrl+C (exit code 130), or a
    signal on Linux and macOS (a negative code), rather than finishing or failing."""
    return returncode == INTERRUPTED or returncode < 0


class Child:
    """A running child and its process tree. Close it once it has ended."""

    def __init__(self, command: list[str], *, stderr: int) -> None:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=stderr, **popen_options()
        )
        try:
            self._tree = ProcessTree(process)
        except BaseException:
            process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            raise
        self.process = process
        self._kill_timer: threading.Timer | None = None

    def stop(self) -> None:
        """Ask the child, and what it started, to stop. Whatever is still running after
        STOP_GRACE_SECONDS is killed."""
        self._tree.interrupt()
        if self._kill_timer is None:
            self._kill_timer = threading.Timer(STOP_GRACE_SECONDS, self._tree.kill)
            self._kill_timer.daemon = True
            self._kill_timer.start()

    def end(self) -> int:
        """Stop the child and wait until it has ended, for a job that can't go on."""
        self.stop()
        return self.process.wait()

    def close(self) -> None:
        """Once the child has ended: end anything it left running, and let go of it.
        Its stdout and stderr are closed by whatever reads them."""
        if self._kill_timer is not None:
            self._kill_timer.cancel()
        self._tree.close()
        if self.process.stdin is not None:
            self.process.stdin.close()


def start_lines(command: list[str]) -> Child:
    """Start a child whose stdout and stderr are read together, line by line."""
    return Child(command, stderr=subprocess.STDOUT)


def read_lines(stream: IO[bytes]) -> Iterator[str]:
    """Each line of a child's output, decoded, without its line end, and cut to a bound."""
    while True:
        raw = stream.readline(MAX_LINE_CHARS * 4)
        if not raw:
            return
        if not raw.endswith(b"\n"):
            # The rest of an overlong line is dropped, not shown as lines of its own.
            while (more := stream.readline(64 * 1024)) and not more.endswith(b"\n"):
                pass
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        yield line[:MAX_LINE_CHARS]


@dataclass(frozen=True)
class Captured:
    returncode: int
    stdout: bytes
    stderr: str  # its last MAX_STDERR_LINES lines, where `Error:` and `What to do:` are
    too_long: bool  # stdout went past its limit, so `stdout` is incomplete
    timed_out: bool


def run_captured(
    command: list[str],
    *,
    timeout: float,
    max_stdout: int,
    stopper: Stopper | None = None,
    on_line: Callable[[str], None] | None = None,
) -> Captured:
    """Run a child to the end, keeping at most `max_stdout` bytes of its stdout. Each line
    it writes to stderr goes to `on_line`, if given, as it comes.

    The child is stopped once it has gone `timeout` seconds without writing a line to
    stderr, where `dogear` reports how it's going: one that has said nothing for that
    long is taken to be stuck. A long job that keeps reporting, such as reading a long
    scanned PDF with OCR, runs for as long as it needs."""
    child = Child(command, stderr=subprocess.PIPE)
    process = child.process
    if stopper is not None:
        stopper.attach(child)
    out = _Collector(process.stdout, max_stdout)
    err = _LineReader(process.stderr, on_line)
    timed_out = False
    try:
        timed_out = not _wait_while_heard(process, err, timeout)
        if timed_out:
            child.end()
    except BaseException:
        child.end()
        raise
    finally:
        if stopper is not None:
            stopper.detach()
        child.close()
    out.join()
    err.join()
    return Captured(
        returncode=process.returncode,
        stdout=out.data(),
        stderr=err.text(),
        too_long=out.overflowed,
        timed_out=timed_out,
    )


def _wait_while_heard(process: subprocess.Popen, err: _LineReader, timeout: float) -> bool:
    """Wait for the child to end. False if it went `timeout` seconds without a line first."""
    while True:
        left = err.heard_at + timeout - time.monotonic()
        if left <= 0:
            return False
        try:
            process.wait(timeout=min(left, WAIT_POLL_SECONDS))
            return True
        except subprocess.TimeoutExpired:
            continue


class _LineReader:
    """Reads a child's stderr line by line on a thread, keeping the last lines and when
    the latest came, and handing each to `on_line`. If `on_line` fails (a bug), the rest
    is still read, so the child never waits on a full pipe, and `join` raises the error."""

    def __init__(self, stream: IO[bytes] | None, on_line: Callable[[str], None] | None) -> None:
        self._lines: deque[str] = deque(maxlen=MAX_STDERR_LINES)
        self._on_line = on_line
        self._error: Exception | None = None
        self.heard_at = time.monotonic()
        self._thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self._thread.start()

    def _read(self, stream: IO[bytes] | None) -> None:
        if stream is None:
            return
        with stream:
            for line in read_lines(stream):
                self.heard_at = time.monotonic()
                self._lines.append(line)
                if self._on_line is not None and self._error is None:
                    try:
                        self._on_line(line)
                    except Exception as e:
                        self._error = e

    def join(self) -> None:
        self._thread.join()
        if self._error is not None:
            raise self._error

    def text(self) -> str:
        return "\n".join(self._lines)


class _Collector:
    """Reads a pipe to its end on a thread, keeping the first `limit` bytes."""

    def __init__(self, stream: IO[bytes] | None, limit: int) -> None:
        self._chunks: list[bytes] = []
        self._kept = 0
        self._limit = limit
        self.overflowed = False
        self._thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self._thread.start()

    def _read(self, stream: IO[bytes] | None) -> None:
        if stream is None:
            return
        with stream:
            while chunk := stream.read(64 * 1024):
                room = self._limit - self._kept
                if len(chunk) > room:
                    self.overflowed = True
                    chunk = chunk[: max(0, room)]
                self._chunks.append(chunk)
                self._kept += len(chunk)

    def join(self) -> None:
        self._thread.join()

    def data(self) -> bytes:
        return b"".join(self._chunks)


class Stopper:
    """Lets another thread stop whichever child a job is running now, or the next one it
    starts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._child: Child | None = None
        self.requested = threading.Event()

    def attach(self, child: Child) -> None:
        with self._lock:
            self._child = child
            if self.requested.is_set():
                child.stop()

    def detach(self) -> None:
        with self._lock:
            self._child = None

    def stop(self) -> None:
        with self._lock:
            self.requested.set()
            if self._child is not None:
                self._child.stop()
