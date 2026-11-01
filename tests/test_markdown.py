import codecs
import logging
from pathlib import Path

import pytest
from network_watch import remote_references, watched_server

from dogear import cli
from dogear.config import load_config
from dogear.document import (
    Book,
    Footnote,
    Heading,
    Image,
    ListBlock,
    Paragraph,
    SceneBreak,
    Table,
)
from dogear.errors import ConfigError, ExtractionError
from dogear.extract import front_matter
from dogear.extract.markdown_book import extract

FRONT = """---
title: "Dogear: A Story"
authors:
  - Ada Writer
  - Bo Coauthor
lang: en-GB
date: 2026-10-30
---
"""


def read(tmp_path: Path, config, text: str | bytes, name: str = "book.md") -> Book:
    path = tmp_path / name
    if isinstance(text, str):
        path.write_text(text, encoding="utf-8")
    else:
        path.write_bytes(text)
    return extract(path, config)


def blocks(tmp_path: Path, config, text: str) -> list:
    book = read(tmp_path, config, text)
    assert len(book.sections) == 1
    return book.sections[0].blocks


# Metadata


def test_front_matter_gives_the_metadata(tmp_path, config):
    book = read(tmp_path, config, FRONT + "# Heading\n\nText.\n")
    assert book.metadata.title == "Dogear: A Story"
    assert book.metadata.authors == ["Ada Writer", "Bo Coauthor"]
    assert book.metadata.language == "en-GB"
    assert book.source_format == "md"
    assert book.sections[0].blocks == [Heading(1, "Heading"), Paragraph("Text.")]


@pytest.mark.parametrize(
    ("front", "authors", "language"),
    [
        ("author: Ada Writer\nlanguage: de\n", ["Ada Writer"], "de"),
        ("authors: [Ada, Bo, Ada]\nlang: no\n", ["Ada", "Bo"], "no"),  # Norwegian, not false
        ("Title: X\nAuthor: Cap\n", ["Cap"], None),  # keys in any case
    ],
)
def test_front_matter_variants(tmp_path, config, front, authors, language):
    book = read(tmp_path, config, f"---\n{front}---\n\nText.\n")
    assert book.metadata.authors == authors
    assert book.metadata.language == language


def test_without_front_matter_the_title_is_the_lone_top_heading(tmp_path, config):
    text = "# The Book\n\n## One\n\nA.\n\n## Two\n\nB.\n"
    book = read(tmp_path, config, text)
    assert book.metadata.title == "The Book"
    assert [(s.title, s.kind) for s in book.sections] == [
        ("The Book", "front"),
        ("One", "body"),
        ("Two", "body"),
    ]
    assert read(
        tmp_path, config, "# One\n\nA.\n\n# Two\n", name="notes.markdown"
    ).metadata.title == ("notes")


def test_a_rule_at_the_top_isnt_front_matter(tmp_path, config, caplog):
    with caplog.at_level(logging.INFO):
        found = blocks(tmp_path, config, "---\n\nIntro.\n\n---\n\nMore.\n")
    assert found == [Paragraph("Intro."), SceneBreak(), Paragraph("More.")]
    assert "aren't front matter" in caplog.text


def test_invalid_front_matter_says_how_to_fix_it(tmp_path, config):
    with pytest.raises(ExtractionError, match="isn't valid YAML.*line 2") as e:
        read(tmp_path, config, "---\ntitle: Dogear: A Story\n---\n\nText.\n")
    assert "needs quotes" in e.value.hint


def test_front_matter_cant_build_objects(tmp_path, config):
    marker = tmp_path / "ran"
    front = f"---\ntitle: !!python/object/apply:os.system ['touch {marker}']\n---\n\nText.\n"
    book = read(tmp_path, config, front)
    assert not marker.exists()
    assert book.metadata.title == "book"  # the value is a list, not text: ignored


@pytest.mark.parametrize(
    "value",
    [
        "[" * 400 + "]" * 400,  # a RecursionError before
        "{a: " * 400 + "}" * 400,
        "[" * 50 + "]" * 50,  # 51 deep, with the mapping around it
        "\n" + "\n".join("  " * n + "- " for n in range(1, 60)) + "x",
    ],
)
def test_front_matter_nested_too_deep_is_an_error(tmp_path, config, value):
    with pytest.raises(ExtractionError, match="more than 50 levels deep") as e:
        read(tmp_path, config, f"---\ntitle: {value}\n---\n\nText.\n")
    assert "title, author, and language" in e.value.hint


