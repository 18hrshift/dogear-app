"""The audio a service sends back: read, checked, and turned into one channel of samples."""

import io
import wave

import numpy as np
import pytest
from fake_service import tone

from dogear_openai_speech.audio import BadAudio, decode, most_bytes, wav_samples


def wav(samples: bytes, rate=24000, channels=1, width=2, code=1) -> bytes:
    """A WAV file made by hand, for forms the wave module doesn't write."""
    block = channels * width
    fmt = (
        code.to_bytes(2, "little")
        + channels.to_bytes(2, "little")
        + rate.to_bytes(4, "little")
        + (rate * block).to_bytes(4, "little")
        + block.to_bytes(2, "little")
        + (width * 8).to_bytes(2, "little")
    )
    size = len(samples).to_bytes(4, "little")
    body = b"WAVE" + b"fmt " + len(fmt).to_bytes(4, "little") + fmt + b"data" + size + samples
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def test_sixteen_bit_wav_is_read_at_its_rate():
    samples, rate = wav_samples(tone(0.5))
    assert rate == 24000 and samples.size == 12000 and samples.dtype == np.float32
    assert 0.25 < np.abs(samples).max() < 0.35


def test_two_channels_become_one():
    samples, _ = wav_samples(tone(0.1, channels=2))
    assert samples.size == 2400


def test_float_and_24_bit_wav_are_read():
    floats = np.array([0.5, -0.25], dtype="<f4").tobytes()
    assert wav_samples(wav(floats, width=4, code=3))[0].tolist() == [0.5, -0.25]
    deep = bytes([0, 0, 0x40, 0, 0, 0xC0])  # +0.5 and -0.5 in 24 bits
    assert wav_samples(wav(deep, width=3))[0].tolist() == [0.5, -0.5]


def test_a_streamed_wav_with_a_placeholder_size_runs_to_the_end():
    data = bytearray(wav(b"\x00\x10" * 100))
    data[40:44] = (0xFFFFFFFF).to_bytes(4, "little")  # the data chunk's size
    assert wav_samples(bytes(data))[0].size == 100


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b"", "isn't WAV audio (nothing at all)"),
        (b"ID3\x04" + bytes(100), "isn't WAV audio (it looks like MP3)"),
        (b'{"error": "no"}', "isn't WAV audio (it looks like text)"),
        (wav(b"\x00" * 8, width=1), "in a form Dogear doesn't read"),
        (wav(b"")[:36], "has no samples in it"),
    ],
)
def test_what_isnt_usable_wav_says_why(data, problem):
    with pytest.raises(BadAudio) as error:
        wav_samples(data)
    assert problem in error.value.message


def test_a_rate_other_than_the_settings_says_which_to_set():
    with pytest.raises(BadAudio, match="audio at 22,050 Hz, but sample_rate says 24,000") as error:
        decode(tone(0.5, rate=22050), "wav", 24000, 20)
    assert error.value.hint == "Set sample_rate = 22050 under [openai_speech]."


def test_no_audio_or_far_too_much_is_refused():
    with pytest.raises(BadAudio, match="no audio"):
        decode(wav(b""), "wav", 24000, 20)
    with pytest.raises(BadAudio, match="11 seconds of audio for a sentence of 20 characters"):
        decode(tone(11), "wav", 24000, 20)
    assert decode(tone(9), "wav", 24000, 20).size == 9 * 24000  # at least 10 s is allowed


def test_samples_that_arent_numbers_are_refused():
    broken = np.array([0.1, np.nan], dtype="<f4").tobytes()
    with pytest.raises(BadAudio, match="aren't numbers"):
        decode(wav(broken, width=4, code=3), "wav", 24000, 20)


def test_pcm_is_sixteen_bit_samples_with_no_header():
    samples = decode(np.array([16384, -32768], dtype="<i2").tobytes(), "pcm", 24000, 5)
    assert samples.tolist() == [0.5, -1.0]
    with pytest.raises(BadAudio, match="isn't whole 16-bit samples"):
        decode(b"\x00\x01\x02", "pcm", 24000, 5)
    with pytest.raises(BadAudio, match="sent WAV audio, but response_format says pcm"):
        decode(tone(0.1), "pcm", 24000, 5)


def test_a_response_is_read_only_as_far_as_a_sentence_could_need():
    assert most_bytes(20, 24000) == 10 * 24000 * 8 + (1 << 20)
    assert most_bytes(400, 24000) == 200 * 24000 * 8 + (1 << 20)


def test_the_tone_is_a_real_wav():
    with wave.open(io.BytesIO(tone(0.25))) as read:
        assert (read.getframerate(), read.getnchannels(), read.getnframes()) == (24000, 1, 6000)
