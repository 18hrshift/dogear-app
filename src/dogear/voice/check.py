"""`dogear download --check`: hash every voice file Dogear has on disk against the SHA-256
pinned for it, and download again the ones in the models folder that don't match. Kokoro's
and spaCy's are pinned in catalog.toml; another engine's are the ones its `pinned_files`
lists.

Starting Dogear never hashes the voice files (the model alone is 325 MB): a download is
checked before it is renamed into place, and a standalone build's own files when it was
built. This is for when a file may have changed since: a failing disk, an edit by hand,
or an older Dogear, whose overlapping downloads could leave a model with the wrong bytes.

Every file of the catalog that is on disk is checked, whichever voice it belongs to, in
the models folder and in a standalone build's bundled folder, and so is every file another
installed engine pins that is on disk. spaCy's unpacked model is
checked as one folder (`folder_sha256`), and its downloaded wheel, if one is left, as a
file. A bad file in the models folder is downloaded again. A bundled one can't be: the
app's own folder isn't Dogear's to change, and its files are used before any download.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from dogear import bundle
from dogear.book_summary import count
from dogear.config import Config
from dogear.errors import DogearError, VoiceError
from dogear.paths import models_dir
from dogear.voice.catalog import (
    catalog,
    downloaded_kokoro_file,
    downloaded_spacy_model_dir,
    kokoro_files,
    spacy_wheel,
)
from dogear.voice.downloads import (
    RemoteFile,
    Report,
    download,
    download_lock,
    file_sha256,
    folder_sha256,
    megabytes,
)
from dogear.voice.engines import installed, run_plugin
from dogear.voice.spacy_model import unpack_spacy_model

Say = Callable[[str], None]

REINSTALL = (
    "Install Dogear again: the voice files that came with it can't be downloaded again in place."
)
CHECK_AGAIN = "Check the internet connection, then run `dogear download --check` again."


@dataclass(frozen=True)
class VoiceFile:
    """A voice file (or spaCy's unpacked model folder) on disk, and what it should be."""

    label: str  # such as "the voice af_heart"
    path: Path
    sha256: str
    bundled: bool  # came with a standalone build
    remote: RemoteFile | None  # how to download it again; None for spaCy's model folder

    @property
    def is_folder(self) -> bool:
        return self.remote is None

    def size(self) -> int:
        if self.is_folder:
            return sum(p.stat().st_size for p in self.path.rglob("*") if p.is_file())
        return self.path.stat().st_size

    def matches(self, report: Report) -> bool:
        if self.is_folder:
            return folder_sha256(self.path) == self.sha256
        return file_sha256(self.path, report) == self.sha256


def check_voice_files(config: Config, say: Say, report: Report) -> None:
    """Check every voice file on disk, saying how each is (`say`), with progress lines
    for big files and downloads (`report`). Bad files in the models folder are
    downloaded again. Raises DogearError if any file is still bad at the end."""
    models = models_dir(config)
    shipped = bundled_files(models)
    downloaded = [f for f in [*downloaded_files(models), *engine_files(config)] if f.path.exists()]
    if not shipped and not downloaded:
        say(f"Dogear has no voice files yet. Downloads go into {models}.")
        say("Run `dogear download` to get the voice now, or let `dogear make` do it.")
        return
    bad: list[VoiceFile] = []
    root = bundle.folder()
    if shipped and root is not None:
        bad += _check(shipped, f"that came with Dogear, in {root / bundle.MODELS}", say, report)
    if downloaded:
        bad += _check(downloaded, f"in {models}", say, report)
    if not bad:
        say("Every voice file is good.")
        return
    failed = _download_again([f for f in bad if not f.bundled], models, config, say, report)
    still_bad = [f for f in bad if f.bundled] + failed
    if still_bad:
        hints = [REINSTALL] if any(f.bundled for f in still_bad) else []
        hints += [CHECK_AGAIN] if failed else []
        raise DogearError(
            f"{count(len(still_bad), 'voice file')} still damaged: "
            f"{', '.join(f.label for f in still_bad)}.",
            hint=" ".join(hints),
        )
    say("Every voice file is good now.")


def downloaded_files(models: Path) -> list[VoiceFile]:
    """Every file in the catalog, where a download puts it in `models`, on disk or not.
    The spaCy wheel, normally deleted once unpacked, comes before the folder it fills."""
    wheel = spacy_wheel(models)
    label = "spaCy's English model, as downloaded"
    spacy = VoiceFile(label, wheel.path, wheel.sha256, False, wheel)
    return [*_kokoro_files(models), spacy, _spacy_folder(models)]


def engine_files(config: Config) -> list[VoiceFile]:
    """Every file the installed engines other than Kokoro pin (`pinned_files`), on disk or
    not. An engine that downloads nothing, such as a speech service, pins none."""
    files = []
    for engine in installed().engines:
        pinned = getattr(engine.plugin, "pinned_files", None)
        if pinned is None:
            continue
        for remote in run_plugin(engine, "listing its files", partial(pinned, config)):
            if not isinstance(remote, RemoteFile):
                raise VoiceError(
                    f"The {engine.title} voice engine listed {remote!r} as a file it pins.",
                    hint=f"This is a bug in {engine.package}: it must list RemoteFiles. Please "
                    f"report it to {engine.report_to}.",
                )
            label = f"the {engine.title} file {remote.path.name}"
            files.append(VoiceFile(label, remote.path, remote.sha256, False, remote))
    return files


def bundled_files(models: Path) -> list[VoiceFile]:
    """The catalog's files a standalone build carries and would use: none in a normal
    install. A build carries the unpacked spaCy model, never its wheel. Each is listed in
    the build's manifest by the SHA-256 of its download: for the spaCy folder, the wheel's."""
    shipped = []
    wheel_sha256 = catalog()["spacy"]["sha256"]
    for file in [*_kokoro_files(models), _spacy_folder(models)]:
        relative = file.path.relative_to(models).as_posix()
        path = bundle.model(relative, wheel_sha256 if file.is_folder else file.sha256)
        if path is not None:
            shipped.append(VoiceFile(file.label, path, file.sha256, True, file.remote))
    return shipped


def _kokoro_files(models: Path) -> list[VoiceFile]:
    files = []
    for kind, name in kokoro_files():
        remote = downloaded_kokoro_file(kind, name, models)
        label = {
            "model": f"the Kokoro model ({name} build)",
            "tokenizer": "the Kokoro tokenizer",
            "voice": f"the voice {name}",
        }[kind]
        files.append(VoiceFile(label, remote.path, remote.sha256, False, remote))
    return files


def _spacy_folder(models: Path) -> VoiceFile:
    sha256 = catalog()["spacy"]["model_sha256"]
    folder = downloaded_spacy_model_dir(models)
    return VoiceFile("spaCy's English model", folder, sha256, False, None)


def _check(files: list[VoiceFile], where: str, say: Say, report: Report) -> list[VoiceFile]:
    """Hash each file, saying how it is, as a numbered list. Returns the bad ones."""
    total = sum(f.size() for f in files)
    say(f"Checking {count(len(files), 'voice file')} {where}, {megabytes(total)} in all.")
    bad = []
    for number, file in enumerate(files, start=1):
        if file.matches(report):
            say(f"{number}. Good: {file.label}.")
        else:
            say(f"{number}. Damaged: {file.label}. It doesn't match its checksum.")
            bad.append(file)
    return bad


def _download_again(
    bad: list[VoiceFile], models: Path, config: Config, say: Say, report: Report
) -> list[VoiceFile]:
    """Download each bad file again, under the models folder's lock. Returns the ones
    that are still bad."""
    if not bad:
        return []
    say(f"Downloading {count(len(bad), 'damaged file')} again.")
    failed = []
    with download_lock(models, report):
        for file in bad:
            try:
                _replace(file, models, config, report)
            except (DogearError, OSError) as e:
                reason = e.message if isinstance(e, DogearError) else f"{e.strerror or e}."
                say(f"Still damaged: {file.label}. {reason}")
                failed.append(file)
                continue
            say(f"Downloaded again, and good now: {file.label}.")
    return failed


def _replace(file: VoiceFile, models: Path, config: Config, report: Report) -> None:
    timeout = config["models"]["download_timeout_seconds"]
    if file.remote is not None:
        download(file.remote, timeout, report)  # replaces the file only once it matches
        return
    # spaCy's model folder is unpacked again from its wheel: one checked earlier in this
    # run, or a new download.
    wheel = spacy_wheel(models)
    if not wheel.path.is_file() or file_sha256(wheel.path) != wheel.sha256:
        download(wheel, timeout, report)
    shutil.rmtree(file.path)
    if folder_sha256(unpack_spacy_model(models, file.path)) != file.sha256:
        raise DogearError("The model unpacked from its download doesn't match its checksum.")
