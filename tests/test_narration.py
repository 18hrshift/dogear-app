"""Spoken structure: which words and pauses each kind of block becomes, per [narration]."""

import logging
from pathlib import Path

import pytest

from dogear.config import load_config
from dogear.document import (
    Book,
    Footnote,
    Heading,
    Image,
    ListBlock,
    Metadata,
    PageMarker,
    Paragraph,
    SceneBreak,
    Section,
    Table,
)
from dogear.errors import ConfigError
from dogear.narration import Utterance, has_narration_words, narrate_book, narrate_title


def book(*sections: Section, lang: str | None = None) -> Book:
    return Book(Metadata("A Book", language=lang), list(sections), Path("book.epub"), "epub")


def narrate(config, *blocks, title="The Storm", **section) -> list[Utterance]:
    """The utterances of a one-section book."""
    [narration] = narrate_book(book(Section(title, blocks=list(blocks), **section)), config)
    return narration.utterances


def spoken(utterances: list[Utterance]) -> list[str]:
    return [u.spoken for u in utterances if u.spoken]


def heard(utterances: list[Utterance]) -> list[tuple[str, int]]:
    """What is said and the silence after it, skipping the opening pause."""
    return [(u.spoken, u.pause_ms) for u in utterances[1:]]


@pytest.fixture
def pause(config):
    return config["narration"]["pauses"]


@pytest.fixture
def narration(config):
    return config["narration"]


# The shape of a section.


def test_a_section_from_start_to_end(config, pause):
    utterances = narrate(
        config,
        Heading(1, "Chapter 1"),
        Paragraph("It was dark. It rained."),
        Paragraph("Then it stopped."),
    )
    assert utterances[0] == Utterance("", "", pause["section_start_ms"])
    assert utterances[1] == Utterance("Chapter 1", "Chapter one.", pause["heading_ms"])
    assert heard(utterances)[1:] == [
        ("It was dark.", pause["sentence_ms"]),
        ("It rained.", pause["paragraph_ms"]),
        ("Then it stopped.", pause["section_end_ms"]),
    ]


def test_display_text_is_kept_beside_the_spoken_text(config):
    utterances = narrate(config, Paragraph("Dr. Jekyll paid $5."))
    assert [(u.text, u.spoken) for u in utterances if u.spoken][-1] == (
        "Dr. Jekyll paid $5.",
        "Doctor Jekyll paid five dollars.",
    )


def test_a_section_without_a_heading_says_its_title(config, pause):
    utterances = narrate(config, Paragraph("Rain fell."), title="The Storm")
    assert heard(utterances) == [
        ("The Storm.", pause["heading_ms"]),
        ("Rain fell.", pause["section_end_ms"]),
    ]
    assert utterances[1].text == "The Storm"


def test_a_heading_after_page_markers_still_counts_as_the_first_block(config):
    utterances = narrate(config, PageMarker("7"), Heading(1, "Storms"), Paragraph("Rain."))
    assert spoken(utterances) == ["Storms.", "Rain."]


def test_a_section_with_no_blocks_says_its_title(config, pause):
    assert heard(narrate(config, title="Blank Page")) == [("Blank Page.", pause["section_end_ms"])]


@pytest.mark.parametrize(
    ("heading", "said"),
    [
        ("Who Goes There?", "Who Goes There?"),
        ("Wait!", "Wait!"),
        ("Part I: Dawn", "Part one: Dawn."),
    ],
)
def test_headings_end_with_one_closing_mark(config, heading, said):
    assert spoken(narrate(config, Heading(2, heading)))[0] == said


def test_a_heading_after_text_gets_a_breath_before_it(config, pause):
    utterances = narrate(
        config, Heading(1, "One"), ListBlock(("a",)), Heading(2, "Two"), Paragraph("B.")
    )
    before_two = utterances[[u.spoken for u in utterances].index("Two.") - 1]
    assert before_two.pause_ms == pause["paragraph_ms"]


