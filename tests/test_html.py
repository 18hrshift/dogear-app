import codecs
import logging
import urllib.request
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
    PageMarker,
    Paragraph,
    SceneBreak,
    Table,
)
from dogear.errors import ConfigError, ExtractionError
from dogear.extract.html_book import extract
from dogear.extract.html_encodings import CODECS, codec_for_label


def page(body: str, *, head: str = "<title>A Page</title>", html_attrs: str = "") -> str:
    return f"<!DOCTYPE html><html {html_attrs}><head>{head}</head><body>{body}</body></html>"


def read(tmp_path: Path, config, text: str | bytes, name: str = "book.html") -> Book:
    path = tmp_path / name
    if isinstance(text, str):
        path.write_text(text, encoding="utf-8")
    else:
        path.write_bytes(text)
    return extract(path, config)


def blocks(tmp_path: Path, config, body: str, **page_kwargs) -> list:
    book = read(tmp_path, config, page(body, **page_kwargs))
    assert len(book.sections) == 1
    return book.sections[0].blocks


# Metadata


def test_metadata_from_the_head(tmp_path, config):
    head = (
        "<title> The   Sample Book </title>"
        '<meta name="author" content="Ada Writer">'
        '<meta name="DC.Creator" content="Bo Coauthor">'
        '<meta name="citation_author" content="Ada Writer">'
    )
    book = read(tmp_path, config, page("<p>Text.</p>", head=head, html_attrs='lang="en-GB"'))
    assert book.metadata.title == "The Sample Book"
    assert book.metadata.authors == ["Ada Writer", "Bo Coauthor"]
    assert book.metadata.language == "en-GB"
    assert book.source_format == "html"


def test_language_from_a_meta_tag(tmp_path, config):
    head = '<meta http-equiv="Content-Language" content="de, en">'
    assert read(tmp_path, config, page("<p>Text.</p>", head=head)).metadata.language == "de"
    head = '<meta name="dc.language" content="fr">'
    assert read(tmp_path, config, page("<p>Text.</p>", head=head)).metadata.language == "fr"


def test_title_falls_back_to_a_lone_top_heading_then_the_file_name(tmp_path, config):
    text = page("<h1>Heading Title</h1><h2>One</h2><p>A.</p>", head="")
    assert read(tmp_path, config, text).metadata.title == "Heading Title"
    text = page("<h1>One</h1><p>A.</p><h1>Two</h1><p>B.</p>", head="")
    assert read(tmp_path, config, text, name="my-page.htm").metadata.title == "my-page"


# Sections


def test_sections_start_at_the_top_heading_level(tmp_path, config):
    body = (
        "<h1>The Book</h1><p>By someone.</p>"
        '<h2 id="c1">One</h2><p>A.</p><h3>Part of one</h3><p>A2.</p>'
        "<h2>Two</h2><p>B.</p>"
    )
    book = read(tmp_path, config, page(body))
    assert [(s.title, s.kind, s.source) for s in book.sections] == [
        ("The Book", "front", "book.html"),
        ("One", "body", "book.html#c1"),
        ("Two", "body", "book.html"),
    ]
    assert Heading(3, "Part of one") in book.sections[1].blocks


def test_the_page_headers_title_stays_in_the_front_matter(tmp_path, config):
    body = (
        '<header><h1 class="title">The Book</h1><p class="author">Ada Writer</p></header>'
        "<h1>One</h1><p>A.</p>"
        "<article><header><h1>Two</h1></header><p>B.</p></article>"
    )
    book = read(tmp_path, config, page(body))
    assert [(s.title, s.kind) for s in book.sections] == [
        ("The Book", "front"),
        ("One", "body"),
        ("Two", "body"),  # an article's own header does start a section
    ]


def test_a_page_with_no_headings_is_one_section(tmp_path, config):
    [section] = read(
        tmp_path, config, page("<p>Just text.</p>", head="<title>Notes</title>")
    ).sections
    assert (section.title, section.kind) == ("Notes", "body")


