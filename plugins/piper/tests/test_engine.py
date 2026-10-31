"""The Piper engine with onnxruntime faked: Piper's own eSpeak NG makes the phonemes, and
nothing loads a real model."""

import numpy as np
import pytest
from dogear.errors import VoiceError
from piper_fakes import write_voice_config

from dogear_piper.engine import PiperEngine


@pytest.fixture
def voice_files(tmp_path):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"not a real model")
    config = tmp_path / "voice.onnx.json"
    write_voice_config(config)
    return model, config


def make(voice_files, speed=1.0, threads=0, identity="piper|model|config"):
    model, config = voice_files
    return PiperEngine(model=model, config=config, speed=speed, threads=threads, identity=identity)


def test_plans_are_phonemes_one_line_per_sentence(voice_files, fake_session):
    plan = make(voice_files).plan("Hello there. Good night!")
    lines = plan.payload.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("həlˈoʊ")
    assert lines[1].endswith("!")


def test_text_in_double_brackets_is_read_not_taken_as_phonemes(voice_files, fake_session):
    """Piper's own text handling reads [[ ]] as phonemes. A book's text never gets that."""
    payload = make(voice_files).plan("It was [[ ʃʃʃ ]] night.").payload
    assert "ʃʃʃ" not in payload


def test_keys_cover_the_voice_the_speed_and_the_phonemes(voice_files, fake_session):
    engine = make(voice_files)
    assert engine.plan("Hello.").key == engine.plan("Hello.").key
    assert engine.plan("Hello.").key != engine.plan("Goodbye.").key
    assert make(voice_files, speed=1.1).plan("Hello.").key != engine.plan("Hello.").key
    other = make(voice_files, identity="piper|another model|config")
    assert other.plan("Hello.").key != engine.plan("Hello.").key


def test_render_speaks_each_sentence_and_keeps_audio_in_range(voice_files, fake_session):
    engine = make(voice_files)
    audio = engine.render(engine.plan("Hello there. Good night!"))
    [session] = fake_session.instances
    assert len(session.feeds) == 2  # one run per sentence
    ids = sum(feed["input"].shape[1] for feed in session.feeds)
    assert len(audio) == ids * 256
    assert audio.dtype == np.float32
    assert audio.max() == 1.0  # the fake's 2.0, clipped
    assert engine.sample_rate == 22050


def test_speed_sets_how_long_each_sound_lasts(voice_files, fake_session):
    for speed in (1.0, 0.5):
        engine = make(voice_files, speed=speed)
        engine.render(engine.plan("Hello."))
    normal, slow = (s.feeds[0]["scales"].tolist() for s in fake_session.instances)
    assert normal == pytest.approx([0.667, 1.0, 0.8])
    assert slow == pytest.approx([0.667, 2.0, 0.8])  # half the pace: sounds twice as long


def test_threads_and_the_cpu_go_to_onnxruntime(voice_files, fake_session):
    make(voice_files, threads=3)
    [session] = fake_session.instances
    assert session.options.intra_op_num_threads == 3
    assert session.providers == ["CPUExecutionProvider"]
    assert session.model == str(voice_files[0])


def test_a_voice_of_many_speakers_speaks_with_its_default_one(tmp_path, fake_session):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"")
    config = tmp_path / "voice.onnx.json"
    write_voice_config(config, num_speakers=125, speaker_id_map={"1840": 0, "3698": 1})
    engine = PiperEngine(model=model, config=config, speed=1.0, threads=0, identity="x")
    engine.render(engine.plan("Bonjour."))
    assert fake_session.instances[0].feeds[0]["sid"].tolist() == [0]


def test_a_voice_that_isnt_read_with_espeak_is_refused(tmp_path, fake_session):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"")
    config = tmp_path / "voice.onnx.json"
    write_voice_config(config, phoneme_type="text")
    with pytest.raises(VoiceError, match="isn't a Piper voice this plugin can read"):
        PiperEngine(model=model, config=config, speed=1.0, threads=0, identity="x")


def test_nothing_to_say_is_silence(voice_files, fake_session):
    engine = make(voice_files)
    assert len(engine.render(engine.plan(""))) == 0
