"""Pronunciation files on the command line: --words, and [pronunciation] in a config file."""

from pathlib import Path

import pytest

from dogear import cli
from dogear.document import Book, Heading, Metadata, Paragraph, Section


@pytest.fixture
def book(monkeypatch):
    book = Book(
        metadata=Metadata(title="Test Book", authors=["Ann Author"], language="en"),
        sections=[Section("One", blocks=[Heading(1, "One"), Paragraph("Mr. Utterson met Poole.")])],
        source_path=Path("test.epub"),
        source_format="epub",
    )
    monkeypatch.setattr(cli, "extract_book", lambda path, config, report: book)
    return book


def words_file(folder, text, name="words.toml"):
    path = folder / name
    path.write_text("[words]\n" + text, encoding="utf-8")
    return path


def config_file(folder, *files):
    path = folder / "dogear.toml"
    listed = ", ".join(f"'{f}'" for f in files)
    path.write_text(f"[pronunciation]\nfiles = [{listed}]\n", encoding="utf-8")
    return path


def test_text_spoken_uses_words_files(book, capsys, tmp_path):
    assert cli.main(["text", "test.epub", "--spoken"]) == 0
    assert "Mister Utterson met Poole." in capsys.readouterr().out
    words = words_file(tmp_path, 'Utterson = "utter son"\n')
    assert cli.main(["text", "test.epub", "--spoken", "--words", str(words)]) == 0
    assert "Mister Utter son met Poole." in capsys.readouterr().out


def test_the_config_setting_lists_files(book, capsys, tmp_path):
    words = words_file(tmp_path, 'Poole = "pool"\n')
    settings = config_file(tmp_path, words)
    assert cli.main(["text", "test.epub", "--spoken", "--config", str(settings)]) == 0
    assert "Mister Utterson met Pool." in capsys.readouterr().out


def test_words_files_come_after_the_configured_ones_and_win(book, capsys, tmp_path):
    configured = words_file(tmp_path, 'Poole = "pool"\nUtterson = "utter son"\n', "a.toml")
    first = words_file(tmp_path, 'Poole = "pole"\n', "b.toml")
    second = words_file(tmp_path, 'Poole = "paul"\n', "c.toml")
    args = ["text", "test.epub", "--spoken", "--config", str(config_file(tmp_path, configured))]
    assert cli.main([*args, "--words", str(first), "--words", str(second)]) == 0
    assert "Mister Utter son met Paul." in capsys.readouterr().out


def test_a_missing_words_file_is_an_error_with_a_hint(book, capsys, tmp_path):
    missing = tmp_path / "nope.toml"
    assert cli.main(["text", "test.epub", "--spoken", "--words", str(missing)]) == 1
    err = capsys.readouterr().err
    assert f"Error: Pronunciation file not found: {missing}" in err
    assert "What to do: Check the file's name and folder." in err


def test_a_mistake_in_a_words_file_names_the_entry(book, capsys, tmp_path):
    words = words_file(tmp_path, 'Utterson = ""\n')
    assert cli.main(["text", "test.epub", "--words", str(words)]) == 1
    assert f"Error: The entry 'Utterson' in {words} is empty." in capsys.readouterr().err


def test_plain_text_doesnt_read_pronunciation_files(book, capsys, tmp_path):
    settings = config_file(tmp_path, tmp_path / "missing.toml")
    assert cli.main(["text", "test.epub", "--config", str(settings)]) == 0
    assert "Mr. Utterson met Poole." in capsys.readouterr().out


def test_make_passes_words_files_on_and_checks_them_first(monkeypatch, capsys, tmp_path):
    seen = []

    def fake_make(request, config, report):
        seen.append(config["pronunciation"]["files"])
        return [tmp_path / "Book.m4b"]

    monkeypatch.setattr(cli, "make_audiobook", fake_make)
    configured = words_file(tmp_path, 'Poole = "pool"\n', "a.toml")
    words = words_file(tmp_path, 'Poole = "pole"\n', "b.toml")
    args = ["make", "book.epub", "--config", str(config_file(tmp_path, configured))]
    assert cli.main([*args, "--words", str(words)]) == 0
    assert seen == [[str(configured), str(words)]]
    assert cli.main([*args, "--words", str(tmp_path / "missing.toml")]) == 1
    assert "Pronunciation file not found" in capsys.readouterr().err
    assert len(seen) == 1  # stopped before making anything
