import logging
import zipfile
from pathlib import Path

import pytest
from docx_builder import (
    NAMESPACES,
    STYLES,
    build_docx,
    drawing,
    endnote_ref,
    footnote_ref,
    heading,
    p,
    r,
    settings_xml,
    table,
    text_box,
)
from network_watch import watched_server

from dogear import cli
from dogear.config import load_config
from dogear.document import Book, Footnote, Heading, Image, ListBlock, Paragraph, Table
from dogear.errors import ConfigError, ExtractionError
from dogear.extract.docx import extract


def read(tmp_path: Path, config, body: str, **kwargs) -> Book:
    return extract(build_docx(tmp_path / "doc.docx", body, **kwargs), config)


def blocks(tmp_path: Path, config, body: str, **kwargs) -> list:
    book = read(tmp_path, config, body, **kwargs)
    assert len(book.sections) == 1
    return book.sections[0].blocks


def all_blocks(book: Book) -> list:
    return [b for s in book.sections for b in s.blocks]


# Metadata


def test_metadata_from_the_document_properties(tmp_path, config):
    book = read(
        tmp_path,
        config,
        p("Text."),
        title="A Tale",
        creator="Ada Writer; Bo Coauthor",
        language="en-GB",
    )
    assert book.metadata.title == "A Tale"
    assert book.metadata.authors == ["Ada Writer", "Bo Coauthor"]
    assert book.metadata.language == "en-GB"
    assert book.source_format == "docx"


def test_title_falls_back_to_the_title_paragraph_then_the_file_name(tmp_path, config):
    body = p("The Real Title", style="Title") + p("Text.")
    assert read(tmp_path, config, body, title=None).metadata.title == "The Real Title"
    path = build_docx(tmp_path / "my-notes.docx", p("Text."), core=False)
    book = extract(path, config)
    assert book.metadata.title == "my-notes"
    assert book.metadata.authors == []


def test_language_falls_back_to_the_default_in_the_styles(tmp_path, config):
    assert read(tmp_path, config, p("Text.")).metadata.language == "en-US"
    no_language = STYLES.replace('w:val="en-US"', 'w:val="x-none"')
    book = read(tmp_path, config, p("Text."), styles=no_language)
    assert book.metadata.language is None


# Headings and sections


def test_headings_by_style_id_name_and_outline_level(tmp_path, config):
    styles = STYLES.replace(
        "</w:styles>",
        # Word in German: the id is translated, the built-in name is not.
        '<w:style w:type="paragraph" w:styleId="berschrift2"><w:name w:val="heading 2"/>'
        "</w:style>"
        # A renamed style, based on a heading style.
        '<w:style w:type="paragraph" w:styleId="ChapterTitle"><w:name w:val="Chapter Title"/>'
        '<w:basedOn w:val="Heading1"/></w:style>'
        # A custom style with its own outline level.
        '<w:style w:type="paragraph" w:styleId="Sub"><w:name w:val="Sub"/>'
        '<w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>'
        # Outline level 9 is body text, even in a style based on a heading.
        '<w:style w:type="paragraph" w:styleId="NotHeading"><w:name w:val="Not"/>'
        '<w:basedOn w:val="Heading1"/><w:pPr><w:outlineLvl w:val="9"/></w:pPr></w:style>'
        "</w:styles>",
    )
    body = (
        p("Chapter", style="ChapterTitle")
        + p("German", style="berschrift2")
        + p("Custom", style="Sub")
        + p("Direct", outline=1)
        + p("Body text", style="NotHeading")
        + p("Deep", outline=7)
    )
    assert all_blocks(read(tmp_path, config, body, styles=styles)) == [
        Heading(1, "Chapter"),
        Heading(2, "German"),
        Heading(3, "Custom"),
        Heading(2, "Direct"),
        Paragraph("Body text"),
        Heading(6, "Deep"),  # Word has nine levels; the model has six
    ]