def test_headings_in_lists_tables_asides_and_quotes_dont_start_sections(tmp_path, config):
    body = (
        "<h2>One</h2><p>A.</p>"
        "<blockquote><h2>Quoted</h2><p>Q.</p></blockquote>"
        "<aside><h2>Sidebar</h2></aside>"
        "<table><tr><td><h2>In a cell</h2></td></tr></table>"
        "<ul><li><h2>In a list</h2></li></ul>"
        "<h2>Two</h2><p>B.</p>"
    )
    assert [s.title for s in read(tmp_path, config, page(body)).sections] == ["One", "Two"]


def test_a_section_element_goes_with_its_heading(tmp_path, config):
    body = (
        "<section><h1>One</h1><p>A.</p></section>"
        '<section role="doc-appendix"><h1>Appendix</h1><p>Extra.</p></section>'
        '<section epub:type="backmatter"><h1>Colophon</h1><p>Set in type.</p></section>'
    )
    book = read(tmp_path, config, page(body))
    assert [(s.title, s.kind) for s in book.sections] == [
        ("One", "body"),
        ("Appendix", "back"),
        ("Colophon", "back"),  # epub:type, as written in a document parsed as HTML
    ]


def test_a_heading_after_other_content_cuts_inside_its_section(tmp_path, config):
    body = "<section><h1>One</h1><p>A.</p><h1>Two</h1><p>B.</p></section>"
    book = read(tmp_path, config, page(body))
    assert [s.blocks for s in book.sections] == [
        [Heading(1, "One"), Paragraph("A.")],
        [Heading(1, "Two"), Paragraph("B.")],
    ]


def test_a_page_break_before_a_heading_starts_the_next_section(tmp_path, config):
    body = (
        '<h1>One</h1><p>A.</p><span role="doc-pagebreak" aria-label="7"></span>'
        "<h1>Two</h1><p>B.</p>"
    )
    book = read(tmp_path, config, page(body))
    assert book.sections[0].blocks == [Heading(1, "One"), Paragraph("A.")]
    assert book.sections[1].blocks[0] == PageMarker("7")


# Blocks


def test_blocks_like_an_epub(tmp_path, config):
    body = (
        "<p>Para <b>bold</b><br>next line.</p>"
        "<ol><li>First</li><li>Second</li></ol>"
        "<table><tr><th>Name</th><th>Age</th></tr><tr><td>Ann</td><td>3</td></tr></table>"
        '<img src="cat.png" alt="A sleeping cat"><img src="rule.png" alt=""><img src="x.png">'
        "<hr><p>After the break.</p>"
        "<script>var x = 1;</script><style>p {}</style>"
    )
    assert blocks(tmp_path, config, body) == [
        Paragraph("Para bold\nnext line."),
        ListBlock(("First", "Second"), ordered=True),
        Table((("Name", "Age"), ("Ann", "3")), header_rows=1),
        Image(alt="A sleeping cat"),
        Image(alt=""),
        Image(alt=None),
        SceneBreak(),
        Paragraph("After the break."),
    ]


def test_language_tags(tmp_path, config):
    body = (
        '<p lang="fr">Tout en français.</p>'
        '<p xml:lang="de">Auf Deutsch.</p>'
        '<p>English, <span lang="fr">un peu</span>.</p>'
    )
    assert blocks(tmp_path, config, body, html_attrs='lang="en"') == [
        Paragraph("Tout en français.", lang="fr"),
        Paragraph("Auf Deutsch.", lang="de"),
        Paragraph("English, un peu."),
    ]


def test_notes_link_to_their_references(tmp_path, config):
    body = (
        '<p>It began.<a href="#n1" role="doc-noteref">1</a></p>'
        '<aside role="doc-footnote" id="n1"><p>The note. <a href="#r1" role="doc-backlink">'
        "Back</a></p></aside>"
    )
    assert blocks(tmp_path, config, body) == [
        Paragraph("It began.", note_refs=("book.html#n1",)),
        Footnote("book.html#n1", "The note.", label="1"),
    ]


def test_xhtml_is_read_as_xml(tmp_path, config):
    text = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'
        ' xml:lang="en"><head><title>X</title></head><body>'
        '<section epub:type="chapter"><h1>One</h1><p>A&nbsp;b.</p></section>'
        '<section epub:type="index"><h1>Index</h1><p>Words.</p></section>'
        "</body></html>"
    )
    book = read(tmp_path, config, text, name="book.xhtml")
    assert [(s.title, s.kind) for s in book.sections] == [("One", "body"), ("Index", "back")]
    assert book.sections[0].blocks[1] == Paragraph("A b.")
    assert book.source_format == "xhtml"


