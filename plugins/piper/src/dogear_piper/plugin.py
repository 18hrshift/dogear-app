"""Piper as a Dogear voice engine: what the `dogear.engines` entry point gives Dogear.

This package's pyproject.toml registers `ENGINE`. Importing this module is cheap: Piper
and onnxruntime load only when a voice is made.
"""

from __future__ import annotations

from importlib import resources

from dogear.config import Config
from dogear.errors import VoiceError
from dogear.paths import models_dir
from dogear.pronunciations import PronunciationDictionary
from dogear.voice.downloads import RemoteFile, Report, ensure_downloaded
from dogear.voice.engine import VoiceEngine
from dogear.voice.engines import Voice, VoicePace

from dogear_piper.catalog import (
    PiperFiles,
    piper_voices,
    sample_rate,
    voice_files,
    words_per_minute,
)


class PiperPlugin:
    name = "piper"
    title = "Piper"
    license = "GPL-3.0-or-later"
    # Piper reads eSpeak NG's phonemes, not misaki's, so phoneme entries don't apply.
    reads_misaki_phonemes = False
    about_voices = (
        "Highest quality first. Quality is Piper's measure of a voice's size and detail, "
        "not a rating."
    )

    @property
    def settings(self) -> str:
        return resources.files("dogear_piper").joinpath("settings.toml").read_text(encoding="utf-8")

    def voices(self) -> list[Voice]:
        return piper_voices()

    def prepare(self, config: Config, voice: Voice, report: Report) -> None:
        prepare_piper(config, voice.name, report)

    def engine(
        self,
        config: Config,
        voice: Voice,
        pronunciations: PronunciationDictionary,
        report: Report,
    ) -> VoiceEngine:
        files = prepare_piper(config, voice.name, report)

        from dogear_piper.engine import PiperEngine

        engine = PiperEngine(
            model=files.model.path,
            config=files.config.path,
            speed=config["voice"]["speed"],
            threads=config["piper"]["threads"],
            identity=f"piper|{files.model.sha256}|{files.config.sha256}",
        )
        if engine.sample_rate != sample_rate(voice.name):
            raise VoiceError(
                f"The Piper voice {voice.name} makes audio at {engine.sample_rate} Hz, but "
                f"this plugin's catalog says {sample_rate(voice.name)} Hz.",
                hint="This is a bug in the Piper plugin's catalog.toml. Please report it.",
            )
        return engine

    def pace(self, config: Config, voice: Voice) -> VoicePace:
        return VoicePace(sample_rate(voice.name), words_per_minute() * config["voice"]["speed"])

    def sends_text_to(self, config: Config, voice: Voice) -> None:
        return None  # Piper runs on this computer

    def pinned_files(self, config: Config) -> list[RemoteFile]:
        """Every voice's two files, for `dogear download --check`."""
        models = models_dir(config)
        every = [voice_files(voice.name, models) for voice in piper_voices()]
        return [file for files in every for file in (files.model, files.config)]


ENGINE = PiperPlugin()


def prepare_piper(config: Config, name: str, report: Report) -> PiperFiles:
    """The voice's files, downloading whichever aren't on disk yet."""
    models = models_dir(config)
    files = voice_files(name, models)
    ensure_downloaded([files.model, files.config], models, config, report)
    return files
