"""What the renderer speaks with: a voice for each utterance's language, and the one
sample rate every section's audio is written at."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dogear.voice.engine import VoiceEngine


class Voices(Protocol):
    @property
    def sample_rate(self) -> int:
        """The rate the sections' audio is written at. A voice that makes another rate
        is resampled to it."""
        ...

    def for_language(self, lang: str | None) -> VoiceEngine:
        """The voice that says an utterance in `lang` (a BCP 47 tag, or None)."""
        ...


@dataclass(frozen=True)
class OneVoice:
    """One voice for every utterance, at its own rate."""

    engine: VoiceEngine

    @property
    def sample_rate(self) -> int:
        return self.engine.sample_rate

    def for_language(self, lang: str | None) -> VoiceEngine:
        return self.engine