def test_malformed_xhtml_suggests_html(tmp_path, config):
    with pytest.raises(ExtractionError, match="isn't well-formed XML") as e:
        read(tmp_path, config, "<html><body><p>Unclosed</body></html>", name="book.xhtml")
    assert "rename it to end in .html" in e.value.hint


def test_messy_html_is_read_anyway(tmp_path, config):
    text = "<title>T</title><p>One<p>Two <b>bold<i>both</b> after</i><ul><li>A<li>B</ul>"
    assert read(tmp_path, config, text).sections[0].blocks == [
        Paragraph("One"),
        Paragraph("Two boldboth after"),
        ListBlock(("A", "B")),
    ]


# Character sets


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("<p>café — naïve</p>".encode(), "café — naïve"),  # UTF-8 with no declaration
        (b'<meta charset="windows-1252"><p>caf\xe9 \x97 x</p>', "café — x"),
        (
            b'<meta http-equiv="Content-Type" content="text/html; charset=ISO-8859-1">'
            b"<p>caf\xe9 \x93q\x94</p>",
            "café “q”",
        ),  # read as Windows-1252, as browsers do
        (codecs.BOM_UTF16_LE + "<p>café</p>".encode("utf-16-le"), "café"),
        (b'<?xml version="1.0" encoding="iso-8859-15"?><html><p>\xa4uro</p></html>', "€uro"),
        (b"<p>caf\xe9, not UTF-8</p>", "café, not UTF-8"),  # undeclared: Windows-1252
    ],
)
def test_character_sets(tmp_path, config, data, expected):
    [block] = read(tmp_path, config, data).sections[0].blocks
    assert block == Paragraph(expected)


# Damaged and hostile files


def test_the_watched_server_sees_a_fetch():
    with watched_server() as (base, requests):
        with urllib.request.urlopen(f"{base}/probe.png", timeout=5) as response:
            assert response.read() == b"FETCHED"
    assert requests == ["/probe.png"]


def test_nothing_is_fetched(tmp_path, config):
    with watched_server() as (base, requests):
        text = (
            f'<!DOCTYPE html SYSTEM "{base}/html.dtd">'
            f'<html><head><link rel="icon" href="{base}/icon.png"></head><body>'
            f"{remote_references(base)}<p>Text.</p></body></html>"
        )
        book = read(tmp_path, config, text)
        xhtml = (
            f'<?xml version="1.0"?><!DOCTYPE html SYSTEM "{base}/xhtml.dtd" '
            f'[<!ENTITY e SYSTEM "{base}/entity.txt">]>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            f'<img src="{base}/photo.jpg" alt="A photo"/><p>Text.</p></body></html>'
        )
        read(tmp_path, config, xhtml, name="book.xhtml")
    assert requests == []
    assert Image(alt="A photo") in book.sections[0].blocks


def test_external_entities_are_never_read(tmp_path, config):
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")
    entity = f'<!DOCTYPE html [<!ENTITY x SYSTEM "{secret.as_uri()}">]>'
    book = read(tmp_path, config, f"{entity}<html><body><p>a &x; b</p></body></html>")
    assert "SECRET" not in book.sections[0].blocks[-1].text
    xhtml = (
        f'<?xml version="1.0"?>{entity}<html xmlns="http://www.w3.org/1999/xhtml">'
        "<body><p>a &x; b</p></body></html>"
    )
    with pytest.raises(ExtractionError) as e:
        read(tmp_path, config, xhtml, name="book.xhtml")
    assert "SECRET" not in e.value.message


def test_too_deep_to_read_is_an_error_not_a_part(tmp_path, config):
    deep = "<div>" * 300 + "<p>Deep.</p>" + "</div>" * 300 + "<p>After.</p>"
    with pytest.raises(ExtractionError, match="can't be read completely") as e:
        read(tmp_path, config, page(deep))
    assert "damaged" in e.value.hint


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "is empty"),
        (b"   \n", "is empty"),
        (b"\x00\x01\x02 PK binary", "binary data"),
        (b"<html><head><title>Only a head</title></head></html>", "has no <body>"),
        (b"<html><body><p> </p><nav><p>Contents</p></nav></body></html>", "no readable text"),
    ],
)
def test_unreadable_files(tmp_path, config, data, message):
    with pytest.raises(ExtractionError, match=message):
        read(tmp_path, config, data)