def test_sections_start_at_the_top_heading_level(tmp_path, config):
    body = (
        p("My Report", style="Title")
        + p("Prepared for the board.")
        + heading(1, "Introduction")
        + p("Intro text.")
        + heading(2, "Background")
        + p("More.")
        + heading(1, "Findings")
        + p("Found.")
    )
    book = read(tmp_path, config, body)
    assert [(s.title, s.kind) for s in book.sections] == [
        ("My Report", "front"),
        ("Introduction", "body"),
        ("Findings", "body"),
    ]
    assert book.sections[0].blocks == [
        Heading(1, "My Report"),
        Paragraph("Prepared for the board."),
    ]
    assert Heading(2, "Background") in book.sections[1].blocks


def test_a_heading_used_once_doesnt_cut_the_chapters_below_it(tmp_path, config):
    body = heading(1, "The Book") + heading(2, "One") + p("A.") + heading(2, "Two") + p("B.")
    book = read(tmp_path, config, body)
    assert [s.title for s in book.sections] == ["The Book", "One", "Two"]


def test_a_document_with_no_headings_is_one_section(tmp_path, config):
    book = read(tmp_path, config, p("Just text.") + p("More text."), title="Notes")
    [section] = book.sections
    assert (section.title, section.kind) == ("Notes", "body")


def test_empty_paragraphs_and_headings_are_left_out(tmp_path, config):
    body = p() + heading(1, "") + p("Text.") + p(r(" "))
    assert blocks(tmp_path, config, body) == [Paragraph("Text.")]


# Running text


def test_runs_breaks_and_tabs(tmp_path, config):
    body = p(
        r("Line one", extra="<w:br/>"),
        r("line two", extra="<w:tab/>"),
        r("tab", extra='<w:br w:type="page"/>'),
        r("well", extra="<w:noBreakHyphen/>"),
        r("known"),
    )
    assert blocks(tmp_path, config, body) == [Paragraph("Line one line two tab well-known")]


def test_tracked_changes_read_as_accepted(tmp_path, config):
    body = p(
        r("Kept "),
        "<w:ins><w:r><w:t>inserted </w:t></w:r></w:ins>",
        "<w:del><w:r><w:delText>deleted </w:delText></w:r></w:del>",
        "<w:moveFrom><w:r><w:t>moved away </w:t></w:r></w:moveFrom>",
        "<w:moveTo><w:r><w:t>moved here.</w:t></w:r></w:moveTo>",
    )
    assert blocks(tmp_path, config, body) == [Paragraph("Kept inserted moved here.")]


def test_fields_hyperlinks_and_hidden_text(tmp_path, config):
    body = p(
        r("See "),
        '<w:hyperlink r:id="rId9"><w:r><w:t>the site</w:t></w:r></w:hyperlink>',
        r(" on page "),
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        "<w:r><w:instrText> PAGEREF _Toc1 \\h </w:instrText></w:r>"
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        "<w:r><w:t>4</w:t></w:r>"
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>',
        r(" secret", hidden=True),
        r("."),
    )
    assert blocks(tmp_path, config, body) == [Paragraph("See the site on page 4.")]


def test_content_control_placeholders_are_not_read(tmp_path, config):
    body = (
        "<w:sdt><w:sdtPr><w:showingPlcHdr/></w:sdtPr><w:sdtContent>"
        + p("Click or tap here to enter text.")
        + "</w:sdtContent></w:sdt>"
        + "<w:sdt><w:sdtPr/><w:sdtContent>"
        + p("Filled in.")
        + "</w:sdtContent></w:sdt>"
    )
    assert blocks(tmp_path, config, body) == [Paragraph("Filled in.")]


def test_equations_are_read_as_their_characters(tmp_path, config):
    body = p(r("So "), "<m:oMath><m:r><m:t>x</m:t></m:r><m:r><m:t>=2</m:t></m:r></m:oMath>", r("."))
    assert blocks(tmp_path, config, body) == [Paragraph("So x =2 .")]


