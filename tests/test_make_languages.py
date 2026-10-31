"""`dogear make` for books in more than one language, and books in another language than
English, with stand-in voices at two sample rates: Kokoro's 24 kHz for English, and a
stand-in engine's 22.05 kHz for French and German."""

from pathlib import Path

import pytest
from conftest import require_tool
from engine_fakes import fake_engine, hum_voice
from epub3_reader import Epub
from fake_voice import HumEngine

from dogear import make
from dogear.document import Book, Heading, Metadata, Paragraph, Section
from dogear.errors import VoiceError
from dogear.make import MakeRequest, make_audiobook

FRENCH = "La vieille dame posa sa tasse sur la table et regarda longuement par la fenêtre."
LILT_RATE = 22050


def lilt():
    voices = [
        hum_voice("fr_one", "fr-FR", description="French"),
        hum_voice("de_one", "de-DE", description="German"),
    ]
    return fake_engine("lilt", "Lilt", voices, sample_rate=LILT_RATE)


def two_language_book(language: str | None = "en", french_lang: str | None = "fr") -> Book:
    one = Section(
        "One",
        blocks=[
            Heading(1, "One"),
            Paragraph("It began in English."),
            Paragraph(FRENCH, lang=french_lang),
            Paragraph("And it ended in English."),
        ],
    )
    two = Section("Two", blocks=[Heading(1, "Two"), Paragraph("The second chapter.")])
    return Book(
        Metadata("A Mixed Book", authors=["Ann Author"], language=language),
        [one, two],
        Path("mixed.epub"),
        "epub",
    )


def french_book(language: str | None = "fr") -> Book:
    sections = [
        Section("Un", blocks=[Heading(1, "Un"), Paragraph("Il était une fois une petite fille.")]),
        Section("Deux", blocks=[Heading(1, "Deux"), Paragraph("Elle vivait près de la forêt.")]),
    ]
    return Book(
        Metadata("Un livre", authors=["Anne Autrice"], language=language),
        sections,
        Path("livre.epub"),
        "epub",
    )


@pytest.fixture
def setup(monkeypatch, config, tmp_path, installed_engines):
    """make.py with a stand-in book and a stand-in voice for each spec it loads."""
    installed_engines(lilt())
    config["render"]["cache_dir"] = str(tmp_path / "cache")
    used = {"book": two_language_book(), "voices": {}, "lines": []}

    def make_voice(cfg, chosen, report):
        rate = LILT_RATE if chosen.engine.name == "lilt" else HumEngine.sample_rate
        used["voices"][chosen.spec] = HumEngine(rate)
        return used["voices"][chosen.spec]

    monkeypatch.setattr(make, "extract_book", lambda path, cfg, report: used["book"])
    monkeypatch.setattr(make, "make_voice", make_voice)
    return used


def run(config, tmp_path, used, **request):
    request = MakeRequest(book=tmp_path / "book.epub", output_dir=tmp_path / "out", **request)
    return make_audiobook(request, config, used["lines"].append)


def rendered(used, spec: str) -> list[str]:
    return used["voices"][spec].rendered if spec in used["voices"] else []


def test_a_french_paragraph_gets_the_voice_for_french(setup, config, tmp_path):
    require_tool("ffmpeg")
    config["voice"]["languages"] = {"fr": "lilt:fr_one"}
    run(config, tmp_path, setup)
    assert rendered(setup, "lilt:fr_one") == [FRENCH]
    assert "It began in English." in rendered(setup, "kokoro:af_heart")
    assert FRENCH not in rendered(setup, "kokoro:af_heart")
    lines = setup["lines"]
    assert "The voice fr_one (Lilt, French) reads 1 sentence in fr." in lines
    assert any(
        line.startswith("The voice af_heart (Kokoro, American English) reads") for line in lines
    )


def test_without_a_voice_for_it_the_main_voice_reads_french_and_says_so(setup, config, tmp_path):
    require_tool("ffmpeg")
    run(config, tmp_path, setup)
    assert list(setup["voices"]) == ["kokoro:af_heart"]
    assert FRENCH in rendered(setup, "kokoro:af_heart")
    assert (
        "1 sentence in fr is read by af_heart, which speaks American English. Voices for fr: "
        "lilt:fr_one. Give it one under [voice.languages] in your settings, or set "
        'other_languages = "best" under [voice].'
    ) in setup["lines"]


def test_best_finds_the_french_voice_by_itself(setup, config, tmp_path):
    require_tool("ffmpeg")
    config["voice"]["other_languages"] = "best"
    run(config, tmp_path, setup)
    assert rendered(setup, "lilt:fr_one") == [FRENCH]


def test_an_unmarked_french_paragraph_is_found_when_detection_is_on(setup, config, tmp_path):
    require_tool("ffmpeg")
    setup["book"] = two_language_book(french_lang=None)
    config["voice"]["languages"] = {"fr": "lilt:fr_one"}
    run(config, tmp_path, setup)
    assert "lilt:fr_one" not in setup["voices"]  # off by default
    config["language_detection"]["enabled"] = True
    run(config, tmp_path, setup)
    assert rendered(setup, "lilt:fr_one") == [FRENCH]
    assert (
        "Looking for passages the book doesn't mark as being in another language, among: en, fr."
        in setup["lines"]
    )


