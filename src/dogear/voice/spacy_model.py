"""Unpack spaCy's English model from its downloaded wheel into the models folder.

The wheel is checked against its pinned SHA-256 before this runs. Only the model folder
inside it is kept; the Python package around it is never installed or imported.

The model is unpacked into a staging folder of this process's own, then renamed into
place whole. If another Dogear put the same model there first, that one is used.
"""

from __future__ import annotations

import os
import secrets
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath

from dogear.errors import DownloadError
from dogear.voice.catalog import catalog, spacy_model_dir, spacy_wheel
from dogear.voice.downloads import STALE_SECONDS

STAGING_SUFFIX = ".unpacking"


def unpack_spacy_model(models_dir: Path, target: Path | None = None) -> Path:
    """The unpacked model folder, unpacking the downloaded wheel into it first if needed:
    `target`, or else the folder Dogear loads the model from. Call it holding the models
    folder's `download_lock`."""
    target = target or spacy_model_dir(models_dir)
    if target.is_dir():
        return target
    wheel = spacy_wheel(models_dir).path
    target.parent.mkdir(parents=True, exist_ok=True)
    _remove_stale_staging(target.parent)
    staging = target.with_name(f".{target.name}.{secrets.token_hex(8)}{STAGING_SUFFIX}")
    staging.mkdir()
    try:
        _extract_model(wheel, staging)
        try:
            os.replace(staging, target)
        except OSError:
            if not target.is_dir():
                raise
            # Another Dogear unpacked the same pinned wheel first.
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    wheel.unlink(missing_ok=True)
    return target


def _extract_model(wheel: Path, staging: Path) -> None:
    prefix = catalog()["spacy"]["model_dir"] + "/"
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            if member.is_dir() or not member.filename.startswith(prefix):
                continue
            relative = PurePosixPath(member.filename[len(prefix) :])
            # A drive ("C:") or backslash in a part could escape the folder on Windows.
            if relative.is_absolute() or any(
                part == ".." or ":" in part or "\\" in part for part in relative.parts
            ):
                raise DownloadError(f"{wheel.name} contains an unsafe path: {member.filename}")
            destination = staging.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as out:
                shutil.copyfileobj(source, out)
    if not (staging / "config.cfg").is_file():
        raise DownloadError(f"{wheel.name} doesn't contain the spaCy model Dogear expected.")


def _remove_stale_staging(folder: Path) -> None:
    """Delete staging folders an unpack that stopped left behind, once untouched for
    STALE_SECONDS, so an unpack still running is never disturbed."""
    now = time.time()
    for leftover in folder.glob(f".*{STAGING_SUFFIX}"):
        try:
            if now - leftover.stat().st_mtime > STALE_SECONDS:
                shutil.rmtree(leftover)
        except FileNotFoundError:
            continue  # another Dogear tidied it first
