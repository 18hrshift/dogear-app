"""The choose form: what starts out chosen comes from the config, and what comes back is
checked."""

from dataclasses import replace

import pytest
from engine_fakes import fake_engine, hum_voice
from web_samples import CHOSEN, add_book, sample_app, sample_summary, visitor

from dogear.make import FORMATS
from dogear.web import choices


def fields(**values):
    sent = {
        "format": ["m4b"],
        "voice": ["kokoro:af_heart"],
        "speed": ["0.9"],
        "section": ["1", "2"],
    }
    sent.update(values)
    return [(name, value) for name, many in sent.items() for value in many]


def test_every_format_dogear_makes_is_offered():
    assert tuple(f.name for f in choices.format_options()) == FORMATS


def test_defaults_come_from_the_config(config):
    config["output"]["formats"] = ["mp3", "daisy"]
    config["voice"]["voice"] = "bf_emma"
    config["voice"]["speed"] = 1.1
    picked = choices.defaults(sample_summary(), config, None)
    assert picked.formats == {"mp3", "daisy"}
    assert picked.voice == "kokoro:bf_emma"
    assert picked.speed == "1.1"
    assert picked.sections == {1, 2, 3, 4}


def test_a_book_made_before_keeps_its_choices(config):
    picked = choices.defaults(sample_summary(), config, CHOSEN)
    assert picked.formats == {"m4b", "mp3"}
    assert picked.sections == set(CHOSEN.sections)
    # Saved before there were other engines, as the voice's name alone.
    assert CHOSEN.voice == "af_heart"
    assert picked.voice == "kokoro:af_heart"


def test_a_voice_sent_by_name_alone_is_kept_with_its_engine(config):
    _, chosen, errors = choices.check(fields(voice=["bf_emma"]), sample_summary(), config)
    assert errors == {}
    assert chosen.voice == "kokoro:bf_emma"


def test_speeds_are_words_and_mark_the_usual_one(config):
    labels = [option.label for option in choices.speed_options(config)]
    assert labels == [
        "Slower (0.8)",
        "A little slower (0.9), the usual",
        "Natural (1.0)",
        "Faster (1.1)",
    ]
    config["voice"]["speed"] = 1.25
    options = choices.speed_options(config)
    assert [o.value for o in options] == ["0.8", "0.9", "1.0", "1.1", "1.25"]
    assert options[-1].label == "Your usual speed (1.25), the usual"


def test_voices_are_grouped_by_engine_and_best_rated_first(config):
    [kokoro] = choices.voice_options(config)
    assert kokoro.label == "Kokoro voices"
    assert kokoro.options[0] == choices.Option(
        "kokoro:af_heart", "af_heart: American English, female, grade A"
    )
    assert kokoro.options[-1].label.endswith("grade F+")


def test_each_installed_engine_has_its_own_group(installed_engines, config):
    installed_engines(fake_engine("hum", "Hum", [hum_voice("low"), hum_voice("high")]))
    groups = choices.voice_options(config)
    assert [g.label for g in groups] == ["Hum voices", "Kokoro voices"]
    assert [o.value for o in groups[0].options] == ["hum:low", "hum:high"]


def test_good_choices_are_kept_in_format_order(config):
    picked, chosen, errors = choices.check(
        fields(format=["daisy", "m4b"], section=["4", "2"]), sample_summary(), config
    )
    assert errors == {}
    assert chosen.formats == ("m4b", "daisy")
    assert chosen.sections == (2, 4)
    assert chosen.speed == 0.9


def test_each_problem_is_named_by_its_field(config):
    sent = fields(format=["wav"], voice=["nobody"], speed=["3"], section=["9", "x"])
    picked, chosen, errors = choices.check(sent, sample_summary(), config)
    assert chosen is None
    assert set(errors) == {"format", "voice", "speed", "section"}
    assert picked.voice == "nobody"  # shown again as sent, though not in the list