def test_sentences_and_paragraphs_get_their_pauses(config, pause):
    utterances = narrate(config, Heading(1, "H"), Paragraph("One. Two! Three?"), Paragraph("Four."))
    assert heard(utterances)[1:] == [
        ("One.", pause["sentence_ms"]),
        ("Two!", pause["sentence_ms"]),
        ("Three?", pause["paragraph_ms"]),
        ("Four.", pause["section_end_ms"]),
    ]


def test_a_scene_break_lengthens_the_pause_before_it(config, pause):
    utterances = narrate(
        config, Heading(1, "H"), Paragraph("Before."), SceneBreak(), Paragraph("After.")
    )
    assert heard(utterances)[1:] == [
        ("Before.", pause["scene_break_ms"]),
        ("After.", pause["section_end_ms"]),
    ]


def test_the_section_end_pause_is_last(config, pause):
    utterances = narrate(config, Paragraph("Only."), SceneBreak())
    assert utterances[-1].pause_ms == pause["section_end_ms"]
    assert utterances[-1].spoken == "Only."


def test_sections_are_numbered_from_one(config):
    narrations = narrate_book(
        book(Section("A", blocks=[Paragraph("x.")]), Section("B", blocks=[Paragraph("y.")])),
        config,
    )
    assert [(n.number, n.section.title) for n in narrations] == [(1, "A"), (2, "B")]
    assert narrations[0].words() == 2  # "A." and "x."


# Lists and tables.


def test_ordered_list_items_are_numbered(config, pause):
    utterances = narrate(
        config, Heading(1, "H"), ListBlock(("milk", "eggs"), ordered=True), Paragraph("Done.")
    )
    assert " ".join(spoken(utterances)) == "H. one: milk. two: eggs. Done."
    pauses = dict(heard(utterances))
    assert pauses["one: milk."] == pause["list_item_ms"]
    assert pauses["two: eggs."] == pause["paragraph_ms"]


def test_ordered_items_stay_one_utterance_before_capitals(config):
    utterances = narrate(config, ListBlock(("Buy milk.", "Get eggs."), ordered=True))
    assert spoken(utterances)[1:] == ["one: Buy milk.", "two: Get eggs."]


def test_unordered_list_items_are_not_numbered(config, pause):
    utterances = narrate(config, Heading(1, "H"), ListBlock(("Milk.", "Eggs.")), Paragraph("x."))
    assert heard(utterances)[1:3] == [
        ("Milk.", pause["list_item_ms"]),
        ("Eggs.", pause["paragraph_ms"]),
    ]


TABLE = Table(rows=(("Name", "Age"), ("Ann", "30"), ("Bob", "")), header_rows=1, caption="Ages")


def test_tables_are_read_row_by_row_with_header_labels(config, pause):
    utterances = narrate(config, Heading(1, "H"), TABLE, Paragraph("After."))
    assert heard(utterances)[1:4] == [
        ("Table: Ages.", pause["sentence_ms"]),
        ("Row one: Name: Ann; Age: thirty.", pause["list_item_ms"]),
        ("Row two: Name: Bob.", pause["paragraph_ms"]),  # empty cells are skipped
    ]


def test_a_table_without_headers_or_caption(config):
    table = Table(rows=(("Ann", "30"), ("Bob", "41")))
    assert spoken(narrate(config, Heading(1, "H"), table))[1:] == [
        "Table.",
        "Row one: Ann; thirty.",
        "Row two: Bob; forty-one.",
    ]


def test_a_header_cell_is_not_repeated_as_its_own_label(config):
    table = Table(rows=(("Item", "Done"), ("Pack", "Done")), header_rows=1)
    assert spoken(narrate(config, Heading(1, "H"), table))[-1] == "Row one: Item: Pack; Done."


def test_tables_can_be_skipped(config, narration, pause):
    narration["tables"] = "skip"
    utterances = narrate(config, Heading(1, "H"), TABLE, Paragraph("After."))
    assert heard(utterances)[1:3] == [
        ("Table: Ages.", pause["sentence_ms"]),
        ("Table not read.", pause["paragraph_ms"]),
    ]
    assert not any("Ann" in s for s in spoken(utterances))


