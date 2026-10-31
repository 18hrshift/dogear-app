"""The plugin installed beside Dogear: found through its entry point, its voices taken from
its settings and marked with where they send text, refused unless allowed, and speaking
through the fake service when allowed. No request leaves this computer."""

import os
import shutil
from pathlib import Path

import pytest
from dogear import cli
from dogear.config import load_config
from dogear.errors import ConfigError, VoiceError
from dogear.voice import make_engine, voice_pace
from dogear.voice.check import engine_files
from dogear.voice.cloud import text_goes_to
from dogear.voice.engines import choose_voice, find_voice, installed
from dogear.web import choices

from dogear_openai_speech.client import SpeechClient

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
REFUSED = "Dogear doesn't send text off this computer unless you allow it, so nothing was sent."


def settings_file(folder: Path, base_url: str, *more: str) -> str:
    """A settings file pointing the engine at `base_url`, sending no key, with no limit on
    requests a minute (the fake service has none)."""
    path = folder / "settings.toml"
    limits = ['api_key_env = ""', "requests_per_minute = 0"]
    lines = ["[openai_speech]", f'base_url = "{base_url}"', *limits, *more]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_dogear_finds_the_engine_through_its_entry_point():
    engine = installed().get("openai_speech")
    assert engine is not None, installed().broken
    assert engine.package == "dogear-openai-speech 0.1.0"
    assert engine.plugin.license == "Apache-2.0"
    assert engine.report_to == "https://github.com/18hrshift/dogear-app/issues"
    assert [v.name for v in engine.voices][:2] == ["marin", "cedar"]
    assert installed().get("kokoro") is not None


def test_dogear_voices_marks_them_with_where_the_text_goes(capsys):
    assert cli.main(["voices"]) == 0
    out = capsys.readouterr().out
    assert "OpenAI-compatible speech, licensed Apache-2.0: 13 voices. In the order" in out
    assert "1. marin: American English (sends the book's text to api.openai.com).\n" in out
    assert "Dogear uses one only if you allow it: add --allow-cloud" in out


def test_its_voices_and_where_they_go_come_from_the_settings(tmp_path, capsys):
    path = settings_file(tmp_path, "https://speech.example.com/v1", 'voices = ["fritz", "hannah"]')
    assert cli.main(["voices", "--config", path]) == 0
    out = capsys.readouterr().out
    assert "OpenAI-compatible speech, licensed Apache-2.0: 2 voices." in out
    assert "2. hannah: American English (sends the book's text to speech.example.com)." in out
    assert "marin" not in out


def test_a_server_on_this_computer_keeps_the_text_here(config):
    chosen = choose_voice(config, "openai_speech:marin")
    assert text_goes_to(chosen, config) is None
    config["openai_speech"]["base_url"] = "https://api.openai.com/v1"
    assert text_goes_to(chosen, config) == "api.openai.com"


