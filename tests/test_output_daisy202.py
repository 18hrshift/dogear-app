"""DAISY 2.02 output: the files, the links between them, the times, and the output folder.

Most tests build the talking book from the narration with made-up timings and stand-in
MP3 files, so they need no voice and no ffmpeg. The last ones run the real pipeline with
a humming voice. DAISY Pipeline 2's own validator runs outside pytest
(`scripts/check-daisy.sh`); `check_book` here covers what it checks, and more.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from conftest import require_tool
from daisy_samples import VARIANTS, everything_book, make_daisy, sample_config
from lxml import etree

from dogear import __version__
from dogear.document import Book, Heading, Metadata, PageMarker, Paragraph, Section, Table
from dogear.errors import OutputFolderError
from dogear.language_detection import DetectionSettings, LanguageGuesser
from dogear.narration import narrate_book, narrate_title
from dogear.output import daisy202
from dogear.output.book_identity import book_uid
from dogear.output.chapter import ChapterAudio
from dogear.output.daisy202 import build_daisy202
from dogear.output.daisy202_clock import clip_bounds, clock, npt, timecount, whole_seconds
from dogear.output.daisy202_xhtml import language_tag
from dogear.output.xml_text import xml_safe
from dogear.readalong import build_readalong
from dogear.readalong.model import Clip, HeadingText, SectionText, Sync

RATE = 24000
TODAY = date(2026, 10, 27)
X = "{http://www.w3.org/1999/xhtml}"
HEADINGS = {f"{X}h{n}" for n in range(1, 7)}
DTDS = Path(__file__).parent / "dtd"
XHTML_DTD = etree.DTD(str(DTDS / "xhtml1-transitional.dtd"))
SMIL_DTD = etree.DTD(str(DTDS / "SMIL10.dtd"))
PARSER = etree.XMLParser(no_network=True, resolve_entities=False, load_dtd=False)


# --- Building a book without a voice -------------------------------------------------


def sections_for(book: Book, config, audio_dir: Path, guesser=None) -> list[SectionText]:
    """The read-along sections, with uneven made-up timings and a stand-in MP3 each."""
    narrations = narrate_book(book, config, guesser)
    if config["narration"]["announce_title"]:
        narrations.insert(0, narrate_title(book, config))
    chapters = []
    for narration in narrations:
        mp3 = audio_dir / f"section-{narration.number}.mp3"
        mp3.write_bytes(f"stand-in audio {narration.number}".encode())
        # Lengths that aren't whole milliseconds, so rounding is exercised.
        lengths = [9000 + (i * 7919) % 13001 for i in range(len(narration.utterances))]
        marks = [0]
        for length in lengths:
            marks.append(marks[-1] + length)
        chapters.append(ChapterAudio(narration.section.title, mp3, tuple(marks), RATE))
    return build_readalong(narrations, chapters, book.metadata.language)


@pytest.fixture
def write_book(config, tmp_path):
    """Write `book` (the everything book by default) as DAISY; returns the folder."""
    audio = tmp_path / "audio"
    audio.mkdir()

    def write(book: Book | None = None, folder: Path | None = None, cfg=None, guesser=None) -> Path:
        cfg = cfg or config
        book = book or everything_book()
        folder = folder or tmp_path / "book"
        sections = sections_for(book, cfg, audio, guesser)
        build_daisy202(sections, book.metadata, folder, cfg, today=TODAY)
        return folder

    return write


def small_book(*sections: Section, **metadata) -> Book:
    fields = {"title": "Small", "authors": ["Ann Author"], "language": "en", **metadata}
    return Book(Metadata(**fields), list(sections), Path("small.epub"), "epub")


def chapter(title: str, *blocks) -> Section:
    return Section(title, blocks=[Heading(1, title), *blocks])


# --- Reading a book back -------------------------------------------------------------


def parse(path: Path) -> etree._Element:
    return etree.parse(str(path), PARSER).getroot()


def book_files(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if not p.name.startswith("."))


def ncc_of(folder: Path) -> etree._Element:
    return parse(folder / "ncc.html")


def metas(ncc: etree._Element) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for meta in ncc.iter(f"{X}meta"):
        if meta.get("name"):
            found.setdefault(meta.get("name"), []).append(meta.get("content"))
    return found


def entries(ncc: etree._Element) -> list[etree._Element]:
    return list(ncc.find(f"{X}body"))


def ms(value: str) -> int:
    """'npt=1.234s', '12.345s', or '00:01:02.345' in milliseconds."""
    value = value.removeprefix("npt=")
    if value.endswith("s"):
        return round(float(value[:-1]) * 1000)
    hours, minutes, seconds = value.split(":")
    return round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)


def pars(smil: etree._Element) -> list[etree._Element]:
    return list(smil.find("body/seq"))


def check_book(folder: Path) -> None:
    """Everything DAISY Pipeline 2's validator checks that a test can, and more."""
    files = book_files(folder)
    ncc = ncc_of(folder)
    meta = metas(ncc)
    body = entries(ncc)
    smils = {name: parse(folder / name) for name in files if name.endswith(".smil")}
    texts = {n: parse(folder / n) for n in files if n.endswith(".html") and n != "ncc.html"}
    assert all(re.fullmatch(r"[a-z0-9_-]+\.(html|smil|mp3)", name) for name in files)

    # The NCC opens on the title and links every entry to a SMIL par.
    assert body[0].tag == f"{X}h1" and body[0].get("class") == "title"
    for entry in body:
        [link] = entry
        smil_name, _, par_id = link.get("href").partition("#")
        [target] = smils[smil_name].xpath("//*[@id=$id]", id=par_id)
        assert target.tag == "par"

    # Its counts are the ones the validator recomputes.
    spans = [e for e in body if e.tag == f"{X}span"]
    for name, cls in [("pageFront", "front"), ("pageNormal", "normal"), ("pageSpecial", "special")]:
        assert meta[f"ncc:{name}"] == [str(sum(s.get("class") == f"page-{cls}" for s in spans))]
    assert meta["ncc:tocItems"] == [str(len(body))]
    assert meta["ncc:files"] == [str(len(files))]
    levels = [int(e.tag[-1]) for e in body if e.tag in HEADINGS]
    assert meta["ncc:depth"] == [str(max(levels))]
    if spans and spans[-1].get("class") == "page-normal":
        assert meta["ncc:maxPageNormal"] == [str(int(spans[-1].findtext(f"{X}a")))]
    else:
        assert "ncc:maxPageNormal" not in meta
    for name in ["dc:title", "dc:format", "dc:identifier", "dc:publisher", "dc:date"]:
        assert len(meta[name]) == 1, name
    assert meta["dc:creator"] and meta["dc:language"]

    # Headings never skip a level, in the NCC or in any text document.
    for root in [ncc, *texts.values()]:
        previous = None
        for heading in (el for el in root.iter() if el.tag in HEADINGS):
            level = int(heading.tag[-1])
            assert previous is None or level <= previous + 1
            previous = level

    # Every par shows one element of the text and plays one clip; times add up exactly.
    elapsed = 0
    for name in sorted(smils):
        smil = smils[name]
        smil_meta = {m.get("name"): m.get("content") for m in smil.iter("meta")}
        duration, last_end = 0, None
        for k, par in enumerate(pars(smil)):
            assert par.get("endsync") == "last"
            [text_el] = par.findall("text")
            [audio] = par.findall("audio")
            assert text_el.get("id") and audio.get("id")
            text_file, _, target_id = text_el.get("src").partition("#")
            [target] = texts[text_file].xpath("//*[@id=$id]", id=target_id)
            if k == 0:
                assert target.tag in HEADINGS
            assert audio.get("src") in files
            begin, end = ms(audio.get("clip-begin")), ms(audio.get("clip-end"))
            assert re.fullmatch(r"npt=\d+\.\d{3}s", audio.get("clip-begin"))
            assert begin < end and (last_end is None or begin == last_end)
            duration += end - begin
            last_end = end
        assert ms(smil.find("body/seq").get("dur")) == duration
        assert ms(smil_meta["ncc:timeInThisSmil"]) == duration
        assert ms(smil_meta["ncc:totalElapsedTime"]) == elapsed
        assert smil_meta["dc:format"] == "Daisy 2.02"
        elapsed += duration
    assert abs(ms(meta["ncc:totalTime"][0] + ".000") - elapsed) <= 500

    # And every file is valid against the DTDs the spec names.
    for name in files:
        if name.endswith((".html", ".smil")):
            dtd = SMIL_DTD if name.endswith(".smil") else XHTML_DTD
            assert dtd.validate(etree.parse(str(folder / name), PARSER)), (
                name,
                dtd.error_log.filter_from_errors(),
            )


