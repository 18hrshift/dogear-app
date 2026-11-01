"""The PDF extractor end to end, on PDFs built at test time (see pdf_builder.py)."""

import logging

import pytest
from pdf_builder import (
    ALICE,
    CHAPTER_TITLES,
    INDENT,
    MARGIN,
    RUNNING_HEADER,
    PdfBuilder,
    alice,
    marked,
    novel,
    wrap,
)

from dogear.document import Footnote, Heading, PageMarker, Paragraph
from dogear.errors import ConfigError, ExtractionError, ScannedPdfError
from dogear.extract import extract_book
from dogear.extract.pdf import extract

THREE = list(CHAPTER_TITLES[:3])
PARAGRAPHS = 24  # novel()'s default, about two pages a chapter


def paragraphs(section) -> list[str]:
    return [b.text for b in section.blocks if isinstance(b, Paragraph)]


def all_paragraphs(book) -> list[str]:
    return [text for section in book.sections for text in paragraphs(section)]


def titles(book) -> list[str]:
    return [section.title for section in book.sections]


def kinds(book) -> list[str]:
    return [section.kind for section in book.sections]


# The cascade


def test_bookmarks(tmp_path, config):
    book = extract(novel(tmp_path / "b.pdf", bookmarks=["One", "Two", "Three"]), config)
    assert titles(book) == ["One", "Two", "Three"]
    assert [s.source for s in book.sections] == ["pages 1-2", "pages 3-4", "pages 5-6"]


def bookmarked_book(path, outline):
    """One page a chapter. `outline` maps page index to [(level, title), ...]."""
    pdf = PdfBuilder(path)
    for n, title in enumerate(CHAPTER_TITLES):
        for level, mark in outline.get(n, []):
            pdf.bookmark(mark, level=level)
        pdf.heading(title)
        pdf.paragraph(ALICE[n])
        pdf.new_page()
    return pdf.save()


def test_single_top_level_bookmark_uses_its_chapters(tmp_path, config):
    outline = {
        0: [(0, "Alice\u2019s Adventures in Wonderland"), (1, "One")],
        1: [(1, "Two")],
        2: [(1, "Three")],
        3: [(1, "Four")],
    }
    book = extract(bookmarked_book(tmp_path / "book.pdf", outline), config)
    assert titles(book) == ["One", "Two", "Three", "Four"]


def test_two_parts_holding_chapters_stay_parts(tmp_path, config):
    outline = {
        0: [(0, "Part I"), (1, "One")],
        1: [(1, "Two")],
        2: [(0, "Part II"), (1, "Three")],
        3: [(1, "Four")],
    }
    book = extract(bookmarked_book(tmp_path / "parts.pdf", outline), config)
    assert titles(book) == ["Part I", "Part II"]
    assert [s.source for s in book.sections] == ["pages 1-2", "pages 3-4"]


def test_top_level_bookmarks_preferred(tmp_path, config):
    pdf = PdfBuilder(tmp_path / "nested.pdf")
    for n, title in enumerate(THREE):
        pdf.bookmark(f"Chapter {n + 1}")
        pdf.bookmark(f"Section {n + 1}.1", level=1)
        pdf.heading(title)
        pdf.paragraph(ALICE[n])
        pdf.new_page()
    book = extract(pdf.save(), config)
    assert titles(book) == ["Chapter 1", "Chapter 2", "Chapter 3"]


def test_chapter_dividers(tmp_path, config):
    headings = ["Chapter One", "CHAPTER II", "Chapter 3"]
    book = extract(novel(tmp_path / "d.pdf", headings=headings, heading_size=11), config)
    assert titles(book) == headings


def test_font_size(tmp_path, config):
    assert titles(extract(novel(tmp_path / "f.pdf"), config)) == THREE


def test_pattern(tmp_path, config):
    headings = [f"Chapter {n + 1}: {title}" for n, title in enumerate(THREE)]
    book = extract(novel(tmp_path / "p.pdf", headings=headings, heading_size=11), config)
    assert titles(book) == headings


def test_page_split(tmp_path, config):
    path = novel(tmp_path / "s.pdf", headings=[None, None, None])
    assert titles(extract(path, config)) == ["Part 1"]
    config["pdf"]["page_split_size"] = 2
    book = extract(path, config)
    assert titles(book) == ["Part 1", "Part 2", "Part 3"]
    assert [s.source for s in book.sections] == ["pages 1-2", "pages 3-4", "pages 5-6"]