# Images.


def image_words(config, image: Image) -> list[str]:
    return spoken(narrate(config, Heading(1, "H"), image, Paragraph("After.")))[1:-1]


def test_described_images_are_read(config):
    assert image_words(config, Image(alt="A map of London")) == ["Image: A map of London."]
    assert image_words(config, Image(alt="Who drew this?")) == ["Image: Who drew this?"]


def test_decorative_images_are_skipped(config, narration):
    narration["announce_undescribed_images"] = True
    assert image_words(config, Image(alt="")) == []


@pytest.mark.parametrize(("announce", "words"), [(False, []), (True, ["Image."])])
def test_undescribed_images_follow_the_setting(config, narration, announce, words):
    narration["announce_undescribed_images"] = announce
    assert image_words(config, Image(alt=None)) == words


@pytest.mark.parametrize(
    ("image", "words"),
    [
        (Image(alt=None, caption="Fig 1"), ["Image.", "Fig one."]),
        (Image(alt="", caption="The square"), ["Image.", "The square."]),
        (Image(alt="A square", caption="Soho."), ["Image: A square.", "Soho."]),
    ],
)
def test_captions_are_always_read(config, image, words):
    assert image_words(config, image) == words


def test_images_end_with_a_paragraph_pause(config, pause):
    utterances = narrate(config, Heading(1, "H"), Image(alt="A cat"), Paragraph("After."))
    assert dict(heard(utterances))["Image: A cat."] == pause["paragraph_ms"]


# Notes.

NOTE = Footnote("n1", "Written in 1886.", label="1")


def notes_book(*body_blocks, back_notes=(NOTE,)):
    return book(
        Section("One", blocks=list(body_blocks)),
        Section("Notes", kind="back", blocks=[Heading(1, "Notes"), *back_notes]),
    )


def test_notes_after_paragraph_are_read_right_after_it(config, pause):
    narrations = narrate_book(
        notes_book(Paragraph("He wrote it.", note_refs=("n1",)), Paragraph("Later.")),
        config,
    )
    [one] = narrations  # the notes section had nothing left to say
    assert heard(one.utterances)[1:] == [
        ("He wrote it.", pause["paragraph_ms"]),
        ("Note: Written in eighteen eighty-six.", pause["paragraph_ms"]),
        ("Later.", pause["section_end_ms"]),
    ]


def test_a_section_of_notes_already_read_is_left_out(config, caplog):
    with caplog.at_level(logging.INFO, logger="dogear.narration"):
        narrations = narrate_book(notes_book(Paragraph("He wrote it.", note_refs=("n1",))), config)
    assert [n.section.title for n in narrations] == ["One"]
    assert "Section 2, Notes, was left out" in caplog.text


def test_a_notes_section_with_an_unreferenced_note_is_kept(config):
    other = Footnote("n2", "Never referred to.")
    narrations = narrate_book(
        notes_book(Paragraph("He wrote it.", note_refs=("n1",)), back_notes=(NOTE, other)),
        config,
    )
    assert [n.section.title for n in narrations] == ["One", "Notes"]
    # The note read after its paragraph isn't read again; the other one is read here.
    assert spoken(narrations[1].utterances) == ["Notes.", "Note: Never referred to."]


@pytest.mark.parametrize("mode", ["where_printed", "end_of_section"])
def test_a_notes_section_is_kept_when_notes_are_not_read_after_paragraphs(config, narration, mode):
    narration["footnotes"] = mode
    narrations = narrate_book(notes_book(Paragraph("He wrote it.", note_refs=("n1",))), config)
    assert [n.section.title for n in narrations] == ["One", "Notes"]
    assert "Note: Written in eighteen eighty-six." in spoken(narrations[1].utterances)


