"""An OpenAI-compatible speech service as a Dogear voice engine: what the `dogear.engines`
entry point gives Dogear.

The voices are the ones its settings list, since every service has its own. Where the
text goes comes from `base_url` alone: a server on this computer keeps it here, and any
other address is sent it. Dogear's core asks the user first (see Dogear's
docs/voice-engines.md); nothing here sends anything until a sentence is rendered.
"""

from __future__ import annotations

import tomllib
from importlib import resources

from dogear.config import Config
from dogear.errors import ConfigError
from dogear.pronunciations import PronunciationDictionary
from dogear.voice.downloads import Report
from dogear.voice.engine import VoiceEngine
from dogear.voice.engines import Voice, VoicePace

from dogear_openai_speech.address import TABLE
from dogear_openai_speech.settings import api_key, read_settings


class ServicePlugin:
    name = TABLE
    title = "OpenAI-compatible speech"
    license = "Apache-2.0"  # this plugin's code; the voices are the service's
    reads_misaki_phonemes = False  # it is sent words, never phonemes
    about_voices = (
        "In the order your settings list them, as the service names them. The voices are "
        "the service's, and its terms apply."
    )

    @property
    def settings(self) -> str:
        return (
            resources.files("dogear_openai_speech")
            .joinpath("settings.toml")
            .read_text(encoding="utf-8")
        )

    def voices(self) -> list[Voice]:
        """The voices the default settings list."""
        return self.configured_voices({TABLE: tomllib.loads(self.settings)})

    def configured_voices(self, config: Config) -> list[Voice]:
        return read_settings(config).voice_list()

    def sends_text_to(self, config: Config, voice: Voice) -> str | None:
        address = read_settings(config).address
        return None if address.local else address.host

    def prepare(self, config: Config, voice: Voice, report: Report) -> None:
        read_settings(config)  # nothing to download: only the settings to check

    def pace(self, config: Config, voice: Voice) -> VoicePace:
        settings = read_settings(config)
        return VoicePace(settings.sample_rate, settings.words_per_minute * config["voice"]["speed"])

    def engine(
        self,
        config: Config,
        voice: Voice,
        pronunciations: PronunciationDictionary,
        report: Report,
    ) -> VoiceEngine:
        settings = read_settings(config)
        longest = config["speech"]["max_sentence_chars"]
        if longest > settings.max_input_chars:
            raise ConfigError(
                f"Dogear sends sentences of up to {longest:,} characters ([speech] "
                f"max_sentence_chars), but {TABLE}.max_input_chars says the service takes "
                f"at most {settings.max_input_chars:,}.",
                hint=f"Set max_sentence_chars = {settings.max_input_chars} under [speech].",
            )

        from dogear_openai_speech.client import SpeechClient
        from dogear_openai_speech.engine import ServiceVoice

        client = SpeechClient(settings, api_key(settings), report)
        return ServiceVoice(settings, voice.name, config["voice"]["speed"], client)


ENGINE = ServicePlugin()