# --- The book as a whole -------------------------------------------------------------


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_the_everything_book_is_a_valid_daisy_book(write_book, variant, tmp_path):
    folder = write_book(cfg=sample_config(variant, tmp_path / "cache"))
    check_book(folder)


def test_one_smil_text_and_mp3_per_section_and_one_ncc(write_book, tmp_path):
    folder = write_book()
    names = book_files(folder)
    # The title track and the four sections.
    per_section = [f"s00{n}.{ext}" for n in range(1, 6) for ext in ("html", "mp3", "smil")]
    assert names == ["ncc.html", *per_section]
    assert (folder / "s002.mp3").read_bytes() == b"stand-in audio 1"
    assert sorted(p.name for p in folder.iterdir() if p.name.startswith(".")) == [".dogear.json"]


def test_files_are_named_to_sort_in_reading_order(write_book):
    many = small_book(*(chapter(f"Part {n}", Paragraph("Words.")) for n in range(1, 1001)))
    names = book_files(write_book(many))
    assert names[:2] == ["ncc.html", "s0001.html"] and "s1001.smil" in names


# --- The NCC -------------------------------------------------------------------------


def test_the_title_track_is_the_title_entry(write_book):
    first = entries(ncc_of(write_book()))[0]
    assert (first.tag, first.get("class")) == (f"{X}h1", "title")
    assert first.findtext(f"{X}a") == "Every Kind of Block"
    assert first.find(f"{X}a").get("href") == "s001.smil#s001-par1"


