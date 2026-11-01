"""The plugin installed beside Dogear: found through its entry point, its voices listed and
chosen like Kokoro's, its settings checked like Dogear's, and its files downloaded through
Dogear's pinned downloads. onnxruntime is faked; no file is really downloaded."""

import pytest
from dogear import cli
from dogear.config import load_config
from dogear.errors import ConfigError, VoiceError
from dogear.unknown_words import WordsRequest, find_unknown_words
from dogear.voice import VoicePace, check, downloads, make_engine, prepare_voice, voice_pace
from dogear.voice.engines import choose_voice, find_voice, installed
from dogear.web import choices
from piper_fakes import write_voice_config

from dogear_piper.catalog import voice_files, words_per_minute
from dogear_piper.plugin import ENGINE

VOICE = "en_US-ljspeech-high"


def test_dogear_finds_the_engine_through_its_entry_point():
    found = installed()
    piper = found.get("piper")
    assert piper is not None, found.broken
    assert piper.package == "dogear-piper 0.1.0"
    assert piper.plugin.license == "GPL-3.0-or-later"
    assert piper.report_to == "https://github.com/18hrshift/dogear-app/issues"
    assert found.get("kokoro") is not None  # Kokoro is still there


def test_its_voices_are_listed_in_their_own_group(capsys):
    assert cli.main(["voices"]) == 0
    out = capsys.readouterr().out
    assert "Piper, licensed GPL-3.0-or-later: 8 voices. Highest quality first." in out
    assert "1. en_US-ljspeech-high: American English, female, high quality.\n" in out
    assert out.index("Kokoro, licensed") < out.index("Piper, licensed")


def test_its_voices_are_chosen_by_name_or_with_the_engines_name(config):
    assert find_voice(VOICE).spec == f"piper:{VOICE}"
    choose_voice(config, "piper:en_GB-cori-high")
    assert (config["voice"]["engine"], config["voice"]["voice"]) == ("piper", "en_GB-cori-high")


def test_the_web_page_offers_them_under_piper(config):
    groups = {group.label: group for group in choices.voice_options(config)}
    assert list(groups) == ["Kokoro voices", "Piper voices"]
    assert groups["Piper voices"].options[0].value == f"piper:{VOICE}"


def test_its_settings_are_the_piper_table_and_checked_like_dogears(tmp_path):
    assert load_config()["piper"] == {"threads": 0}
    path = tmp_path / "mine.toml"
    path.write_text("[piper]\nthreads = 2\n", encoding="utf-8")
    assert load_config(path)["piper"]["threads"] == 2
    path.write_text("[piper]\nvoice = 'x'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown setting 'piper.voice'"):
        load_config(path)


@pytest.fixture
def fetched(monkeypatch):
    """Dogear's real ensure_files, choosing what's missing; the download itself writes
    stand-ins: a voice config in Piper's shape, or a few bytes for a model."""
    seen = []

    def fake_download(remote, timeout, report):
        seen.append((remote.url, remote.sha256, remote.path.name))
        if remote.path.suffix == ".json":
            write_voice_config(remote.path)
        else:
            remote.path.parent.mkdir(parents=True, exist_ok=True)
            remote.path.write_bytes(b"stand-in model")

    monkeypatch.setattr(downloads, "download", fake_download)
    return seen


def test_dogear_download_gets_both_files_once(config, models, fetched):
    choose_voice(config, VOICE)
    prepare_voice(config, print)
    files = voice_files(VOICE, models)
    assert fetched == [
        (files.model.url, files.model.sha256, f"{VOICE}.onnx"),
        (files.config.url, files.config.sha256, f"{VOICE}.onnx.json"),
    ]
    prepare_voice(config, print)
    assert len(fetched) == 2  # both there: nothing more to fetch


def test_make_engine_speaks_with_the_settings(config, models, fetched, fake_session):
    choose_voice(config, VOICE)
    config["piper"]["threads"] = 2
    config["voice"]["speed"] = 0.8
    engine = make_engine(config, print)
    audio = engine.render(engine.plan("Hello there."))
    [session] = fake_session.instances
    assert session.options.intra_op_num_threads == 2
    assert session.feeds[0]["scales"][1] == pytest.approx(1 / 0.8)
    assert len(audio) > 0
    assert engine.sample_rate == 22050


def test_two_voices_never_share_a_cached_sentence(config, models, fetched, fake_session):
    choose_voice(config, VOICE)
    first = make_engine(config, print).plan("Hello there.")
    choose_voice(config, "en_US-kristin-medium")
    second = make_engine(config, print).plan("Hello there.")
    assert first.payload == second.payload  # the stand-in configs are the same
    assert first.key != second.key


def test_phoneme_entries_are_skipped_and_it_says_so(
    config, models, fetched, fake_session, tmp_path
):
    words = tmp_path / "words.toml"
    words.write_text('[words]\nLanyon = { phonemes = "lˈænjən" }\n', encoding="utf-8")
    config["pronunciation"]["files"] = [str(words)]
    choose_voice(config, VOICE)
    lines = []
    make_engine(config, lines.append)
    assert any("1 phoneme entry" in line and "Piper voice" in line for line in lines)


def test_words_says_why_it_doesnt_apply(config, tmp_path):
    choose_voice(config, VOICE)
    with pytest.raises(VoiceError, match=f"{VOICE} is a Piper voice"):
        find_unknown_words(WordsRequest(tmp_path / "book.epub"), config, print)


def test_kokoro_is_still_the_default(config):
    assert config["voice"]["engine"] == "kokoro"
    assert find_voice("af_heart").engine.name == "kokoro"


def test_its_pace_comes_from_the_catalog_with_nothing_downloaded(config, models):
    choose_voice(config, VOICE)
    config["voice"]["speed"] = 0.9
    assert voice_pace(config) == VoicePace(22050, words_per_minute() * 0.9)
    choose_voice(config, "piper:es_ES-carlfm-x_low")
    assert voice_pace(config).sample_rate == 16000
    assert not any(models.rglob("*.onnx*"))


def test_a_voice_whose_config_disagrees_with_the_catalog_is_refused(
    config, models, fetched, fake_session
):
    choose_voice(config, "piper:es_ES-carlfm-x_low")  # the stand-in config says 22050
    with pytest.raises(VoiceError, match="catalog says 16000 Hz"):
        make_engine(config, print)


def test_it_pins_both_files_of_every_voice(config, models):
    pinned = ENGINE.pinned_files(config)
    assert len(pinned) == 2 * len(ENGINE.voices())
    files = voice_files(VOICE, models)
    assert pinned[:2] == [files.model, files.config]


def test_download_check_checks_its_files_and_downloads_a_damaged_one(config, models, monkeypatch):
    files = voice_files(VOICE, models)
    files.model.path.parent.mkdir(parents=True)
    files.model.path.write_bytes(b"not the model")
    asked = []
    monkeypatch.setattr(check, "download", lambda remote, timeout, report: asked.append(remote))
    lines = []
    check.check_voice_files(config, lines.append, lines.append)
    assert f"1. Damaged: the Piper file {VOICE}.onnx. It doesn't match its checksum." in lines
    assert asked == [files.model]


def test_it_keeps_the_text_on_this_computer(config):
    assert ENGINE.sends_text_to(config, find_voice(VOICE).voice) is None
