"""`dogear words`: ranking unknown words, and the command with no real model or download.

spaCy's blank English pipeline stands in for the downloaded model, as in test_g2p.py.
"""

import json
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from dogear import cli, unknown_words
from dogear.document import Book, Heading, Metadata, Paragraph, Section
from dogear.errors import VoiceError
from dogear.unknown_words import (
    UnknownWord,
    WordsRequest,
    find_unknown_words,
    rank_words,
    words_from_json,
)
from dogear.voice import downloads
from dogear.voice.catalog import catalog

# misaki 0.9.4 loads its dictionaries with a deprecated importlib call.
pytestmark = pytest.mark.filterwarnings("ignore:open_text is deprecated:DeprecationWarning")


# Ranking.


def test_most_frequent_first_then_alphabetical():
    counts = Counter({"Poole": 2, "Hyde": 5, "Carew": 2, "Enfield": 1})
    assert rank_words(counts) == [
        UnknownWord("Hyde", 5),
        UnknownWord("Carew", 2),
        UnknownWord("Poole", 2),
        UnknownWord("Enfield", 1),
    ]


def test_capitals_possessives_and_punctuation_count_as_one_word():
    counts = Counter(
        {"Utterson": 3, "UTTERSON": 1, "Utterson's": 2, "Utterson’s.": 1, "'Utterson": 1}
    )
    assert rank_words(counts) == [UnknownWord("Utterson", 8)]


def test_words_keep_their_inside_punctuation():
    counts = Counter({"Messrs.": 2, "cheval-glass": 1, "standardebooks.org": 1, "O'Brien's": 1})
    assert [w.word for w in rank_words(counts)] == [
        "Messrs",
        "cheval-glass",
        "O'Brien",
        "standardebooks.org",
    ]


def test_nothing_unknown():
    assert rank_words(Counter()) == []


def test_lone_punctuation_is_not_a_word():
    assert rank_words(Counter({".": 3, "—": 1, "Hyde": 1})) == [UnknownWord("Hyde", 1)]


def test_a_sounds_like_spelling_with_its_own_unknown_word_is_listed(fakes, config, tmp_path):
    # The spelling is what the voice reads, so a made-up word in it gets guessed too.
    path = tmp_path / "words.toml"
    path.write_text('[words]\nQuimby = "quim bee"\n', encoding="utf-8")
    config["pronunciation"]["files"] = [str(path)]
    found = find_unknown_words(WordsRequest(tmp_path / "notes.epub", sections={2}), config, print)
    assert UnknownWord("Quim", 1) in found


# The whole command, faked.


def small_book(language="en"):
    return Book(
        metadata=Metadata(title="Notes", authors=["Ann Author"], language=language),
        sections=[
            Section(
                "One",
                blocks=[Heading(1, "One"), Paragraph("Utterson met Zorblax. Zorblax's dog ran.")],
            ),
            Section(
                "Two", blocks=[Heading(1, "Two"), Paragraph("Utterson saw Quimby and Utterson.")]
            ),
        ],
        source_path=Path("notes.epub"),
        source_format="epub",
    )


@pytest.fixture
def fakes(monkeypatch, config, tmp_path):
    """A small book, a blank spaCy pipeline, and downloads that only write stand-ins.

    Returns what was asked to be downloaded, one list of file names per request.
    """
    import spacy

    config["models"]["dir"] = str(tmp_path / "models")
    monkeypatch.setattr(unknown_words, "extract_book", lambda path, cfg, report: small_book())
    monkeypatch.setattr(spacy, "load", lambda path, enable: spacy.blank("en"))
    requested = []

    def fake_ensure_files(files, cfg, report):
        requested.append([f.path.name for f in files])
        for remote in files:
            remote.path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(remote.path, "w") as wheel:
                wheel.writestr(f"{catalog()['spacy']['model_dir']}/config.cfg", "[nlp]")

    def no_network(*args, **kwargs):
        raise AssertionError("tests must not download anything")

    monkeypatch.setattr("dogear.voice.kokoro_plugin.ensure_files", fake_ensure_files)
    monkeypatch.setattr(downloads.urllib.request, "urlopen", no_network)
    return requested


def test_lists_the_unknown_words_and_downloads_only_spacy(fakes, config, tmp_path):
    found = find_unknown_words(WordsRequest(tmp_path / "notes.epub"), config, print)
    assert found == [
        UnknownWord("Utterson", 3),
        UnknownWord("Zorblax", 2),
        UnknownWord("Quimby", 1),
    ]
    assert fakes == [["en_core_web_sm-3.8.0-py3-none-any.whl"]]


