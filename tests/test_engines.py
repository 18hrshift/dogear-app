"""Voice engines as plugins: finding them, their settings, choosing a voice, and running
an engine's code, with stand-in plugins and no real voice."""

import numpy as np
import pytest
from engine_fakes import FakeEntryPoint, entry_point_for, fake_engine, hum_voice
from fake_voice import HumEngine

from dogear import cli
from dogear.config import load_config
from dogear.errors import ConfigError, VoiceError
from dogear.unknown_words import WordsRequest, find_unknown_words
from dogear.voice import engines, make_engine, prepare_voice
from dogear.voice.engine import SpeechPlan
from dogear.voice.engines import choose_voice, configured_voice, find_voice, installed


def hum(**attributes):
    voices = attributes.pop("voices", [hum_voice("low"), hum_voice("emma", "en-GB")])
    return fake_engine(attributes.pop("name", "hum"), "Hum", voices, **attributes)


# Finding the engines.


def test_kokoro_is_registered_through_dogears_own_entry_points():
    [kokoro] = installed().engines
    assert kokoro.name == "kokoro"
    assert kokoro.package.startswith("dogear ")
    assert kokoro.voices[0].name == "af_heart"
    assert kokoro.voices[0].language == "en-US"
    assert kokoro.settings == {"model": "full", "threads": 0}


def test_engines_are_listed_by_name(installed_engines):
    installed_engines(hum(), fake_engine("alto", "Alto", [hum_voice("one")]))
    assert [e.name for e in installed().engines] == ["alto", "hum", "kokoro"]


@pytest.mark.parametrize(
    ("plugin", "problem"),
    [
        (ModuleNotFoundError("No module named 'piper'"), "It failed to load: ModuleNotFoundError"),
        (object(), "It has no name (str), so it isn't a voice engine."),
        (hum(name="humming"), "It calls itself 'humming' but is registered as 'hum'."),
        (hum(settings="threads = "), "Its settings aren't valid TOML"),
        (hum(voices=[]), "It has no voices."),
        (hum(voices=[hum_voice("a:b")]), "needs a name without a colon"),
        (hum(voices=[hum_voice("a"), hum_voice("a")]), "lists the voice 'a' twice"),
        (hum(voices=[hum_voice("a", "English")]), "isn't a BCP 47 tag"),
        (hum(voices=[hum_voice("a", gender="loud")]), "has the gender 'loud'"),
        (hum(voices=["a"]), "which isn't a Voice"),
    ],
)
def test_an_engine_that_cant_be_used_is_set_aside_with_a_reason(installed_engines, plugin, problem):
    installed_engines(FakeEntryPoint("hum", plugin, package="dogear-hum"))
    found = installed()
    assert [e.name for e in found.engines] == ["kokoro"]
    [broken] = found.broken
    assert (broken.name, broken.package) == ("hum", "dogear-hum 1.0")
    assert problem in broken.problem


def test_an_engine_whose_voices_fail_to_list_is_set_aside(installed_engines):
    plugin = hum()
    plugin.voices = lambda: 1 / 0
    installed_engines(plugin)
    [broken] = installed().broken
    assert broken.problem == "It failed to list its voices: ZeroDivisionError: division by zero."


def test_an_engine_named_like_dogears_own_settings_is_set_aside(installed_engines):
    installed_engines(fake_engine("pdf", "PDF voice", [hum_voice("one")]))
    [broken] = installed().broken
    assert broken.problem == "Its name is taken by Dogear's own [pdf] settings."


def test_another_package_cant_replace_kokoro(installed_engines):
    installed_engines(fake_engine("kokoro", "Not Kokoro", [hum_voice("one")]))
    [kokoro] = installed().engines
    assert kokoro.title == "Kokoro"
    [broken] = installed().broken
    assert broken.package == "dogear-fake 1.0"
    assert "already has a voice engine of that name" in broken.problem