def test_a_note_printed_in_the_same_section_is_read_once(config):
    utterances = narrate(
        config,
        Heading(1, "H"),
        Paragraph("He wrote it.", note_refs=("n1",)),
        Paragraph("Later."),
        NOTE,
    )
    assert spoken(utterances) == [
        "H.",
        "He wrote it.",
        "Note: Written in eighteen eighty-six.",
        "Later.",
    ]


def test_a_note_referred_to_twice_is_read_once(config):
    utterances = narrate(
        config,
        Heading(1, "H"),
        Paragraph("First.", note_refs=("n1",)),
        Paragraph("Second.", note_refs=("n1",)),
        NOTE,
    )
    assert [s for s in spoken(utterances) if s.startswith("Note")] == [
        "Note: Written in eighteen eighty-six."
    ]


def test_a_reference_to_a_missing_note_is_ignored(config):
    utterances = narrate(config, Heading(1, "H"), Paragraph("Text.", note_refs=("gone",)))
    assert spoken(utterances) == ["H.", "Text."]


def test_notes_where_printed(config, narration, pause):
    narration["footnotes"] = "where_printed"
    utterances = narrate(
        config,
        Heading(1, "H"),
        Paragraph("He wrote it.", note_refs=("n1",)),
        Paragraph("Later."),
        NOTE,
    )
    assert heard(utterances)[1:] == [
        ("He wrote it.", pause["paragraph_ms"]),
        ("Later.", pause["paragraph_ms"]),
        ("Note: Written in eighteen eighty-six.", pause["section_end_ms"]),
    ]


def test_notes_at_the_end_of_the_section(config, narration, pause):
    narration["footnotes"] = "end_of_section"
    second = Footnote("n2", "See chapter 2.")
    utterances = narrate(
        config,
        Heading(1, "H"),
        Paragraph("He wrote it.", note_refs=("n1",)),
        NOTE,
        Paragraph("Later.", note_refs=("n2",)),
        second,
    )
    assert heard(utterances)[1:] == [
        ("He wrote it.", pause["paragraph_ms"]),
        ("Later.", pause["paragraph_ms"]),
        ("Notes.", pause["heading_ms"]),
        ("Note: Written in eighteen eighty-six.", pause["paragraph_ms"]),
        ("Note: See chapter two.", pause["section_end_ms"]),
    ]


def test_notes_can_be_skipped(config, narration):
    narration["footnotes"] = "skip"
    utterances = narrate(
        config, Heading(1, "H"), Paragraph("He wrote it.", note_refs=("n1",)), NOTE
    )
    assert spoken(utterances) == ["H.", "He wrote it."]


def test_notes_follow_their_own_language(config):
    note = Footnote("n1", "Écrit en 1886.", lang="fr")
    utterances = narrate(config, Heading(1, "H"), Paragraph("Text.", note_refs=("n1",)), note)
    # French words for the structure; the number is left for the French voice to read.
    assert spoken(utterances)[-1] == "Note: Écrit en 1886."
    assert utterances[-1].lang == "fr"


# Pages.


def test_pages_are_silent_by_default(config):
    assert spoken(narrate(config, Heading(1, "H"), PageMarker("42"), Paragraph("Text."))) == [
        "H.",
        "Text.",
    ]


def test_pages_can_be_announced(config, narration, pause):
    narration["announce_pages"] = True
    utterances = narrate(config, Heading(1, "H"), PageMarker("42"), Paragraph("Text."))
    assert heard(utterances)[1] == ("Page forty-two.", pause["sentence_ms"])


# Languages.


@pytest.mark.parametrize(
    ("book_lang", "section_lang", "block_lang", "expected"),
    [
        (None, None, None, None),
        ("fr", None, None, "fr"),
        ("fr", "de", None, "de"),
        ("fr", "de", "es", "es"),
        (None, None, "es", "es"),
    ],
)
def test_block_language_beats_section_beats_book(
    config, book_lang, section_lang, block_lang, expected
):
    section = Section(
        "Title", lang=section_lang, blocks=[Paragraph("I have 42 cats.", lang=block_lang)]
    )
    [narration] = narrate_book(book(section, lang=book_lang), config)
    assert narration.utterances[-1].lang == expected


