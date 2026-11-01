"""Read the files inside a ZIP-based book (EPUB, Word) in memory, with a size limit.

Nothing is unpacked to disk. No file is read past `max_member_bytes`, so a tiny archive
that unpacks to gigabytes (a zip bomb) fails with an error instead of exhausting memory.
Every XML file goes through `safe_xml.parse_xml`.
"""

from __future__ import annotations

import zipfile
import zlib
from pathlib import Path
from types import TracebackType
from typing import Self

from lxml import etree

from dogear.errors import ExtractionError
from dogear.extract.safe_xml import parse_xml


class ZipArchive:
    """Read-only access to the files inside a ZIP archive.

    `kind` names what the archive should be, with its article ("an EPUB"), for errors.
    `damaged_hint` is the hint for a damaged archive, `not_zip_hint` (if different) the
    hint for a file that isn't a ZIP archive at all, and `limit_setting` the setting that
    raises the size limit, such as "epub.max_member_mb".
    """

    def __init__(
        self,
        path: Path,
        *,
        max_member_bytes: int,
        kind: str,
        damaged_hint: str,
        limit_setting: str,
        not_zip_hint: str | None = None,
    ) -> None:
        self.name = path.name
        self.damaged_hint = damaged_hint
        self._max_bytes = max_member_bytes
        self._limit_setting = limit_setting
        try:
            self._zip = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            raise ExtractionError(
                f"{path.name} can't be opened as {kind} because it isn't a ZIP archive.",
                hint=not_zip_hint or damaged_hint,
            ) from None
        self._members = frozenset(self._zip.namelist())

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._zip.close()

    def has(self, member: str) -> bool:
        return member in self._members

    def read(self, member: str) -> bytes:
        if member not in self._members:
            raise ExtractionError(f"{member} is missing from {self.name}.", hint=self.damaged_hint)
        if self._zip.getinfo(member).file_size > self._max_bytes:
            raise self._too_large(member)
        try:
            with self._zip.open(member) as f:
                # The size in the archive's header is the file's own claim, so don't trust
                # it: read at most one byte past the limit. Data is inflated in small
                # chunks, so memory stays bounded however far the member would expand.
                data = f.read(self._max_bytes + 1)
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError) as e:
            raise ExtractionError(
                f"{member} in {self.name} can't be read ({e}).", hint=self.damaged_hint
            ) from None
        if len(data) > self._max_bytes:
            raise self._too_large(member)
        return data

    def parse(self, member: str) -> etree._Element:
        return parse_xml(self.read(member), f"{member} in {self.name}", self.damaged_hint)

    def _too_large(self, member: str) -> ExtractionError:
        limit_mb = self._max_bytes / (1024 * 1024)
        return ExtractionError(
            f"{member} in {self.name} unpacks to more than {limit_mb:g} MB, far more than "
            "a book's text needs, so Dogear stopped reading it.",
            hint=(
                "The file may be damaged or built to exhaust memory (a 'zip bomb'). If it's "
                f"a genuine book, raise {self._limit_setting} in your config file."
            ),
        )
