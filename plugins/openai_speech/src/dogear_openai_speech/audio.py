"""The audio a service sends back, checked and turned into samples for Dogear.

A WAV file says its own sample rate, which must be the one the settings give (Dogear
needs it before the first sentence is spoken). PCM is 16-bit samples, one channel, little
endian, with no header, as OpenAI sends it. Either way the result is one channel of
float32 samples from -1 to 1, with at least one sample and no more than a sentence of
that length could need. Some servers stream WAV with a placeholder size (0 or
0xFFFFFFFF) in the header; the audio then runs to the end of what was sent.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# The longest a sentence's audio may be: generous, for a slow voice at half speed. Normal
# speech is under a tenth of a second a character.
SECONDS_PER_CHARACTER = 0.5
SECONDS_AT_LEAST = 10.0
_STREAMED_SIZES = (0, 0xFFFFFFFF)
_PCM, _FLOAT, _EXTENSIBLE = 1, 3, 0xFFFE


class BadAudio(Exception):
    """What a service sent back isn't audio Dogear can use. `message` says why, `hint`
    what to change, if anything."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass(frozen=True)
class _Format:
    code: int
    channels: int
    rate: int
    bits: int


def longest_seconds(characters: int) -> float:
    return max(SECONDS_AT_LEAST, SECONDS_PER_CHARACTER * characters)


def most_bytes(characters: int, rate: int) -> int:
    """The most a response for a sentence this long is read: enough for its longest audio
    in two channels of 32-bit samples, and a header."""
    return int(longest_seconds(characters) * rate * 8) + (1 << 20)


def decode(data: bytes, response_format: str, rate: int, characters: int) -> np.ndarray:
    """The samples in `data`, checked against `rate` and the sentence's length."""
    if response_format == "wav":
        samples, got = wav_samples(data)
        if got != rate:
            raise BadAudio(
                f"The service sent audio at {got:,} Hz, but sample_rate says {rate:,} Hz.",
                hint=f"Set sample_rate = {got} under [openai_speech].",
            )
    else:
        samples = pcm_samples(data)
    if samples.size == 0:
        raise BadAudio("The service sent back no audio for a sentence.")
    if not np.isfinite(samples).all():
        raise BadAudio("The service sent back audio with samples that aren't numbers.")
    seconds = samples.size / rate
    if seconds > longest_seconds(characters):
        raise BadAudio(
            f"The service sent back {seconds:,.0f} seconds of audio for a sentence of "
            f"{characters:,} characters, far longer than speaking it could take.",
            hint="Check sample_rate and response_format under [openai_speech].",
        )
    return np.clip(samples, -1.0, 1.0).astype(np.float32, copy=False)


def pcm_samples(data: bytes) -> np.ndarray:
    if data[:4] == b"RIFF":
        raise BadAudio(
            "The service sent WAV audio, but response_format says pcm.",
            hint='Set response_format = "wav" under [openai_speech].',
        )
    if len(data) % 2:
        raise BadAudio("The service's PCM audio isn't whole 16-bit samples.")
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def wav_samples(data: bytes) -> tuple[np.ndarray, int]:
    """One channel of samples from a WAV file, and its sample rate."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise BadAudio(
            f"The service sent back something that isn't WAV audio ({_looks_like(data)}).",
            hint="Check that it can send WAV. If it sends only raw PCM, set "
            'response_format = "pcm" under [openai_speech].',
        )
    found: _Format | None = None
    at = 12
    while at + 8 <= len(data):
        name, size = data[at : at + 4], int.from_bytes(data[at + 4 : at + 8], "little")
        start = at + 8
        if name == b"fmt ":
            found = _format(data[start : start + size])
        elif name == b"data":
            if found is None:
                raise BadAudio("The service's WAV audio has its samples before its format.")
            whole = size in _STREAMED_SIZES or start + size > len(data)
            return _samples(data[start:] if whole else data[start : start + size], found)
        at = start + size + (size & 1)  # chunks are padded to an even length
    raise BadAudio("The service's WAV audio has no samples in it.")


def _format(chunk: bytes) -> _Format:
    if len(chunk) < 16:
        raise BadAudio("The service's WAV audio has a broken format header.")
    code = int.from_bytes(chunk[0:2], "little")
    if code == _EXTENSIBLE and len(chunk) >= 26:
        code = int.from_bytes(chunk[24:26], "little")  # the first part of the subformat
    channels = int.from_bytes(chunk[2:4], "little")
    rate = int.from_bytes(chunk[4:8], "little")
    bits = int.from_bytes(chunk[14:16], "little")
    supported = (code == _PCM and bits in (16, 24, 32)) or (code == _FLOAT and bits == 32)
    if not supported or not 1 <= channels <= 8 or rate <= 0:
        raise BadAudio(
            f"The service's WAV audio is in a form Dogear doesn't read (format {code}, "
            f"{bits}-bit, {channels} channels).",
            hint="Dogear reads 16-, 24-, or 32-bit PCM and 32-bit float WAV audio.",
        )
    return _Format(code, channels, rate, bits)


def _samples(raw: bytes, fmt: _Format) -> tuple[np.ndarray, int]:
    width = fmt.bits // 8
    frames = len(raw) // (width * fmt.channels)
    raw = raw[: frames * width * fmt.channels]
    if fmt.code == _FLOAT:
        values = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    elif width == 2:
        values = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        values = (np.frombuffer(raw, dtype="<i4") / 2147483648.0).astype(np.float32)
    else:  # 24-bit: three bytes, the last one signed
        triples = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = triples[:, 0] | (triples[:, 1] << 8) | (triples[:, 2] << 16)
        values = (np.where(ints >= 1 << 23, ints - (1 << 24), ints) / 8388608.0).astype(np.float32)
    channels = values.reshape(frames, fmt.channels)
    return channels.mean(axis=1, dtype=np.float32), fmt.rate


def _looks_like(data: bytes) -> str:
    if not data:
        return "nothing at all"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "it looks like MP3"
    if data[:4] == b"OggS":
        return "it looks like Ogg"
    if data[:4] == b"fLaC":
        return "it looks like FLAC"
    if data.lstrip()[:1] in (b"{", b"<"):
        return "it looks like text"
    return f"it starts with the bytes {data[:4].hex(' ')}"
