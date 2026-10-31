import pytest

from dogear.document import Heading, PageMarker, Paragraph
from dogear.extract.stretches import Stretch, carry_page_markers, section_level, sections_from


@pytest.mark.parametrize(
    ("levels", "expected"),
    [
        ([], None),
        ([1], 1),
        ([2, 3, 3], 3),  # the highest level used more than once
        ([1, 2, 2, 2], 2),  # a title over chapters
        ([1, 2, 1, 2], 1),  # parts over chapters: parts
        ([3, 4], 3),  # nothing repeats: the highest
    ],
)
def test_section_level(levels, expected):
    assert section_level(levels) == expected


def test_sections_are_titled_by_their_first_heading_or_their_number():
    stretches = [
        Stretch([], "front", "a"),
        Stretch([Paragraph("Opening.")], "front", "b"),
        Stretch([Heading(1, "  Chapter\n One ")], "body", "c"),
    ]
    sections = sections_from(stretches, lang="en", lone_title="Book")
    assert [(s.title, s.kind, s.source, s.lang) for s in sections] == [
        ("Section 1", "front", "b", "en"),
        ("Chapter One", "body", "c", "en"),
    ]


def test_a_lone_section_without_a_heading_takes_the_lone_title():
    [section] = sections_from(
        [Stretch([Paragraph("Text.")], "body", "a")], lang=None, lone_title="Book"
    )
    assert section.title == "Book"


def test_page_markers_ending_a_stretch_move_to_the_next():
    first = [Paragraph("A."), PageMarker("2"), PageMarker("3")]
    second = [Heading(1, "Two")]
    last = [PageMarker("9")]
    carry_page_markers([first, second, last])
    assert first == [Paragraph("A.")]
    assert second == [PageMarker("2"), PageMarker("3"), Heading(1, "Two")]
    assert last == [PageMarker("9")]  # nothing after it for the page to begin with
