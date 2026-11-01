"""A service's voice as Dogear's renderer uses it: one request per sentence.

Planning is quick and sends nothing: the payload is the sentence on one line. The cache
key covers everything that changes the audio (the service's address, model, voice, speed,
instructions, and audio format), never the key. Rendering sends the sentence and checks
the audio that comes back (audio.py).
"""

from __future__ import annotations

import hashlib

import numpy as np
from dogear.errors import VoiceError
from dogear.voice.engine import SpeechPlan

from dogear_openai_speech.address import TABLE
from dogear_openai_speech.audio import BadAudio, decode, most_bytes
from dogear_openai_speech.client import SpeechClient
from dogear_openai_speech.settings import Settings


class ServiceVoice:
    def __init__(self, settings: Settings, voice: str, speed: float, client: SpeechClient):
        self.settings = settings
        self.sample_rate = settings.sample_rate
        self._voice = voice
        self._speed = speed
        self._client = client
        self._identity = "\n".join(
            [
                "openai_speech",
                settings.address.base_url,
                settings.model,
                voice,
                f"speed={speed:.6g}",
                f"instructions={settings.instructions}",
                f"{settings.response_format}@{settings.sample_rate}",
            ]
        )

    def plan(self, text: str) -> SpeechPlan:
        payload = " ".join(text.split())
        if len(payload) > self.settings.max_input_chars:
            raise VoiceError(
                f"A sentence is {len(payload):,} characters long, but the service takes at "
                f"most {self.settings.max_input_chars:,} at once.",
                hint=f"Set max_sentence_chars under [speech] to {self.settings.max_input_chars} "
                "or less.",
            )
        key = hashlib.sha256(f"{self._identity}\n{payload}".encode()).hexdigest()
        return SpeechPlan(key=key, payload=payload)

    def render(self, plan: SpeechPlan) -> np.ndarray:
        characters = len(plan.payload)
        rate = self.settings.sample_rate
        answer = self._client.speak(
            self._voice, plan.payload, self._speed, most_bytes(characters, rate)
        )
        try:
            return decode(answer.audio, self.settings.response_format, rate, characters)
        except BadAudio as e:
            raise VoiceError(
                f"{e.message} It is the service at {self.settings.address.host}.",
                hint=e.hint or f"Check model, response_format, and sample_rate under [{TABLE}].",
            ) from None
