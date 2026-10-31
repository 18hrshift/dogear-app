"""EPUB 3 talking books: a valid zip, documents whose ids the overlays point at, overlays
whose clips add up, navigation by heading and page, and metadata from the book."""

import re
import zipfile
from dataclasses import replace

import pytest
from audio_builder import image_bytes
from epub3_reader import EPUB_TYPE, NS, XML_LANG, Epub, milliseconds
from readalong_builder import UTTERANCE_MS, metadata, talking_book

from dogear.document import (
    Cover,
    Footnote,
    Heading,
    Image,
    ListBlock,
    PageMarker,
    Paragraph,
    SceneBreak,
    Section,
    Table,
)
from dogear.language_detection import DetectionSettings, LanguageGuesser
from dogear.output import epub3
from dogear.output.book_identity import book_uid, narrator
from dogear.output.epub3 import ACTIVE_CLASS, build_epub3

CHAPTER = Section(
    "One",
    blocks=[
        Heading(1, "One"),
        PageMarker("7"),
        Paragraph("It rained. It poured.", note_refs=("n1",)),
        Footnote("n1", "Ecrit en 1886.", label="1"),
        ListBlock(("Milk.", "Eggs."), ordered=True),
        ListBlock(("Rope.",)),
        SceneBreak(),
        Heading(3, "Deeper"),
        Table(rows=(("Name", "Age"), ("Ann", "30")), header_rows=1, caption="Ages"),
        Image(alt="A map.", caption="The town."),
        Paragraph("Il pleut.", lang="fr"),
        PageMarker("8"),
    ],
)
PLAIN = Section("Two", blocks=[Heading(1, "Two"), Paragraph("Nothing more.")])


def make(config, tmp_path, *sections, **options) -> Epub:
    """Build a talking book of `sections` and read it back."""
    built, book_metadata = talking_book(config, tmp_path, *sections, **options)
    path = tmp_path / "out" / "book.epub"
    build_epub3(built, book_metadata, path, config)
    return Epub(path)


def body_docs(book: Epub) -> list:
    return [book.doc(item.get("href")) for item in book.spine()]


def toc_tree(book: Epub, ol=None) -> list:
    """The table of contents as nested (text, [children]) pairs."""
    if ol is None:
        [ol] = book.xpath(book.nav(), "//x:nav[@epub:type='toc']/x:ol")
    tree = []
    for li in book.xpath(ol, "x:li"):
        [link] = book.xpath(li, "x:a")
        children = book.xpath(li, "x:ol")
        tree.append((link.text, toc_tree(book, children[0]) if children else []))
    return tree


def test_the_zip_opens_with_an_uncompressed_mimetype(config, tmp_path):
    book = make(config, tmp_path, CHAPTER)
    first = book.entries[0]
    assert (first.filename, first.compress_type, first.extra) == (
        "mimetype",
        zipfile.ZIP_STORED,
        b"",
    )
    assert book.files["mimetype"] == b"application/epub+zip"
    assert book.package_path == "EPUB/package.opf"
    assert not [name for name in book.files if " " in name]
    kinds = {e.filename.rsplit(".", 1)[-1]: e.compress_type for e in book.entries}
    assert kinds["mp3"] == zipfile.ZIP_STORED and kinds["xhtml"] == zipfile.ZIP_DEFLATED


def test_every_file_is_in_the_manifest_once(config, tmp_path):
    book = make(config, tmp_path, CHAPTER, PLAIN)
    items = book.xpath(book.package, "//opf:item")
    assert len({i.get("id") for i in items}) == len(items)
    hrefs = [book.root + i.get("href") for i in items]
    container = {"mimetype", "META-INF/container.xml", book.package_path}
    assert sorted(hrefs) == sorted(set(book.files) - container)


def test_each_document_has_its_own_overlay(config, tmp_path):
    book = make(config, tmp_path, CHAPTER, PLAIN)
    items = book.items()
    overlays = [doc.get("media-overlay") for doc in book.spine()]
    assert len(overlays) == 3 and len(set(overlays)) == 3
    for doc, overlay_id in zip(book.spine(), overlays, strict=True):
        assert doc.get("media-type") == "application/xhtml+xml"
        overlay = items[overlay_id]
        assert overlay.get("media-type") == "application/smil+xml"
        [seq] = book.xpath(book.doc(overlay.get("href")), "/smil:smil/smil:body/smil:seq")
        assert seq.get(f"{{{NS['epub']}}}textref") == doc.get("href")


