"""Gather the default voice into the bundle, with the manifest Dogear reads it by.

The defaults decide what's bundled: the Kokoro build `[kokoro] model`, the voice
`[voice] voice`, the tokenizer, and spaCy's English model. Every file is checked against
its SHA-256 here, at build time, so Dogear never needs to hash them when it starts.

Only Kokoro is ever bundled. The defaults come from Dogear's own files, never from a
plugin installed where the build runs, and the default engine must be Kokoro.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import tomllib
from pathlib import Path

from dogear import bundle
from dogear.config import core_defaults
from dogear.voice.catalog import catalog, kokoro_file, kokoro_voice, spacy_wheel
from dogear.voice.downloads import RemoteFile, ensure_files
from dogear.voice.kokoro_plugin import ENGINE as KOKORO
from dogear.voice.spacy_model import unpack_spacy_model
from dogear_build import BuildError
from dogear_build.fetch import Report


def bundle_voice(cache: Path, models: Path, report: Report) -> Path:
    """Put the default voice in `models` (the bundle's models folder), downloading it to
    `cache` first if needed. Returns the bundled spaCy model folder."""
    config = core_defaults()
    if config["voice"]["engine"] != KOKORO.name:
        raise BuildError(
            f"The default voice engine is '{config['voice']['engine']}', but builds carry "
            "Kokoro only. Set engine back to kokoro in defaults.toml."
        )
    voice = kokoro_voice(config["voice"]["voice"])
    kokoro_settings = tomllib.loads(KOKORO.settings)
    kokoro = [
        kokoro_file("model", kokoro_settings["model"], cache),
        kokoro_file("tokenizer", "", cache),
        kokoro_file("voice", voice.name, cache),
    ]
    wheel = spacy_wheel(cache)
    ensure_files([*kokoro, wheel], config, report)
    for remote in [*kokoro, wheel]:
        _check(remote)
    manifest = {}
    for remote in kokoro:
        relative = remote.path.relative_to(cache).as_posix()
        target = models / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(remote.path, target)
        manifest[relative] = {"sha256": remote.sha256}
    relative = _unpack_spacy(wheel, models)
    manifest[relative] = {"sha256": catalog()["spacy"]["sha256"]}
    (models / bundle.MANIFEST).write_text(
        json.dumps({"files": manifest}, indent=2) + "\n", encoding="utf-8"
    )
    return models / relative


def _check(remote: RemoteFile) -> None:
    digest = hashlib.sha256()
    with remote.path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != remote.sha256:
        raise BuildError(
            f"{remote.path} doesn't match its SHA-256 in catalog.toml. Delete it and build again."
        )


def _unpack_spacy(wheel: RemoteFile, models: Path) -> str:
    """Unpack the checked wheel the way Dogear does, into `models`. Returns where it went."""
    with tempfile.TemporaryDirectory() as scratch:
        scratch_models = Path(scratch)
        copy = spacy_wheel(scratch_models).path
        copy.parent.mkdir(parents=True)
        shutil.copy2(wheel.path, copy)
        unpacked = unpack_spacy_model(scratch_models)  # removes the copy of the wheel
        relative = unpacked.relative_to(scratch_models).as_posix()
        shutil.copytree(unpacked, models / relative)
    return relative