def test_without_a_title_track_the_first_heading_is_the_title(write_book, config):
    config["narration"]["announce_title"] = False
    folder = write_book()
    first = entries(ncc_of(folder))[0]
    assert (first.tag, first.get("class"), first.findtext(f"{X}a")) == (
        f"{X}h1",
        "title",
        "Preface",
    )
    assert first.find(f"{X}a").get("href") == "s001.smil#s001-par1"
    check_book(folder)


def test_every_heading_and_page_is_listed_in_reading_order(write_book):
    body = entries(ncc_of(write_book()))
    listed = [(e.tag.removeprefix(X), e.get("class"), e.findtext(f"{X}a")) for e in body]
    assert listed == [
        ("h1", "title", "Every Kind of Block"),
        ("h1", None, "Preface"),
        ("span", "page-front", "iii"),
        ("span", "page-front", "iv"),
        ("h1", None, "Chapter One"),
        ("span", "page-normal", "1"),
        ("h2", None, "A Deeper Part"),  # level 4 in the book, after a level 2
        ("span", "page-normal", "2"),
        ("h2", None, "Level Three"),
        ("span", "page-special", "2a"),
        ("h1", None, "Chapter Two"),
        ("span", "page-normal", "3"),
        ("h1", None, "Appendix"),
        ("span", "page-normal", "4"),
    ]


def test_metadata(write_book, config):
    meta = metas(ncc_of(write_book()))
    book = everything_book()
    assert meta["dc:identifier"] == [book_uid(book.metadata)]
    assert meta["dc:title"] == ["Every Kind of Block"]
    assert meta["dc:creator"] == ["Ann Author", "Bob Writer"]
    assert meta["dc:date"] == ["2026-10-27"]
    assert meta["dc:language"] == ["en"]
    assert meta["dc:publisher"] == ["Sample Press"]
    assert meta["dc:source"] == ["urn:isbn:9780000000002"]
    assert meta["dc:format"] == ["Daisy 2.02"]
    assert meta["ncc:charset"] == ["utf-8"]
    assert meta["ncc:multimediaType"] == ["audioFullText"]
    assert meta["ncc:narrator"] == ["af_heart, a synthetic Kokoro voice"]
    assert meta["ncc:generator"] == [f"Dogear {__version__}"]
    assert re.fullmatch(r"\d\d:\d\d:\d\d", meta["ncc:totalTime"][0])