def test_detection_with_one_language_says_there_is_nothing_to_find(setup, config, tmp_path):
    require_tool("ffmpeg")
    config["language_detection"]["enabled"] = True
    run(config, tmp_path, setup)
    assert any(
        line.startswith("Language detection is on, but every voice") for line in setup["lines"]
    )


def test_the_book_is_written_at_the_highest_rate_with_exact_timings(
    setup, config, tmp_path, monkeypatch
):
    require_tool("ffmpeg")
    config["voice"]["languages"] = {"fr": "lilt:fr_one"}
    seen = {}

    def keep(chapters, work, cfg):
        seen["chapters"] = chapters
        return chapters

    monkeypatch.setattr(make, "normalize_chapters", keep)
    run(config, tmp_path, setup)
    assert {c.sample_rate for c in seen["chapters"]} == {HumEngine.sample_rate}


def test_a_talking_book_marks_the_french_passage_and_times_it(setup, config, tmp_path):
    require_tool("ffmpeg")
    config["voice"]["languages"] = {"fr": "lilt:fr_one"}
    config["output"]["formats"] = ["epub"]
    [path] = run(config, tmp_path, setup, sections={1})
    book = Epub(path)
    one = book.spine()[1].get("href")
    doc = book.doc(one)
    [french] = book.xpath(doc, "//x:p[@lang='fr']")
    assert french.get("{http://www.w3.org/XML/1998/namespace}lang") == "fr"
    [span] = book.xpath(french, "x:span")
    # Its clip in the Media Overlay runs from where the French voice starts to where the
    # next sentence does: 0.1 seconds of humming a word (resampled from 22.05 kHz, so the
    # same length in time), then the pause after a paragraph. The hum has no quiet
    # around it to trim.
    _, smil = book.overlays()[1]
    targets = book.xpath(smil, "//smil:text/@src")
    clips = book.clips(smil)
    index = next(i for i, t in enumerate(targets) if t.endswith("#" + span.get("id")))
    begin, end = clips[index]
    expected = len(FRENCH.split()) * 100 + config["narration"]["pauses"]["paragraph_ms"]
    assert abs((end - begin) - expected) <= 1


def test_a_talking_book_names_every_voice_that_reads_it(setup, config, tmp_path):
    require_tool("ffmpeg")
    config["voice"]["languages"] = {"fr": "lilt:fr_one"}
    config["output"]["formats"] = ["epub"]
    [path] = run(config, tmp_path, setup)
    assert Epub(path).meta("media:narrator") == [
        "af_heart, a synthetic Kokoro voice; fr_one, a synthetic Lilt voice"
    ]


# Books in another language.


def test_a_french_book_is_made_with_a_french_main_voice(setup, config, tmp_path):
    require_tool("ffmpeg")
    setup["book"] = french_book()
    config["voice"]["engine"], config["voice"]["voice"] = "lilt", "fr_one"
    run(config, tmp_path, setup)
    assert list(setup["voices"]) == ["lilt:fr_one"]  # Kokoro isn't even loaded
    said = rendered(setup, "lilt:fr_one")
    assert said[:2] == ["Un livre.", "Par Anne Autrice."]  # the title track, in French


def test_a_french_book_with_its_language_mapped_needs_no_other_main_voice(setup, config, tmp_path):
    require_tool("ffmpeg")
    setup["book"] = french_book("fr-FR")
    config["voice"]["languages"] = {"fr": "lilt:fr_one"}
    run(config, tmp_path, setup)
    assert list(setup["voices"]) == ["lilt:fr_one"]


def test_a_french_book_with_only_an_english_voice_names_the_french_ones(setup, config, tmp_path):
    setup["book"] = french_book()
    with pytest.raises(VoiceError) as err:
        run(config, tmp_path, setup)
    assert "marked as being in 'fr'" in err.value.message
    assert "--voice, such as lilt:fr_one" in err.value.hint
    assert "--language en" in err.value.hint
    assert setup["voices"] == {}


def test_a_book_in_a_language_no_voice_speaks_is_refused(setup, config, tmp_path):
    setup["book"] = french_book("ja")
    with pytest.raises(VoiceError) as err:
        run(config, tmp_path, setup)
    assert "No installed voice speaks ja." in err.value.hint


def test_a_book_that_doesnt_say_takes_the_main_voices_language(setup, config, tmp_path):
    require_tool("ffmpeg")
    setup["book"] = french_book(language=None)
    config["voice"]["engine"], config["voice"]["voice"] = "lilt", "fr_one"
    run(config, tmp_path, setup)
    assert setup["book"].metadata.language == "fr"
    assert any(
        line.startswith(
            "This book doesn't say what language it's in, so Dogear takes it to be in fr"
        )
        for line in setup["lines"]
    )


def test_a_language_with_no_words_of_its_own_is_said_once(setup, config, tmp_path):
    require_tool("ffmpeg")
    setup["book"] = two_language_book(french_lang="it")
    run(config, tmp_path, setup)
    said = [line for line in setup["lines"] if "no it words" in line]
    assert said == [
        "Dogear has no it words for the structure it reads aloud (such as Image and Note), so "
        "it says them in English. Add a [narration.words.it] table to your settings to change "
        "that."
    ]
