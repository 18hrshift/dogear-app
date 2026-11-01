"""The Piper voices this plugin offers (`catalog.toml`), and the files each one needs.

Every voice is two files: the model (ONNX) and its config (JSON, with the phonemes the
model knows and how fast it speaks). Both are pinned by SHA-256 and downloaded into
`piper/` in Dogear's models folder.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from dogear.errors import VoiceError
from dogear.voice.downloads import RemoteFile
from dogear.voice.engines import Voice

FOLDER = "piper"  # in the models folder
# Piper's quality levels, as `dogear voices` says them.
QUALITY = {
    "x_low": "lowest quality",
    "low": "low quality",
    "medium": "medium quality",
    "high": "high quality",
}


@dataclass(frozen=True)
class PiperFiles:
    model: RemoteFile
    config: RemoteFile


@cache
def catalog() -> dict[str, Any]:
    text = resources.files("dogear_piper").joinpath("catalog.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)


def piper_voices() -> list[Voice]:
    """Every voice, best first, in the catalog's order."""
    return [
        Voice(
            name=name,
            language=entry["language"],
            description=entry["description"],
            gender=entry.get("gender", ""),
            quality=QUALITY[entry["quality"]],
        )
        for name, entry in catalog()["voices"].items()
    ]


def sample_rate(name: str) -> int:
    """The sample rate of the voice `name`'s audio, as its config file gives it."""
    return _entry(name)["sample_rate"]


def words_per_minute() -> float:
    """About how many words a minute Piper's voices say at speed 1.0."""
    return catalog()["words_per_minute"]


def voice_files(name: str, models_dir: Path) -> PiperFiles:
    """The model and config files of the voice `name`, where they live in `models_dir`."""
    entry = _entry(name)
    base = f"{catalog()['base_url']}/{entry['path']}"
    folder = models_dir / FOLDER
    return PiperFiles(
        model=_remote(f"{base}/{name}.onnx", entry["model"], folder / f"{name}.onnx"),
        config=_remote(f"{base}/{name}.onnx.json", entry["config"], folder / f"{name}.onnx.json"),
    )


def _remote(url: str, pin: dict[str, Any], path: Path) -> RemoteFile:
    return RemoteFile(url=url, sha256=pin["sha256"], size=pin["size"], path=path)


def _entry(name: str) -> dict[str, Any]:
    entry = catalog()["voices"].get(name)
    if entry is None:
        raise VoiceError(
            f"The Piper voice engine has no voice called '{name}'.",
            hint="Run `dogear voices` to list them.",
        )
    return entry