def test_only_english_text_is_spelled_out(config):
    """Other languages write numbers their own way, and their voices read them."""
    english = Section("One", blocks=[Paragraph("I have 42 cats.")])
    french = Section("Deux", lang="fr", blocks=[Paragraph("J'ai 42 chats, soit 1,5 par pièce.")])
    first, second = narrate_book(book(english, french, lang="en"), config)
    assert spoken(first.utterances)[-1] == "I have forty-two cats."
    assert spoken(second.utterances)[-1] == "J'ai 42 chats, soit 1,5 par pièce."


def test_spoken_titles_use_the_section_language(config):
    section = Section("Chapitre 12", lang="fr", blocks=[Paragraph("Texte.")])
    [narration] = narrate_book(book(section, lang="en"), config)
    assert spoken(narration.utterances)[0] == "Chapitre 12."
    assert narration.utterances[1].lang == "fr"


# Settings change what is heard.


def test_pause_settings_change_the_pauses(config, pause):
    pause.update(
        sentence_ms=111,
        paragraph_ms=222,
        heading_ms=333,
        list_item_ms=144,
        scene_break_ms=555,
        section_start_ms=66,
        section_end_ms=777,
    )
    utterances = narrate(
        config,
        Heading(1, "H"),
        Paragraph("One. Two."),
        SceneBreak(),
        ListBlock(("a", "b")),
        Paragraph("End."),
    )
    assert utterances[0].pause_ms == 66
    assert heard(utterances) == [
        ("H.", 333),
        ("One.", 111),
        ("Two.", 555),
        ("a.", 144),
        ("b.", 222),
        ("End.", 777),
    ]


def test_word_settings_change_the_words(config, narration):
    narration["words"].update(
        image="Picture",
        note="Footnote",
        notes="Endnotes",
        table="Grid",
        row="Line",
        page="Leaf",
        table_skipped="Grid left out.",
    )
    narration["announce_pages"] = True
    narration["footnotes"] = "end_of_section"
    table = Table(rows=(("x",),))
    said = spoken(
        narrate(
            config,
            Heading(1, "H"),
            PageMarker("3"),
            Image(alt="A cat"),
            table,
            Paragraph("Text.", note_refs=("n1",)),
            NOTE,
        )
    )
    assert said == [
        "H.",
        "Leaf three.",
        "Picture: A cat.",
        "Grid.",
        "Line one: x.",
        "Text.",
        "Endnotes.",
        "Footnote: Written in eighteen eighty-six.",
    ]
    narration["tables"] = "skip"
    assert spoken(narrate(config, Heading(1, "H"), table))[1:] == ["Grid.", "Grid left out."]


# Passages: where each block sits among the utterances, for read-along text.


def section_narration(config, *blocks, title="The Storm", **section):
    [narration] = narrate_book(book(Section(title, blocks=list(blocks), **section)), config)
    return narration


def shown(narration, part: range) -> list[str]:
    return [narration.utterances[i].text for i in part]


def test_each_block_gets_a_passage_in_reading_order(config):
    note = Footnote("n1", "Written in 1886.")
    blocks = (
        Heading(1, "One"),
        PageMarker("7"),
        Paragraph("It rained. It poured.", note_refs=("n1",)),
        ListBlock(("Milk.", "Eggs. Then bread."), ordered=True),
        SceneBreak(),
        TABLE,
        Image(alt="A map.", caption="The town."),
        note,
    )
    narration = section_narration(config, *blocks)
    passages = narration.passages
    # The note is read after its paragraph, so its passage is there, not where it's printed.
    assert [p.block for p in passages] == [*blocks[:3], note, *blocks[3:7]]
    heading, page, paragraph, read_note, items, scene, table, image = passages
    assert shown(narration, heading.parts[0]) == ["One"]
    assert page.parts == (range(2, 2),)  # silent: a position only
    assert shown(narration, paragraph.parts[0]) == ["It rained.", "It poured."]
    assert shown(narration, read_note.parts[0]) == ["Note: Written in 1886."]
    assert [shown(narration, p) for p in items.parts] == [["Milk."], ["Eggs.", "Then bread."]]
    assert scene.parts == ()
    assert [shown(narration, p) for p in table.parts] == [
        ["Table: Ages."],
        ["Row 1: Name: Ann; Age: 30."],
        ["Row 2: Name: Bob."],
    ]
    assert [shown(narration, p) for p in image.parts] == [["Image: A map."], ["The town."]]