def test_every_overlay_points_at_text_that_exists_and_audio_in_the_book(config, tmp_path):
    book = make(config, tmp_path, CHAPTER, PLAIN)
    audio = {i.get("href") for i in book.items().values() if i.get("media-type") == "audio/mpeg"}
    for doc, (_, smil) in zip(book.spine(), book.overlays(), strict=True):
        ids = book.ids(doc.get("href"))
        pars = book.xpath(smil, "//smil:par")
        assert pars
        for par in pars:
            text, sound = par
            assert (text.tag, sound.tag) == (f"{{{NS['smil']}}}text", f"{{{NS['smil']}}}audio")
            href, target = text.get("src").split("#")
            assert href == doc.get("href") and target in ids
            assert sound.get("src") in audio


def test_clips_run_back_to_back_and_are_never_empty(config, tmp_path):
    book = make(config, tmp_path, CHAPTER)
    for _, smil in book.overlays():
        clips = book.clips(smil)
        assert all(end > begin for begin, end in clips)
        assert all(a[1] == b[0] for a, b in zip(clips, clips[1:], strict=False))
        # Milliseconds are kept: every utterance lasts 1.337 seconds.
        assert all((end - begin) % UTTERANCE_MS == 0 for begin, end in clips)
        assert not any("npt=" in a for a in book.xpath(smil, "//smil:audio/@clipBegin"))


def test_durations_add_up(config, tmp_path):
    book = make(config, tmp_path, CHAPTER, PLAIN)
    per_overlay = book.refined("media:duration")
    played = {}
    for overlay_id, smil in book.overlays():
        played[overlay_id] = sum(end - begin for begin, end in book.clips(smil))
    assert {k: milliseconds(v) for k, v in per_overlay.items()} == played
    [total] = book.meta("media:duration")
    assert milliseconds(total) == sum(played.values())


def test_overlay_times_are_the_syncs_to_the_millisecond(config, tmp_path):
    sections, book_metadata = talking_book(config, tmp_path, PLAIN, title_track=False)
    path = tmp_path / "book.epub"
    build_epub3(sections, book_metadata, path, config)
    [(_, smil)] = Epub(path).overlays()
    expected = [(round(s.clip.begin * 1000), round(s.clip.end * 1000)) for s in sections[0].syncs]
    assert Epub(path).clips(smil) == expected


def test_contents_nest_every_heading_by_level(config, tmp_path):
    nested = Section(
        "One",
        blocks=[
            Heading(1, "One"),
            Heading(2, "A"),
            Paragraph("a."),
            Heading(3, "B"),
            Paragraph("b."),
            Heading(2, "C"),
            Paragraph("c."),
        ],
    )
    book = make(config, tmp_path, nested, PLAIN)
    assert toc_tree(book) == [
        ("A Book", []),
        ("One", [("A", [("B", [])]), ("C", [])]),
        ("Two", []),
    ]
    for link in book.xpath(book.nav(), "//x:nav[@epub:type='toc']//x:a"):
        href, target = link.get("href").split("#")
        [heading] = book.xpath(book.doc(href), f"//*[@id='{target}']")
        assert re.fullmatch(r"\{.*\}h[1-6]", heading.tag) and heading.text == link.text


def test_the_contents_are_labelled_for_assistive_technology(config, tmp_path):
    nav = make(config, tmp_path, CHAPTER).nav()
    [toc] = nav.xpath("//x:nav[@epub:type='toc']", namespaces=NS)
    assert toc.get("role") == "doc-toc"
    [page_list] = nav.xpath("//x:nav[@epub:type='page-list']", namespaces=NS)
    assert page_list.get("role") == "doc-pagelist"


def test_page_list_points_at_labelled_page_breaks(config, tmp_path):
    book = make(config, tmp_path, CHAPTER)
    links = book.xpath(book.nav(), "//x:nav[@epub:type='page-list']//x:a")
    assert [a.text for a in links] == ["7", "8"]
    for link in links:
        href, target = link.get("href").split("#")
        [marker] = book.xpath(book.doc(href), f"//*[@id='{target}']")
        assert marker.get(EPUB_TYPE) == "pagebreak" and marker.get("role") == "doc-pagebreak"
        assert marker.get("aria-label") == marker.text == link.text


def test_no_page_list_without_pages(config, tmp_path):
    book = make(config, tmp_path, PLAIN)
    assert not book.xpath(book.nav(), "//x:nav[@epub:type='page-list']")
    assert not book.meta("pageBreakSource")
    features = book.meta("schema:accessibilityFeature")
    assert "pageNavigation" not in features and "pageBreakMarkers" not in features


