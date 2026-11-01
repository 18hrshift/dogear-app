"""Voices: turning sentences into audio.

`engine.py` defines what a voice gives the renderer, and `engines.py` what a voice
engine plugin gives Dogear, and finds the installed ones. Kokoro (`kokoro_plugin.py`)
comes with Dogear and registers the way any plugin does. This module makes the voice
the settings choose, whatever its engine.
"""

from __future__ import annotations

from dogear.config import Config
from dogear.errors import VoiceError
from dogear.language_match import is_english
from dogear.pronunciations import PronunciationDictionary, dictionary_from_config
from dogear.voice.cloud import refuse_unless_allowed, warning
from dogear.voice.downloads import Report
from dogear.voice.engine import VoiceEngine
from dogear.voice.engines import (
    ChosenVoice,
    EngineVoice,
    VoicePace,
    configured_voice,
    run_plugin,
)

__all__ = ["VoicePace", "make_engine", "make_voice", "prepare_voice", "voice_pace"]


def make_engine(config: Config, report: Report) -> VoiceEngine:
    """The configured voice, downloading its files first if needed."""
    return make_voice(config, configured_voice(config), report)


def make_voice(config: Config, chosen: ChosenVoice, report: Report) -> VoiceEngine:
    """The voice `chosen`, downloading its files first if needed. Every voice a run uses
    is made here, so every one is checked the same way: a voice that sends text off this
    computer is refused unless the settings allow it, and is said to before it is made
    (cloud.py)."""
    for cloud in refuse_unless_allowed([chosen], config):
        report(" ".join(warning(cloud)))
    # Read first, so a mistake in a pronunciation file is reported before a long download.
    pronunciations = dictionary_from_config(config)
    _say_if_phoneme_entries_are_skipped(chosen, pronunciations, report)
    inner = run_plugin(
        chosen.engine,
        "loading its voice",
        lambda: chosen.engine.plugin.engine(config, chosen.voice, pronunciations, report),
    )
    return EngineVoice(chosen, inner)


def prepare_voice(config: Config, report: Report) -> ChosenVoice:
    """Make sure every file the configured voice needs is on disk, without loading it."""
    chosen = configured_voice(config)
    run_plugin(
        chosen.engine,
        "getting its voice files ready",
        lambda: chosen.engine.plugin.prepare(config, chosen.voice, report),
    )
    return chosen


def voice_pace(config: Config, chosen: ChosenVoice | None = None) -> VoicePace:
    """A voice's sample rate and about how many words a minute it says, known without
    loading or downloading anything. The voice is `chosen`, or the configured one."""
    speed = config["voice"]["speed"]
    if speed <= 0:
        raise VoiceError(
            f"voice.speed is {speed}, but it must be more than 0.",
            hint="Use a value from 0.5 to 2.0, such as 0.9 or 1.1.",
        )
    chosen = chosen or configured_voice(config)
    return run_plugin(
        chosen.engine,
        "estimating how long its audio will be",
        lambda: chosen.engine.plugin.pace(config, chosen.voice),
    )


def _say_if_phoneme_entries_are_skipped(
    chosen: ChosenVoice, pronunciations: PronunciationDictionary, report: Report
) -> None:
    """Phoneme entries are written in misaki's symbols, which only some engines read.
    Sounds-like entries are applied before any engine sees the text, so they always work.
    Phoneme entries are English, so a voice of another language never needed them."""
    entries = pronunciations.with_phonemes().entries
    if (
        not entries
        or chosen.engine.plugin.reads_misaki_phonemes
        or not is_english(chosen.voice.language)
    ):
        return
    count = "1 phoneme entry" if len(entries) == 1 else f"{len(entries)} phoneme entries"
    report(
        f"Your pronunciation files have {count}, such as {entries[0].where()}. They are "
        f"skipped: phoneme entries are written in the Kokoro voice's symbols, and "
        f"{chosen.voice.name} is a {chosen.engine.title} voice. Entries that give a "
        "spelling still apply."
    )
