"""Changing a sentence's audio from one sample rate to another, so voices that make audio
at different rates (Kokoro 24 kHz, Piper 22.05 kHz) can share one section's WAV file.

Band-limited interpolation: each output sample is a weighted sum of the input samples
around its moment in time, with weights from a Kaiser-windowed sinc. The ratio of two
whole rates repeats, so the weights are worked out once per phase (polyphase). Going
down in rate, the cutoff moves down with it, so nothing above the new Nyquist frequency
folds back as noise. Pure numpy: about 40 ms for a 5-second sentence, a small part of
the time it takes to speak one.
"""

from __future__ import annotations

from functools import cache
from math import gcd

import numpy as np

_HALF_WIDTH = 32  # input samples on each side of an output sample, at the cutoff
_KAISER_BETA = 8.6  # about 80 dB of stopband attenuation
_ROLL_OFF = 0.95  # the cutoff, as a share of the lower rate's Nyquist frequency
_CHUNK = 16384  # output samples worked out at once, to bound memory


def resample(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """`samples` (mono) at `to_rate` instead of `from_rate`, as float32. The result has
    round(len * to_rate / from_rate) samples, so its length in time is the same."""
    audio = np.asarray(samples, dtype=np.float64)
    if from_rate == to_rate:
        return audio.astype(np.float32)
    if from_rate <= 0 or to_rate <= 0:
        raise ValueError(f"sample rates must be positive, got {from_rate} and {to_rate}")
    common = gcd(from_rate, to_rate)
    up, down = to_rate // common, from_rate // common
    taps, width = _taps(up, down)
    count = round(len(audio) * to_rate / from_rate)
    padded = np.concatenate([np.zeros(width), audio, np.zeros(width + 1)])
    offsets = np.arange(-width + 1, width + 1)
    out = np.empty(count, dtype=np.float64)
    for start in range(0, count, _CHUNK):
        n = np.arange(start, min(start + _CHUNK, count))
        base, phase = np.divmod(n * down, up)  # where each output sample falls in the input
        window = padded[(base + width)[:, None] + offsets[None, :]]
        out[start : start + len(n)] = np.einsum("ij,ij->i", window, taps[phase])
    return out.astype(np.float32)


@cache
def _taps(up: int, down: int) -> tuple[np.ndarray, int]:
    """The weights for each of the `up` phases, and how many input samples each side."""
    cutoff = _ROLL_OFF * min(1.0, up / down)  # of the input's Nyquist frequency
    width = int(np.ceil(_HALF_WIDTH / cutoff))
    offsets = np.arange(-width + 1, width + 1)  # input samples, relative to the base one
    phases = np.arange(up)[:, None] / up  # how far past the base sample each phase falls
    distance = offsets[None, :] - phases  # from the output moment to each input sample
    edge = np.clip(distance / width, -1.0, 1.0)  # the window falls to its edge at ±width
    window = np.i0(_KAISER_BETA * np.sqrt(1.0 - edge**2)) / np.i0(_KAISER_BETA)
    taps = cutoff * np.sinc(cutoff * distance) * window
    return taps / taps.sum(axis=1, keepdims=True), width
