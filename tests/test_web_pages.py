"""The structure every page needs for screen readers and keyboards, checked on every page
in every state (tests/web_samples.py). The browser check (scripts/check-web-a11y.sh) runs
axe-core over the same pages; these run with the unit tests, with no browser."""

import re

import lxml.html
import pytest
from web_samples import render_pages

from dogear.web.templating import NOT_IN_HTML

ERROR_PAGES = {
    "home-upload-error",
    "choose-errors",
    "choose-language-errors",
    "choose-language-voices-errors",
    "access-error",
    "words-errors",
    "words-changed",
    "pronunciations-add-errors",
    "pronunciations-errors",
    "pronunciations-try-error",
    "pronunciations-changed",
}
# Pages whose title may repeat: the same page, with different things in it or a notice,
# or an error in one form or another.
SAME_PAGE = [
    {"home-empty", "home-books", "home-deleted"},
    {"choose-language", "choose-language-voices"},
    {"choose-language-errors", "choose-language-voices-errors"},
    {"send-text", "send-text-languages"},
    {"words-list", "words-saved", "words-many"},
    {"words-errors", "words-changed"},
    {"pronunciations-empty", "pronunciations-full", "pronunciations-added"},
    {
        "pronunciations-add-errors",
        "pronunciations-errors",
        "pronunciations-try-error",
        "pronunciations-changed",
    },
]
# Pages with words to fix, each in its own group with a Listen button.
WORD_PAGES = {
    "words-list",
    "words-saved",
    "words-many",
    "words-all",
    "words-errors",
    "words-changed",
    "pronunciations-full",
    "pronunciations-added",
    "pronunciations-errors",
    "pronunciations-changed",
}


@pytest.fixture(scope="module")
def page_texts(tmp_path_factory):
    folder = tmp_path_factory.mktemp("pages")
    return {name: path.read_text(encoding="utf-8") for name, path in render_pages(folder).items()}


@pytest.fixture(scope="module")
def pages(page_texts):
    return {name: lxml.html.fromstring(text) for name, text in page_texts.items()}


def test_no_page_holds_a_character_html_forbids(page_texts):
    # words-errors echoes a typed NUL back; libxml2 on Windows once ended the page there.
    for name, text in page_texts.items():
        assert not NOT_IN_HTML.search(text), name


def test_every_state_has_a_page(pages):
    assert set(pages) == {
        "home-empty",
        "home-upload-error",
        "home-books",
        "home-deleted",
        "reading",
        "reading-ocr",
        "read-stopped",
        "unreadable",
        "choose",
        "choose-errors",
        "choose-language",
        "choose-language-errors",
        "choose-cloud",
        "send-text",
        "send-text-languages",
        "choose-language-voices",
        "choose-language-voices-errors",
        "waiting",
        "making",
        "making-long",
        "done",
        "failed",
        "stopped",
        "delete",
        "help",
        "not-found",
        "access",
        "access-error",
        "words-start",
        "words-start-language",
        "words-waiting",
        "words-checking",
        "words-list",
        "words-saved",
        "words-many",
        "words-all",
        "words-empty",
        "words-errors",
        "words-changed",
        "words-failed",
        "listen-waiting",
        "listen-done",
        "listen-failed",
        "listen-gone",
        "pronunciations-empty",
        "pronunciations-full",
        "pronunciations-added",
        "pronunciations-add-errors",
        "pronunciations-errors",
        "pronunciations-try-error",
        "pronunciations-changed",
    }


def test_language_title_and_landmarks(pages):
    for name, page in pages.items():
        assert page.get("lang") == "en", name
        [title] = page.xpath("//title/text()")
        assert title.endswith(" - Dogear"), name
        assert title.startswith("Error: ") == (name in ERROR_PAGES), name
        for landmark in ("header", "main", "footer", "nav"):
            assert len(page.xpath(f"//{landmark}")) == 1, (name, landmark)
        assert page.xpath("//nav/@aria-label"), name
        assert not page.xpath("//meta[@http-equiv]"), name  # no refresh, no time limits


def test_titles_say_which_page_and_state(pages):
    titles = {name: page.xpath("//title/text()")[0] for name, page in pages.items()}
    # Each group of the same page counts once.
    firsts = {min(group) for group in SAME_PAGE}
    repeats = set().union(*SAME_PAGE) - firsts
    distinct = [t for name, t in titles.items() if name not in repeats]
    assert titles["choose-language"] == "Choose what to make: Le Roman - Dogear"
    assert len(distinct) == len(set(distinct)), titles
    for group in SAME_PAGE:
        assert len({titles[name] for name in group}) == 1, group
    assert titles["making"] == "Making: The Strange Case of Dr Jekyll and Mr Hyde - Dogear"
    assert titles["waiting"].startswith("Waiting: ")
    assert titles["done"].startswith("Ready: ")


def test_one_h1_and_no_skipped_heading_levels(pages):
    for name, page in pages.items():
        levels = [int(h.tag[1]) for h in page.xpath("//h1|//h2|//h3|//h4|//h5|//h6")]
        assert levels.count(1) == 1, name
        assert levels[0] == 1, name
        for before, after in zip(levels, levels[1:], strict=False):
            assert after <= before + 1, (name, levels)


