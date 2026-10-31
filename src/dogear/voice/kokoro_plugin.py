"""Kokoro, Dogear's built-in voice engine, as a plugin like any other.

Dogear's own pyproject.toml registers `ENGINE` under the `dogear.engines` entry points,
the way a plugin package registers its engine. Its model files are downloaded on first
use and checked against `catalog.toml`. A standalone build carries the default voice's
files (see `dogear.bundle`), so it downloads only other voices. One Dogear at a time
downloads into a models folder (`downloads.download_lock`).

Importing this module is cheap: onnxruntime, misaki, and spaCy load only when a voice
is made, because every command lists the engines when it loads its settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from dogear import bundle
from dogear.config import Config
from dogear.errors import VoiceError
from dogear.paths import models_dir
from dogear.pronunciations import PronunciationDictionary
from dogear.tools import find_tool
from dogear.voice.catalog import (
    VoiceInfo,
    catalog,
    kokoro_file,
    kokoro_voice,
    kokoro_voices_best_first,
    spacy_model_dir,
    spacy_wheel,
)
from dogear.voice.downloads import RemoteFile, Report, download_lock, ensure_files
from dogear.voice.engine import VoiceEngine
from dogear.voice.engines import Voice, VoicePace
from dogear.voice.spacy_model import unpack_spacy_model

_LANGUAGES = {"American": "en-US", "British": "en-GB"}


class KokoroPlugin:
    name = "kokoro"
    title = "Kokoro"
    license = "Apache-2.0"
    reads_misaki_phonemes = True
    about_voices = "Best rated first. Grades are Kokoro's own, A best."

    @property
    def settings(self) -> str:
        return resources.files("dogear.voice").joinpath("kokoro.toml").read_text(encoding="utf-8")

    def voices(self) -> list[Voice]:
        return [
            Voice(
                name=v.name,
                language=_LANGUAGES[v.accent],
                description=f"{v.accent} English",
                gender=v.gender,
                quality=f"grade {v.grade}",
            )
            for v in kokoro_voices_best_first()
        ]

    def prepare(self, config: Config, voice: Voice, report: Report) -> None:
        prepare_kokoro(config, report, voice.name)

    def pace(self, config: Config, voice: Voice) -> VoicePace:
        kokoro = catalog()["kokoro"]
        return VoicePace(
            kokoro["sample_rate"], kokoro["words_per_minute"] * config["voice"]["speed"]
        )

    def sends_text_to(self, config: Config, voice: Voice) -> None:
        return None  # Kokoro runs on this computer

    def engine(
        self,
        config: Config,
        voice: Voice,
        pronunciations: PronunciationDictionary,
        report: Report,
    ) -> VoiceEngine:
        # Found first, so a missing program is reported before a long download.
        espeak = find_tool("espeak_ng", config)
        setup = prepare_kokoro(config, report, voice.name)

        from dogear.voice.g2p import EnglishG2P
        from dogear.voice.kokoro import KokoroEngine, check_phonemes, load_vocab

        check_phonemes(pronunciations, load_vocab(setup.tokenizer.path))
        kokoro = catalog()["kokoro"]
        return KokoroEngine(
            model=setup.model.path,
            tokenizer=setup.tokenizer.path,
            voice=setup.voice_file.path,
            g2p=EnglishG2P(
                spacy_model_dir(models_dir(config)),
                setup.voice.british,
                espeak,
                pronunciations,
                espeak_data=bundle.espeak_data(espeak),
            ),
            speed=config["voice"]["speed"],
            threads=config["kokoro"]["threads"],
            sample_rate=kokoro["sample_rate"],
            max_phonemes=kokoro["max_phonemes"],
            identity=f"kokoro|{setup.model.sha256}|{setup.voice_file.sha256}",
        )


ENGINE = KokoroPlugin()


@dataclass(frozen=True)
class KokoroSetup:
    voice: VoiceInfo
    model: RemoteFile
    tokenizer: RemoteFile
    voice_file: RemoteFile


def prepare_kokoro(config: Config, report: Report, voice_name: str | None = None) -> KokoroSetup:
    """Check the Kokoro settings and make sure every file the voice needs is on disk.
    The voice is `voice_name`, or the one the settings choose."""
    voice = kokoro_voice(voice_name or config["voice"]["voice"])
    build = config["kokoro"]["model"]
    builds = catalog()["kokoro"]["models"]
    if build not in builds:
        raise VoiceError(
            f"kokoro.model is '{build}', which isn't a Kokoro model build.",
            hint=f"Use one of: {', '.join(builds)}.",
        )
    models = models_dir(config)
    setup = KokoroSetup(
        voice=voice,
        model=kokoro_file("model", build, models),
        tokenizer=kokoro_file("tokenizer", "", models),
        voice_file=kokoro_file("voice", voice.name, models),
    )
    _ensure_downloaded([setup.model, setup.tokenizer, setup.voice_file], models, config, report)
    return setup


def prepare_spacy(config: Config, report: Report) -> Path:
    """spaCy's English model folder, downloading it (about 13 MB) first if needed.

    Enough for misaki to tag and look up words without the Kokoro model, as
    `dogear words` does.
    """
    return _ensure_downloaded([], models_dir(config), config, report)


def _ensure_downloaded(
    files: list[RemoteFile], models: Path, config: Config, report: Report
) -> Path:
    """Make sure `files` and spaCy's unpacked model are on disk, downloading whatever
    isn't, under the models folder's lock. Returns spaCy's model folder."""
    spacy = spacy_model_dir(models)
    if spacy.is_dir() and all(f.path.is_file() for f in files):
        return spacy  # nothing to download, so no lock, and nothing written
    with download_lock(models, report):
        # Asked again: another Dogear may have downloaded them while this one waited.
        ensure_files([*files, *_spacy_download(models)], config, report)
        return unpack_spacy_model(models)


def _spacy_download(models: Path) -> list[RemoteFile]:
    """The spaCy wheel, unless its model folder is already unpacked."""
    return [] if spacy_model_dir(models).is_dir() else [spacy_wheel(models)]