def test_the_narrator_is_the_configured_voice(write_book, config):
    config["voice"]["voice"] = "bf_emma"
    assert metas(ncc_of(write_book()))["ncc:narrator"] == ["bf_emma, a synthetic Kokoro voice"]


def test_unknown_creator_publisher_and_language_get_honest_values(write_book):
    book = small_book(chapter("One", Paragraph("Words.")), authors=[], language=None)
    meta = metas(ncc_of(write_book(book)))
    assert meta["dc:creator"] == ["Unknown"]
    assert meta["dc:publisher"] == ["Unknown"]
    # The voice speaks English, so a book with no language tag is heard in English.
    assert meta["dc:language"] == ["en"]
    assert "dc:source" not in meta


def test_language_tags_are_made_valid_or_left_out(write_book, caplog):
    book = small_book(chapter("One", Paragraph("Words."), Paragraph("Mots.", lang="français")))
    book.metadata.language = "en_US"
    folder = write_book(book)
    assert metas(ncc_of(folder))["dc:language"] == ["en-US"]
    paragraphs = parse(folder / "s002.html").findall(f".//{X}p")
    assert [p.get("lang") for p in paragraphs] == [None, None]
    assert "Ignoring the language 'français'" in caplog.text
    check_book(folder)


def test_languages_found_by_detection_are_marked_in_the_text(write_book, config):
    settings = DetectionSettings.from_config(config)
    guesser = LanguageGuesser(replace(settings, sentence_check_min_letters=100), {"en", "fr"})
    mixed = (
        "The old lady put her cup down on the table and looked out of the window. Then she "
        "said: La vieille dame posa sa tasse sur la table et regarda par la fenêtre. And she "
        "smiled at the rain falling on the garden for a long while."
    )
    french = "Le lendemain matin, nous partîmes de bonne heure pour la gare."
    book = small_book(chapter("One", Paragraph(mixed), Paragraph(french)))
    folder = write_book(book, guesser=guesser)
    first, second = parse(folder / "s002.html").findall(f".//{X}p")
    assert [s.get("lang") for s in first.findall(f"{X}span")] == [None, "fr", None]
    assert second.get("lang") == "fr"
    check_book(folder)


@pytest.mark.parametrize(
    ("tag", "expected"), [("en", "en"), ("en-GB", "en-GB"), ("en_GB", "en-GB"), (" fr ", "fr")]
)
def test_language_tag(tag, expected):
    assert language_tag(tag, "x") == expected


@pytest.mark.parametrize("tag", [None, "", "  ", "en GB", "123", "englishlanguage", "en--GB"])
def test_language_tag_rejects_non_tags(tag):
    assert language_tag(tag, "x") is None


def test_a_page_at_a_section_end_starts_with_the_next_section(write_book):
    book = small_book(
        chapter("One", Paragraph("First."), PageMarker("5")),
        chapter("Two", Paragraph("Second."), PageMarker("6")),
    )
    folder = write_book(book)
    pages = {e.findtext(f"{X}a"): e.find(f"{X}a").get("href") for e in entries(ncc_of(folder))}
    two = pars(parse(folder / "s003.smil"))
    assert pages["5"] == f"s003.smil#{two[0].get('id')}"
    # At the end of the book, the last words are as near as the page gets.
    assert pages["6"] == f"s003.smil#{two[-1].get('id')}"
    check_book(folder)


@pytest.mark.parametrize(("last", "declared"), [("12", ["12"]), ("12a", None), ("xii", None)])
def test_max_page_normal_only_when_the_last_page_is_normal(write_book, last, declared):
    book = small_book(chapter("One", PageMarker("11"), Paragraph("A."), PageMarker(last)))
    folder = write_book(book)
    assert metas(ncc_of(folder)).get("ncc:maxPageNormal") == declared
    check_book(folder)


def test_a_book_without_pages_declares_none(write_book):
    folder = write_book(small_book(chapter("One", Paragraph("Words."))))
    meta = metas(ncc_of(folder))
    assert [meta[f"ncc:page{k}"] for k in ("Front", "Normal", "Special")] == [["0"]] * 3
    check_book(folder)