def test_only_the_chosen_sections(fakes, config, tmp_path):
    found = find_unknown_words(WordsRequest(tmp_path / "notes.epub", sections={2}), config, print)
    assert found == [UnknownWord("Utterson", 2), UnknownWord("Quimby", 1)]


def test_words_in_pronunciation_files_are_not_listed(fakes, config, tmp_path):
    path = tmp_path / "words.toml"
    path.write_text(
        '[words]\nUtterson = "utter son"\nZorblax = { phonemes = "zˈɔɹblæks" }\n',
        encoding="utf-8",
    )
    config["pronunciation"]["files"] = [str(path)]
    found = find_unknown_words(WordsRequest(tmp_path / "notes.epub"), config, print)
    assert found == [UnknownWord("Quimby", 1)]


def test_the_voices_accent_is_used(fakes, config, tmp_path, monkeypatch):
    seen = []

    def count(texts, spacy_model, british, dictionary):
        seen.append(british)
        return Counter()

    monkeypatch.setattr("dogear.voice.g2p.count_unknown_words", count)
    config["voice"]["voice"] = "bf_emma"
    assert find_unknown_words(WordsRequest(tmp_path / "notes.epub"), config, print) == []
    assert seen == [True]


def test_a_book_in_another_language_needs_the_override(fakes, config, tmp_path, monkeypatch):
    monkeypatch.setattr(unknown_words, "extract_book", lambda path, cfg, report: small_book("fr"))
    with pytest.raises(VoiceError, match="'fr'"):
        find_unknown_words(WordsRequest(tmp_path / "notes.epub"), config, print)
    found = find_unknown_words(WordsRequest(tmp_path / "notes.epub", language="en"), config, print)
    assert found[0] == UnknownWord("Utterson", 3)


# The command's output.


def test_cli_words_reads_as_a_numbered_list(fakes, capsys):
    assert cli.main(["words", "notes.epub"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(
        "3 words in this book the voice doesn't know, most frequent first. "
        "It guesses how to say them.\n\n"
        "1. Utterson, 3 times.\n2. Zorblax, 2 times.\n3. Quimby, 1 time.\n\n"
    )
    assert "dogear say" in out
    assert "--words" in out


def test_cli_words_with_sections_and_a_pronunciation_file(fakes, capsys, tmp_path):
    path = tmp_path / "words.toml"
    path.write_text('[words]\nUtterson = "utter son"\nQuimby = "quick bee"\n', encoding="utf-8")
    assert cli.main(["words", "notes.epub", "--sections", "2", "--words", str(path)]) == 0
    assert capsys.readouterr().out == "The voice knows every word in these sections.\n"


def test_cli_words_when_every_word_is_known(fakes, capsys, monkeypatch):
    monkeypatch.setattr("dogear.voice.g2p.count_unknown_words", lambda *args: Counter())
    assert cli.main(["words", "notes.epub"]) == 0
    assert capsys.readouterr().out == "The voice knows every word in this book.\n"


def test_cli_words_json_is_the_same_list_in_ascii(fakes, capsys, monkeypatch):
    monkeypatch.setattr(
        "dogear.voice.g2p.count_unknown_words",
        lambda *args: Counter({"Zoë": 2, "Utterson": 3, '"Back\\slash"': 1}),
    )
    assert cli.main(["words", "notes.epub", "--json"]) == 0
    out = capsys.readouterr().out
    assert out.isascii()
    assert json.loads(out) == {
        "words": [
            {"word": "Utterson", "count": 3},
            {"word": "Zoë", "count": 2},
            {"word": "Back\\slash", "count": 1},
        ]
    }
    assert words_from_json(json.loads(out)) == [
        UnknownWord("Utterson", 3),
        UnknownWord("Zoë", 2),
        UnknownWord("Back\\slash", 1),
    ]


def test_cli_words_json_when_every_word_is_known(fakes, capsys, monkeypatch):
    monkeypatch.setattr("dogear.voice.g2p.count_unknown_words", lambda *args: Counter())
    assert cli.main(["words", "notes.epub", "--sections", "2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"words": []}


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"words": {}},
        {"words": [{"word": "Hyde"}]},
        {"words": [{"word": "Hyde", "count": 0}]},
        {"words": [{"word": "Hyde", "count": True}]},
        {"words": [{"word": "Hyde", "count": 1.5}]},
        {"words": [{"word": " ", "count": 1}]},
        {"words": [{"word": 3, "count": 1}]},
        {"words": ["Hyde"]},
    ],
)
def test_words_from_json_checks_every_word(data):
    with pytest.raises(ValueError):
        words_from_json(data)