def test_cascade_order_comes_from_config(tmp_path, config):
    path = novel(tmp_path / "o.pdf", bookmarks=["One", "Two", "Three"])
    assert titles(extract(path, config)) == ["One", "Two", "Three"]
    config["pdf"]["chapter_detection"] = ["font_size", "bookmarks"]
    assert titles(extract(path, config)) == THREE


def test_heading_size_ratio_comes_from_config(tmp_path, config):
    path = novel(tmp_path / "r.pdf")  # 22 pt headings over 11 pt text: twice the size
    config["pdf"]["heading_size_ratio"] = 2.5
    assert titles(extract(path, config)) == ["Part 1"]


def test_unknown_method_fails_before_reading(tmp_path, config):
    config["pdf"]["chapter_detection"] = ["bookmarks", "guesswork"]
    with pytest.raises(ConfigError) as caught:
        extract(tmp_path / "not-even-there.pdf", config)
    assert "guesswork" in caught.value.message
    assert "chapter_dividers" in caught.value.hint


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("page_split_size", 0),
        ("heading_size_ratio", 1.0),
        ("subheading_size_ratio", 1.0),
        ("running_line_min_pages", 1),
        ("running_line_min_share", 0),
        ("running_line_max_lines", 0),
        ("paragraph_gap_ratio", 0.9),
        ("paragraph_indent_ratio", 0),
        ("hyphen_evidence_min", 0),
        ("hyphen_without_evidence", "sometimes"),
        ("column_gap_ratio", 0),
        ("column_min_lines", 0),
        ("column_min_width_ratio", 0),
        ("image_min_page_share", 0),
        ("image_min_page_share", 1.5),
        ("scanned_image_min_page_share", 0),
        ("scanned_image_min_page_share", 1.5),
    ],
)
def test_out_of_range_settings(tmp_path, config, key, value):
    config["pdf"][key] = value
    with pytest.raises(ConfigError, match=key):
        extract(tmp_path / "x.pdf", config)


# Pages and furniture


def test_page_labels(tmp_path, config):
    pdf = PdfBuilder(tmp_path / "labels.pdf")
    pdf.page_label("ROMAN_LOWER")
    for text in ("A title page.", "A dedication."):
        pdf.paragraph(text)
        pdf.new_page()
    pdf.page_label("ARABIC", start=1)
    for n, title in enumerate(THREE[:2]):
        pdf.heading(title)
        for text in alice(PARAGRAPHS, start=n):
            pdf.paragraph(text)
        pdf.new_page()
    book = extract(pdf.save(), config)
    front, first, second = book.sections
    assert (front.kind, front.page_labels(), front.source) == ("front", ["i", "ii"], "pages i-ii")
    assert first.page_labels() == ["1", "2"]
    assert second.page_labels() == ["3", "4"]


def test_pages_numbered_by_position_without_labels(tmp_path, config):
    book = extract(novel(tmp_path / "n.pdf"), config)
    assert [s.page_labels() for s in book.sections] == [["1", "2"], ["3", "4"], ["5", "6"]]


def test_running_headers_and_page_numbers_removed(tmp_path, config):
    # Every paragraph comes back exactly as written: no header, page number, or
    # page-break damage inside any of them.
    book = extract(novel(tmp_path / "h.pdf"), config)
    for n, section in enumerate(book.sections):
        assert paragraphs(section) == alice(PARAGRAPHS, start=n)


def test_running_line_min_pages_comes_from_config(tmp_path, config):
    path = novel(tmp_path / "h.pdf")  # the header is on 3 pages
    config["pdf"]["running_line_min_pages"] = 4
    assert RUNNING_HEADER in all_paragraphs(extract(path, config))


def test_running_line_min_share_comes_from_config(tmp_path, config):
    path = novel(tmp_path / "h.pdf")  # on 3 of the 5 pages from its first to its last
    config["pdf"]["running_line_min_share"] = 0.9
    assert RUNNING_HEADER in all_paragraphs(extract(path, config))