# --- Text documents ------------------------------------------------------------------


def test_text_shows_every_kind_of_block(write_book):
    doc = parse(write_book() / "s003.html")
    body = doc.find(f"{X}body")
    assert [el.tag.removeprefix(X) for el in body][:6] == ["h1", "span", "p", "div", "h2", "p"]
    assert doc.findtext(f"{X}head/{X}title") == "Chapter One"
    [ordered] = doc.iter(f"{X}ol")
    [unordered] = doc.iter(f"{X}ul")
    assert ["".join(li.itertext()).strip() for li in ordered] == ["Bread.", "Candles."]
    assert len(unordered) == 2
    [table] = doc.iter(f"{X}table")
    assert [th.text for th in table.iter(f"{X}th")] == ["Name", "Age"]
    assert {th.get("scope") for th in table.iter(f"{X}th")} == {"col"}
    rows = table.findall(f"{X}tbody/{X}tr")
    assert [[td.text for td in row] for row in rows] == [["Ann", "30"], ["Bob", "41"]]
    assert all(row.get("id") for row in rows)
    images = [el for el in body if el.get("class") == "image"]
    assert ["".join(i.itertext()).split() for i in images] == [
        ["Image:", "A", "flooded", "street.", "Main", "Street,", "1903."],
        # No description, but a caption: "Image." says what the caption belongs to.
        ["Image.", "A", "caption", "with", "no", "description."],
    ]
    notes = [el for el in body if el.get("class") == "note"]
    assert [n.find(f".//{X}span").text for n in notes] == ["1", "*"]
    [french] = (p for p in doc.iter(f"{X}p") if p.get("lang"))
    assert (french.get("lang"), french.get("{http://www.w3.org/XML/1998/namespace}lang")) == (
        "fr",
        "fr",
    )
    assert body.find(f"{X}hr").get("class") == "scene-break"
    pages = [el for el in body if el.tag == f"{X}span"]
    assert [(p.get("class"), p.text) for p in pages] == [
        ("page-normal", "1"),
        ("page-normal", "2"),
        ("page-special", "2a"),
    ]


def test_a_skipped_table_shows_its_rows_without_ids(write_book, config):
    config["narration"]["tables"] = "skip"
    [table] = parse(write_book() / "s003.html").iter(f"{X}table")
    assert "".join(table.find(f"{X}caption").itertext()) == "Table: Ages. Table not read."
    assert [row.get("id") for row in table.findall(f"{X}tbody/{X}tr")] == [None, None]


def test_text_has_no_links_back_and_nothing_html5(write_book):
    folder = write_book()
    for name in book_files(folder):
        if name.endswith(".html") and name != "ncc.html":
            doc = parse(folder / name)
            assert not list(doc.iter(f"{X}a"))
            assert not any(el.get("role") or el.get("epub:type") for el in doc.iter())


def test_empty_elements_get_end_tags(write_book):
    book = small_book(chapter("One", Paragraph("Words."), Table(rows=(("A", ""),))))
    raw = (write_book(book) / "s002.html").read_text(encoding="utf-8")
    assert "<td></td>" in raw and not re.search(r"<(?!meta|hr)\w+[^>]*/>", raw)


def test_characters_xml_cannot_hold_are_left_out(write_book):
    book = small_book(chapter("One", Paragraph("A bell\x07 rang.")))
    folder = write_book(book)
    assert "A bell rang." in (folder / "s002.html").read_text(encoding="utf-8")
    assert xml_safe("a\x0cb\ud800c\ufffe") == "abc"


def test_the_dtd_check_catches_html5(write_book):
    path = write_book() / "s003.html"
    doc = etree.parse(str(path), PARSER)
    assert XHTML_DTD.validate(doc)
    doc.getroot().find(f"{X}body/{X}div").set("role", "note")
    assert not XHTML_DTD.validate(doc)


# --- SMIL files ----------------------------------------------------------------------


def test_announced_pages_can_be_skipped(write_book, config):
    config["narration"]["announce_pages"] = True
    folder = write_book()
    smil = parse(folder / "s003.smil")
    skippable = [p for p in pars(smil) if p.get("system-required") == "pagenumber-on"]
    texts = [p.find("text").get("src") for p in skippable]
    assert texts == ["s003.html#s003-page1", "s003.html#s003-page2", "s003.html#s003-page3"]
    check_book(folder)


