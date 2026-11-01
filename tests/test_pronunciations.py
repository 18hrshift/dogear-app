"""Pronunciation files: reading and checking them, and sounds-like entries in the text."""

import pytest

from dogear.errors import PronunciationError
from dogear.pronunciations import (
    Pronunciation,
    PronunciationDictionary,
    dictionary_from_config,
    load_dictionary,
    read_pronunciation_file,
)

EXAMPLE = """\
[words]
Utterson = "utter son"             # sounds like: the voice reads this spelling instead
"St. John" = "sin jun"             # multi-word entries are allowed
Lanyon = { phonemes = "lˈænjən" }  # exact phonemes in misaki's symbols, for experts
"""


def write(folder, text, name="words.toml"):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_both_kinds_of_entry(tmp_path):
    path = write(tmp_path, EXAMPLE)
    assert read_pronunciation_file(path) == [
        Pronunciation("Utterson", sounds_like="utter son", phonemes=None, source=path),
        Pronunciation("St. John", sounds_like="sin jun", phonemes=None, source=path),
        Pronunciation("Lanyon", sounds_like=None, phonemes="lˈænjən", source=path),
    ]


def test_spacing_is_tidied(tmp_path):
    path = write(
        tmp_path, '[words]\n"  St.   John " = "  sin   jun "\nX = { phonemes = " a  b " }\n'
    )
    st_john, x = read_pronunciation_file(path)
    assert (st_john.word, st_john.sounds_like) == ("St. John", "sin jun")
    assert x.phonemes == "a b"


def test_an_empty_words_table_is_fine(tmp_path):
    assert read_pronunciation_file(write(tmp_path, "[words]\n")) == []


def test_later_files_override_earlier_ones_whatever_the_case(tmp_path):
    first = write(tmp_path, '[words]\nLanyon = "lan yon"\nPoole = "pool"\n', "first.toml")
    second = write(tmp_path, '[words]\nlanyon = { phonemes = "lˈænjən" }\n', "second.toml")
    entries = {e.word.casefold(): e for e in load_dictionary([first, second]).entries}
    assert entries["lanyon"] == Pronunciation("lanyon", None, "lˈænjən", second)
    assert entries["poole"].sounds_like == "pool"
    assert len(entries) == 2


def test_config_lists_files_in_order_and_expands_home(tmp_path, monkeypatch, config):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    write(tmp_path, '[words]\nPoole = "pool"\n', "home.toml")
    other = write(tmp_path, '[words]\nPoole = "pole"\n', "other.toml")
    config["pronunciation"]["files"] = ["~/home.toml", str(other)]
    [poole] = dictionary_from_config(config).entries
    assert poole.sounds_like == "pole"
    config["pronunciation"]["files"] = [str(other), "~/home.toml"]
    [poole] = dictionary_from_config(config).entries
    assert poole.sounds_like == "pool"


def test_no_files_by_default(config):
    assert dictionary_from_config(config).entries == ()


@pytest.mark.parametrize(
    ("text", "message", "hint"),
    [
        ('Utterson = "utter son"\n', "has 'Utterson' outside the [words] table", "[words]"),
        ("# nothing here\n", "has no [words] table, so it changes nothing", "[words]"),
        ('words = "Utterson"\n', "'words' must be a table", ""),
        ('[words]\nUtterson = ""\n', "The entry 'Utterson' in", "between the quotes"),
        ('[words]\nUtterson = "   "\n', "The entry 'Utterson' in", "between the quotes"),
        ("[words]\nUtterson = 5\n", "must be a spelling in quotes", "For example"),
        ("[words]\nUtterson = ['utter', 'son']\n", "must be a spelling in quotes", ""),
        ('[words]\nLanyon = { phonemes = "" }\n', "needs its phonemes written", "Lanyon ="),
        ("[words]\nLanyon = { phonemes = 1 }\n", "needs its phonemes written", ""),
        ("[words]\nLanyon = {}\n", "needs its phonemes written", ""),
        ('[words]\nLanyon = { phoneme = "x" }\n', "has 'phoneme', which an entry can't", ""),
        ('[words]\nLanyon = { phonemes = "x", sounds = "y" }\n', "has 'sounds'", ""),
        # Without quotes, TOML reads St. John as St, then John inside it.
        ('[words]\nSt. John = "sin jun"\n', "The entry 'St' in", "put the whole word in quotes"),
        ('[words]\n"" = "nothing"\n', "an empty word", "remove the entry"),
        ('[words]\n"..." = "dot dot dot"\n', "has no letters or digits", ""),
        ('[words]\nLanyon = "a"\nlanyon = "b"\n', "lists 'Lanyon' and 'lanyon'", "Keep one"),
        ("[words]\n\"O'Brien\" = 'a'\n\"O’Brien\" = 'b'\n", "which are the same word", ""),
        ('[words]\n"St. John" = { phonemes = "x" }\n', "can only use letters", "Saint John"),
        ('[words]\nR2D2 = { phonemes = "x" }\n', "can only use letters", "text --spoken"),
        ('[words]\nZoë = "zo ee"\n', "is not valid TOML", "needs quotes"),
        ('[words\nUtterson = "x"\n', "is not valid TOML", "Check the line it names"),
    ],
)
def test_mistakes_name_the_file_and_entry(tmp_path, text, message, hint):
    path = write(tmp_path, text)
    with pytest.raises(PronunciationError) as err:
        read_pronunciation_file(path)
    assert message in err.value.message
    assert str(path) in err.value.message
    assert hint in err.value.hint