def test_two_line_running_header_removed(tmp_path, config):
    # The book's title over the chapter's title, on chapters of about four pages so the
    # chapter's title repeats often enough to count as running.
    path = novel(tmp_path / "two.pdf", headings=THREE[:2], paragraphs=72, chapter_header=True)
    book = extract(path, config)
    assert titles(book) == THREE[:2]
    for n, section in enumerate(book.sections):
        assert len(section.page_labels()) >= 4
        assert paragraphs(section) == alice(72, start=n)


def test_running_line_max_lines_comes_from_config(tmp_path, config):
    path = novel(tmp_path / "two.pdf", headings=THREE[:2], paragraphs=72, chapter_header=True)
    config["pdf"]["running_line_max_lines"] = 1
    found = all_paragraphs(extract(path, config))
    # One line deep: neither header line is apart from the text on its own, so both stay.
    assert RUNNING_HEADER in found
    assert THREE[0] in found


def test_hyphenation_rejoined(tmp_path, config):
    pdf = PdfBuilder(tmp_path / "hyphen.pdf")
    pdf.line("The rabbit-hole went straight on like a tun-", x=MARGIN + INDENT)
    pdf.line("nel for some way, and then dipped suddenly down.")
    pdf.paragraph(ALICE[4])
    book = extract(pdf.save(), config)
    assert all_paragraphs(book)[0] == (
        "The rabbit-hole went straight on like a tunnel for some way, and then dipped "
        "suddenly down."
    )


# ALICE[1] and ALICE[2], set with chosen line breaks. "daisy-" + "chain" is a compound the
# book prints whole elsewhere; nothing else in the book says how to spell "remark-able".
DAISY_CHAIN = (
    "So she was considering in her own mind (as well as she could, for the hot day",
    "made her feel very sleepy and stupid), whether the pleasure of making a daisy-",
    "chain would be worth the trouble of getting up and picking the daisies, when",
    "suddenly a White Rabbit with pink eyes ran close by her.",
)
REMARKABLE = (
    "There was nothing so very remark-",
    "able in that; nor did Alice think it so very much out of the way to hear the Rabbit",
    "say to itself, “Oh dear! Oh dear! I shall be late!”",
)


def hyphen_book(path):
    """ALICE[1] and ALICE[3] set normally, as evidence, then the broken paragraphs."""
    pdf = PdfBuilder(path)
    pdf.paragraph(ALICE[1])
    pdf.paragraph(ALICE[3])
    for lines in (DAISY_CHAIN, REMARKABLE):
        pdf.line(lines[0], x=MARGIN + INDENT)
        for line in lines[1:]:
            pdf.line(line)
    return pdf.save()


def test_line_end_hyphens_decided_by_the_book(tmp_path, config):
    found = all_paragraphs(extract(hyphen_book(tmp_path / "h.pdf"), config))
    assert found[2:] == [ALICE[1], ALICE[2]]  # "daisy-chain" kept, "remarkable" joined


def test_hyphen_without_evidence_comes_from_config(tmp_path, config):
    config["pdf"]["hyphen_without_evidence"] = "keep"
    found = all_paragraphs(extract(hyphen_book(tmp_path / "h.pdf"), config))
    assert found[2:] == [ALICE[1], ALICE[2].replace("remarkable", "remark-able")]


def test_hyphen_evidence_min_comes_from_config(tmp_path, config):
    config["pdf"]["hyphen_evidence_min"] = 2  # "daisy-chain" is printed only once
    found = all_paragraphs(extract(hyphen_book(tmp_path / "h.pdf"), config))
    assert found[2] == ALICE[1].replace("daisy-chain", "daisychain")


# Paragraphs


def test_paragraphs_split_by_indent(tmp_path, config):
    book = extract(novel(tmp_path / "i.pdf", style="indent", running_header=False), config)
    assert all_paragraphs(book) == [p for n in range(3) for p in alice(PARAGRAPHS, start=n)]


def test_paragraphs_split_by_gap(tmp_path, config):
    book = extract(novel(tmp_path / "g.pdf", style="gap", running_header=False), config)
    assert all_paragraphs(book) == [p for n in range(3) for p in alice(PARAGRAPHS, start=n)]


def test_paragraph_gap_ratio_comes_from_config(tmp_path, config):
    path = novel(tmp_path / "g.pdf", style="gap")
    config["pdf"]["paragraph_gap_ratio"] = 3.0  # wider than the blank line between them
    assert len(all_paragraphs(extract(path, config))) < 3 * PARAGRAPHS


