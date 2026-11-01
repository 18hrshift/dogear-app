"""Voices that send text off this computer: refused unless allowed, said plainly before any
text is sent, and marked wherever voices are listed. Stand-in engines; nothing is sent."""

import pytest
from engine_fakes import fake_engine, hum_voice

from dogear import cli
from dogear.config import default_config
from dogear.errors import VoiceError
from dogear.voice import make_engine, make_voice
from dogear.voice.cloud import (
    CloudVoice,
    hosts_in_words,
    refuse_unless_allowed,
    run_warning,
    text_goes_to,
    warning,
)
from dogear.voice.engines import choose_voice, configured_voice, find_voice, installed

HOST = "speech.example.com"


def cloud(**attributes):
    voices = attributes.pop("voices", [hum_voice("nova")])
    return fake_engine("cloud", "Cloud", voices, **{"sends_to": HOST, **attributes})


@pytest.fixture
def cloud_installed(installed_engines):
    plugin = cloud()
    installed_engines(plugin)
    return plugin


def test_kokoro_keeps_its_text_on_this_computer(config):
    assert text_goes_to(configured_voice(config), config) is None


def test_an_engine_without_the_method_is_set_aside(installed_engines):
    class Old:
        name, title, license, settings = "old", "Old", "MIT", ""
        reads_misaki_phonemes, about_voices = False, "Best first."
        voices = staticmethod(lambda: [hum_voice("one")])
        prepare = engine = pace = staticmethod(lambda *args: None)

    installed_engines(Old())
    [broken] = installed().broken
    assert broken.problem == "It has no sends_text_to method, so it isn't a voice engine."


@pytest.mark.parametrize("answer", ["", "two words", 42])
def test_a_nonsense_answer_is_a_bug_in_the_engine(installed_engines, config, answer):
    installed_engines(cloud(sends_to=answer))
    with pytest.raises(VoiceError, match="gave .* as where its voice sends text"):
        text_goes_to(find_voice("cloud:nova"), config)


def test_make_refuses_before_reading_the_book_and_nothing_is_sent(cloud_installed, capsys):
    """The book isn't there: the refusal comes first, so it wasn't even read. The engine
    was never made, so nothing could be sent."""
    assert cli.main(["make", "no-such-book.epub", "--voice", "cloud:nova"]) == 1
    err = capsys.readouterr().err
    assert (
        f"Error: The voice cloud:nova sends the text it speaks to {HOST}, off this computer. "
        "Dogear doesn't send text off this computer unless you allow it, so nothing was sent."
    ) in err
    assert "add --allow-cloud" in err and "allow_cloud = true under [voice]" in err
    assert cloud_installed.made == [] and cloud_installed.prepared == []


def test_say_refuses_too(cloud_installed, tmp_path, capsys):
    output = tmp_path / "test.wav"
    assert cli.main(["say", "Hello.", "-o", str(output), "--voice", "cloud:nova"]) == 1
    assert "nothing was sent" in capsys.readouterr().err
    assert cloud_installed.made == [] and not output.exists()


def test_make_engine_refuses_whatever_called_it(cloud_installed, config):
    choose_voice(config, "cloud:nova")
    with pytest.raises(VoiceError, match="unless you allow it"):
        make_engine(config, print)
    assert cloud_installed.made == []


def test_allowed_for_one_run_it_says_where_the_text_goes_once(cloud_installed, tmp_path, capsys):
    output = tmp_path / "test.wav"
    args = ["say", "Hello there. Goodbye.", "-o", str(output), "--voice", "nova", "--allow-cloud"]
    assert cli.main(args) == 0
    err = capsys.readouterr().err
    assert err.count(f"The voice nova is spoken by a service at {HOST}") == 1
    assert f"Everything it says is sent to {HOST} as text, one sentence at a time" in err
    assert "own terms and privacy policy apply" in err
    assert "Stopping Dogear stops the sending." in err
    assert len(cloud_installed.made) == 1 and output.is_file()


def test_make_goes_on_to_read_the_book_when_allowed(cloud_installed, capsys):
    assert cli.main(["make", "no-such-book.epub", "--voice", "nova", "--allow-cloud"]) == 1
    err = capsys.readouterr().err
    assert "no-such-book.epub" in err and "unless you allow it" not in err


def test_the_setting_allows_it_every_time(cloud_installed, tmp_path, capsys):
    settings = tmp_path / "settings.toml"
    settings.write_text('[voice]\nengine = "cloud"\nvoice = "nova"\nallow_cloud = true\n')
    output = tmp_path / "test.wav"
    assert cli.main(["say", "Hello.", "-o", str(output), "--config", str(settings)]) == 0
    assert f"spoken by a service at {HOST}" in capsys.readouterr().err


def test_a_voice_on_this_computer_needs_no_permission_and_no_warning(installed_engines, config):
    plugin = cloud(sends_to=None)
    installed_engines(plugin)
    choose_voice(config, "cloud:nova")
    lines = []
    make_engine(config, lines.append)
    assert lines == [] and len(plugin.made) == 1


def test_every_voice_a_run_uses_is_checked(installed_engines, config):
    installed_engines(cloud(), fake_engine("hum", "Hum", [hum_voice("low")]))
    voices = [find_voice("hum:low"), find_voice("cloud:nova")]
    with pytest.raises(VoiceError, match="cloud:nova"):
        refuse_unless_allowed(voices, config)
    config["voice"]["allow_cloud"] = True
    [found] = refuse_unless_allowed(voices, config)
    assert (found.chosen.spec, found.host) == ("cloud:nova", HOST)
    said = warning(found)[0]
    assert said == f"The voice nova is spoken by a service at {HOST}, not on this computer."


