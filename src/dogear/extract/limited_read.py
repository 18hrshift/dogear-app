"""Read a whole book file into memory, refusing one bigger than its format's limit.

HTML and Markdown books are single files read whole, so the limit is the file's size.
Bytes are counted as they are read, so a file that grows while it is read can't get past it.
"""

from __future__ import annotations

from pathlib import Path

from dogear.config import Config
from dogear.errors import ConfigError, ExtractionError


def read_limited(path: Path, config: Config, table: str, what: str) -> bytes:
    """The bytes of `path`, a `what` ("HTML") file limited by `[table] max_file_mb`."""
    limit_mb = config[table]["max_file_mb"]
    if limit_mb <= 0:
        raise ConfigError(f"{table}.max_file_mb must be more than 0, got {limit_mb}.")
    limit = limit_mb * 1024 * 1024
    try:
        with path.open("rb") as f:
            data = f.read(limit + 1)
    except OSError as e:
        raise ExtractionError(
            f"{path.name} can't be read ({e.strerror or e}).",
            hint="Check that the file is there and that you are allowed to open it.",
        ) from None
    if len(data) > limit:
        raise ExtractionError(
            f"{path.name} is bigger than {limit_mb:,} MB, far more than the text of a book "
            f"needs, so Dogear didn't read it.",
            hint=f"If it's a genuine {what} book, raise {table}.max_file_mb in your config file.",
        )
    return data