def test_paragraph_indent_ratio_comes_from_config(tmp_path, config):
    path = novel(tmp_path / "i.pdf", style="indent")
    config["pdf"]["paragraph_indent_ratio"] = 3.0  # deeper than the 18 pt indent
    assert len(all_paragraphs(extract(path, config))) < 3 * PARAGRAPHS


def test_paragraph_continued_on_the_next_page(tmp_path, config):
    split = wrap(ALICE[3], [400 - INDENT], 400)
    assert not split[1].endswith(".")  # the page breaks mid-sentence
    pdf = PdfBuilder(tmp_path / "across.pdf")
    pdf.paragraph(ALICE[0])
    pdf.line(split[0], x=MARGIN + INDENT)
    pdf.line(split[1])
    pdf.new_page()
    for text in split[2:]:
        pdf.line(text)
    pdf.paragraph(ALICE[4])
    [section] = extract(pdf.save(), config).sections
    # Page 2's marker comes before the whole merged paragraph, so jumping to page 2
    # starts reading a little early rather than mid-sentence.
    assert section.blocks == [
        PageMarker("1"),
        Paragraph(ALICE[0]),
        PageMarker("2"),
        Paragraph(ALICE[3]),
        Paragraph(ALICE[4]),
    ]


def test_new_paragraph_on_the_next_page_is_not_merged(tmp_path, config):
    pdf = PdfBuilder(tmp_path / "fresh.pdf")
    pdf.paragraph(ALICE[0])
    pdf.new_page()
    pdf.paragraph(ALICE[1])
    [section] = extract(pdf.save(), config).sections
    assert section.blocks == [
        PageMarker("1"),
        Paragraph(ALICE[0]),
        PageMarker("2"),
        Paragraph(ALICE[1]),
    ]


# Sections and headings


def test_heading_not_duplicated_as_paragraph(tmp_path, config):
    book = extract(novel(tmp_path / "t.pdf"), config)
    for section in book.sections:
        assert section.blocks[:2] == [
            PageMarker(section.page_labels()[0]),
            Heading(1, section.title),
        ]
        assert section.title not in paragraphs(section)


def test_body_size_divider_becomes_heading(tmp_path, config):
    path = novel(tmp_path / "d.pdf", headings=["Chapter 1", "Chapter 2"], heading_size=11)
    for section in extract(path, config).sections:
        assert section.blocks[1] == Heading(1, section.title)
        assert section.title not in paragraphs(section)


def test_front_and_back_matter_kept_and_classified(tmp_path, config):
    path = novel(
        tmp_path / "fb.pdf",
        front=["Alice’s Adventures in Wonderland, by Lewis Carroll.", "For my sister."],
        back=["Acknowledgments", "Index"],
    )
    book = extract(path, config)
    assert titles(book) == ["Front matter", *THREE, "Acknowledgments", "Index"]
    assert kinds(book) == ["front", "body", "body", "body", "back", "back"]
    assert paragraphs(book.sections[0]) == [
        "Alice’s Adventures in Wonderland, by Lewis Carroll.",
        "For my sister.",
    ]


def test_back_matter_found_after_the_last_chapter(tmp_path, config):
    # Chapter dividers find only the chapters; the back matter is found by its headings.
    path = novel(
        tmp_path / "dv.pdf",
        headings=["Chapter 1", "Chapter 2"],
        heading_size=11,
        back=["Notes", "About the Author"],
    )
    book = extract(path, config)
    assert titles(book) == ["Chapter 1", "Chapter 2", "Notes", "About the Author"]
    assert kinds(book) == ["body", "body", "back", "back"]
    assert book.sections[1].source == "pages 3-4"  # ends where the notes begin


@pytest.mark.parametrize("opening_size", [22, 11])
def test_back_matter_running_header_is_one_section(tmp_path, config, opening_size):
    notes_start = 4

    def header(page: int) -> str | None:
        return "NOTES" if page > notes_start else None

    pdf = PdfBuilder(tmp_path / "notes.pdf", header=header, footer=lambda page: str(page + 1))
    for n, title in enumerate(["Chapter 1", "Chapter 2"]):
        pdf.heading(title, size=opening_size)
        for text in alice(PARAGRAPHS, start=n):
            pdf.paragraph(text)
        pdf.new_page()
    assert pdf.page == notes_start
    pdf.heading("Notes", size=opening_size)
    for text in alice(50):
        pdf.paragraph(text)
    book = extract(pdf.save(), config)
    assert titles(book) == ["Chapter 1", "Chapter 2", "Notes"]
    notes = book.sections[-1]
    assert len(notes.page_labels()) >= 3
    assert "NOTES" not in paragraphs(notes)
    assert paragraphs(notes) == alice(50)