def test_dogear_voices_says_plainly_which_engine_failed_and_still_lists_kokoro(
    installed_engines, capsys
):
    installed_engines(
        FakeEntryPoint("hum", ModuleNotFoundError("No module named 'piper'"), "dogear-hum"),
    )
    assert cli.main(["voices"]) == 0
    out, err = capsys.readouterr()
    assert "Traceback" not in out + err
    assert out.startswith("Kokoro, licensed Apache-2.0: 28 voices. Best rated first.")
    assert (
        "The voice engine hum, from dogear-hum 1.0, is installed but can't be used, so its "
        "voices aren't listed. It failed to load: ModuleNotFoundError: No module named "
        "'piper'. Reinstall it, or uninstall it if you don't need it."
    ) in out


def test_dogear_voices_groups_voices_by_engine(installed_engines, capsys):
    installed_engines(hum(license="GPL-3.0-or-later", about_voices="Best first."))
    assert cli.main(["voices"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(
        "Hum, licensed GPL-3.0-or-later: 2 voices. Best first.\n\n"
        "1. low: American English.\n"
        "2. emma: American English.\n\n"
        "Kokoro, licensed Apache-2.0: 28 voices."
    )
    assert "1. af_heart: American English, female, grade A. This is the one" in out


def test_a_bug_in_an_engine_is_reported_to_its_makers(installed_engines):
    plugin = hum()
    installed_engines(
        entry_point_for(plugin, package="dogear-hum", urls=["Issues, https://example.org/hum"])
    )
    engine = installed().get("hum")
    with pytest.raises(VoiceError) as err:
        engines.run_plugin(engine, "speaking a sentence", lambda: [][1])
    assert err.value.message == (
        "The Hum voice engine stopped while speaking a sentence: IndexError: list index out "
        "of range"
    )
    assert "dogear-hum 1.0" in err.value.hint
    assert "https://example.org/hum" in err.value.hint
    assert "-v" in err.value.hint


def test_an_engines_own_dogear_errors_pass_through(installed_engines):
    installed_engines(hum())
    engine = installed().get("hum")
    error = VoiceError("The voice file is damaged.", hint="Delete it.")

    def fail():
        raise error

    with pytest.raises(VoiceError) as err:
        engines.run_plugin(engine, "loading its voice", fail)
    assert err.value is error


# An engine's settings.


def test_an_engines_settings_are_its_own_table_and_checked_like_dogears(
    installed_engines, tmp_path
):
    installed_engines(hum(settings="threads = 0\nvariety = 0.5\n"))
    assert load_config()["hum"] == {"threads": 0, "variety": 0.5}
    path = tmp_path / "mine.toml"
    path.write_text("[hum]\nthreads = 4\n", encoding="utf-8")
    assert load_config(path)["hum"] == {"threads": 4, "variety": 0.5}
    path.write_text("[hum]\nloudness = 4\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown setting 'hum.loudness'"):
        load_config(path)
    path.write_text('[hum]\nthreads = "four"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="'hum.threads' must be int"):
        load_config(path)


def test_settings_for_an_engine_that_failed_to_load_say_so(installed_engines, tmp_path):
    installed_engines(FakeEntryPoint("hum", ImportError("broken"), package="dogear-hum"))
    path = tmp_path / "mine.toml"
    path.write_text("[hum]\nthreads = 4\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="the voice engine 'hum' \\(dogear-hum 1.0\\)") as err:
        load_config(path)
    assert "It failed to load: ImportError: broken" in err.value.message


# Choosing a voice.


def test_a_voice_is_found_by_name_or_by_engine_and_name(installed_engines):
    installed_engines(hum())
    assert find_voice("low").spec == "hum:low"
    assert find_voice("hum:emma").voice.language == "en-GB"
    assert find_voice("kokoro:bf_emma").engine.name == "kokoro"


def test_a_name_two_engines_share_needs_the_engines_name(installed_engines):
    installed_engines(hum(voices=[hum_voice("af_heart")]))
    with pytest.raises(VoiceError, match="Hum and Kokoro") as err:
        find_voice("af_heart")
    assert "hum:af_heart" in err.value.hint
    assert find_voice("kokoro:af_heart").engine.name == "kokoro"


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("nobody", "There's no voice called 'nobody'."),
        ("hum:nobody", "The Hum voice engine has no voice called 'nobody'."),
        ("piper:low", "voice.engine is 'piper', which isn't installed."),
    ],
)
def test_a_voice_that_isnt_there_is_explained(installed_engines, spec, message):
    installed_engines(hum())
    with pytest.raises(VoiceError) as err:
        find_voice(spec)
    assert err.value.message == message
    assert "dogear voices" in err.value.hint


def test_a_broken_engines_voice_says_why_it_cant_be_used(installed_engines):
    installed_engines(FakeEntryPoint("hum", ImportError("no piper"), package="dogear-hum"))
    with pytest.raises(VoiceError, match="is installed, but Dogear can't use it") as err:
        find_voice("hum:low")
    assert "ImportError: no piper" in err.value.message


def test_choosing_a_voice_sets_its_engine_too(installed_engines, config):
    installed_engines(hum())
    choose_voice(config, "emma")
    assert (config["voice"]["engine"], config["voice"]["voice"]) == ("hum", "emma")
    assert configured_voice(config).voice.language == "en-GB"


def test_a_voice_set_without_its_engine_gets_a_hint(installed_engines, config):
    installed_engines(hum())
    config["voice"]["voice"] = "low"
    with pytest.raises(VoiceError, match="The voice low is a Hum voice") as err:
        configured_voice(config)
    assert 'engine = "hum"' in err.value.hint
    assert "--voice hum:low" in err.value.hint


def test_make_takes_engine_and_voice(installed_engines, monkeypatch, tmp_path):
    installed_engines(hum())
    seen = {}

    def fake_make(request, config, report):
        seen["config"] = config
        return []

    monkeypatch.setattr(cli, "make_audiobook", fake_make)
    book = tmp_path / "book.epub"
    assert cli.main(["make", str(book), "--voice", "hum:emma"]) == 0
    assert seen["config"]["voice"]["engine"] == "hum"
    assert seen["config"]["voice"]["voice"] == "emma"


def test_download_prepares_the_chosen_engines_voice(installed_engines, capsys):
    plugin = hum()
    installed_engines(plugin)
    assert cli.main(["download", "--voice", "hum:emma"]) == 0
    assert plugin.prepared == ["emma"]
    assert capsys.readouterr().out == "The voice emma is ready to use offline.\n"


# Making the voice.


@pytest.fixture
def hum_chosen(installed_engines, config):
    plugin = hum()
    installed_engines(plugin)
    choose_voice(config, "hum:low")
    return plugin


def test_make_engine_gives_the_plugin_the_voice_and_pronunciations(hum_chosen, config):
    engine = make_engine(config, print)
    [made] = hum_chosen.made
    assert made["voice"].name == "low"
    assert made["config"] is config
    assert engine.sample_rate == HumEngine.sample_rate
    audio = engine.render(engine.plan("Two words."))
    assert audio.dtype == np.float32 and audio.ndim == 1


def test_cache_keys_name_the_engine_and_the_voice(hum_chosen, config):
    """HumEngine keys on the text alone. Dogear's key adds the engine and the voice, so
    two engines, or two voices, never share a cached sentence."""
    first = make_engine(config, print).plan("Hello.")
    choose_voice(config, "hum:emma")
    second = make_engine(config, print).plan("Hello.")
    assert first.payload == second.payload
    assert first.key != second.key
    assert make_engine(config, print).plan("Hello.").key == second.key


def test_a_bug_while_speaking_becomes_a_voice_error(hum_chosen, config):
    engine = make_engine(config, print)
    hum_chosen.engine_made.render = lambda plan: 1 / 0
    with pytest.raises(VoiceError, match="stopped while speaking a sentence: ZeroDivisionError"):
        engine.render(SpeechPlan("key", "Hello."))


def test_audio_of_the_wrong_shape_is_a_bug_in_the_engine(hum_chosen, config):
    engine = make_engine(config, print)
    hum_chosen.engine_made.render = lambda plan: np.zeros((2, 5), dtype=np.float32)
    with pytest.raises(VoiceError, match="gave audio of shape \\(2, 5\\)"):
        engine.render(SpeechPlan("key", "Hello."))


@pytest.mark.parametrize(
    ("audio", "problem"),
    [
        ("abc", "gave audio that isn't samples (ValueError: could not convert string"),
        ([{}], "gave audio that isn't samples (TypeError"),
        (None, "gave audio of shape ()"),
        (np.array([0.1, np.nan, 0.2]), "samples that aren't numbers (NaN) or are infinite"),
        (np.array([0.1, np.inf]), "samples that aren't numbers (NaN) or are infinite"),
        (np.array([1e300, 0.0]), "samples that aren't numbers (NaN) or are infinite"),
    ],
)
def test_audio_that_isnt_samples_is_a_bug_in_the_engine(hum_chosen, config, audio, problem):
    """Before, these raised a traceback, or (NaN) were silently spoken as silence."""
    engine = make_engine(config, print)
    hum_chosen.engine_made.render = lambda plan: audio
    with pytest.raises(VoiceError) as err:
        engine.render(SpeechPlan("key", "Hello."))
    assert err.value.message.startswith("The Hum voice engine ")
    assert problem in err.value.message
    assert "This is a bug in dogear-fake 1.0" in err.value.hint


@pytest.mark.parametrize(
    ("plan", "problem"),
    [
        (None, "gave None as a sentence's plan"),
        ("key", "gave a str as a sentence's plan"),
        (SpeechPlan(None, "Hello."), "gave a plan whose key or payload isn't text"),
        (SpeechPlan("key", b"Hello."), "gave a plan whose key or payload isn't text"),
    ],
)
def test_a_plan_that_isnt_a_speech_plan_is_a_bug_in_the_engine(hum_chosen, config, plan, problem):
    engine = make_engine(config, print)
    hum_chosen.engine_made.plan = lambda text: plan
    with pytest.raises(VoiceError, match=problem) as err:
        engine.plan("Hello.")
    assert "This is a bug in dogear-fake 1.0" in err.value.hint


def test_a_bug_while_loading_becomes_a_voice_error(hum_chosen, config):
    def broken(*args):
        raise RuntimeError("model file is empty")

    hum_chosen.engine = broken
    with pytest.raises(VoiceError, match="stopped while loading its voice: RuntimeError"):
        make_engine(config, print)


def test_words_lists_only_what_misakis_dictionaries_lack(hum_chosen, config, tmp_path):
    with pytest.raises(VoiceError, match="low is a Hum voice") as err:
        find_unknown_words(WordsRequest(tmp_path / "no-book.epub"), config, print)
    assert "Kokoro voice" in err.value.hint


def test_phoneme_entries_are_skipped_with_another_engine_and_it_says_why(
    hum_chosen, config, tmp_path
):
    words = tmp_path / "words.toml"
    words.write_text(
        '[words]\nUtterson = "utter son"\nLanyon = { phonemes = "lˈænjən" }\n', encoding="utf-8"
    )
    config["pronunciation"]["files"] = [str(words)]
    lines = []
    make_engine(config, lines.append)
    assert lines == [
        f"Your pronunciation files have 1 phoneme entry, such as 'Lanyon' in {words}. They "
        "are skipped: phoneme entries are written in the Kokoro voice's symbols, and low is "
        "a Hum voice. Entries that give a spelling still apply."
    ]


def test_nothing_is_said_about_phoneme_entries_when_there_are_none(hum_chosen, config, tmp_path):
    words = tmp_path / "words.toml"
    words.write_text('[words]\nUtterson = "utter son"\n', encoding="utf-8")
    config["pronunciation"]["files"] = [str(words)]
    lines = []
    make_engine(config, lines.append)
    assert lines == []


def test_prepare_voice_asks_the_engine_for_its_files(hum_chosen, config):
    assert prepare_voice(config, print).spec == "hum:low"
    assert hum_chosen.prepared == ["low"]