@pytest.mark.parametrize("announced", [True, False])
def test_announced_page_numbers_are_marked_in_the_overlay(config, tmp_path, announced):
    config["narration"]["announce_pages"] = announced
    book = make(config, tmp_path, CHAPTER, title_track=False)
    [doc] = book.spine()
    page_ids = set(book.xpath(book.doc(doc.get("href")), "//*[@epub:type='pagebreak']/@id"))
    [(_, smil)] = book.overlays()
    marked = book.xpath(smil, "//smil:par[@epub:type='pagebreak']/smil:text/@src")
    targets = [src.split("#")[1] for src in book.xpath(smil, "//smil:text/@src")]
    if announced:
        assert [src.split("#")[1] for src in marked] == ["s001-page1", "s001-page2"]
        assert [t for t in targets if t in page_ids] == ["s001-page1", "s001-page2"]
    else:
        assert not marked and not set(targets) & page_ids


def test_text_shows_each_kind_of_block(config, tmp_path):
    book = make(config, tmp_path, CHAPTER, title_track=False)
    doc = body_docs(book)[0]

    def find(path):
        return book.xpath(doc, path)

    assert [h.text for h in find("//x:h1 | //x:h2")] == ["One", "Deeper"]
    assert [s.text for s in find("//x:body/x:p[1]/x:span")] == ["It rained.", "It poured."]
    # The note is a visible block, not a footnote a reading app might hide.
    [note] = find("//x:div[@role='note']")
    assert [s.text for s in note.iter(f"{{{NS['x']}}}span")] == ["1", "Note: Ecrit en 1886."]
    assert not find("//x:aside") and not find("//*[@epub:type='footnote']")
    assert [len(find(f"//x:{kind}/x:li")) for kind in ("ol", "ul")] == [2, 1]
    assert len(find("//x:hr")) == 1
    assert [th.text for th in find("//x:thead//x:th[@scope='col']")] == ["Name", "Age"]
    [row] = find("//x:tbody/x:tr")
    assert row.get("id") and [td.text for td in row] == ["Ann", "30"]
    assert [s.text for s in find("//x:caption/x:span")] == ["Table: Ages."]
    [figure] = find("//x:figure")
    assert [s.text for s in find("//x:figure/x:p/x:span")] == ["Image: A map."]
    assert [s.text for s in find("//x:figcaption/x:span")] == ["The town."]
    assert not find("//x:img") and figure.get("class") == "image"
    [french] = find("//x:p[@lang='fr']")
    assert french.get(XML_LANG) == "fr"


MIXED = Section(
    "Three",
    blocks=[
        Heading(1, "Three"),
        Paragraph(
            "The old lady put her cup down on the table and looked out of the window. "
            "Then she said: La vieille dame posa sa tasse sur la table et regarda par la "
            "fenêtre. And she smiled at the rain falling on the garden for a long while."
        ),
        Paragraph("Le lendemain matin, nous partîmes de bonne heure pour la gare."),
    ],
)


def test_languages_found_by_detection_are_marked_in_the_text(config, tmp_path):
    settings = DetectionSettings.from_config(config)
    guesser = LanguageGuesser(replace(settings, sentence_check_min_letters=100), {"en", "fr"})
    built, book_metadata = talking_book(config, tmp_path, MIXED, title_track=False, guesser=guesser)
    path = tmp_path / "out" / "book.epub"
    build_epub3(built, book_metadata, path, config)
    book = Epub(path)
    doc = body_docs(book)[0]
    first, second = book.xpath(doc, "//x:body/x:p")
    # A French sentence in an English paragraph is marked on its own span...
    spans = book.xpath(first, "x:span")
    assert [s.get("lang") for s in spans] == [None, "fr", None]
    assert spans[1].get(XML_LANG) == "fr"
    # ...and a French paragraph as a whole, its sentences inheriting it.
    assert second.get("lang") == "fr" and second.get(XML_LANG) == "fr"
    assert [s.get("lang") for s in book.xpath(second, "x:span")] == [None]


def test_a_header_row_with_nothing_under_it_is_an_ordinary_row(config, tmp_path):
    tables = Section(
        "One",
        blocks=[
            Heading(1, "One"),
            Table(rows=(("North", "South"),), header_rows=1),
            Table(rows=(("", "Price"), ("Milk", "$1")), header_rows=1),
        ],
    )
    doc = body_docs(make(config, tmp_path, tables, title_track=False))[0]
    first, second = doc.xpath("//x:table", namespaces=NS)
    assert not first.xpath(".//x:th", namespaces=NS)
    assert [td.text for td in first.xpath("x:tbody/x:tr/x:td", namespaces=NS)] == ["North", "South"]
    # An empty header cell heads nothing, so it isn't a header.
    assert [c.tag.split("}")[1] for c in second.xpath("x:thead/x:tr/*", namespaces=NS)] == [
        "td",
        "th",
    ]


