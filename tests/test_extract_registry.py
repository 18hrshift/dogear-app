"""The list of formats Dogear reads, which every surface (CLI help, web page) names from."""

import pytest

from dogear import cli
from dogear.extract import EXTRACTORS, FORMATS, format_names


def test_every_extension_is_lower_case_with_its_dot_and_listed_once():
    extensions = [e for f in FORMATS for e in f.extensions]
    assert len(extensions) == len(set(extensions)) == len(EXTRACTORS)
    assert all(e.startswith(".") and e == e.lower() for e in extensions)


def test_format_names():
    assert format_names("or") == "EPUB, PDF, Word (DOCX), HTML, Markdown, MOBI, or AZW3"
    assert format_names("and").endswith("MOBI, and AZW3")


@pytest.mark.parametrize("name", ["book.EPUB", "book.MD", "book.Docx", "page.HTM"])
def test_extensions_match_in_any_case(tmp_path, capsys, name):
    path = tmp_path / name
    path.write_bytes(b"")
    assert cli.main(["outline", str(path)]) == 1  # empty: the extractor says why
    err = capsys.readouterr().err
    assert "Error:" in err and "files yet" not in err


def test_an_unknown_kind_lists_every_extension(tmp_path, capsys):
    path = tmp_path / "notes.odt"
    path.write_text("x")
    assert cli.main(["outline", str(path)]) == 1
    err = capsys.readouterr().err
    assert all(extension in err for extension in EXTRACTORS)