def subheaded_chapter(path):
    """One chapter with subheadings: 14 pt ones, one of them opening a page, and a bold
    one in body size."""
    pdf = PdfBuilder(path)
    pdf.heading(CHAPTER_TITLES[0])
    pdf.paragraph(ALICE[0])
    pdf.heading(CHAPTER_TITLES[1], size=14)
    pdf.paragraph(ALICE[1])
    pdf.heading(CHAPTER_TITLES[2], size=11, bold=True)
    pdf.paragraph(ALICE[2])
    pdf.new_page()
    pdf.heading(CHAPTER_TITLES[3], size=14)
    pdf.paragraph(ALICE[3])
    return pdf.save()


def test_subheadings_inside_a_chapter(tmp_path, config):
    [section] = extract(subheaded_chapter(tmp_path / "sub.pdf"), config).sections
    assert [b for b in section.blocks if not isinstance(b, PageMarker)] == [
        Heading(1, CHAPTER_TITLES[0]),
        Paragraph(ALICE[0]),
        Heading(2, CHAPTER_TITLES[1]),
        Paragraph(ALICE[1]),
        Heading(3, CHAPTER_TITLES[2]),
        Paragraph(ALICE[2]),
        Heading(2, CHAPTER_TITLES[3]),
        Paragraph(ALICE[3]),
    ]


def test_subheading_size_ratio_comes_from_config(tmp_path, config):
    config["pdf"]["subheading_size_ratio"] = 1.3  # more than the 14 pt subheadings
    [section] = extract(subheaded_chapter(tmp_path / "sub.pdf"), config).sections
    assert [b for b in section.blocks if isinstance(b, Heading)] == [
        Heading(1, CHAPTER_TITLES[0]),
        Heading(2, CHAPTER_TITLES[2]),  # the bold one is still a subheading
    ]
    assert CHAPTER_TITLES[1] in paragraphs(section)


def test_drop_cap_is_not_a_heading(tmp_path, config):
    pdf = PdfBuilder(tmp_path / "dropcap.pdf")
    for n, title in enumerate(THREE[:2]):
        pdf.heading(title)
        pdf.drop_cap_paragraph(ALICE[n])
        for text in alice(4, start=n + 1):
            pdf.paragraph(text)
        pdf.new_page()
    book = extract(pdf.save(), config)
    assert titles(book) == THREE[:2]
    for n, section in enumerate(book.sections):
        assert [b for b in section.blocks if isinstance(b, Heading)] == [Heading(1, THREE[n])]
        assert paragraphs(section)[0] == ALICE[n]


def test_no_chapters_found_is_one_section(tmp_path, config):
    config["pdf"]["chapter_detection"] = ["bookmarks"]
    path = novel(tmp_path / "one.pdf", title="Wonderland")
    [section] = extract(path, config).sections
    assert (section.title, section.kind, section.source) == ("Wonderland", "body", "pages 1-6")


# Failures


def test_blank_pdf_is_scanned(tmp_path, config):
    pdf = PdfBuilder(tmp_path / "blank.pdf")
    pdf.new_page()
    pdf.new_page()
    with pytest.raises(ScannedPdfError) as caught:
        extract(pdf.save(), config)
    assert caught.value.hint == (
        "Run `dogear ocr <file>` first to add a text layer, then open the new file."
    )


def test_image_only_pdf_is_scanned(tmp_path, config):
    pdf = PdfBuilder(tmp_path / "scan.pdf")
    for _ in range(3):
        pdf.image()
        pdf.new_page()
    with pytest.raises(ScannedPdfError):
        extract(pdf.save(), config)


def test_min_chars_per_page_comes_from_config(tmp_path, config):
    path = novel(tmp_path / "thin.pdf")
    config["pdf"]["min_chars_per_page"] = 1_000_000
    with pytest.raises(ScannedPdfError):
        extract(path, config)