def test_the_title_track_is_a_title_page(config, tmp_path):
    book = make(config, tmp_path, PLAIN)
    title, chapter = body_docs(book)
    [page] = title.xpath("//x:section", namespaces=NS)
    assert page.get(EPUB_TYPE) == "titlepage"
    body = title.find(f"{{{NS['x']}}}body")
    assert body.get(EPUB_TYPE) == "frontmatter"
    assert chapter.find(f"{{{NS['x']}}}body").get(EPUB_TYPE) == "bodymatter"
    landmarks = book.xpath(book.nav(), "//x:nav[@epub:type='landmarks']//x:a")
    first, second = (item.get("href") for item in book.spine())
    assert [(a.get(EPUB_TYPE), a.get("href")) for a in landmarks] == [
        ("titlepage", first),
        ("bodymatter", second),
    ]


def test_without_the_title_track_there_is_no_title_page(config, tmp_path):
    book = make(config, tmp_path, PLAIN, title_track=False)
    assert not book.xpath(body_docs(book)[0], "//*[@epub:type='titlepage']")
    landmarks = book.xpath(book.nav(), "//x:nav[@epub:type='landmarks']//x:a/@epub:type")
    assert landmarks == ["bodymatter"]


def test_reading_starts_at_the_first_body_section(config, tmp_path):
    preface = Section("Preface", kind="front", blocks=[Heading(1, "Preface"), Paragraph("P.")])
    book = make(config, tmp_path, preface, PLAIN)
    [start] = book.xpath(book.nav(), "//x:a[@epub:type='bodymatter']/@href")
    assert start == book.spine()[2].get("href")
    assert body_docs(book)[1].find(f"{{{NS['x']}}}body").get(EPUB_TYPE) == "frontmatter"


def test_package_metadata_comes_from_the_book(config, tmp_path):
    book_metadata = metadata(
        authors=["Ann Author", "Bob Writer"],
        language="en-GB",
        publisher="Small Press",
        identifier="urn:isbn:9780000000002",
    )
    book = make(config, tmp_path, PLAIN, book_metadata=book_metadata)
    package = book.package
    assert package.get("unique-identifier") == "uid"
    [identifier] = book.xpath(package, "//dc:identifier[@id='uid']/text()")
    assert identifier == book_uid(book_metadata)
    assert book.dc("title") == ["A Book"] and book.dc("publisher") == ["Small Press"]
    assert book.dc("creator") == ["Ann Author", "Bob Writer"]
    assert set(book.refined("role").values()) == {"aut"}
    assert book.dc("language") == ["en-GB"] and package.get(XML_LANG) == "en-GB"
    assert all(doc.get("lang") == "en-GB" for doc in body_docs(book))
    [modified] = book.meta("dcterms:modified")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", modified)
    assert not book.meta("dcterms:conformsTo") and not book.meta("a11y:certifiedBy")


def test_the_narrator_is_the_configured_voice(config, tmp_path):
    config["voice"]["voice"] = "bf_emma"
    book = make(config, tmp_path, PLAIN)
    assert book.meta("media:narrator") == [narrator(config)]
    assert "bf_emma" in book.meta("media:narrator")[0]


def test_the_active_class_is_styled_and_every_document_links_the_style(config, tmp_path):
    book = make(config, tmp_path, PLAIN)
    assert book.meta("media:active-class") == [ACTIVE_CLASS]
    [css] = [i for i in book.items().values() if i.get("media-type") == "text/css"]
    rules = book.files[book.root + css.get("href")].decode()
    rule = re.search(r"\." + re.escape(ACTIVE_CLASS) + r"\s*\{([^}]*)\}", rules)
    assert rule and "background-color" in rule[1] and "underline" in rule[1]
    for doc in body_docs(book):
        assert doc.xpath("//x:link/@href", namespaces=NS) == [css.get("href")]


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, "en"), ("en_US", "en-US"), ("English", "en"), ("und", "en"), ("fr", "fr")],
)
def test_the_book_language_falls_back_to_english(config, tmp_path, given, expected):
    book = make(config, tmp_path, PLAIN, book_metadata=metadata(language=given))
    assert book.dc("language") == [expected]
    assert all(doc.get("lang") == expected for doc in body_docs(book))


