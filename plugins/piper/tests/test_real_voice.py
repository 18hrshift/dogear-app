"""One sentence with a real Piper voice, if one is downloaded: set DOGEAR_PIPER_TEST_MODELS
to a models folder that has en_US-ljspeech-high in it (`dogear download --voice
piper:en_US-ljspeech-high` with that folder as models.dir). Skips when it isn't set."""

import os
from pathlib import Path

import numpy as np
import pytest
from dogear.voice import make_engine
from dogear.voice.engines import choose_voice

from dogear_piper.catalog import voice_files

VOICE = "en_US-ljspeech-high"
MODELS_ENV = "DOGEAR_PIPER_TEST_MODELS"  # a models folder with the voice downloaded


@pytest.fixture
def real_models() -> Path:
    folder = os.environ.get(MODELS_ENV)
    if not folder:
        pytest.skip(f"{MODELS_ENV} isn't set")
    files = voice_files(VOICE, Path(folder))
    if not (files.model.path.is_file() and files.config.path.is_file()):
        # Asked for by name, so missing files are a mistake, not a reason to skip.
        pytest.fail(f"{MODELS_ENV} is {folder}, but {VOICE} isn't downloaded there")
    return Path(folder)


def test_a_real_voice_says_a_sentence(config, real_models, tmp_path):
    config["models"]["dir"] = str(real_models)
    choose_voice(config, VOICE)
    engine = make_engine(config, print)
    plan = engine.plan("Hello there, this is Dogear.")
    assert "ð" in plan.payload  # the "th" of "there", in eSpeak NG's phonemes
    audio = engine.render(plan)
    seconds = len(audio) / engine.sample_rate
    assert engine.sample_rate == 22050
    assert 1.0 < seconds < 5.0
    assert audio.dtype == np.float32
    assert 0.05 < float(np.abs(audio).max()) <= 1.0