@pytest.mark.parametrize(
    "word", ["Zoë", "Jekyll-Hyde", "O'Brien", "O’Brien", "Hyde Park", "Ελένη", "Ångström"]
)
def test_phoneme_entries_may_use_any_letters(tmp_path, word):
    [entry] = read_pronunciation_file(
        write(tmp_path, f'[words]\n"{word}" = {{ phonemes = "x" }}\n')
    )
    assert entry.phonemes == "x"


def test_a_missing_file_says_where_dogear_looked(tmp_path):
    with pytest.raises(PronunciationError, match="Pronunciation file not found") as err:
        read_pronunciation_file(tmp_path / "nope.toml")
    assert str(tmp_path / "nope.toml") in err.value.message
    assert "folder you run Dogear in" in err.value.hint


def test_a_folder_is_not_a_file(tmp_path):
    with pytest.raises(PronunciationError, match="Couldn't read pronunciation file") as err:
        read_pronunciation_file(tmp_path)
    assert "not a folder" in err.value.hint


def test_a_file_that_isnt_utf8_says_how_to_save_it(tmp_path):
    path = tmp_path / "words.toml"
    path.write_bytes('[words]\nZoë = "zo ee"\n'.encode("latin-1"))
    with pytest.raises(PronunciationError, match="isn't plain UTF-8 text") as err:
        read_pronunciation_file(path)
    assert "UTF-8" in err.value.hint


@pytest.mark.parametrize("files", [[""], ["  "]])
def test_an_empty_path_in_config_is_an_error(config, files):
    config["pronunciation"]["files"] = files
    with pytest.raises(PronunciationError, match="empty entry"):
        dictionary_from_config(config)


# Sounds-like entries in text.


@pytest.fixture
def example(tmp_path):
    return load_dictionary([write(tmp_path, EXAMPLE)])


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("utterson came.", "utter son came."),
        ("Utterson came.", "Utter son came."),  # keeps the word's capital
        ("UTTERSON came.", "Utter son came."),
        ("Utterson's house.", "Utter son's house."),
        ("Utterson’s house.", "Utter son’s house."),
        ("Uttersons came.", "Uttersons came."),
        ("He met St. John.", "He met Sin jun."),
        ("He met St.\nJohn.", "He met Sin jun."),
        ("Lanyon's house.", "Lanyon's house."),  # phonemes are pinned by the voice instead
    ],
)
def test_say_sounds_like(example, text, said):
    assert example.say_sounds_like(text) == said


def test_a_capital_in_the_spelling_is_kept(tmp_path):
    words = load_dictionary([write(tmp_path, '[words]\nhyde = "Hide"\n')])
    assert words.say_sounds_like("hyde and Hyde") == "Hide and Hide"


def test_the_longest_entry_wins_across_both_kinds(tmp_path):
    text = '[words]\nHyde = "hide"\n"Hyde Park" = { phonemes = "x" }\n'
    words = load_dictionary([write(tmp_path, text)])
    assert words.say_sounds_like("Hyde walked in Hyde Park.") == "Hide walked in Hyde Park."


def test_with_phonemes_keeps_only_phoneme_entries(example):
    assert [e.word for e in example.with_phonemes().entries] == ["Lanyon"]


def test_an_empty_dictionary_changes_nothing():
    assert PronunciationDictionary().say_sounds_like("Utterson's house.") == "Utterson's house."


# Languages: spellings are for English text unless an entry says.


LANGUAGES = """\
[words]
Utterson = "utter son"
Mme = { sounds_like = "madame", languages = ["fr"] }
Lanyon = { sounds_like = "lan yon", languages = ["en", "fr"] }
"""


def test_an_entry_can_say_which_languages_it_is_for(tmp_path):
    path = write(tmp_path, LANGUAGES)
    entries = {e.word: e for e in read_pronunciation_file(path)}
    assert entries["Utterson"].languages == ("en",)
    assert entries["Mme"] == Pronunciation("Mme", "madame", None, path, ("fr",))
    assert entries["Lanyon"].languages == ("en", "fr")


@pytest.mark.parametrize(
    ("lang", "said"),
    [
        (None, "Utter son, Mme, Lan yon"),
        ("en-GB", "Utter son, Mme, Lan yon"),
        ("fr", "Utterson, Madame, Lan yon"),
        ("fr-CA", "Utterson, Madame, Lan yon"),
        ("de", "Utterson, Mme, Lanyon"),
    ],
)
def test_each_language_gets_only_its_own_entries(tmp_path, lang, said):
    dictionary = load_dictionary([write(tmp_path, LANGUAGES)])
    assert dictionary.for_language(lang).say_sounds_like("Utterson, Mme, Lanyon") == said


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[words]\nMme = { sounds_like = "madame", languages = "fr" }\n', "isn't a list"),
        ('[words]\nMme = { sounds_like = "madame", languages = [] }\n', "isn't a list"),
        ('[words]\nMme = { sounds_like = "madame", languages = ["French"] }\n', "isn't a list"),
        ('[words]\nMme = { sounds_like = "" }\n', "needs its sounds_like written"),
        ('[words]\nMme = { sounds_like = "a", phonemes = "b" }\n', "has both sounds_like"),
        ('[words]\nLanyon = { phonemes = "x", languages = ["fr"] }\n', "applies to English only"),
    ],
)
def test_mistakes_in_languages_name_the_entry(tmp_path, text, message):
    path = write(tmp_path, text)
    with pytest.raises(PronunciationError) as err:
        read_pronunciation_file(path)
    assert message in err.value.message
    assert str(path) in err.value.message