def test_picture_page_without_text_is_reported(tmp_path, config, caplog):
    pdf = PdfBuilder(tmp_path / "plate.pdf")
    for text in alice(30):
        pdf.paragraph(text)
    pdf.new_page()
    pdf.image()
    with caplog.at_level(logging.WARNING, logger="dogear.extract.pdf"):
        extract(pdf.save(), config)
    assert "Pages 3 of plate.pdf are pictures with no text" in caplog.text


def test_password_protected_pdf(tmp_path, config):
    pdf = PdfBuilder(tmp_path / "locked.pdf", user_password="secret")
    pdf.paragraph(ALICE[0])
    with pytest.raises(ExtractionError) as caught:
        extract(pdf.save(), config)
    assert not isinstance(caught.value, ScannedPdfError)
    assert "password" in caught.value.message
    assert "password" in caught.value.hint


def test_owner_password_only_still_opens(tmp_path, config):
    pdf = PdfBuilder(tmp_path / "restricted.pdf", owner_password="owner")
    pdf.paragraph(ALICE[0])
    assert all_paragraphs(extract(pdf.save(), config)) == [ALICE[0]]


@pytest.mark.parametrize("damage", ["truncated", "junk", "empty"])
def test_damaged_pdf(tmp_path, config, damage):
    path = novel(tmp_path / "broken.pdf")
    data = path.read_bytes()
    contents = {
        "truncated": data[: len(data) // 2],
        "junk": b"this is not a pdf at all\n" * 20,
        "empty": b"",
    }
    path.write_bytes(contents[damage])
    with pytest.raises(ExtractionError) as caught:
        extract(path, config)
    assert not isinstance(caught.value, ScannedPdfError)
    assert caught.value.hint


# Metadata


def test_metadata(tmp_path, config):
    path = novel(
        tmp_path / "m.pdf",
        title="Alice’s Adventures in Wonderland",
        author="Lewis Carroll; John Tenniel",
        lang="en-GB",
    )
    book = extract(path, config)
    assert book.metadata.title == "Alice’s Adventures in Wonderland"
    assert book.metadata.authors == ["Lewis Carroll", "John Tenniel"]
    assert book.metadata.language == "en-GB"
    assert {section.lang for section in book.sections} == {"en-GB"}
    assert (book.source_format, book.source_path) == ("pdf", path)


def test_title_falls_back_to_file_name(tmp_path, config):
    book = extract(novel(tmp_path / "alice-in-wonderland.pdf"), config)
    assert book.metadata.title == "alice-in-wonderland"
    assert book.metadata.authors == []
    assert book.metadata.language is None


def test_language_that_is_not_a_tag_is_ignored(tmp_path, config):
    book = extract(novel(tmp_path / "l.pdf", lang="not a tag!"), config)
    assert book.metadata.language is None


def test_extract_book_reads_pdf(tmp_path, config):
    book = extract_book(novel(tmp_path / "dispatch.pdf"), config)
    assert book.source_format == "pdf"
    assert titles(book) == THREE


def test_columns_notes_running_lines_and_subheadings_together(tmp_path, config):
    # Two-line running headers, page numbers, a subheading over two columns (lined up on
    # odd pages, offset on even ones), a note reference inside a column, and a note
    # across the foot of the page.
    def header(page: int) -> list[str] | None:
        return [RUNNING_HEADER, CHAPTER_TITLES[0]] if page else None

    pdf = PdfBuilder(tmp_path / "mixed.pdf", header=header, footer=lambda page: str(page + 1))
    for n in range(4):
        pdf.heading(f"{CHAPTER_TITLES[0]}, part {n + 1}", size=14)
        columns = [[marked(ALICE[1], "daisy-chain", "1"), ALICE[2]], [ALICE[3], ALICE[4]]]
        pdf.columns(columns, drop=7 * (n % 2))
        pdf.footnotes([("1", ALICE[2])])
        pdf.new_page()
    [section] = extract(pdf.save(), config).sections
    expected = []
    for n in range(4):
        note = f"page{n + 1}-note1"
        expected += [
            PageMarker(str(n + 1)),
            Heading(2, f"{CHAPTER_TITLES[0]}, part {n + 1}"),
            Paragraph(ALICE[1], note_refs=(note,)),
            *(Paragraph(text) for text in ALICE[2:5]),
            Footnote(note, ALICE[2], label="1"),
        ]
    assert section.blocks == expected
