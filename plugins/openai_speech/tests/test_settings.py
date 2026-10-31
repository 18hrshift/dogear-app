"""The [openai_speech] settings: where the text goes, and each mistake explained."""

import pytest
from dogear.config import default_config
from dogear.errors import ConfigError, VoiceError

from dogear_openai_speech.address import is_this_computer, parse_address
from dogear_openai_speech.settings import api_key, read_settings


@pytest.mark.parametrize(
    ("base_url", "host", "local"),
    [
        ("https://api.openai.com/v1", "api.openai.com", False),
        ("https://api.openai.com/v1/", "api.openai.com", False),
        ("http://localhost:8880/v1", "localhost:8880", True),
        ("http://LOCALHOST./v1", "localhost", True),
        ("http://127.0.0.1:8000/v1", "127.0.0.1:8000", True),
        ("http://127.5.6.7/v1", "127.5.6.7", True),
        ("http://[::1]:8880/v1", "[::1]:8880", True),
        ("http://192.168.1.20:8880/v1", "192.168.1.20:8880", False),
        ("http://0.0.0.0:8880/v1", "0.0.0.0:8880", False),
        ("http://localhost.example.com/v1", "localhost.example.com", False),
        ("http://my-server.local/v1", "my-server.local", False),
    ],
)
def test_only_this_computer_keeps_the_text_here(base_url, host, local):
    address = parse_address(base_url)
    assert (address.host, address.local) == (host, local)
    assert address.speech_url.endswith("/v1/audio/speech")


def test_names_are_never_looked_up():
    assert not is_this_computer("my-laptop")  # it might be this computer, but who knows


@pytest.mark.parametrize(
    ("base_url", "problem"),
    [
        ("api.openai.com/v1", "isn't a web address starting with https:// or http://"),
        ("ftp://example.com/v1", "isn't a web address starting with https:// or http://"),
        ("https://example.com:port/v1", "isn't a web address."),
        ("https://example.com/v1?key=1", "has a ? or # part"),
    ],
)
def test_a_wrong_address_says_what_is_wrong(base_url, problem):
    with pytest.raises(ConfigError, match="openai_speech.base_url") as error:
        parse_address(base_url)
    assert problem in error.value.message


def test_an_address_with_a_password_isnt_repeated_back():
    with pytest.raises(ConfigError) as error:
        parse_address("https://me:secret@example.com/v1")
    assert "secret" not in error.value.message + error.value.hint
    assert "api_key_env" in error.value.hint


@pytest.fixture
def table():
    config = default_config()
    return config, config["openai_speech"]


def test_the_defaults_are_openais(table):
    config, _ = table
    settings = read_settings(config)
    assert settings.address.host == "api.openai.com"
    assert settings.model == "gpt-4o-mini-tts"
    assert settings.voices[:2] == ("marin", "cedar")
    assert [v.name for v in settings.voice_list()][-1] == "verse"
    assert settings.voice_list()[0].description == "American English"


@pytest.mark.parametrize(
    ("name", "value", "problem"),
    [
        ("model", " ", "openai_speech.model is empty"),
        ("voices", [], "openai_speech.voices is empty"),
        ("voices", ["a b"], "needs no spaces or colons"),
        ("voices", ["nova", "nova"], "lists a voice twice"),
        ("language", "American", "isn't a language tag"),
        ("api_key_env", "sk-12345", "isn't the name of an environment variable"),
        ("response_format", "mp3", "openai_speech.response_format is 'mp3'"),
        ("sample_rate", 100, "openai_speech.sample_rate is 100"),
        ("retries", 50, "openai_speech.retries is 50"),
        ("timeout_seconds", 0, "must be more than 0"),
    ],
)
def test_each_mistake_says_what_is_wrong(table, name, value, problem):
    config, settings = table
    settings[name] = value
    with pytest.raises(ConfigError) as error:
        read_settings(config)
    assert problem in error.value.message


def test_a_key_is_never_sent_unencrypted_to_another_computer(table):
    config, settings = table
    settings["base_url"] = "http://speech.example.com/v1"
    with pytest.raises(ConfigError, match="would cross the network to speech.example.com"):
        read_settings(config)
    settings["api_key_env"] = ""  # no key, so plain http is allowed
    assert read_settings(config).address.host == "speech.example.com"
    settings["api_key_env"] = "OPENAI_API_KEY"
    settings["base_url"] = "http://localhost:8880/v1"  # on this computer, it never crosses
    assert read_settings(config).address.local


def test_the_key_comes_from_the_environment(table, monkeypatch):
    config, _ = table
    monkeypatch.setenv("OPENAI_API_KEY", " sk-test \n")
    assert api_key(read_settings(config)) == "sk-test"


@pytest.mark.parametrize(
    ("key", "problem"),
    [
        ("sk-proj-FIRSTPART\nsk-proj-SECONDPART", "a line break"),
        ("sk-proj-FIRSTPART\rSECONDPART", "a line break"),
        ("sk-proj-FIRSTPART\x01SECONDPART", "a control character"),
        ("sk-proj-FIRSTPART\tSECONDPART", "a control character"),
        ("sk-proj-FIRSTPART\x85SECONDPART", "a control character"),
        ("sk-proj-FIRSTPART SECONDPART", "a space"),
        ("sk-proj-FIRSTPART’SECONDPART", "a character outside Latin-1"),
    ],
)
def test_a_key_a_request_cant_carry_is_refused_without_showing_it(table, monkeypatch, key, problem):
    config, _ = table
    monkeypatch.setenv("OPENAI_API_KEY", key)
    with pytest.raises(VoiceError) as error:
        api_key(read_settings(config))
    said = error.value.message + error.value.hint + str(error.value)
    assert f"OPENAI_API_KEY has {problem} in it" in error.value.message
    assert "FIRSTPART" not in said and "SECONDPART" not in said and "sk-proj" not in said


def test_a_key_may_have_any_printable_latin_1_character(table, monkeypatch):
    config, _ = table
    monkeypatch.setenv("OPENAI_API_KEY", "sk-AZaz09_-.~+/=é")
    assert api_key(read_settings(config)) == "sk-AZaz09_-.~+/=é"


def test_a_missing_key_for_another_computer_says_how_to_give_it(table):
    config, _ = table
    with pytest.raises(VoiceError, match="OPENAI_API_KEY has no key in it") as error:
        api_key(read_settings(config))
    assert "api_key_env" in error.value.hint


def test_a_server_on_this_computer_needs_no_key(table, monkeypatch):
    config, settings = table
    settings["base_url"] = "http://127.0.0.1:8880/v1"
    assert api_key(read_settings(config)) == ""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local")
    assert api_key(read_settings(config)) == "sk-local"  # given if it is there
    settings["api_key_env"] = ""
    assert api_key(read_settings(config)) == ""