def test_ordered_list_numbers_are_said_but_not_shown(config):
    narration = section_narration(config, ListBlock(("Buy milk.", "Get eggs."), ordered=True))
    item = narration.passages[-1]
    first = narration.utterances[item.parts[0][0]]
    assert (first.text, first.spoken) == ("Buy milk.", "one: Buy milk.")


def test_every_spoken_utterance_belongs_to_a_passage(config, narration):
    narration["announce_pages"] = True
    narration["footnotes"] = "end_of_section"
    result = section_narration(
        config, PageMarker("1"), Paragraph("Text.", note_refs=("n1",)), NOTE, title="Untitled"
    )
    covered = {i for p in result.passages for part in p.parts for i in part}
    spoken_indexes = {i for i, u in enumerate(result.utterances) if u.spoken}
    assert spoken_indexes == covered


def test_added_headings_are_passages_too(config, narration):
    narration["footnotes"] = "end_of_section"
    result = section_narration(config, Paragraph("Text."), NOTE, title="Untitled")
    headings = [p.block for p in result.passages if isinstance(p.block, Heading)]
    assert headings == [Heading(1, "Untitled"), Heading(2, "Notes")]


def test_an_added_title_takes_the_level_most_sections_open_with(config):
    sections = [
        Section("One", blocks=[Heading(2, "One"), Paragraph("x.")]),
        Section("Two", blocks=[Heading(2, "Two"), Paragraph("y.")]),
        Section("Plates", blocks=[Paragraph("z.")]),
    ]
    narrations = narrate_book(book(*sections), config)
    assert narrations[2].passages[0].block == Heading(2, "Plates")


def test_skipped_tables_and_silent_images(config, narration):
    narration["tables"] = "skip"
    result = section_narration(config, Heading(1, "H"), TABLE, Image(alt=""))
    table = result.passages[-1]
    assert table.block == TABLE
    assert [shown(result, p) for p in table.parts] == [["Table: Ages.", "Table not read."]]
    assert len(result.passages) == 2  # the decorative image adds nothing to show


# The title track.


def test_the_title_track_says_the_title_and_authors(config, pause):
    title_book = Book(
        Metadata("Dr. Jekyll", authors=["Ann Smith", "Bob Jones", "Cy Young"]),
        [],
        Path("book.epub"),
        "epub",
    )
    track = narrate_title(title_book, config)
    assert track.number == 0
    assert track.section.title == "Dr. Jekyll"
    assert spoken(track.utterances) == [
        "Doctor Jekyll.",
        "By Ann Smith, Bob Jones and Cy Young.",
    ]
    assert [type(p.block) for p in track.passages] == [Heading, Paragraph]
    assert track.utterances[-1].pause_ms == pause["section_end_ms"]


def test_the_title_track_without_authors_is_just_the_title(config):
    track = narrate_title(book(), config)
    assert spoken(track.utterances) == ["A Book."]


def test_title_track_words_come_from_config(config, narration):
    narration["words"]["by"] = "Written by"
    narration["words"]["and"] = "&"
    title_book = Book(Metadata("T", authors=["A", "B"]), [], Path("b.epub"), "epub")
    assert spoken(narrate_title(title_book, config).utterances)[1] == "Written by A & B."


# Words in other languages.