# Lists


def test_numbered_and_bulleted_lists(tmp_path, config):
    body = (
        p("First", num=(1, 0))
        + p("Second", num=(1, 0))
        + p("nested bullet", num=(1, 1))
        + p("Between.")
        + p("Apples", num=(2, 0))
        + p("Pears", num=(2, 0))
    )
    assert blocks(tmp_path, config, body) == [
        ListBlock(("First", "Second", "nested bullet"), ordered=True),
        Paragraph("Between."),
        ListBlock(("Apples", "Pears"), ordered=False),
    ]


def test_a_new_list_starts_where_the_numbering_changes(tmp_path, config):
    body = p("One", num=(1, 0)) + p("Dot", num=(2, 0))
    assert blocks(tmp_path, config, body) == [
        ListBlock(("One",), ordered=True),
        ListBlock(("Dot",), ordered=False),
    ]


def test_a_nested_level_from_another_numbering_stays_in_its_list(tmp_path, config):
    body = p("Outer", num=(1, 0)) + p("inner", num=(2, 1)) + p("Outer again", num=(1, 0))
    assert blocks(tmp_path, config, body) == [
        ListBlock(("Outer", "inner", "Outer again"), ordered=True)
    ]


def test_numbering_that_shows_nothing_is_not_a_list(tmp_path, config):
    body = p("Indented", num=(3, 0)) + p("No list", num=(0, 0)) + p("Undefined", num=(99, 0))
    assert blocks(tmp_path, config, body) == [
        Paragraph("Indented"),
        Paragraph("No list"),
        Paragraph("Undefined"),
    ]


def test_a_list_style_gives_its_paragraphs_numbering(tmp_path, config):
    styles = STYLES.replace(
        "</w:styles>",
        '<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/>'
        '<w:pPr><w:numPr><w:numId w:val="2"/></w:numPr></w:pPr></w:style></w:styles>',
    )
    body = p("A", style="ListBullet") + p("B", style="ListBullet")
    assert blocks(tmp_path, config, body, styles=styles) == [ListBlock(("A", "B"))]


def test_a_document_with_no_numbering_part_has_no_lists(tmp_path, config):
    assert blocks(tmp_path, config, p("One", num=(1, 0)), numbering=None) == [Paragraph("One")]


# Tables


def test_tables_with_header_rows(tmp_path, config):
    body = table([["Name", "Age"], ["Ann", "3"], ["Bo", "4"]], header_rows=1)
    assert blocks(tmp_path, config, body) == [
        Table((("Name", "Age"), ("Ann", "3"), ("Bo", "4")), header_rows=1)
    ]


def test_table_cells_with_several_paragraphs_and_an_image(tmp_path, config):
    cell = p("Line one") + p("Line two", drawing("A chart"))
    body = table([["Cell", cell]]) + p("After.")
    assert blocks(tmp_path, config, body) == [
        Table((("Cell", "Line one\nLine two"),)),
        Image(alt="A chart"),
        Paragraph("After."),
    ]


def test_a_header_row_counts_only_at_the_top(tmp_path, config):
    rows = "<w:tr>" + f"<w:tc>{p('a')}</w:tc>" + "</w:tr>"
    marked = "<w:tr><w:trPr><w:tblHeader/></w:trPr>" + f"<w:tc>{p('b')}</w:tc>" + "</w:tr>"
    body = f"<w:tbl>{rows}{marked}</w:tbl>"
    [tbl] = blocks(tmp_path, config, body)
    assert tbl.header_rows == 0


# Images and text boxes


def test_images_with_and_without_alt_text(tmp_path, config):
    body = p(
        r("Look."),
        drawing("A sleeping cat"),
        drawing(title="Only a title"),
        drawing(),
        drawing("Ignored when decorative", decorative=True),
    )
    assert blocks(tmp_path, config, body) == [
        Paragraph("Look."),
        Image(alt="A sleeping cat"),
        Image(alt="Only a title"),
        Image(alt=None),
        Image(alt=""),
    ]