def test_an_unusable_language_is_reported(config, tmp_path, caplog):
    make(config, tmp_path, PLAIN, book_metadata=metadata(language="English"))
    assert "'English', isn't a language code" in caplog.text


def test_a_section_in_another_language_says_so(config, tmp_path):
    french = Section("Deux", lang="fr", blocks=[Heading(1, "Deux"), Paragraph("Fin.")])
    book = make(config, tmp_path, french, PLAIN, title_track=False)
    assert [doc.get("lang") for doc in body_docs(book)] == ["fr", "en"]
    [link] = book.xpath(book.nav(), "//x:a[text()='Deux']")
    assert link.get("lang") == link.get(XML_LANG) == "fr"


def test_the_cover_is_the_cover_image(config, tmp_path):
    png = image_bytes("PNG")
    book = make(config, tmp_path, PLAIN, book_metadata=metadata(cover=Cover(png, "image/png")))
    [cover] = [i for i in book.items().values() if i.get("properties") == "cover-image"]
    assert cover.get("media-type") == "image/png"
    assert book.files[book.root + cover.get("href")] == png


def test_a_cover_that_isnt_an_image_is_left_out(config, tmp_path):
    odd = Cover(b"GIF89a not supported", "image/gif")
    book = make(config, tmp_path, PLAIN, book_metadata=metadata(cover=odd))
    assert not [i for i in book.items().values() if i.get("properties") == "cover-image"]


def test_a_failed_build_leaves_the_old_book_and_no_partial_files(config, tmp_path):
    sections, book_metadata = talking_book(config, tmp_path, CHAPTER, PLAIN)
    missing = replace(sections[-1].audio, path=tmp_path / "gone.mp3")
    sections[-1] = replace(sections[-1], audio=missing)
    out = tmp_path / "out"
    out.mkdir()
    path = out / "book.epub"
    path.write_bytes(b"the book made last time")
    with pytest.raises(FileNotFoundError):
        build_epub3(sections, book_metadata, path, config)
    assert path.read_bytes() == b"the book made last time"
    assert [p.name for p in out.iterdir()] == ["book.epub"]


def test_audio_must_be_encoded_first(config, tmp_path):
    sections, book_metadata = talking_book(config, tmp_path, PLAIN)
    wav = replace(sections[0].audio, path=tmp_path / "raw.wav")
    sections[0] = replace(sections[0], audio=wav)
    with pytest.raises(ValueError, match="encode it with talking_audio first"):
        build_epub3(sections, book_metadata, tmp_path / "book.epub", config)
    assert not (tmp_path / "book.epub").exists()


def test_no_sections_is_an_error(config, tmp_path):
    with pytest.raises(ValueError, match="no sections"):
        build_epub3([], metadata(), tmp_path / "book.epub", config)


def test_file_names_grow_with_the_book(config, tmp_path, monkeypatch):
    sections, book_metadata = talking_book(config, tmp_path, PLAIN, title_track=False)
    many = [replace(sections[0], number=n) for n in range(1, 1001)]
    seen = []

    def record(path, package, files):
        seen.extend(name for name, _ in files)
        path.write_bytes(b"")

    monkeypatch.setattr(epub3, "write_container", record)
    build_epub3(many, book_metadata, tmp_path / "book.epub", config)
    names = [name for name in seen if name.endswith(".xhtml") and "nav" not in name]
    assert names[0] == "EPUB/s0001.xhtml" and names[-1] == "EPUB/s1000.xhtml"


def test_built_next_to_the_output(config, tmp_path):
    sections, book_metadata = talking_book(config, tmp_path, PLAIN)
    path = tmp_path / "new" / "folder" / "book.epub"
    build_epub3(sections, book_metadata, path, config)
    assert zipfile.is_zipfile(path)
    assert [p.name for p in path.parent.iterdir()] == ["book.epub"]


def test_xml_cant_hold_control_characters_so_they_are_dropped(config, tmp_path):
    damaged = Section("Bell\x07", blocks=[Heading(1, "Bell\x07"), Paragraph("Ding\x07 dong.")])
    book = make(config, tmp_path, damaged, title_track=False)
    doc = body_docs(book)[0]
    assert doc.xpath("//x:h1/text()", namespaces=NS) == ["Bell"]
    assert doc.xpath("//x:p/x:span/text()", namespaces=NS) == ["Ding dong."]