def structure(config, lang: str | None, section_lang: str | None = None) -> list[str]:
    """What is said for a book in `lang` with a note, an image, a table, and a page."""
    config["narration"]["announce_pages"] = True
    config["narration"]["footnotes"] = "end_of_section"
    blocks = [
        Heading(1, "Un"),
        PageMarker("7"),
        Paragraph("Texte.", note_refs=("n1",)),
        Image("Une carte", caption=""),
        Table((("Nom",), ("Ann",)), header_rows=1),
        Footnote("n1", "Voir plus loin."),
    ]
    section = Section("Un", blocks=blocks, lang=section_lang)
    [narration] = narrate_book(book(section, lang=lang), config)
    return spoken(narration.utterances)


def test_a_book_in_french_hears_french_words_for_its_structure(config):
    assert structure(config, "fr") == [
        "Un.",
        "Page 7.",
        "Texte.",
        "Image: Une carte.",
        "Tableau.",
        "Ligne 1: Nom: Ann.",
        "Notes.",
        "Note: Voir plus loin.",
    ]


@pytest.mark.parametrize(
    ("lang", "image", "table", "note"),
    [
        ("de-AT", "Bild", "Tabelle", "Anmerkung"),
        ("es", "Imagen", "Tabla", "Nota"),
        ("fr-CA", "Image", "Tableau", "Note"),
    ],
)
def test_each_language_uses_its_own_table_most_specific_first(config, lang, image, table, note):
    said = structure(config, lang)
    assert said[3].startswith(f"{image}:") and said[4] == f"{table}."
    assert said[-1].startswith(f"{note}:")


def test_a_chapter_in_another_language_uses_that_languages_words(config):
    assert structure(config, "en", section_lang="de")[4] == "Tabelle."


def test_a_language_without_a_table_hears_english_words(config):
    assert structure(config, "it")[4] == "Table."
    assert not has_narration_words(config, "it")
    assert has_narration_words(config, "fr-CA") and has_narration_words(config, None)


def test_a_title_track_in_spanish(config):
    title_book = Book(Metadata("T", authors=["A", "B"], language="es"), [], Path("b.epub"), "epub")
    assert spoken(narrate_title(title_book, config).utterances)[1] == "Por A y B."


def test_a_settings_file_can_add_a_language_and_change_one(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        '[narration.words.it]\ntable = "Tabella"\n\n[narration.words.fr]\nnote = "Remarque"\n',
        encoding="utf-8",
    )
    config = load_config(path)
    words = config["narration"]["words"]
    assert words["it"]["table"] == "Tabella"
    assert words["it"]["image"] == "Image"  # what it leaves out is English
    assert words["fr"]["note"] == "Remarque" and words["fr"]["table"] == "Tableau"
    assert structure(config, "it")[4] == "Tabella."
    assert has_narration_words(config, "it")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[narration.words.it]\ntabel = "Tabella"\n', "Unknown setting 'narration.words.it.tabel'"),
        (
            '[narration.words.Italian]\ntable = "Tabella"\n',
            "Unknown setting 'narration.words.Italian'",
        ),
        ("[narration.words.it]\ntable = 3\n", "'narration.words.it.table' must be text"),
        ('[narration.words.fr]\ntabel = "x"\n', "Unknown setting 'narration.words.fr.tabel'"),
    ],
)
def test_mistakes_in_word_tables_are_found_as_they_load(tmp_path, text, message):
    path = tmp_path / "settings.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_page_markers_before_the_opening_heading_follow_it(config, narration):
    narration["announce_pages"] = True
    result = section_narration(config, PageMarker("5"), Heading(1, "Two"), Paragraph("Text."))
    assert spoken(result.utterances) == ["Two.", "Page five.", "Text."]
    assert [type(p.block) for p in result.passages] == [Heading, PageMarker, Paragraph]


def test_page_markers_before_other_text_stay_where_they_are(config, narration):
    narration["announce_pages"] = True
    result = section_narration(config, PageMarker("5"), Paragraph("Text."), title="Two")
    # The title Dogear adds still comes first; the page is announced where it was printed.
    assert spoken(result.utterances) == ["Two.", "Page five.", "Text."]