def test_size_limit_is_a_setting(tmp_path):
    text = page("<p>" + "word " * 250_000 + "</p>")  # about 1.25 MB
    config = load_config(_write(tmp_path / "dogear.toml", "[html]\nmax_file_mb = 1\n"))
    with pytest.raises(ExtractionError, match="bigger than 1 MB") as e:
        read(tmp_path, config, text)
    assert "html.max_file_mb" in e.value.hint
    raised = load_config(_write(tmp_path / "big.toml", "[html]\nmax_file_mb = 2\n"))
    assert read(tmp_path, raised, text).word_count() == 250_000


def test_size_limit_must_be_positive(tmp_path):
    config = load_config(_write(tmp_path / "dogear.toml", "[html]\nmax_file_mb = 0\n"))
    with pytest.raises(ConfigError, match="html.max_file_mb"):
        read(tmp_path, config, page("<p>Text.</p>"))


def test_unreadable_file_is_an_extraction_error(tmp_path, config, monkeypatch):
    path = tmp_path / "book.html"
    path.write_text(page("<p>Text.</p>"))

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", refuse)
    with pytest.raises(ExtractionError, match="Permission denied"):
        extract(path, config)


@pytest.mark.parametrize(
    "label", ["base64", "uu", "zlib", "rot13", "rot-13", "hex", "bz2", "punycode", "utf-7"]
)
def test_a_codec_that_isnt_a_browsers_character_set_is_ignored(tmp_path, config, caplog, label):
    """Python's codecs include ones that aren't text at all, and UTF-7, which browsers
    refuse. Before, each of these stopped Dogear with a traceback or read nothing."""
    data = f'<meta charset="{label}"><p>Café text.</p>'.encode()
    with caplog.at_level(logging.WARNING):
        book = read(tmp_path, config, data)
    assert book.sections[0].blocks == [Paragraph("Café text.")]  # guessed: UTF-8
    assert f"says its text is in '{label}', which Dogear doesn't know" in caplog.text


@pytest.mark.parametrize(
    ("label", "data", "expected"),
    [
        ("UTF8", "naïve".encode(), "naïve"),
        ("us-ascii", b"caf\xe9 \x93q\x94", "café “q”"),  # read as Windows-1252
        ("latin1", b"\x80uro", "€uro"),
        ("utf-16", "naïve".encode(), "naïve"),  # a <meta> read as ASCII can't be UTF-16
        ("koi8-r", "мир".encode("koi8_r"), "мир"),
        ("shift_jis", "日本".encode("cp932"), "日本"),
        ("gbk", "中文".encode("gbk"), "中文"),
        ("iso-8859-8-i", "שלום".encode("iso8859_8"), "שלום"),
    ],
)
def test_browsers_character_set_labels(tmp_path, config, label, data, expected):
    page_bytes = f'<meta charset="{label}"><p>'.encode() + data + b"</p>"
    [block] = read(tmp_path, config, page_bytes).sections[0].blocks
    assert block == Paragraph(expected)


def test_every_label_is_a_text_codec():
    for label, codec in CODECS.items():
        assert codecs.lookup(codec)._is_text_encoding, label
    assert codec_for_label(" UTF-8 ") == "utf-8" and codec_for_label("base64") is None


def test_unknown_declared_charset_is_reported(tmp_path, config, caplog):
    with caplog.at_level(logging.WARNING):
        book = read(tmp_path, config, b'<meta charset="klingon"><p>Text.</p>')
    assert book.sections[0].blocks == [Paragraph("Text.")]
    assert "klingon" in caplog.text


# The command line


def test_outline_of_an_html_file(tmp_path, capsys):
    path = tmp_path / "story.htm"
    path.write_text(page("<h1>One</h1><p>First.</p><h1>Two</h1><p>Second.</p>"))
    assert cli.main(["outline", str(path)]) == 0
    out = capsys.readouterr().out
    assert "HTM file, 2 sections, 4 words." in out
    assert "1. One: main text, 2 words." in out


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path