def test_older_vml_pictures(tmp_path, config):
    body = p(
        '<w:r><w:pict><v:shape alt="An old picture"><v:imagedata r:id="rId5" '
        'o:title="unused"/></v:shape></w:pict></w:r>',
        '<w:r><w:pict><v:shape><v:imagedata r:id="rId6" o:title="Titled"/></v:shape>'
        "</w:pict></w:r>",
    )
    assert blocks(tmp_path, config, body) == [Image(alt="An old picture"), Image(alt="Titled")]


def test_text_boxes_are_read_once_after_their_paragraph(tmp_path, config):
    body = p(r("Main text."), text_box(p("In the box."))) + p("Next.")
    assert blocks(tmp_path, config, body) == [
        Paragraph("Main text."),
        Paragraph("In the box."),
        Paragraph("Next."),
    ]


# Notes


def test_footnotes_follow_their_paragraph(tmp_path, config):
    body = p(r("It began."), footnote_ref(2), r(" Then"), footnote_ref(3), r(".")) + p("Next.")
    footnotes = {2: "The first note.", 3: "The second note."}
    assert blocks(tmp_path, config, body, footnotes=footnotes) == [
        Paragraph(
            "It began. Then.",
            note_refs=("word/footnotes.xml#2", "word/footnotes.xml#3"),
        ),
        Footnote("word/footnotes.xml#2", "The first note.", label="1"),
        Footnote("word/footnotes.xml#3", "The second note.", label="2"),
        Paragraph("Next."),
    ]


def test_endnotes_end_the_last_section(tmp_path, config):
    body = (
        heading(1, "One")
        + p(r("A claim."), endnote_ref(5))
        + heading(1, "Two")
        + p(r("Another."), endnote_ref(6))
    )
    book = read(tmp_path, config, body, endnotes={5: "Source one.", 6: "Source two."})
    last = book.sections[-1].blocks
    assert last[-2:] == [
        Footnote("word/endnotes.xml#5", "Source one.", label="i"),
        Footnote("word/endnotes.xml#6", "Source two.", label="ii"),
    ]
    assert book.sections[0].blocks[1].note_refs == ("word/endnotes.xml#5",)


def test_note_marks_custom_and_formatted(tmp_path, config):
    body = p(r("A"), footnote_ref(1, custom="*"), r(" B"), footnote_ref(2), r("."))
    found = blocks(
        tmp_path,
        config,
        body,
        footnotes={1: "Starred.", 2: "Numbered."},
        settings=settings_xml(footnote_format="upperLetter"),
    )
    assert found[0] == Paragraph(
        "A B.", note_refs=("word/footnotes.xml#1", "word/footnotes.xml#2")
    )  # the custom mark "*" is taken out of the text
    assert [(n.label, n.text) for n in found[1:]] == [("*", "Starred."), ("A", "Numbered.")]


def test_the_last_sections_note_format_wins(tmp_path, config):
    sect_pr = '<w:sectPr><w:footnotePr><w:numFmt w:val="lowerRoman"/></w:footnotePr></w:sectPr>'
    body = p(r("Text"), footnote_ref(1))
    found = blocks(
        tmp_path,
        config,
        body,
        footnotes={1: "Note."},
        settings=settings_xml(footnote_format="upperLetter"),
        sect_pr=sect_pr,
    )
    assert found[1].label == "i"


def test_a_note_referenced_from_a_heading_or_list_is_still_read(tmp_path, config):
    body = (
        p(r("Title"), footnote_ref(1), style="Heading1")
        + p(r("Item"), footnote_ref(2), num=(1, 0))
        + p("Text.")
    )
    found = blocks(tmp_path, config, body, footnotes={1: "On the heading.", 2: "On the item."})
    assert found == [
        Heading(1, "Title"),
        Footnote("word/footnotes.xml#1", "On the heading.", label="1"),
        ListBlock(("Item",), ordered=True),
        Footnote("word/footnotes.xml#2", "On the item.", label="2"),
        Paragraph("Text."),
    ]


