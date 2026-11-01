"""Guessing the language of passages a book doesn't mark: the guesser with py3langid's
real model (small, offline, and the same answer every time), its settings, and how
narration uses it."""

from pathlib import Path

import pytest

from dogear.config import load_config
from dogear.document import Book, Footnote, Heading, Metadata, Paragraph, Section
from dogear.errors import ConfigError
from dogear.language_detection import (
    DetectionSettings,
    LanguageGuesser,
    language_guesser,
)
from dogear.narration import narrate_book

FRENCH = "La vieille dame posa sa tasse sur la table et regarda longuement par la fenêtre."
ENGLISH = "The old lady put her cup down on the table and looked out of the window for a while."
GERMAN = "Die alte Dame stellte ihre Tasse auf den Tisch und sah lange aus dem Fenster."


def settings(config, **changes) -> DetectionSettings:
    config["language_detection"].update(changes)
    return DetectionSettings.from_config(config)


@pytest.fixture
def guesser(config):
    return LanguageGuesser(settings(config, enabled=True), {"en", "fr"})


# The guesser.


def test_it_finds_french_and_english(guesser):
    assert guesser.guess(FRENCH) == "fr"
    assert guesser.guess(ENGLISH) == "en"


def test_short_text_is_never_guessed(guesser):
    assert guesser.guess("Oui, monsieur.") is None  # 13 letters
    assert guesser.guess("Le lendemain matin, nous partîmes de bonne heure.") == "fr"


def test_only_languages_with_a_voice_are_found(guesser):
    # Weighed against every language it knows, German is German, so there's no voice for it.
    assert guesser.guess(GERMAN) is None


def test_min_letters_changes_what_is_checked(config):
    text = "Mais où sont les neiges d'antan?"
    loose = LanguageGuesser(settings(config, min_letters=20), {"en", "fr"})
    strict = LanguageGuesser(settings(config, min_letters=40), {"en", "fr"})
    assert loose.guess(text) == "fr"
    assert strict.guess(text) is None


def test_min_confidence_changes_what_is_used(config):
    def unsure(text):
        return ("fr", 0.8)

    assert LanguageGuesser(settings(config, min_confidence=0.75), {"fr"}, unsure).guess(FRENCH)
    assert (
        LanguageGuesser(settings(config, min_confidence=0.9), {"fr"}, unsure).guess(FRENCH) is None
    )


def test_long_text_is_checked_by_the_sentence_too(config):
    guesser = LanguageGuesser(settings(config, sentence_check_min_letters=100), {"en", "fr"})
    assert not guesser.checks_sentences(FRENCH)
    assert guesser.checks_sentences(FRENCH + " " + ENGLISH)


def test_there_is_a_guesser_only_when_detection_is_on_and_has_something_to_find(config):
    assert language_guesser(config, {"en", "fr"}) is None  # off by default
    config["language_detection"]["enabled"] = True
    assert language_guesser(config, {"en"}) is None
    assert language_guesser(config, {"en", "fr"}).languages == {"en", "fr"}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("min_letters = 0", "language_detection.min_letters is 0"),
        ("min_confidence = 0", "language_detection.min_confidence is 0.0"),
        ("min_confidence = 1.5", "language_detection.min_confidence is 1.5"),
        ("sentence_check_min_letters = 10", "sentence_check_min_letters is less than"),
        ("enabled = 1", "must be bool"),
    ],
)
def test_mistakes_in_the_settings_are_found_as_they_load(tmp_path, text, message):
    path = tmp_path / "settings.toml"
    path.write_text(f"[language_detection]\n{text}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path)


# Narration with a guesser.


def book(*sections: Section, lang: str | None = "en") -> Book:
    return Book(Metadata("A Book", language=lang), list(sections), Path("b.epub"), "epub")


def narrated(config, guesser, *blocks, section_lang=None, lang="en"):
    section = Section("One", blocks=[Heading(1, "One"), *blocks], lang=section_lang)
    [narration] = narrate_book(book(section, lang=lang), config, guesser)
    return narration


def langs(narration) -> list[tuple[str, str | None]]:
    return [(u.text, u.lang) for u in narration.utterances if u.spoken]


def test_an_unmarked_french_paragraph_is_read_as_french(config, guesser):
    narration = narrated(config, guesser, Paragraph(ENGLISH), Paragraph(FRENCH))
    assert langs(narration) == [("One", "en"), (ENGLISH, "en"), (FRENCH, "fr")]
    # The passage carries the guess, so a talking book's text is marked as its audio is.
    assert [p.block.lang for p in narration.passages] == [None, None, "fr"]


def test_without_a_guesser_nothing_is_guessed(config):
    narration = narrated(config, None, Paragraph(FRENCH))
    assert langs(narration)[-1] == (FRENCH, "en")


def test_a_language_the_book_marks_is_never_second_guessed(config, guesser):
    narration = narrated(config, guesser, Paragraph(FRENCH, lang="de"))
    assert langs(narration)[-1] == (FRENCH, "de")
    # Nor is a chapter marked in a language other than the book's.
    narration = narrated(config, guesser, Paragraph(ENGLISH), section_lang="fr")
    assert langs(narration)[-1] == (ENGLISH, "fr")


def test_a_book_in_french_can_have_english_found(config, guesser):
    narration = narrated(config, guesser, Paragraph(FRENCH), Paragraph(ENGLISH), lang="fr")
    assert langs(narration)[1:] == [(FRENCH, "fr"), (ENGLISH, "en")]


def test_notes_are_guessed_too(config, guesser):
    note = Footnote("n1", FRENCH)
    narration = narrated(config, guesser, Paragraph(ENGLISH, note_refs=("n1",)), note)
    assert langs(narration)[-1] == (f"Note: {FRENCH}", "fr")


def test_a_french_sentence_in_a_long_english_paragraph_is_found(config):
    guesser = LanguageGuesser(settings(config, sentence_check_min_letters=100), {"en", "fr"})
    text = f"{ENGLISH} She said: {FRENCH} Then she smiled. {ENGLISH}"
    narration = narrated(config, guesser, Paragraph(text))
    found = langs(narration)[1:]
    assert ("She said: " + FRENCH, "fr") in found or (FRENCH, "fr") in found
    assert found[0] == (ENGLISH, "en")
    assert ("Then she smiled.", "en") in found  # too short to guess: the paragraph's
    assert narration.passages[-1].block.lang is None  # the paragraph is English
