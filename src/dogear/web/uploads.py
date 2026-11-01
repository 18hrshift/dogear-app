"""Receiving a book file: streamed to disk, with the size limit enforced as it arrives.

The upload form has one field, `book`. Its file is written to the web folder's
`incoming` folder under a random name and only moved into a book's folder once it is
complete and allowed. Content-Length isn't trusted: bytes are counted as they come.
"""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import python_multipart
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import parse_options_header
from starlette.requests import ClientDisconnect, Request

from dogear.extract import EXTRACTORS, format_names
from dogear.web.private import private_folder

FIELD = "book"
# Room for the form's own boundaries and headers on top of the file.
FORM_OVERHEAD_BYTES = 64 * 1024
NOT_A_FORM = "The upload wasn't a form Dogear understands. Try again."


# Status codes for a book that couldn't be written to disk.
DISK_FULL_STATUS = 507  # Insufficient Storage
CANT_WRITE_STATUS = 500


class UploadError(Exception):
    """An upload Dogear won't take. `message` is shown by the file field."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Upload:
    original_name: str  # as the browser sent it, for showing only
    extension: str  # lower case, one Dogear reads
    path: Path  # the complete file, in the incoming folder


async def receive_book(request: Request, incoming: Path, max_bytes: int) -> Upload:
    content_type, params = parse_options_header(request.headers.get("content-type", ""))
    if content_type != b"multipart/form-data" or b"boundary" not in params:
        raise UploadError("Choose a book file, then choose Upload the book.")
    reader = _BookPart(incoming, max_bytes)
    parser = python_multipart.MultipartParser(params[b"boundary"], reader.callbacks())
    received = 0
    problem: UploadError | None = None
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > max_bytes + FORM_OVERHEAD_BYTES:
                reader.too_big = True
            if problem or reader.too_big:
                # The rest is read and dropped, so the browser gets the page that says
                # what's wrong instead of a broken connection.
                continue
            try:
                parser.write(chunk)
            except (FormParserError, _PartError) as e:
                problem = UploadError(str(e) if isinstance(e, _PartError) else NOT_A_FORM)
            except OSError as e:
                problem = disk_problem(e)
        if not problem and not reader.too_big:
            try:
                parser.finalize()
                reader.close()  # the last of the file is written now, so a full disk can show
            except FormParserError:
                problem = UploadError(NOT_A_FORM)
            except OSError as e:
                problem = disk_problem(e)
    except ClientDisconnect:
        problem = UploadError("The upload stopped before the whole file arrived. Try again.")
    except BaseException:  # a bug, or the server stopping: nothing half-written is kept
        reader.discard()
        raise
    if problem or reader.too_big:
        reader.discard()
        raise problem or UploadError(_too_big(max_bytes), 413)
    return reader.result()


def disk_problem(e: OSError) -> UploadError:
    """What to say when the book couldn't be written to disk, in words that fit the cause."""
    if e.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
        return UploadError(
            "The computer running Dogear has no room left on its disk, so the book wasn't "
            "saved. Free some space on that computer, then upload the book again.",
            DISK_FULL_STATUS,
        )
    if e.errno == errno.EFBIG:
        return UploadError(
            "The book file is bigger than the disk of the computer running Dogear can hold "
            "in one file, so it wasn't saved. Make it with the dogear make command instead.",
            DISK_FULL_STATUS,
        )
    if e.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return UploadError(
            "Dogear isn't allowed to save files in its web folder, so the book wasn't saved. "
            "Check that the folder named in Dogear's window can be written to, then try again.",
            CANT_WRITE_STATUS,
        )
    return UploadError(
        f"Dogear couldn't save the book on the computer it runs on ({e.strerror or e}). "
        "Upload the book again. If it happens again, Dogear's window may say more.",
        CANT_WRITE_STATUS,
    )