def test_notes_never_referred_to_come_last(tmp_path, config, caplog):
    with caplog.at_level(logging.INFO):
        found = blocks(tmp_path, config, p("Text."), footnotes={4: "Orphan."})
    assert found == [Paragraph("Text."), Footnote("word/footnotes.xml#4", "Orphan.")]
    assert "aren't referred to" in caplog.text


def test_a_reference_to_a_missing_note_is_reported(tmp_path, config, caplog):
    with caplog.at_level(logging.WARNING):
        found = blocks(tmp_path, config, p(r("Text"), footnote_ref(7)), footnotes={1: "Other."})
    assert found[0].note_refs == ("word/footnotes.xml#7",)
    assert "notes Dogear didn't find" in caplog.text


# Languages


def test_language_of_paragraphs_and_runs(tmp_path, config):
    body = (
        p(r("Tout en français.", lang="fr-FR"))
        + p(r("Mostly English, "), r("un peu", lang="fr-FR"), r("."))
        + p(r("Par style.", style="French"))
        + p(r("Same as the book.", lang="en-US"))
    )
    assert blocks(tmp_path, config, body) == [
        Paragraph("Tout en français.", lang="fr-FR"),
        Paragraph("Mostly English, un peu."),
        Paragraph("Par style.", lang="fr-FR"),
        Paragraph("Same as the book."),
    ]


def test_right_to_left_text_takes_its_bidi_language(tmp_path, config):
    run = '<w:r><w:rPr><w:rtl/><w:lang w:val="en-US" w:bidi="ar-SA"/></w:rPr><w:t>مرحبا</w:t></w:r>'
    assert blocks(tmp_path, config, p(run)) == [Paragraph("مرحبا", lang="ar-SA")]


# Skipped on purpose


def test_the_table_of_contents_is_skipped(tmp_path, config, caplog):
    contents = (
        '<w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/>'
        "</w:docPartObj></w:sdtPr><w:sdtContent>"
        + p("Contents")
        + p("Introduction 1")
        + "</w:sdtContent></w:sdt>"
    )
    body = contents + p("Chapter One 3", style="TOC1") + p("Text.")
    with caplog.at_level(logging.INFO):
        assert blocks(tmp_path, config, body) == [Paragraph("Text.")]
    assert "Skipped the table of contents" in caplog.text


# Damaged, unusual, and hostile files


def test_not_a_zip(tmp_path, config):
    path = tmp_path / "old.docx"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 an old .doc file")
    with pytest.raises(ExtractionError, match="isn't a ZIP") as e:
        extract(path, config)
    assert ".doc" in e.value.hint and "password" in e.value.hint


def test_a_zip_that_isnt_a_word_document(tmp_path, config):
    path = tmp_path / "renamed.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml", "<office:document-content/>")
    with pytest.raises(ExtractionError, match="isn't a Word document") as e:
        extract(path, config)
    assert "save it as a Word document" in e.value.hint


def test_malformed_document_xml(tmp_path, config):
    path = build_docx(tmp_path / "doc.docx", p("x"))
    broken = tmp_path / "broken.docx"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(broken, "w") as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "word/document.xml":
                data = data.replace(b"</w:body>", b"")
            dst.writestr(item, data)
    with pytest.raises(ExtractionError, match="isn't well-formed XML") as e:
        extract(broken, config)
    assert "save it again" in e.value.hint


def test_strict_open_xml_is_refused_with_a_way_out(tmp_path, config):
    strict = (
        '<w:document xmlns:w="http://purl.oclc.org/ooxml/wordprocessingml/main">'
        "<w:body><w:p><w:r><w:t>x</w:t></w:r></w:p></w:body></w:document>"
    )
    with pytest.raises(ExtractionError, match="Strict Open XML") as e:
        read(tmp_path, config, "", document=strict)
    assert "save it as a Word document" in e.value.hint


