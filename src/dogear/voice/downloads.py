"""Download voice model files once, each checked against a pinned SHA-256.

Each download is written to a temporary file of its own beside the finished one, and
renamed into place only after the file on disk matches its checksum. So an interrupted,
tampered, or overlapping download never looks complete, and the file Dogear loads is
always exactly the one that was checked.

Two Dogear processes can need the same files at once: `dogear make` and the web page's
Listen, say, or two commands on a first run. `download_lock` lets one download at a time
into a models folder; the others wait, then find the files already there.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from dogear.config import Config
from dogear.errors import DownloadError
from dogear.file_lock import FileLock

Report = Callable[[str], None]

_CHUNK = 1 << 20
_REPORT_FROM = 50 << 20  # files at least this big get progress lines...
_REPORT_EVERY = 0.25  # ...at each quarter
ISSUES_URL = "https://github.com/18hrshift/dogear-app/issues"

LOCK_FILE = "download.lock"  # in the models folder
WAITING = "Waiting for another Dogear to finish downloading voice files."
TEMPORARY_SUFFIX = ".download"
# A temporary file untouched for this long belongs to a download that stopped. A live one
# is written at least every megabyte, so it is never this old.
STALE_SECONDS = 60 * 60


@dataclass(frozen=True)
class RemoteFile:
    url: str
    sha256: str
    size: int  # bytes, as published; used for messages and as an upper bound
    path: Path  # where the file lives once downloaded


@contextmanager
def download_lock(folder: Path, report: Report) -> Iterator[None]:
    """Hold the lock that lets one Dogear at a time download into `folder`. If another
    has it, say so once, then wait until it's done. Whoever holds the lock should check
    again what's on disk: the files may have arrived while it waited."""
    folder.mkdir(parents=True, exist_ok=True)
    lock = FileLock.wait_for(folder / LOCK_FILE, lambda: report(WAITING))
    try:
        yield
    finally:
        lock.release()


def ensure_downloaded(
    files: list[RemoteFile], folder: Path, config: Config, report: Report
) -> None:
    """Make sure every one of `files` is on disk, downloading whatever isn't under the
    lock on `folder` (the models folder), so two Dogears never download the same file at
    once. When every file is there already, nothing is locked or written. This is what a
    voice engine's `prepare` calls."""
    if all(f.path.is_file() for f in files):
        return
    with download_lock(folder, report):
        # Asked again inside: another Dogear may have downloaded them while this one waited.
        ensure_files(files, config, report)


def ensure_files(files: list[RemoteFile], config: Config, report: Report) -> None:
    """Download whichever of `files` aren't on disk yet. Call it holding `download_lock`
    when another Dogear could be downloading into the same folder."""
    missing = [f for f in files if not f.path.is_file()]
    if not missing:
        return
    for folder in sorted({f.path.parent for f in missing}):
        remove_stale_temporaries(folder)
    total = sum(f.size for f in missing)
    report(f"Downloading voice files, {megabytes(total)} in all. This only happens once.")
    for remote in missing:
        download(remote, config["models"]["download_timeout_seconds"], report)
    report("Voice files downloaded.")


def download(remote: RemoteFile, timeout: float, report: Report) -> None:
    name = remote.path.name
    remote.path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = _new_temporary(remote.path)
    try:
        try:
            with os.fdopen(descriptor, "wb") as out:
                _fetch(remote, out, timeout, report)
                out.flush()
                os.fsync(out.fileno())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", None) or e
            raise DownloadError(
                f"Couldn't download {name}: {reason}.",
                hint="Check the internet connection, then run the same command again.",
            ) from None
        if file_sha256(temporary) != remote.sha256:
            raise DownloadError(
                f"The downloaded {name} doesn't match its expected checksum, so it wasn't used.",
                hint=f"Run the command again. If it keeps happening, please report it at "
                f"{ISSUES_URL}.",
            )
        os.replace(temporary, remote.path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _new_temporary(target: Path) -> tuple[int, Path]:
    """A new, empty file beside `target` that no other download writes, open for writing.
    Its permissions follow the umask, as any file Dogear writes."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    while True:
        path = target.with_name(f".{target.name}.{secrets.token_hex(8)}{TEMPORARY_SUFFIX}")
        try:
            return os.open(path, flags, 0o666), path
        except FileExistsError:
            continue


def _fetch(remote: RemoteFile, out: BinaryIO, timeout: float, report: Report) -> None:
    received = 0
    progress = Quarters(remote.path.name, remote.size, "downloaded", report)
    with urllib.request.urlopen(remote.url, timeout=timeout) as response:
        while chunk := response.read(_CHUNK):
            received += len(chunk)
            if received > remote.size:
                raise DownloadError(
                    f"{remote.path.name} is bigger than expected, so the download was stopped.",
                    hint=f"Please report this at {ISSUES_URL}.",
                )
            out.write(chunk)
            progress.reached(received)


class Quarters:
    """A progress line at each quarter of a big file, such as "model.onnx: 25% checked."
    Files smaller than _REPORT_FROM get none."""

    def __init__(self, name: str, size: int, done: str, report: Report) -> None:
        self._name, self._size, self._done, self._report = name, size, done, report
        self._next = _REPORT_EVERY

    def reached(self, count: int) -> None:
        """`count` bytes of the file are done."""
        if self._size < _REPORT_FROM:
            return
        while self._next <= 1 and count >= self._next * self._size:
            self._report(f"{self._name}: {round(self._next * 100)}% {self._done}.")
            self._next += _REPORT_EVERY


def file_sha256(path: Path, report: Report | None = None) -> str:
    """The file's SHA-256. With `report`, a big file says how far it has got (Quarters)."""
    digest = hashlib.sha256()
    progress = Quarters(path.name, path.stat().st_size, "checked", report) if report else None
    done = 0
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress.reached(done)
    return digest.hexdigest()


def folder_sha256(folder: Path) -> str:
    """One SHA-256 for a folder of files, such as an unpacked model: of a line per file,
    "<the file's SHA-256>  <its path in the folder, with />", sorted by path, the way
    `sha256sum` writes them. A file added, removed, renamed, or changed changes it."""
    files = sorted(
        (path.relative_to(folder).as_posix(), path) for path in folder.rglob("*") if path.is_file()
    )
    lines = "".join(f"{file_sha256(path)}  {relative}\n" for relative, path in files)
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def remove_stale_temporaries(folder: Path) -> None:
    """Delete temporary download files in `folder` that a stopped download left: only
    those untouched for STALE_SECONDS, so a download still running is never disturbed."""
    if not folder.is_dir():
        return
    now = time.time()
    for leftover in folder.glob(f".*{TEMPORARY_SUFFIX}"):
        try:
            if now - leftover.stat().st_mtime > STALE_SECONDS:
                leftover.unlink()
        except FileNotFoundError:
            continue  # another Dogear tidied it first


def megabytes(size: int) -> str:
    return f"{max(1, round(size / 1_000_000))} MB"