def check_name(name: str) -> str:
    """The file's extension, if Dogear reads that kind of book."""
    extension = os.path.splitext(name)[1].lower()
    if not name:
        raise UploadError("Choose a book file to upload.")
    if extension not in EXTRACTORS:
        raise UploadError(
            f"Dogear can't read {extension or 'files without an extension'} files. "
            f"Choose an {format_names('or')} file."
        )
    return extension


def display_name(sent: bytes) -> str:
    """The file's name as the browser sent it, without any folders, and with the quote
    marks browsers write as %22 put back. Used only for showing."""
    name = sent.decode("utf-8", errors="replace").replace("%22", '"')
    name = "".join(c for c in name if c.isprintable())
    return os.path.basename(name.replace("\\", "/"))


class _PartError(Exception):
    pass


class _BookPart:
    """Callbacks for python-multipart that write the `book` field's data to a file."""

    def __init__(self, incoming: Path, max_bytes: int) -> None:
        self._incoming = incoming
        self._max = max_bytes
        self._header_name = b""
        self._header_value = b""
        self._disposition = b""
        self._writing = False
        self._file = None
        self._path: Path | None = None
        self._size = 0
        self._name = ""
        self._extension = ""
        self._done = False
        self.too_big = False

    def callbacks(self) -> dict:
        return {
            "on_part_begin": self._part_begin,
            "on_header_field": self._header_field,
            "on_header_value": self._header_value_data,
            "on_header_end": self._header_end,
            "on_headers_finished": self._headers_finished,
            "on_part_data": self._part_data,
            "on_part_end": self._part_end,
        }

    def _part_begin(self) -> None:
        self._disposition = b""
        self._writing = False

    # python-multipart bounds each header's size and count.
    def _header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def _header_value_data(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def _header_end(self) -> None:
        if self._header_name.lower() == b"content-disposition":
            self._disposition = self._header_value
        self._header_name = b""
        self._header_value = b""

    def _headers_finished(self) -> None:
        _, options = parse_options_header(self._disposition)
        if options.get(b"name") != FIELD.encode() or b"filename" not in options:
            return  # not the book: ignored
        if self._done:
            raise _PartError("Upload one book at a time.")
        self._name = display_name(options[b"filename"])
        if not self._name:
            raise _PartError("Choose a book file to upload.")
        try:
            self._extension = check_name(self._name)
        except UploadError as e:
            raise _PartError(e.message) from None
        private_folder(self._incoming)
        self._path = self._incoming / f"{secrets.token_hex(8)}.partial"
        self._file = _open_new(self._path)
        self._writing = True

    def _part_data(self, data: bytes, start: int, end: int) -> None:
        if not self._writing or self._file is None:
            return
        self._size += end - start
        if self._size > self._max:
            self.too_big = True
            self._writing = False
            return
        self._file.write(data[start:end])

    def _part_end(self) -> None:
        if self._writing:
            self._done = True
        self._writing = False

    def close(self) -> None:
        """Finish writing the file. Raises OSError if the last of it can't be written."""
        file, self._file = self._file, None
        if file is not None:
            file.close()

    def discard(self) -> None:
        with contextlib.suppress(OSError):  # it's being thrown away
            self.close()
        if self._path is not None:
            self._path.unlink(missing_ok=True)

    def result(self) -> Upload:
        if not self._done or self._path is None:
            self.discard()
            raise UploadError("Choose a book file to upload.")
        if self._size == 0:
            self.discard()
            raise UploadError(f"{self._name} is empty. Choose another file.")
        return Upload(original_name=self._name, extension=self._extension, path=self._path)


def _open_new(path: Path) -> BinaryIO:
    """A new file for writing, readable by this user only (on Linux and macOS)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    return os.fdopen(os.open(path, flags, 0o600), "wb")


def _too_big(max_bytes: int) -> str:
    return (
        f"That file is bigger than {max_bytes // (1024 * 1024):,} MB, the most this page "
        "takes. To make it anyway, use the dogear make command, or raise web.max_upload_mb "
        "in your config file."
    )