def test_a_book_in_a_language_no_voice_speaks_must_be_read_as_english(config):
    french = replace(sample_summary(), language="fr-FR")
    assert choices.asks_about_language(french, config)
    assert not choices.asks_about_language(sample_summary(), config)
    assert not choices.asks_about_language(replace(sample_summary(), language=None), config)
    _, chosen, errors = choices.check(fields(), french, config)
    assert chosen is None
    assert errors["english"].startswith("None of Dogear's voices speaks this book's language.")
    _, chosen, errors = choices.check(fields(english=["yes"]), french, config)
    assert errors == {}
    assert chosen.as_english


def lilt():
    voices = [
        hum_voice("fr_one", "fr-FR", description="French"),
        hum_voice("de_one", "de-DE", description="German"),
    ]
    return fake_engine("lilt", "Lilt", voices)


def test_a_french_book_offers_the_french_voices_first_and_picks_the_best(config, installed_engines):
    installed_engines(lilt())
    french = replace(sample_summary(), language="fr-FR")
    groups = choices.voice_options(config, french)
    assert groups[0].label == "Voices for this book's language (fr-FR)"
    assert [o.value for o in groups[0].options] == ["lilt:fr_one"]
    assert groups[0].options[0].label == "fr_one: French (Lilt)"
    # It isn't listed twice: the Lilt group has the rest.
    assert [o.value for o in groups[-1].options] == ["lilt:de_one"]
    assert choices.defaults(french, config, None).voice == "lilt:fr_one"
    assert choices.defaults(sample_summary(), config, None).voice == "kokoro:af_heart"


def test_a_french_book_with_a_french_voice_needs_no_read_as_english(config, installed_engines):
    installed_engines(lilt())
    french = replace(sample_summary(), language="fr")
    assert choices.asks_about_language(french, config)  # optional, with the voices first
    _, chosen, errors = choices.check(fields(voice=["lilt:fr_one"]), french, config)
    assert errors == {} and not chosen.as_english
    _, chosen, errors = choices.check(fields(), french, config)
    assert chosen is None
    assert errors == {
        "voice": "The voice you chose doesn't speak fr, the language this book says it is in. "
        "Choose one of the voices for it, listed first, or choose Read it as English."
    }
    _, chosen, errors = choices.check(fields(english=["yes"]), french, config)
    assert errors == {} and chosen.as_english


def test_a_language_the_settings_give_a_voice_asks_nothing(config, installed_engines):
    installed_engines(lilt())
    french = replace(sample_summary(), language="fr")
    config["voice"]["languages"] = {"fr": "lilt:fr_one"}
    assert not choices.asks_about_language(french, config)
    _, chosen, errors = choices.check(fields(), french, config)  # Kokoro, with fr mapped
    assert errors == {} and not chosen.as_english
    config["voice"]["languages"] = {}
    config["voice"]["other_languages"] = "best"
    assert not choices.asks_about_language(french, config)
    assert choices.asks_about_language(replace(french, language="ja"), config)


def test_read_as_english_is_only_kept_when_asked(config):
    _, chosen, _ = choices.check(fields(english=["yes"]), sample_summary(), config)
    assert not chosen.as_english


@pytest.mark.parametrize("value", ["²", "٣", "9" * 5000, "1.0", "-1", " 1", "", "0", "99999"])
def test_a_section_that_is_not_one_of_the_books_is_a_form_error(config, value):
    picked, chosen, errors = choices.check(fields(section=["1", value]), sample_summary(), config)
    assert chosen is None
    assert errors == {"section": "Choose sections from the list."}
    assert picked.sections == {1}


def test_section_numbers_are_ascii_digits_only():
    assert choices.section_number("12") == 12
    assert choices.section_number("007") == 7
    assert choices.section_number("²") is None
    assert choices.section_number("١٢") is None
    assert choices.section_number("123456") is None
    assert choices.section_number("+1") is None


def test_odd_section_values_get_the_form_back_not_a_crash(tmp_path):
    app = sample_app(tmp_path)
    book_id = add_book(app.state.library, tmp_path, state="ready", summary=sample_summary())
    client = visitor(app)
    for value in ("²", "9" * 5000):
        response = client.post(
            f"/books/{book_id}/choose",
            data={"format": "m4b", "voice": "af_heart", "speed": "0.9", "section": value},
        )
        assert response.status_code == 400
        assert "Choose sections from the list." in response.text