def test_a_document_with_no_text(tmp_path, config):
    with pytest.raises(ExtractionError, match="no readable text"):
        read(tmp_path, config, p() + p(r(" ")) + "<w:sectPr/>")


def test_nothing_is_fetched(tmp_path, config):
    with watched_server() as (base, requests):
        document = (
            f'<?xml version="1.0"?><!DOCTYPE w:document SYSTEM "{base}/document.dtd">'
            f"<w:document {NAMESPACES}><w:body>{p(r('Linked.'), drawing('A linked photo'))}"
            "</w:body></w:document>"
        )
        rels = (
            '<?xml version="1.0"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdImage" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/image" Target="{base}/photo.jpg" '
            'TargetMode="External"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/'
            f'2006/relationships/styles" Target="{base}/styles.xml"/></Relationships>'
        )
        path = build_docx(
            tmp_path / "linked.docx",
            "",
            files={"word/document.xml": document, "word/_rels/document.xml.rels": rels},
        )
        found = extract(path, config).sections[0].blocks
    assert requests == []
    assert found == [Paragraph("Linked."), Image(alt="A linked photo")]


def test_external_entities_are_never_read(tmp_path, config):
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")
    document = (
        f'<?xml version="1.0"?><!DOCTYPE w:document [<!ENTITY x SYSTEM "{secret.as_uri()}">]>'
        f"<w:document {NAMESPACES}><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    path = tmp_path / "xxe.docx"
    build_docx(path, "", files={"word/document.xml": document})
    with pytest.raises(ExtractionError) as e:
        extract(path, config)
    assert "SECRET" not in e.value.message


def test_zip_bomb_is_refused_and_the_limit_is_a_setting(tmp_path):
    big = "".join(p("word " * 1000) for _ in range(250))  # about 1.3 MB of XML
    path = build_docx(tmp_path / "big.docx", big)
    config = load_config(_write(tmp_path / "dogear.toml", "[docx]\nmax_member_mb = 1\n"))
    with pytest.raises(ExtractionError, match="more than 1 MB") as e:
        extract(path, config)
    assert "docx.max_member_mb" in e.value.hint
    raised = load_config(_write(tmp_path / "big.toml", "[docx]\nmax_member_mb = 2\n"))
    assert extract(path, raised).word_count() == 250_000


def test_member_limit_must_be_positive(tmp_path):
    config = load_config(_write(tmp_path / "dogear.toml", "[docx]\nmax_member_mb = 0\n"))
    with pytest.raises(ConfigError, match="max_member_mb"):
        extract(build_docx(tmp_path / "doc.docx", p("Text.")), config)


def test_relationships_that_leave_the_archive_are_ignored(tmp_path, config):
    rels = (
        '<?xml version="1.0"?><Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/styles" Target="../../../etc/passwd"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/footnotes" Target="http://example.com/notes.xml" TargetMode="External"/>'
        "</Relationships>"
    )
    path = build_docx(
        tmp_path / "doc.docx", heading(1, "Kept"), files={"word/_rels/document.xml.rels": rels}
    )
    book = extract(path, config)
    assert book.sections[0].blocks == [Heading(1, "Kept")]


# The command line


def test_outline_of_a_word_document(tmp_path, config, capsys):
    body = (
        heading(1, "One")
        + p("First chapter.")
        + heading(1, "Two")
        + p(r("Second."), footnote_ref(1))
    )
    path = build_docx(tmp_path / "story.docx", body, title="Story", footnotes={1: "A note."})
    assert cli.main(["outline", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Title: Story" in out
    assert "DOCX file, 2 sections" in out
    assert "1. One: main text, 3 words." in out
    assert "2. Two: main text, 4 words, 1 note." in out  # the note's words count


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path
