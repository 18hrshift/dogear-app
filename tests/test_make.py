"""`dogear make` from start to finish, with a fake voice and a small built book."""

from pathlib import Path

import pytest
from audio_builder import probe
from conftest import require_tool
from epub3_reader import Epub
from fake_voice import HumEngine
from pdf_builder import CHAPTER_TITLES, novel, scanned

from dogear import cli, disk_space, make
from dogear.disk_space import Disk
from dogear.document import Book, Heading, Metadata, Paragraph, Section
from dogear.errors import (
    DiskSpaceError,
    DogearError,
    OutputFolderError,
    ToolNotFoundError,
    VoiceError,
)
from dogear.make import (
    FORMATS,
    MakeRequest,
    check_language,
    make_audiobook,
    parse_formats,
    parse_sections,
)
from dogear.voice.language_voices import LanguageVoices


def small_book(language: str | None = "en") -> Book:
    return Book(
        metadata=Metadata(title="A Short Book: Part 1", authors=["Ann Author"], language=language),
        sections=[
            Section("One", blocks=[Heading(1, "One"), Paragraph("The first chapter. It ends.")]),
            Section("Two", blocks=[Heading(1, "Two"), Paragraph("The second chapter.")]),
            Section("Three", blocks=[Heading(1, "Three"), Paragraph("The last one.")]),
        ],
        source_path=Path("short.epub"),
        source_format="epub",
    )


@pytest.fixture
def setup(monkeypatch, config, tmp_path):
    """make.py with the small book and the fake voice; returns what the run used."""
    config["render"]["cache_dir"] = str(tmp_path / "cache")
    used = {"book": small_book(), "engine": HumEngine(), "lines": []}
    monkeypatch.setattr(make, "extract_book", lambda path, cfg, report: used["book"])
    monkeypatch.setattr(make, "make_voice", lambda cfg, chosen, report: used["engine"])
    return used


def run(config, tmp_path, used, **request):
    request = MakeRequest(book=tmp_path / "short.epub", output_dir=tmp_path / "out", **request)
    return make_audiobook(request, config, used["lines"].append)


