"""Writing pronunciation files: every word comes back through the reader as it went in."""

import tomllib
from pathlib import Path

import pytest

from dogear.pronunciation_writer import pronunciation_file_text, toml_string
from dogear.pronunciations import Pronunciation, read_pronunciation_file

HERE = Path("words.toml")


def sounds(word, spelling):
    return Pronunciation(word, spelling, None, HERE)


def phonemes(word, written):
    return Pronunciation(word, None, written, HERE)


@pytest.mark.parametrize(
    "text",
    [
        "Utterson",
        'say "hi"',
        "back\\slash",
        "C:\\Users\\me",
        "Zoë Ångström 日本 😀",
        "tab\there",
        "line\nbreak\r\n",
        "bell\x07 nul\x00 del\x7f",
        "",
        "'single' and ''' triple",
        '"""',
    ],
)
def test_any_text_comes_back_as_it_went_in(text):
    assert tomllib.loads(f"value = {toml_string(text)}")["value"] == text


def test_quotes_backslashes_and_controls_are_escaped():
    assert toml_string('a "b" \\c') == '"a \\"b\\" \\\\c"'
    assert toml_string("a\nb\x01") == '"a\\nb\\u0001"'
    assert toml_string("Zoë") == '"Zoë"'  # written as itself, in UTF-8


def test_a_lone_surrogate_is_refused():
    with pytest.raises(ValueError, match="surrogate"):
        toml_string("bad \ud800")


def test_a_file_reads_back_through_the_reader(tmp_path):
    entries = [
        sounds("Utterson", "utter son"),
        sounds('The "Quoted" One', "the quoted one"),
        sounds("St. John", "sin jun"),
        sounds("Zoë", "zo ee"),
        sounds("back\\slash", "back slash"),
        phonemes("Lanyon", "lˈænjən"),
    ]
    path = tmp_path / "words.toml"
    path.write_text(pronunciation_file_text(entries, "Made by a test.\nSecond line."), "utf-8")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Made by a test.\n# Second line.\n[words]\n")
    assert '"Lanyon" = { phonemes = "lˈænjən" }' in text
    found = read_pronunciation_file(path)
    assert [(e.word, e.sounds_like, e.phonemes) for e in found] == [
        (e.word, e.sounds_like, e.phonemes) for e in entries
    ]


def test_an_empty_list_is_still_a_file_the_reader_takes(tmp_path):
    path = tmp_path / "words.toml"
    path.write_text(pronunciation_file_text([]), encoding="utf-8")
    assert read_pronunciation_file(path) == []


def test_an_entry_with_nothing_to_say_is_a_bug():
    with pytest.raises(ValueError, match="neither"):
        pronunciation_file_text([Pronunciation("Hyde", None, None, HERE)])


def test_an_entrys_languages_come_back_as_written(tmp_path):
    entries = [
        Pronunciation("Mme", "madame", None, HERE, ("fr", "fr-CA")),
        sounds("Utterson", "utter son"),
    ]
    path = tmp_path / "words.toml"
    text = pronunciation_file_text(entries)
    path.write_text(text, encoding="utf-8")
    back = read_pronunciation_file(path)
    assert [(e.word, e.sounds_like, e.languages) for e in back] == [
        ("Mme", "madame", ("fr", "fr-CA")),
        ("Utterson", "utter son", ("en",)),
    ]
    assert '"Utterson" = "utter son"' in text  # an English entry stays a plain spelling