def test_without_permission_nothing_is_sent(service, far_away, tmp_path, capsys):
    path = settings_file(tmp_path, far_away)
    output = tmp_path / "test.wav"
    assert (
        cli.main(["say", "Hello there.", "-o", str(output), "--voice", "marin", "--config", path])
        == 1
    )
    book = str(next(FIXTURES.glob("*.epub")))
    assert cli.main(["make", book, "--voice", "marin", "--config", path, "-o", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert err.count(REFUSED) == 2
    assert "openai_speech:marin sends the text it speaks to speech.invalid" in err
    assert service.sent == [] and not output.exists()


def test_allowed_it_says_where_the_text_goes_then_sends_it_there(
    service, far_away, tmp_path, capsys
):
    path = settings_file(tmp_path, far_away)
    output = tmp_path / "test.wav"
    args = ["say", "Hello there. Goodbye.", "-o", str(output), "--voice", "marin"]
    assert cli.main([*args, "--config", path, "--allow-cloud"]) == 0
    captured = capsys.readouterr()
    assert "The voice marin is spoken by a service at speech.invalid" in captured.err
    assert "Said: Hello there.\nSaid: Goodbye.\n" in captured.out
    assert [(s.host, s.body["voice"], s.body["input"]) for s in service.sent] == [
        ("speech.invalid", "marin", "Hello there."),
        ("speech.invalid", "marin", "Goodbye."),
    ]
    assert output.read_bytes().startswith(b"RIFF")
    # Said again: every sentence comes from Dogear's cache, so nothing more is sent.
    assert cli.main([*args, "--config", path, "--allow-cloud"]) == 0
    assert len(service.sent) == 2


def test_a_server_on_this_computer_needs_no_permission_and_no_warning(service, tmp_path, capsys):
    path = settings_file(tmp_path, service.url)
    output = tmp_path / "test.wav"
    assert cli.main(["say", "Hello.", "-o", str(output), "--voice", "marin", "--config", path]) == 0
    assert "spoken by a service" not in capsys.readouterr().err
    assert len(service.sent) == 1 and service.sent[0].host == "127.0.0.1"


def test_a_missing_key_stops_it_before_anything_is_sent(service, config, monkeypatch):
    monkeypatch.setenv("https_proxy", service.url.removesuffix("/v1"))
    config["openai_speech"]["base_url"] = "https://speech.invalid/v1"
    config["voice"]["allow_cloud"] = True
    choose_voice(config, "marin")
    with pytest.raises(Exception, match="OPENAI_API_KEY has no key in it"):
        make_engine(config, print)
    assert service.sent == []


def test_a_key_with_a_line_break_is_never_shown_or_sent(service, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MY_TTS_KEY", "sk-proj-FIRSTPART\nsk-proj-SECONDPART")
    path = tmp_path / "settings.toml"
    path.write_text(
        f'[openai_speech]\nbase_url = "{service.url}"\napi_key_env = "MY_TTS_KEY"\n',
        encoding="utf-8",
    )
    args = ["say", "Hello.", "-o", str(tmp_path / "test.wav"), "--voice", "marin", "-v"]
    assert cli.main([*args, "--config", str(path)]) == 1
    captured = capsys.readouterr()
    said = captured.out + captured.err
    assert "MY_TTS_KEY has a line break in it" in said
    assert "FIRSTPART" not in said and "SECONDPART" not in said
    assert service.sent == []


def test_an_engine_error_with_the_key_in_it_reaches_no_message_or_log(
    service, config, monkeypatch, caplog
):
    key = "sk-proj-FIRSTPARTSECONDPART"
    monkeypatch.setenv("OPENAI_API_KEY", key)

    def fails(self, data, most_bytes):
        raise ValueError(f"Invalid header value b'Bearer {key}'")

    monkeypatch.setattr(SpeechClient, "_send", fails)
    choose_voice(config, "marin")
    engine = make_engine(config, print)
    caplog.set_level("DEBUG")
    with pytest.raises(VoiceError) as error:
        engine.render(engine.plan("Hello."))
    assert "stopped while speaking a sentence" in error.value.message
    assert "(your key)" in error.value.message
    for text in (error.value.message, error.value.hint, caplog.text):
        assert "FIRSTPART" not in text
    assert "Traceback" in caplog.text  # -v still shows where it failed


def test_sentences_longer_than_the_service_takes_stop_it_first(service, config):
    config["openai_speech"]["max_input_chars"] = 200
    choose_voice(config, "marin")
    with pytest.raises(ConfigError, match="the service takes at most 200") as error:
        make_engine(config, print)
    assert error.value.hint == "Set max_sentence_chars = 200 under [speech]."
    config["speech"]["max_sentence_chars"] = 200
    engine = make_engine(config, print)
    assert engine.render(engine.plan("Hello.")).size > 0


def test_cache_keys_change_with_what_changes_the_audio(service, config):
    choose_voice(config, "marin")
    first = make_engine(config, print).plan("Hello.").key
    config["openai_speech"]["model"] = "tts-1"
    assert make_engine(config, print).plan("Hello.").key != first
    config["openai_speech"]["model"] = "gpt-4o-mini-tts"
    config["voice"]["speed"] = 1.1
    assert make_engine(config, print).plan("Hello.").key != first


def test_it_downloads_nothing(service, config, capsys, tmp_path):
    assert not hasattr(installed().get("openai_speech").plugin, "pinned_files")
    assert not [f for f in engine_files(config) if "OpenAI" in f.label]
    path = settings_file(tmp_path, "https://api.openai.com/v1")
    assert cli.main(["download", "--voice", "marin", "--config", path]) == 0
    assert "has nothing to download: it is spoken by a service at api.openai.com" in (
        capsys.readouterr().out
    )
    assert service.sent == []


def test_its_pace_comes_from_the_settings(config):
    choose_voice(config, "marin")
    config["voice"]["speed"] = 0.9
    config["openai_speech"]["sample_rate"] = 22050
    pace = voice_pace(config)
    assert pace.sample_rate == 22050 and pace.words_per_minute == pytest.approx(135)


def test_the_web_page_offers_its_voices_only_when_allowed(config):
    config["openai_speech"]["base_url"] = "https://api.openai.com/v1"
    groups = [g.label for g in choices.voice_options(config)]
    assert "Kokoro voices" in groups and "OpenAI-compatible speech voices" not in groups
    config["voice"]["allow_cloud"] = True
    groups = {g.label: g for g in choices.voice_options(config)}
    first = groups["OpenAI-compatible speech voices"].options[0]
    assert first.label == "marin: American English (sends the book's text to api.openai.com)"


def test_its_settings_are_checked_like_dogears(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("[openai_speech]\napi_key = 'sk-oops'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown setting 'openai_speech.api_key'"):
        load_config(path)


def test_make_speaks_a_book_through_the_service(service, far_away, tmp_path, capsys):
    if shutil.which("ffmpeg") is None:
        # CI installs it and says so, so a broken runner can't skip this quietly.
        if "ffmpeg" in os.environ.get("DOGEAR_REQUIRE_TOOLS", ""):
            pytest.fail("ffmpeg is required (DOGEAR_REQUIRE_TOOLS) but was not found on PATH")
        pytest.skip("ffmpeg not installed")
    path = settings_file(tmp_path, far_away)
    book = str(next(FIXTURES.glob("*.epub")))
    args = ["make", book, "--sections", "2", "--voice", "openai_speech:marin", "--allow-cloud"]
    assert cli.main([*args, "--format", "m4b", "--config", path, "-o", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert captured.err.count("spoken by a service at speech.invalid") == 1
    [saved] = tmp_path.glob("*.m4b")
    assert f"Saved: {saved}" in captured.out
    assert len(service.sent) > 10 and {s.host for s in service.sent} == {"speech.invalid"}


def test_a_voice_name_kokoro_has_too_needs_the_engines_name(config):
    config["openai_speech"]["voices"] = ["af_heart"]
    with pytest.raises(Exception, match="More than one voice engine has a voice called"):
        find_voice("af_heart", config)
    assert find_voice("openai_speech:af_heart", config).engine.name == "openai_speech"
