"""Resampling a sentence's audio to another sample rate, checked against tones whose
true values at any moment are known."""

import numpy as np
import pytest

from dogear.render.resample import resample


def tone(hz: float, rate: int, seconds: float = 0.5) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return 0.5 * np.sin(2 * np.pi * hz * t)


@pytest.mark.parametrize(("source", "target"), [(22050, 24000), (24000, 22050), (16000, 24000)])
def test_a_tone_keeps_its_pitch_and_level(source, target):
    moved = resample(tone(440, source), source, target)
    assert moved.dtype == np.float32
    assert len(moved) == round(len(tone(440, source)) * target / source)
    expected = tone(440, target)[: len(moved)]
    middle = slice(200, len(moved) - 200)  # away from the edges, where the input stops
    assert np.max(np.abs(moved[middle] - expected[middle])) < 1e-3


def test_going_down_takes_out_what_the_new_rate_cant_hold():
    # 11.5 kHz fits under 24 kHz's Nyquist frequency (12 kHz) but not 22.05 kHz's.
    moved = resample(tone(11500, 24000), 24000, 22050)
    assert np.max(np.abs(moved[200:-200])) < 0.01


def test_the_same_rate_changes_nothing():
    samples = tone(440, 24000)
    assert np.array_equal(resample(samples, 24000, 24000), samples.astype(np.float32))


def test_silence_stays_silent_and_empty_stays_empty():
    assert not np.any(resample(np.zeros(1000), 22050, 24000))
    assert len(resample(np.zeros(0), 22050, 24000)) == 0


def test_long_audio_is_worked_out_in_pieces_alike():
    long = tone(300, 22050, seconds=2.0)  # more output samples than one piece holds
    whole = resample(long, 22050, 24000)
    short = resample(long[:22050], 22050, 24000)
    assert np.allclose(whole[: len(short) - 100], short[:-100], atol=1e-6)


def test_rates_must_be_positive():
    with pytest.raises(ValueError):
        resample(np.zeros(10), 0, 24000)