def test_the_skip_link_comes_first_and_goes_to_main(pages):
    for name, page in pages.items():
        first = page.xpath("//body//a|//body//button|//body//input|//body//select")[0]
        assert first.get("href") == "#main", name
        assert page.xpath("//main/@id") == ["main"], name


def test_every_control_has_a_visible_label(pages):
    for name, page in pages.items():
        ids = page.xpath("//@id")
        assert len(ids) == len(set(ids)), (name, "ids repeat")
        for control in page.xpath("//input[not(@type='hidden')]|//select|//textarea"):
            labels = page.xpath(f"//label[@for='{control.get('id')}']")
            assert len(labels) == 1 and labels[0].text_content().strip(), (name, control.attrib)


def test_described_by_and_error_links_point_at_something(pages):
    for name, page in pages.items():
        ids = set(page.xpath("//@id"))
        for value in page.xpath("//@aria-describedby|//@aria-labelledby"):
            assert set(value.split()) <= ids, (name, value)
        for href in page.xpath("//a/@href"):
            if href.startswith("#"):
                assert href[1:] in ids, (name, href)


def test_choices_are_grouped_in_fieldsets_with_legends(pages):
    for name, page in pages.items():
        for control in page.xpath("//input[@type='checkbox' or @type='radio']"):
            fieldsets = control.xpath("ancestor::fieldset")
            assert fieldsets and fieldsets[0].xpath("./legend"), (name, control.get("id"))


def test_required_fields_say_so_in_words(pages):
    choose = pages["choose"]
    legends = [legend.text_content() for legend in choose.xpath("//legend")]
    assert "What to make (choose at least one)" in legends
    assert "Sections to read (choose at least one)" in legends
    assert pages["home-empty"].xpath("//label[@for='book-file']")[0].text == (
        "Book file (required)"
    )


def test_error_pages_sum_up_the_errors_and_mark_each_field(pages):
    for name in ERROR_PAGES:
        page = pages[name]
        [summary] = page.xpath("//*[@role='alert']")
        assert summary.get("autofocus") is not None, name
        targets = [href[1:] for href in summary.xpath(".//a/@href")]
        assert targets, name
        for message in page.xpath("//p[@class='error-message']"):
            assert message.text_content().startswith("Error: "), name
            assert page.xpath(f"//*[contains(@aria-describedby, '{message.get('id')}')]"), name


def test_a_book_in_another_language_offers_its_voices_first(pages):
    page = pages["choose-language-voices"]
    groups = page.xpath("//select[@id='voice']/optgroup/@label")
    assert groups[0] == "Voices for this book's language (fr)"
    assert page.xpath("//select[@id='voice']//option[@selected]/@value") == ["lilt:fr_one"]
    # With a voice for its language, reading it as English is a choice, not required.
    assert "Language" in [legend.text_content() for legend in page.xpath("//legend")]
    assert page.xpath("//input[@id='english'][not(@checked)]")
    errors = pages["choose-language-voices-errors"]
    [message] = errors.xpath("//p[@id='voice-error']")
    assert "doesn't speak fr" in message.text_content()
    # Without one, it is required, as before.
    legends = [legend.text_content() for legend in pages["choose-language"].xpath("//legend")]
    assert "Language (required)" in legends


def test_choices_sent_back_with_errors_are_kept(pages):
    page = pages["choose-errors"]
    assert page.xpath("//select[@id='voice']//option[@selected]/@value") == ["kokoro:af_heart"]
    assert page.xpath("//input[@name='speed'][@checked]/@value") == ["0.9"]
    assert page.xpath("//input[@name='format'][@checked]") == []


def test_book_pages_have_a_status_region(pages):
    for name in (
        "reading",
        "reading-ocr",
        "read-stopped",
        "waiting",
        "making",
        "done",
        "failed",
        "stopped",
        "unreadable",
    ):
        [region] = pages[name].xpath("//*[@role='status']")
        assert region.text_content().strip(), name
    making = pages["making"].xpath("//*[@role='status']")[0].text_content()
    assert making == "Making: Section 2 of 4 done: Story of the Door. About 8 minutes left."
    waiting = pages["waiting"].xpath("//*[@role='status']")[0].text_content()
    assert waiting == "Waiting: 1 book ahead of this one."


def test_pages_being_made_work_without_javascript(pages):
    for name in ("reading", "reading-ocr", "waiting", "making"):
        page = pages[name]
        assert page.xpath("//a[@id='check-progress']/text()") == ["Check progress"], name
        assert page.xpath("//script/@src") == ["/static/progress.js"], name
    for name in ("done", "failed", "read-stopped", "choose", "home-books"):
        assert not pages[name].xpath("//script"), name