def test_a_runs_warning_names_each_cloud_voice_and_each_computer_once(installed_engines):
    installed_engines(
        cloud(voices=[hum_voice("nova"), hum_voice("sage")]),
        fake_engine("far", "Far", [hum_voice("hanna")], sends_to="far.example"),
        fake_engine("hum", "Hum", [hum_voice("low")]),
    )
    nova, sage, hanna = (
        CloudVoice(find_voice("cloud:nova"), HOST),
        CloudVoice(find_voice("cloud:sage"), HOST),
        CloudVoice(find_voice("far:hanna"), "far.example"),
    )
    assert run_warning([nova], find_voice("cloud:nova")) == warning(nova)  # as make says it
    assert hosts_in_words([nova, sage]) == HOST
    assert hosts_in_words([nova, hanna]) == f"{HOST} and far.example"
    third = CloudVoice(find_voice("hum:low"), "c.example")
    assert hosts_in_words([nova, hanna, third]) == f"{HOST}, far.example, and c.example"
    said = run_warning([sage], find_voice("hum:low"))
    assert said[0].endswith("Your settings have it read passages in another language.")
    assert said[-1].endswith("out of [voice.languages] in your settings file.")


def test_voices_marks_them_and_says_how_to_allow_them(cloud_installed, capsys):
    assert cli.main(["voices"]) == 0
    out = capsys.readouterr().out
    assert f"1. nova: American English (sends the book's text to {HOST}).\n" in out
    assert "1. af_heart: American English, female, grade A. This is the one" in out
    assert "Dogear uses one only if you allow it: add --allow-cloud" in out


def test_voices_says_when_the_settings_allow_them(cloud_installed, tmp_path, capsys):
    settings = tmp_path / "settings.toml"
    settings.write_text("[voice]\nallow_cloud = true\n")
    assert cli.main(["voices", "--config", str(settings)]) == 0
    assert "Your settings allow them: allow_cloud is true under [voice]." in capsys.readouterr().out


def test_voices_says_nothing_about_cloud_voices_when_there_are_none(capsys):
    assert cli.main(["voices"]) == 0
    assert "allow_cloud" not in capsys.readouterr().out


def test_download_says_a_cloud_voice_needs_the_internet(cloud_installed, capsys):
    assert cli.main(["download", "--voice", "cloud:nova"]) == 0
    assert capsys.readouterr().out == (
        f"The voice nova has nothing to download: it is spoken by a service at {HOST}, so it "
        "needs the internet, and sends the text it speaks there.\n"
    )


# Voices that come from an engine's settings.


class SettingsVoices:
    """An engine whose voices are listed in its settings table, as a service's are."""

    name, title, license = "service", "Service", "Apache-2.0"
    settings = 'voices = ["alloy"]\n'
    reads_misaki_phonemes, about_voices = False, "As your settings list them."

    def voices(self):
        return self.configured_voices({"service": {"voices": ["alloy"]}})

    def configured_voices(self, config):
        return [hum_voice(name) for name in config["service"]["voices"]]

    def prepare(self, config, voice, report):
        pass

    def engine(self, config, voice, pronunciations, report):
        raise AssertionError("not made in these tests")

    def pace(self, config, voice):
        raise AssertionError("not asked in these tests")

    def sends_text_to(self, config, voice):
        return HOST


@pytest.fixture
def service(installed_engines):
    installed_engines(SettingsVoices())
    return installed().get("service")


@pytest.fixture
def service_config(service):
    config = default_config()
    config["service"]["voices"] = ["onyx", "sage"]
    return config


def test_voices_set_in_the_settings_are_the_ones_offered(service, service_config):
    config = service_config
    assert [v.name for v in service.voices] == ["alloy"]  # the defaults
    assert [v.name for v in service.voices_in(config)] == ["onyx", "sage"]
    assert choose_voice(config, "sage").spec == "service:sage"
    assert configured_voice(config).voice.name == "sage"
    with pytest.raises(VoiceError, match="no voice called 'alloy'"):
        find_voice("service:alloy", config)


def test_voices_from_the_settings_are_checked_like_any(service, service_config):
    config = service_config
    config["service"]["voices"] = ["a:b"]
    with pytest.raises(VoiceError, match="can't use the voices its settings give") as err:
        service.voices_in(config)
    assert "[service]" in err.value.hint


def test_dogear_voices_lists_the_voices_the_settings_give(service, tmp_path, capsys):
    settings = tmp_path / "settings.toml"
    settings.write_text('[service]\nvoices = ["onyx"]\n')
    assert cli.main(["voices", "--config", str(settings)]) == 0
    out = capsys.readouterr().out
    assert "Service, licensed Apache-2.0: 1 voice. As your settings list them." in out
    assert f"1. onyx: American English (sends the book's text to {HOST})." in out


def test_a_cloud_voice_for_one_language_stops_make_before_the_book_is_read(
    installed_engines, tmp_path, capsys
):
    """Kokoro is the main voice, but French passages would go to a cloud voice: that is
    refused as well, before anything is read or sent."""
    plugin = cloud(voices=[hum_voice("nova_fr", "fr-FR", description="French")])
    installed_engines(plugin)
    settings = tmp_path / "settings.toml"
    settings.write_text('[voice.languages]\nfr = "cloud:nova_fr"\n', encoding="utf-8")
    assert cli.main(["make", "no-such-book.epub", "--config", str(settings)]) == 1
    assert "The voice cloud:nova_fr sends the text it speaks to" in capsys.readouterr().err
    assert plugin.made == [] and plugin.prepared == []


def test_make_voice_checks_whichever_voice_it_is_given(cloud_installed, config):
    with pytest.raises(VoiceError, match="unless you allow it"):
        make_voice(config, find_voice("cloud:nova"), print)
    assert cloud_installed.made == []