def test_running_out_of_recursion_in_yaml_is_an_error_too(tmp_path, config, monkeypatch):
    def too_deep(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(front_matter.yaml, "load", too_deep)
    with pytest.raises(ExtractionError, match="more than 50 levels deep"):
        read(tmp_path, config, "---\ntitle: Fine\n---\n\nText.\n")


def test_front_matter_nested_a_little_is_read(tmp_path, config):
    nested = "[" * 49 + "]" * 49  # 50 deep, with the mapping around it
    book = read(tmp_path, config, f"---\ntitle: Shallow\nother: {nested}\n---\n\nText.\n")
    assert book.metadata.title == "Shallow"


def test_a_block_too_long_for_front_matter_is_read_as_text(tmp_path, config, caplog):
    # 66,000 characters, which as YAML would be a mistake: "mapping values not allowed"
    chapter = "A chapter after a rule: its words. " * 1_900
    with caplog.at_level(logging.INFO):
        found = blocks(tmp_path, config, f"---\n\n{chapter}\n\n---\n\nMore.\n")
    assert found == [Paragraph(chapter.strip()), SceneBreak(), Paragraph("More.")]
    assert "aren't front matter" in caplog.text


def test_values_that_arent_text_are_ignored_with_a_warning(tmp_path, config, caplog):
    with caplog.at_level(logging.WARNING):
        book = read(tmp_path, config, "---\nauthor: {name: Ada}\n---\n\nText.\n")
    assert book.metadata.authors == []
    assert "Ignored 'author'" in caplog.text


# Blocks


def test_markdown_blocks(tmp_path, config):
    text = """Para with *emphasis*,
a soft line break, and ~~struck~~ text.

1. First
2. Second
   - nested

| Name | Age |
|------|-----|
| Ann  | 3   |

***

```
code  block
```

After.
"""
    assert blocks(tmp_path, config, text) == [
        Paragraph("Para with emphasis, a soft line break, and struck text."),
        ListBlock(("First", "Second", "nested"), ordered=True),
        Table((("Name", "Age"), ("Ann", "3")), header_rows=1),
        SceneBreak(),
        Paragraph("code block"),
        Paragraph("After."),
    ]


def test_images(tmp_path, config):
    text = '![A sleeping cat](cat.png)\n\n![](photo.jpg)\n\n<img src="rule.png" alt="">\n'
    assert blocks(tmp_path, config, text) == [
        Image(alt="A sleeping cat"),
        Image(alt=None),  # Markdown with no description: none given, not decorative
        Image(alt=""),  # HTML can mark an image decorative
    ]


def test_footnotes(tmp_path, config):
    text = "# One\n\nIt began.[^a] It went on.[^b]\n\n[^a]: First note.\n[^b]: Second, *long*.\n"
    [section] = read(tmp_path, config, text).sections
    assert section.kind == "body"  # notes at the end don't make the chapter back matter
    assert section.blocks == [
        Heading(1, "One"),
        Paragraph("It began. It went on.", note_refs=("book.md#fn1", "book.md#fn2")),
        Footnote("book.md#fn1", "First note.", label="1"),
        Footnote("book.md#fn2", "Second, long.", label="2"),
    ]


def test_html_in_markdown_is_read_safely(tmp_path, config):
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")
    text = (
        f'<!DOCTYPE x [<!ENTITY s SYSTEM "{secret.as_uri()}">]>\n\n'
        'English, <span lang="fr">un peu</span>. &s;\n\n'
        '<p lang="fr">Tout en français.</p>\n\n'
        "<script>alert('hi')</script>\n\n"
        "<!-- a comment -->\n"
    )
    found = blocks(tmp_path, config, "---\nlang: en\n---\n" + text)
    assert Paragraph("Tout en français.", lang="fr") in found
    assert all("SECRET" not in getattr(b, "text", "") for b in found)
    assert all("alert" not in getattr(b, "text", "") for b in found)


def test_nothing_is_fetched(tmp_path, config):
    with watched_server() as (base, requests):
        text = f"![A photo]({base}/photo.jpg)\n\n{remote_references(base)}\n\nText.\n"
        found = blocks(tmp_path, config, text)
    assert requests == []
    assert found[0] == Image(alt="A photo")


# Character sets and limits


def test_utf16_with_a_byte_order_mark(tmp_path, config):
    data = codecs.BOM_UTF16_LE + "# Café\n\nText.\n".encode("utf-16-le")
    assert read(tmp_path, config, data).sections[0].title == "Café"


def test_text_that_isnt_utf8_is_an_error(tmp_path, config):
    with pytest.raises(ExtractionError, match="isn't UTF-8") as e:
        read(tmp_path, config, "# Café\n".encode("cp1252"))
    assert "UTF-8" in e.value.hint


def test_nesting_too_deep_to_read_is_an_error(tmp_path, config):
    with pytest.raises(ExtractionError, match="more than 100 levels deep"):
        read(tmp_path, config, ">" * 150 + " deep\n\nAfter.\n")


def test_size_limit_is_a_setting(tmp_path):
    text = "word " * 250_000  # about 1.25 MB
    config = load_config(_write(tmp_path / "dogear.toml", "[markdown]\nmax_file_mb = 1\n"))
    with pytest.raises(ExtractionError, match="bigger than 1 MB") as e:
        read(tmp_path, config, text)
    assert "markdown.max_file_mb" in e.value.hint
    raised = load_config(_write(tmp_path / "big.toml", "[markdown]\nmax_file_mb = 2\n"))
    assert read(tmp_path, raised, text).word_count() == 250_000


def test_size_limit_must_be_positive(tmp_path):
    config = load_config(_write(tmp_path / "dogear.toml", "[markdown]\nmax_file_mb = -1\n"))
    with pytest.raises(ConfigError, match="markdown.max_file_mb"):
        read(tmp_path, config, "Text.\n")


def wide_table(columns: int, rows: int) -> str:
    """A header `columns` wide over `rows` rows of one short cell each, which markdown-it
    fills out to the header's width: a small file with many cells."""
    return "|" + "h|" * columns + "\n|" + "-|" * columns + "\n" + "x\n" * rows + "\n"


def test_a_wide_table_is_refused_before_it_fills_the_memory(tmp_path, config):
    """Before, a 1.6 MB table of 2,000 columns by 400 rows took over 1 GB to read."""
    config["markdown"]["max_pieces"] = 5_000
    small = wide_table(100, 10)  # 1,100 cells, about 3,300 pieces
    assert read(tmp_path, config, small).sections
    text = wide_table(100, 20)  # under 700 bytes, but 2,100 cells
    assert len(text) < 700
    with pytest.raises(ExtractionError, match="more than 5,000 pieces") as e:
        read(tmp_path, config, text)
    assert "filled out to its header's width" in e.value.hint
    assert "markdown.max_pieces" in e.value.hint


def test_pieces_inside_paragraphs_count_too(tmp_path, config):
    config["markdown"]["max_pieces"] = 5_000
    assert read(tmp_path, config, "*a* " * 1_000 + "\n").sections  # 3 pieces each
    with pytest.raises(ExtractionError, match="more than 5,000 pieces"):
        read(tmp_path, config, "*a* " * 2_000 + "\n")


def test_tables_in_quotes_and_lists_count_too(tmp_path, config):
    config["markdown"]["max_pieces"] = 5_000
    quoted = "\n".join("> " + line for line in wide_table(100, 20).splitlines())
    listed = "- " + "\n  ".join(wide_table(100, 20).splitlines())
    for text in (quoted, listed):
        with pytest.raises(ExtractionError, match="more than 5,000 pieces"):
            read(tmp_path, config, text + "\n")


def test_too_many_lines_are_refused_before_they_are_parsed(tmp_path, config):
    config["markdown"]["max_lines"] = 1_000
    assert read(tmp_path, config, "Text.\n" + "\n" * 998).sections  # 1,000 lines
    with pytest.raises(ExtractionError, match="has 1,001 lines, more than the 1,000") as e:
        read(tmp_path, config, "Text.\n" + "\n" * 999)
    assert "markdown.max_lines" in e.value.hint


def test_the_default_limits_read_a_long_novel(tmp_path, config):
    paragraph = "A sentence of a novel, written out at some length, " * 4
    novel = "".join(f"## Chapter {n}\n\n" + (paragraph + "*Now*.\n\n") * 100 for n in range(60))
    assert len(read(tmp_path, config, novel).sections) == 60


@pytest.mark.parametrize("name", ["max_lines", "max_pieces"])
def test_the_work_limits_must_be_positive(tmp_path, name):
    config = load_config(_write(tmp_path / "dogear.toml", f"[markdown]\n{name} = 0\n"))
    with pytest.raises(ConfigError, match=f"markdown.{name}"):
        read(tmp_path, config, "Text.\n")


def test_an_empty_file_has_no_text(tmp_path, config):
    with pytest.raises(ExtractionError, match="no readable text"):
        read(tmp_path, config, FRONT)


# The command line


def test_outline_of_a_markdown_file(tmp_path, capsys):
    path = tmp_path / "story.md"
    path.write_text(FRONT + "# One\n\nFirst.[^1]\n\n# Two\n\nSecond.\n\n[^1]: A note.\n")
    assert cli.main(["outline", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Title: Dogear: A Story" in out
    assert "By: Ada Writer, Bo Coauthor" in out
    assert "MD file, 2 sections" in out
    assert "1. One: main text, 2 words." in out


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path