def chapter_titles(m4b: Path) -> list[str]:
    import subprocess

    result = subprocess.run(
        [require_tool("ffprobe"), "-v", "error", "-show_entries", "chapter_tags=title"]
        + ["-of", "csv=p=0", str(m4b)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def test_makes_an_m4b_named_after_the_title(setup, config, tmp_path):
    require_tool("ffmpeg")
    saved = run(config, tmp_path, setup)
    assert saved == [tmp_path / "out" / "A Short Book Part 1.m4b"]
    # The title track comes first.
    assert chapter_titles(saved[0]) == ["A Short Book: Part 1", "One", "Two", "Three"]
    assert any(
        line.startswith("Making an audiobook of A Short Book: Part 1: 3 sections and a title")
        for line in setup["lines"]
    )
    assert "Section 4 of 4 done: Three." in setup["lines"]


def test_a_scanned_pdf_is_read_with_ocr_then_made(monkeypatch, config, tmp_path):
    """The whole way with the real Tesseract and the fake voice: OCR, then the M4B."""
    require_tool("tesseract")
    require_tool("ffmpeg")
    config["render"]["cache_dir"] = str(tmp_path / "cache")
    config["ocr"]["dpi"] = 150
    engine = HumEngine()
    monkeypatch.setattr(make, "make_voice", lambda cfg, chosen, report: engine)
    text = novel(
        tmp_path / "text.pdf", headings=CHAPTER_TITLES[:2], paragraphs=4, running_header=False
    )
    scan = scanned(text, tmp_path / "scan.pdf", dpi=200)
    lines = []
    request = MakeRequest(book=scan, output_dir=tmp_path / "out")
    [m4b] = make_audiobook(request, config, lines.append)
    assert chapter_titles(m4b) == ["scan", "Down the Rabbit-Hole", "The Pool of Tears"]
    assert lines[0].startswith("This PDF is scanned, so Dogear is reading its 2 pages with OCR")
    making = next(n for n, line in enumerate(lines) if line.startswith("Making an audiobook"))
    assert lines[making - 1] == "Finished reading 2 pages with OCR."
    assert any(text.startswith("Alice was beginning") for text in engine.rendered)


def test_the_title_track_can_be_turned_off(setup, config, tmp_path):
    require_tool("ffmpeg")
    config["narration"]["announce_title"] = False
    [m4b] = run(config, tmp_path, setup)
    assert chapter_titles(m4b) == ["One", "Two", "Three"]
    assert not any("By Ann Author" in text for text in setup["engine"].rendered)


def test_a_title_page_that_says_the_title_replaces_the_title_track(setup, config, tmp_path):
    require_tool("ffmpeg")
    title_page = Section(
        "Titlepage",
        kind="front",
        blocks=[Heading(1, "A short book: part 1"), Paragraph("By Ann Author.")],
    )
    setup["book"].sections.insert(0, title_page)
    [m4b] = run(config, tmp_path, setup)
    assert chapter_titles(m4b) == ["Titlepage", "One", "Two", "Three"]
    # Choosing sections without it brings the title track back.
    [m4b] = run(config, tmp_path, setup, sections={2})
    assert chapter_titles(m4b) == ["A Short Book: Part 1", "One"]


def test_both_formats_and_chosen_sections(setup, config, tmp_path):
    require_tool("ffmpeg")
    config["output"]["formats"] = ["m4b", "mp3"]
    m4b, folder = run(config, tmp_path, setup, sections={1, 3})
    assert chapter_titles(m4b) == ["A Short Book: Part 1", "One", "Three"]
    assert folder.name == "A Short Book Part 1 MP3"
    assert sorted(p.name for p in folder.glob("*.mp3")) == [
        "01 - A Short Book Part 1.mp3",
        "02 - One.mp3",
        "03 - Three.mp3",
    ]
    assert not any("second" in text for text in setup["engine"].rendered)


def test_second_run_speaks_nothing_new(setup, config, tmp_path):
    require_tool("ffmpeg")
    run(config, tmp_path, setup)
    first = len(setup["engine"].rendered)
    setup["engine"].rendered.clear()
    run(config, tmp_path, setup)
    assert first > 0
    assert setup["engine"].rendered == []


def test_a_run_clears_away_the_work_folder_of_a_run_that_died(setup, config, tmp_path):
    require_tool("ffmpeg")
    work = tmp_path / "cache" / "work"
    died = work / "run-died"
    died.mkdir(parents=True)
    (died / "in-use.lock").write_bytes(b"")
    (died / "001.wav").write_bytes(b"x" * 1000)
    run(config, tmp_path, setup)
    assert list(work.iterdir()) == []


def test_old_ffmpeg_stops_before_any_speaking(setup, config, tmp_path, monkeypatch):
    def too_old(cfg):
        raise ToolNotFoundError("This ffmpeg is too old for Dogear.")

    monkeypatch.setattr(make, "check_ffmpeg", too_old)
    with pytest.raises(ToolNotFoundError):
        run(config, tmp_path, setup)
    assert setup["engine"].rendered == []


def test_book_in_another_language_explains_the_override(setup, config, tmp_path):
    setup["book"] = small_book(language="fr")
    with pytest.raises(VoiceError) as err:
        run(config, tmp_path, setup)
    assert "'fr'" in err.value.message
    assert "--language en" in err.value.hint


def test_language_override_lets_it_through(setup, config, tmp_path):
    require_tool("ffmpeg")
    setup["book"] = small_book(language="fr")
    assert run(config, tmp_path, setup, language="en")


@pytest.mark.parametrize("language", [None, "en", "en-GB", "EN-us"])
def test_english_or_unlabeled_books_are_accepted(language, config):
    check_language(small_book(language), LanguageVoices.from_config(config))


def test_unknown_format_in_config_is_an_error(setup, config, tmp_path):
    config["output"]["formats"] = ["m4b", "wav"]
    with pytest.raises(DogearError, match="each entry must be one of: m4b, mp3"):
        run(config, tmp_path, setup)


def test_talking_books_get_the_text_tied_to_the_audio(setup, config, tmp_path, monkeypatch):
    require_tool("ffmpeg")
    built = {}

    def fake_builder(name):
        def build(sections, metadata, path, cfg, voices=()):
            built[name] = (sections, path)

        return build

    monkeypatch.setattr(make, "build_epub3", fake_builder("epub"))
    monkeypatch.setattr(make, "build_daisy202", fake_builder("daisy"))
    config["output"]["formats"] = ["daisy", "epub"]
    saved = run(config, tmp_path, setup, sections={2})
    out = tmp_path / "out"
    assert saved == [
        out / "A Short Book Part 1 talking book.epub",
        out / "A Short Book Part 1 DAISY",
    ]
    sections, path = built["epub"]
    assert path == saved[0] and built["daisy"][0] == sections
    assert [(s.number, s.title) for s in sections] == [(0, "A Short Book: Part 1"), (2, "Two")]
    two = sections[1]
    assert [h.text for h in two.headings()] == ["Two"]
    # Real timings from the render: one sync per sentence, back to back to the end.
    assert len(two.syncs) == 2
    assert two.syncs[0].clip.end == two.syncs[1].clip.begin
    assert two.syncs[-1].clip.end == two.audio.marks[-1] / two.audio.sample_rate


def test_makes_an_epub_talking_book(setup, config, tmp_path):
    require_tool("ffmpeg")
    config["output"]["formats"] = ["epub"]
    [path] = run(config, tmp_path, setup, sections={1, 3})
    assert path == tmp_path / "out" / "A Short Book Part 1 talking book.epub"
    assert "Building the EPUB talking book." in setup["lines"]
    book = Epub(path)
    titles = [book.xpath(book.doc(i.get("href")), "//x:title/text()") for i in book.spine()]
    assert titles == [["A Short Book: Part 1"], ["One"], ["Three"]]
    items = book.items()
    for overlay_id, smil in book.overlays():
        [audio_href] = set(book.xpath(smil, "//smil:audio/@src"))
        mp3 = tmp_path / overlay_id
        mp3.write_bytes(book.files[book.root + audio_href])
        [stream] = probe(mp3)["streams"]
        assert (stream["codec_name"], stream["sample_rate"]) == ("mp3", "44100")
        # The last clip ends where the spoken audio does, within the MP3's length.
        last_end = book.clips(smil)[-1][1] / 1000
        assert last_end <= float(probe(mp3)["format"]["duration"])
        assert items[overlay_id].get("media-type") == "application/smil+xml"


def test_an_epub_without_the_title_track_has_no_title_page(setup, config, tmp_path):
    require_tool("ffmpeg")
    config["output"]["formats"] = ["epub"]
    config["narration"]["announce_title"] = False
    book = Epub(run(config, tmp_path, setup, sections={2})[0])
    [doc] = book.spine()
    assert not book.xpath(book.doc(doc.get("href")), "//*[@epub:type='titlepage']")
    assert book.xpath(book.nav(), "//x:nav[@epub:type='landmarks']//x:a/@epub:type") == [
        "bodymatter"
    ]


def test_nothing_may_replace_the_book_itself(setup, config, tmp_path):
    config["output"]["formats"] = ["epub"]
    source = tmp_path / "out" / "A Short Book Part 1 talking book.epub"
    request = MakeRequest(book=source, output_dir=tmp_path / "out")
    with pytest.raises(OutputFolderError, match="would replace the book it was made from"):
        make_audiobook(request, config, setup["lines"].append)
    assert setup["engine"].rendered == []


def test_section_outside_the_book_is_an_error(setup, config, tmp_path):
    with pytest.raises(DogearError, match="There is no section 9; this book has 3."):
        run(config, tmp_path, setup, sections={2, 9})


@pytest.mark.parametrize(
    ("spec", "expected"), [("3", {3}), ("1-3,7", {1, 2, 3, 7}), (" 2 , 4-5 ", {2, 4, 5})]
)
def test_parse_sections(spec, expected):
    assert parse_sections(spec) == expected


@pytest.mark.parametrize(
    ("spec", "expected"),
    [("m4b", ["m4b"]), ("daisy, M4B", ["daisy", "m4b"]), ("mp3,mp3", ["mp3"])],
)
def test_parse_formats(spec, expected):
    assert parse_formats(spec) == expected


def test_parse_formats_all_is_every_format():
    assert parse_formats("all") == list(FORMATS)


@pytest.mark.parametrize(("spec", "named"), [("wav", "wav"), ("m4b,both", "both"), (",", ",")])
def test_parse_formats_rejects_unknown_names(spec, named):
    with pytest.raises(DogearError, match=f"'{named}' isn't a format") as err:
        parse_formats(spec)
    assert "m4b, mp3, epub, daisy" in err.value.hint


@pytest.mark.parametrize("spec", ["", "two", "1-", "3,x"])
def test_parse_sections_rejects_nonsense(spec):
    with pytest.raises(DogearError, match="isn't a list of section numbers"):
        parse_sections(spec)


def test_parse_sections_rejects_a_backwards_range():
    with pytest.raises(DogearError, match="'5-2' counts backwards") as err:
        parse_sections("1,5-2")
    assert err.value.hint == "Write the lower number first: 2-5."


def test_cli_make_passes_options_through(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_make(request, config, report):
        seen.update(request=request, config=config)
        return [tmp_path / "Book.m4b"]

    monkeypatch.setattr(cli, "make_audiobook", fake_make)
    book = tmp_path / "book.epub"
    args = ["make", str(book), "--format", "m4b,mp3", "--voice", "bf_emma", "--speed", "1.1"]
    assert cli.main([*args, "--sections", "2-3", "--language", "en"]) == 0
    assert capsys.readouterr().out == f"Saved: {tmp_path / 'Book.m4b'}\n"
    assert seen["request"] == MakeRequest(
        book=book, output_dir=tmp_path, sections={2, 3}, language="en"
    )
    assert seen["config"]["output"]["formats"] == ["m4b", "mp3"]
    assert seen["config"]["voice"]["voice"] == "bf_emma"
    assert seen["config"]["voice"]["speed"] == 1.1


def test_cli_make_rejects_a_speed_the_voice_cant_do(capsys, tmp_path):
    assert cli.main(["make", str(tmp_path / "b.epub"), "--speed", "5"]) == 1
    err = capsys.readouterr().err
    assert "A speed of 5.0 is outside what the voice can do." in err
    assert "What to do: Use a value from 0.5 to 2.0" in err


def test_cli_make_says_how_to_continue_after_ctrl_c(monkeypatch, capsys, tmp_path):
    def interrupted(request, config, report):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "make_audiobook", interrupted)
    assert cli.main(["make", str(tmp_path / "b.epub")]) == 130
    assert "Run the same command again to carry on." in capsys.readouterr().err


def test_cli_voices_says_only_stopped_after_ctrl_c(monkeypatch, capsys):
    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_voices", interrupted)
    assert cli.main(["voices"]) == 130
    assert capsys.readouterr().err.strip() == "Stopped."


def test_a_book_too_big_for_the_disk_stops_before_the_voice_loads(
    setup, config, tmp_path, monkeypatch
):
    def no_voice(cfg, chosen, report):
        raise AssertionError("the voice was loaded")

    monkeypatch.setattr(make, "make_voice", no_voice)
    monkeypatch.setattr(disk_space, "disk_of", lambda folder: Disk(1, 10**12, 1000))
    with pytest.raises(DiskSpaceError, match="This book needs about"):
        run(config, tmp_path, setup)
    assert not (tmp_path / "out").exists()