def test_reading_a_scanned_pdf_shows_its_progress_and_can_be_stopped(pages):
    page = pages["reading-ocr"]
    status = page.xpath("//*[@role='status']")[0].text_content()
    assert status == "Read 20 of 240 pages with OCR. About 8 minutes left."
    lines = page.xpath("//ol[@id='progress-lines']/li/text()")
    assert lines[0].startswith("This PDF is scanned, so Dogear is reading its 240 pages")
    assert len(lines) == 4
    assert page.xpath("//form[@action][button]/button/text()") == ["Stop reading this book"]
    assert page.xpath("//*[@id='live-status']/@data-continues") == ["reading"]
    # An ordinary book being read has no lines yet, and says what reading is for.
    reading = pages["reading"].xpath("//*[@role='status']")[0].text_content()
    assert reading.startswith("Reading the book to find its sections.")


def test_a_stopped_read_says_so_and_can_be_read_again(pages):
    page = pages["read-stopped"]
    assert page.xpath("//h1/text()") == ["Reading this book stopped"]
    status = page.xpath("//*[@role='status']")[0].text_content()
    assert status == "Stopped. Choose Read again to carry on."
    buttons = page.xpath("//form/button/text()")
    assert "Read again" in buttons and "Stop reading this book" not in buttons
    assert len(page.xpath("//ol[@id='progress-lines']/li")) == 2


def test_actions_are_buttons_and_links_go_somewhere(pages):
    for name, page in pages.items():
        for link in page.xpath("//a"):
            assert link.get("href"), name
            assert link.text_content().strip(), name
        for button in page.xpath("//button"):
            assert button.get("type") == "submit", name
            assert button.text_content().strip(), name
        assert not page.xpath("//*[@onclick or @role='button' or @style]"), name
        assert not page.xpath("//script[not(@src)]"), name  # nothing inline (CSP)


def test_every_page_links_to_pronunciations(pages):
    for name, page in pages.items():
        assert page.xpath("//nav//a[@href='/pronunciations']/text()") == ["Pronunciations"], name


def test_each_word_is_a_group_that_starts_with_the_word(pages):
    for name in WORD_PAGES:
        page = pages[name]
        groups = page.xpath("//fieldset[@class='word' or @class='word has-error']")
        assert groups, name
        for group in groups:
            legend = group[0]
            assert legend.tag == "legend", name
            word = legend.text_content().strip()
            assert legend.xpath("./h3/text()") == [word], name
            assert group.xpath(".//input[@type='hidden']/@value")[0] == word, name
            # Its Listen button says which word, and sends that word's own Listen form.
            [button] = group.xpath(".//button")
            assert " ".join(button.text_content().split()) == f"Listen to {word}", name
            [form] = page.xpath(f"//form[@id='{button.get('form')}']")
            assert form.get("action") == "/listen" and form.get("method") == "post", name
            assert dict(form.form_values())["text"] == word, name
            [region] = group.xpath(".//*[@role='status']")
            assert form.get("data-listen") == region.get("id"), name
            assert not form.xpath("ancestor::form"), name
        assert page.xpath("//script/@src") == ["/static/listen.js"], name


def test_word_pages_without_words_load_no_listen_script(pages):
    for name in ("words-start", "words-empty", "words-failed"):
        assert "/static/listen.js" not in pages[name].xpath("//script/@src"), name


def test_waiting_for_words_or_a_recording_works_without_javascript(pages):
    for name in ("words-waiting", "words-checking", "listen-waiting"):
        page = pages[name]
        assert page.xpath("//a[@id='check-progress']/text()") == ["Check progress"], name
        assert page.xpath("//script/@src") == ["/static/progress.js"], name
        [region] = page.xpath("//*[@role='status']")
        assert region.text_content().strip(), name


def test_the_recording_page_has_a_player_a_download_and_what_was_said(pages):
    page = pages["listen-done"]
    [audio] = page.xpath("//audio")
    assert audio.get("controls") is not None and audio.get("autoplay") is None
    assert audio.get("src").endswith("/audio")
    assert audio.get("aria-label") == "Recording of Mr. Utterson the lawyer."
    assert page.xpath("//a[@download]/text()") == ["Download the recording"]
    assert "Said: Mister Utter son the lawyer." in page.text_content()
    assert not page.xpath("//script")
    for name, other in pages.items():
        assert not other.xpath("//*[@autoplay]"), name


def test_phoneme_entries_are_listed_without_fields(pages):
    page = pages["pronunciations-full"]
    assert page.xpath("//ul[@class='phoneme-list']/li/text()") == ["Lanyon: lˈænjən"]
    assert "Lanyon" not in [legend.text_content() for legend in page.xpath("//legend")]


def test_done_lists_each_download_with_its_size(pages):
    links = pages["done"].xpath("//ul[@class='downloads']/li")
    texts = [re.sub(r"\s+", " ", li.text_content()).strip() for li in links]
    assert texts == [
        "M4B audiobook: The Strange Case of Dr Jekyll and Mr Hyde.m4b (3.3 MB)",
        "MP3 folder: The Strange Case of Dr Jekyll and Mr Hyde MP3.zip (3.4 MB, a zip file of a "
        "folder)",
    ]