def test_silent_pages_have_no_pars(write_book):
    folder = write_book()
    for name in book_files(folder):
        if name.endswith(".smil"):
            assert not parse(folder / name).xpath("//par[@system-required]")


def test_a_silent_opening_heading_still_starts_its_smil(config, tmp_path):
    book = small_book(chapter("One", Paragraph("Words. More words.")))
    [narration] = narrate_book(book, config)
    heading = next(i for i, u in enumerate(narration.utterances) if u.text == "One")
    # One second per utterance, except none at all for the heading.
    marks = [i if i <= heading else i - 1 for i in range(len(narration.utterances) + 1)]
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"audio")
    sections = build_readalong([narration], [ChapterAudio("One", mp3, tuple(marks), 1)], "en")
    assert sections[0].syncs[0].target != sections[0].items[0].id
    build_daisy202(sections, book.metadata, tmp_path / "book", config, today=TODAY)
    first = pars(parse(tmp_path / "book" / "s001.smil"))[0]
    assert first.find("text").get("src") == f"s001.html#{sections[0].items[0].id}"
    check_book(tmp_path / "book")


def test_a_section_must_open_with_its_heading(config, tmp_path):
    config["narration"]["announce_title"] = False
    [section] = sections_for(small_book(chapter("One", Paragraph("Words."))), config, tmp_path)
    reordered = replace(section, items=section.items[::-1])
    with pytest.raises(ValueError, match="doesn't open with a heading"):
        build_daisy202([reordered], Metadata("Small"), tmp_path / "book", config)
    # Found before anything was written.
    assert not (tmp_path / "book").exists()


def test_the_first_heading_must_be_level_1(config, tmp_path):
    config["narration"]["announce_title"] = False
    [section] = sections_for(small_book(chapter("One", Paragraph("Words."))), config, tmp_path)
    heading = replace(section.items[0], level=2)
    lowered = replace(section, items=(heading, *section.items[1:]))
    with pytest.raises(ValueError, match="must be level 1"):
        build_daisy202([lowered], Metadata("Small"), tmp_path / "book", config)


# --- Times ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "npt=0.000s"), (72200, "npt=72.200s"), (3_723_456, "npt=3723.456s")],
)
def test_npt(value, expected):
    assert npt(value) == expected


def test_timecount_and_clock():
    assert timecount(158_485) == "158.485s"
    assert clock(0) == "00:00:00.000"
    assert clock(3_723_456) == "01:02:03.456"
    assert clock(100 * 3600 * 1000 + 1) == "100:00:00.001"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "00:00:00"), (1499, "00:00:01"), (1500, "00:00:02"), (5_445_500, "01:30:46")],
)
def test_whole_seconds_round_half_up(value, expected):
    assert whole_seconds(value) == expected


def test_negative_times_are_refused():
    with pytest.raises(ValueError, match="negative"):
        clock(-1)


def fake_section(clips: list[tuple[float, float]]) -> SectionText:
    syncs = tuple(Sync(f"p{k}", "h", Clip(b, e)) for k, (b, e) in enumerate(clips))
    heading = HeadingText("h", 1, "One", 0)
    return SectionText(1, "One", "body", "en", (heading,), syncs, ChapterAudio("One", Path("a")))


def test_clip_bounds_round_each_boundary_once():
    section = fake_section([(0.5, 1.2345), (1.2345, 2.0006)])
    assert clip_bounds(section) == (500, 1234, 2001)


def test_a_clip_under_a_millisecond_is_stretched_to_one():
    section = fake_section([(1.0, 1.0002), (1.0002, 1.0004), (1.0004, 2.0)])
    assert clip_bounds(section) == (1000, 1001, 1002, 2000)


def test_clips_with_a_gap_are_refused():
    with pytest.raises(ValueError, match="back to back"):
        clip_bounds(fake_section([(0.0, 1.0), (1.5, 2.0)]))


