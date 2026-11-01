"""A Piper voice, run on the CPU with onnxruntime, as Dogear's renderer uses it.

Planning turns a sentence into phonemes with the eSpeak NG built into piper-tts; the
voice's config says which eSpeak NG voice it was trained with. Rendering turns the
phonemes into audio. The book's text goes to eSpeak NG directly, never through Piper's
own text handling, which reads anything inside [[ ]] as phonemes: a book could
otherwise put sounds in the voice's mouth.

Audio is kept at the level the model makes it. Piper's own `synthesize` scales each
sentence to full volume by default; Dogear evens out the volume across each chapter
instead, so one loud sentence can't stand out.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import numpy as np
from dogear.errors import VoiceError
from dogear.voice.engine import SpeechPlan

log = logging.getLogger(__name__)


class PiperEngine:
    def __init__(
        self,
        *,
        model: Path,
        config: Path,
        speed: float,
        threads: int,
        identity: str,
    ) -> None:
        # Slow to import, so only when a voice is actually needed.
        import onnxruntime
        from piper import PhonemeType, PiperConfig, PiperVoice, SynthesisConfig
        from piper.phonemize_espeak import EspeakPhonemizer

        settings = PiperConfig.from_dict(json.loads(config.read_text(encoding="utf-8")))
        if settings.phoneme_type != PhonemeType.ESPEAK or settings.vowel_clusters:
            raise VoiceError(
                f"{model.name} isn't a Piper voice this plugin can read: it doesn't use "
                "eSpeak NG phonemes one symbol at a time.",
                hint="Choose another voice from `dogear voices`.",
            )
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.log_severity_level = 3  # errors only
        session = onnxruntime.InferenceSession(
            str(model), options, providers=["CPUExecutionProvider"]
        )
        self._voice = PiperVoice(session=session, config=settings)
        self._phonemizer = EspeakPhonemizer()
        speaker = settings.default_speaker_id if settings.num_speakers > 1 else None
        self._synthesis = SynthesisConfig(
            speaker_id=speaker,
            length_scale=settings.length_scale / speed,  # longer sounds are a slower pace
            noise_scale=settings.noise_scale,
            noise_w_scale=settings.noise_w_scale,
            normalize_audio=False,
        )
        self.sample_rate = settings.sample_rate
        self._identity = (
            f"{identity}|speaker={speaker}|length={self._synthesis.length_scale:.6g}"
            f"|noise={settings.noise_scale:.6g},{settings.noise_w_scale:.6g}"
        )

    def plan(self, text: str) -> SpeechPlan:
        """The sentence's phonemes, one line for each sentence eSpeak NG finds in it."""
        known = self._voice.config.phoneme_id_map
        lines = []
        spoken = " ".join(text.split())  # one line: line breaks separate eSpeak's sentences
        for sentence in self._phonemizer.phonemize(self._voice.config.espeak_voice, spoken):
            dropped = {p for p in sentence if p not in known}
            if dropped:
                log.debug("The Piper voice has no id for %s; left out.", "".join(sorted(dropped)))
            line = "".join(p for p in sentence if p in known)
            if line.strip():
                lines.append(line)
        payload = "\n".join(lines)
        key = hashlib.sha256(f"{self._identity}\n{payload}".encode()).hexdigest()
        return SpeechPlan(key=key, payload=payload)

    def render(self, plan: SpeechPlan) -> np.ndarray:
        pieces = []
        for line in plan.payload.split("\n"):
            if not line:
                continue
            ids = self._voice.phonemes_to_ids(list(line))
            audio = self._voice.phoneme_ids_to_audio(ids, self._synthesis)
            pieces.append(np.clip(np.asarray(audio, dtype=np.float32).reshape(-1), -1.0, 1.0))
        return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
