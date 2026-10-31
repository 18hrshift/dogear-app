"""Stand-in voice engine plugins, installed for a test through stand-in entry points."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from fake_voice import HumEngine

from dogear.voice.engines import Voice, VoicePace


def hum_voice(name: str, language: str = "en-US", **fields: str) -> Voice:
    return Voice(name, language, fields.pop("description", "American English"), **fields)


@dataclass
class FakePlugin:
    """A voice engine plugin whose voices hum. Records what Dogear asked of it."""

    name: str
    title: str
    voice_list: list[Voice]
    license: str = "MIT"
    settings: str = ""
    reads_misaki_phonemes: bool = False
    about_voices: str = "Best first."
    sends_to: str | None = None  # where its voices send text: None, this computer
    sample_rate: int = HumEngine.sample_rate  # of the audio its voices hum
    prepared: list[str] = field(default_factory=list)
    made: list[dict[str, Any]] = field(default_factory=list)
    engine_made: HumEngine | None = None

    def voices(self) -> list[Voice]:
        return list(self.voice_list)

    def sends_text_to(self, config, voice) -> str | None:
        return self.sends_to

    def prepare(self, config, voice, report) -> None:
        self.prepared.append(voice.name)

    def engine(self, config, voice, pronunciations, report) -> HumEngine:
        self.made.append({"config": config, "voice": voice, "pronunciations": pronunciations})
        self.engine_made = HumEngine(self.sample_rate)
        return self.engine_made

    def pace(self, config, voice) -> VoicePace:
        return VoicePace(self.sample_rate, 150 * config["voice"]["speed"])


def fake_engine(name: str, title: str, voices: list[Voice], **attributes: Any) -> FakePlugin:
    return FakePlugin(name, title, voices, **attributes)


class FakeEntryPoint:
    """What importlib.metadata gives for an entry point: a name, a package, and load()."""

    def __init__(self, name: str, loads: Any, package: str = "dogear-fake", urls=()) -> None:
        self.name = name
        self.value = f"{package.replace('-', '_')}:ENGINE"
        self._loads = loads
        metadata = {"Project-URL": list(urls)}
        self.dist = SimpleNamespace(
            name=package,
            version="1.0",
            metadata=SimpleNamespace(get_all=lambda key: metadata.get(key)),
        )

    def load(self) -> Any:
        if isinstance(self._loads, BaseException):
            raise self._loads
        return self._loads


def entry_point_for(plugin: Any, name: str | None = None, **kwargs: Any) -> FakeEntryPoint:
    return FakeEntryPoint(name or plugin.name, plugin, **kwargs)