def test_declared_times_stay_within_a_millisecond_of_the_audio(write_book, config, tmp_path):
    folder = write_book()
    smils = sorted(n for n in book_files(folder) if n.endswith(".smil"))
    exact = sections_for(everything_book(), config, tmp_path / "audio")
    for name, section in zip(smils, exact, strict=True):
        declared = ms(parse(folder / name).find("body/seq").get("dur"))
        assert abs(declared - section.duration * 1000) <= 1


# --- The output folder ---------------------------------------------------------------


def test_a_rerun_replaces_its_own_files_and_leaves_others(write_book, tmp_path):
    folder = write_book()
    (folder / "my notes.txt").write_text("mine")
    shorter = small_book(chapter("One", Paragraph("Words.")))
    shorter.metadata = everything_book().metadata  # the same book, cut down
    write_book(shorter, folder)
    assert book_files(folder) == ["my notes.txt", "ncc.html"] + [
        f"s00{n}.{ext}" for n in (1, 2) for ext in "html mp3 smil".split()
    ]
    assert (folder / "my notes.txt").read_text() == "mine"


def test_a_file_dogear_did_not_make_is_never_replaced(write_book, tmp_path):
    folder = tmp_path / "book"
    folder.mkdir()
    (folder / "s001.mp3").write_bytes(b"someone else's")
    with pytest.raises(OutputFolderError, match='already has a file named "s001.mp3"'):
        write_book(folder=folder)
    assert book_files(folder) == ["s001.mp3"]
    assert (folder / "s001.mp3").read_bytes() == b"someone else's"


def test_another_books_folder_is_refused(write_book, tmp_path):
    folder = write_book()
    with pytest.raises(OutputFolderError, match="another book"):
        write_book(small_book(chapter("One", Paragraph("Words."))), folder)


def test_a_file_in_place_of_the_folder_is_refused(write_book, tmp_path):
    (tmp_path / "book").write_text("not a folder")
    with pytest.raises(OutputFolderError, match="is a file, not a folder") as err:
        write_book()
    assert "DAISY" in err.value.hint


def test_a_failed_first_run_leaves_no_book(write_book, tmp_path, monkeypatch):
    def broken(smil, path):
        raise OSError("disk full")

    monkeypatch.setattr(daisy202, "write_smil", broken)
    with pytest.raises(OSError, match="disk full"):
        write_book()
    folder = tmp_path / "book"
    assert book_files(folder) == []
    assert [p.name for p in folder.iterdir()] == [".dogear.json"]


def test_a_failed_rerun_leaves_the_earlier_book_whole(write_book, tmp_path, monkeypatch):
    folder = write_book()
    before = {name: (folder / name).read_bytes() for name in book_files(folder)}
    calls = []

    def broken(smil, path):
        calls.append(path)
        if len(calls) == 3:
            raise OSError("disk full")
        daisy202_smil_write(smil, path)

    daisy202_smil_write = daisy202.write_smil
    monkeypatch.setattr(daisy202, "write_smil", broken)
    with pytest.raises(OSError, match="disk full"):
        write_book()
    assert {name: (folder / name).read_bytes() for name in book_files(folder)} == before
    assert sorted(p.name for p in folder.iterdir() if p.name.startswith(".")) == [".dogear.json"]


def test_a_book_interrupted_while_swapping_files_has_no_ncc(write_book, tmp_path, monkeypatch):
    folder = write_book()
    real_replace = os.replace

    def replace(src, dst):
        if Path(dst).name == "ncc.html":
            raise OSError("interrupted")
        real_replace(src, dst)

    monkeypatch.setattr(daisy202.os, "replace", replace)
    with pytest.raises(OSError, match="interrupted"):
        write_book()
    assert not (folder / "ncc.html").exists()


# --- Through `dogear make`, with a humming voice -------------------------------------


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_dogear_make_writes_a_valid_daisy_book(variant, tmp_path):
    require_tool("ffmpeg")
    config = sample_config(variant, tmp_path / "cache")
    folder = make_daisy(everything_book(), config, tmp_path / "out")
    assert folder.name == "Every Kind of Block DAISY"
    check_book(folder)
    titles = [e.findtext(f"{X}a") for e in entries(ncc_of(folder))]
    assert ("Every Kind of Block" in titles) == config["narration"]["announce_title"]
