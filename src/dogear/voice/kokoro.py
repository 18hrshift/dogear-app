"""Kokoro-82M (Apache-2.0), run on the CPU with onnxruntime.

Kokoro reads phonemes, not text. Each sentence becomes token ids from the model's
vocabulary, plus a style vector from the voice file chosen by the sentence's length.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

import numpy as np

from dogear.errors import PronunciationError, VoiceError
from dogear.pronunciations import PronunciationDictionary
from dogear.voice.engine import SpeechPlan

log = logging.getLogger(__name__)

_STYLE_WIDTH = 256
_PAD = 0  # token id that starts and ends every input
# Where to split an overlong phoneme string: after punctuation first, then at a space.
_PUNCTUATION_BREAK = re.compile(r"[.!?;:,—…]\s*")
# Keyboard characters people type for a symbol Kokoro writes another way.
_LOOKALIKES = {
    "g": "the g sound is written ɡ, the script g (U+0261), not the keyboard g",
    "'": "stress is written ˈ (U+02C8) before the stressed syllable, or ˌ (U+02CC) for "
    "a lighter stress, not with an apostrophe",
}
PHONEMES_GUIDE = "https://github.com/hexgrad/misaki/blob/main/EN_PHONES.md"
CHECK_FILES = (
    "Run `dogear download --check`. It checks every voice file and downloads the damaged "
    "ones again."
)


class KokoroEngine:
    def __init__(
        self,
        *,
        model: Path,
        tokenizer: Path,
        voice: Path,
        g2p: Callable[[str], str],
        speed: float,
        threads: int,
        sample_rate: int,
        max_phonemes: int,
        identity: str,
    ) -> None:
        import onnxruntime  # slow to import, so only when a voice is actually needed
        from onnxruntime.capi.onnxruntime_pybind11_state import InvalidGraph, InvalidProtobuf

        self.sample_rate = sample_rate
        self._g2p = g2p
        self._speed = speed
        self._max_phonemes = max_phonemes
        self._vocab = load_vocab(tokenizer)
        row_bytes = _STYLE_WIDTH * np.dtype(np.float32).itemsize
        size = voice.stat().st_size
        if size == 0 or size % row_bytes:
            raise damaged(voice, "it isn't the size a voice file is")
        self._styles = np.fromfile(voice, dtype=np.float32).reshape(-1, 1, _STYLE_WIDTH)
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.log_severity_level = 3  # errors only
        try:
            self._session = onnxruntime.InferenceSession(
                str(model), options, providers=["CPUExecutionProvider"]
            )
        # A damaged model fails to parse, as a protobuf or as a graph, or its text parts
        # turn out not to be UTF-8.
        except (InvalidProtobuf, InvalidGraph, UnicodeDecodeError) as e:
            log.info("onnxruntime: %s", e)
            raise damaged(model, "onnxruntime couldn't load it") from None
        self._identity = f"{identity}|speed={speed}"

    def plan(self, text: str) -> SpeechPlan:
        phonemes = self._g2p(text)
        dropped = {c for c in phonemes if c not in self._vocab}
        if dropped:
            log.debug("Kokoro has no token for %s; left out.", "".join(sorted(dropped)))
            phonemes = "".join(c for c in phonemes if c in self._vocab)
        key = hashlib.sha256(f"{self._identity}\n{phonemes}".encode()).hexdigest()
        return SpeechPlan(key=key, payload=phonemes)

    def render(self, plan: SpeechPlan) -> np.ndarray:
        pieces = [self._infer(p) for p in split_phonemes(plan.payload, self._max_phonemes)]
        return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)

    def _infer(self, phonemes: str) -> np.ndarray:
        ids = [self._vocab[c] for c in phonemes]
        feeds = {
            "input_ids": np.array([[_PAD, *ids, _PAD]], dtype=np.int64),
            # As in Kokoro's reference pipeline: the style row for this input length.
            "style": self._styles[len(ids) - 1],
            "speed": np.array([self._speed], dtype=np.float32),
        }
        audio = self._session.run(None, feeds)[0]
        return np.asarray(audio, dtype=np.float32).reshape(-1)


def load_vocab(tokenizer: Path) -> dict[str, int]:
    """Phoneme to token id, from the model's tokenizer.json."""
    try:
        data = json.loads(tokenizer.read_text(encoding="utf-8"))
        return {symbol: int(i) for symbol, i in data["model"]["vocab"].items()}
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise damaged(tokenizer, f"it isn't the tokenizer Kokoro has ({e})") from None


def damaged(path: Path, why: str) -> VoiceError:
    """The error for a voice file that is there but can't be used: most likely damaged."""
    return VoiceError(
        f"The voice can't use {path}: {why}. The file may be damaged.", hint=CHECK_FILES
    )


def check_phonemes(dictionary: PronunciationDictionary, vocab: dict[str, int]) -> None:
    """Stop on a phoneme entry using a symbol Kokoro has no sound for.

    `plan` leaves such symbols out, which suits stray symbols from misaki or espeak-ng,
    but a symbol someone typed into an entry is a mistake to show them.
    """
    for entry in dictionary.entries:
        missing = list(dict.fromkeys(c for c in entry.phonemes or "" if c not in vocab))
        if not missing:
            continue
        named = [_describe_symbol(c) for c in missing]
        named = ", ".join(named[:-1]) + " and " + named[-1] if len(named) > 1 else named[0]
        tips = [_LOOKALIKES[c] for c in missing if c in _LOOKALIKES]
        raise PronunciationError(
            f"The phonemes for {entry.where()} use {named}, which the voice has no sound for.",
            hint=" ".join(
                [
                    *(tip[0].upper() + tip[1:] + "." for tip in tips),
                    f"The symbols are listed at {PHONEMES_GUIDE}. To start from what the voice "
                    'says now, run: dogear say "the word" -o test.wav --phonemes',
                ]
            ),
        )


def _describe_symbol(c: str) -> str:
    """A symbol by its look and its Unicode name, which a screen reader can say clearly."""
    return f"'{c}' ({unicodedata.name(c, f'U+{ord(c):04X}').lower()})"


def split_phonemes(phonemes: str, limit: int) -> list[str]:
    """Pieces of at most `limit` phonemes, broken after punctuation or at spaces if possible."""
    phonemes = phonemes.strip()
    pieces = []
    while len(phonemes) > limit:
        head = phonemes[:limit]
        cut = max((m.end() for m in _PUNCTUATION_BREAK.finditer(head)), default=0)
        if cut == 0:
            cut = head.rfind(" ") + 1 or limit
        pieces.append(phonemes[:cut].strip())
        phonemes = phonemes[cut:].strip()
    if phonemes:
        pieces.append(phonemes)
    return [p for p in pieces if p]
