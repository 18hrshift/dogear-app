"""A book's "Check how words are said" page: checking its words in a child (the stand-in,
tests/fake_dogear.py), listing them, saving spellings, and the errors and limits."""

import json
import os
import re
import stat
import sys
import time
from dataclasses import replace
from pathlib import Path

import lxml.html
import pytest
from web_samples import (
    CHOSEN,
    add_book,
    changed_elsewhere,
    sample_app,
    sample_config,
    sample_summary,
    save_form,
    visitor,
)

from dogear.unknown_words import UnknownWord
from dogear.web import child
from dogear.web.books import BookRecord
from dogear.web.word_check import (
    CHECK_AGAIN,
    CHECK_FILE,
    CLOSED_ERROR,
    WordCheck,
    check_for,
    parse_words,
)
from dogear.web.word_pages import WORDS_SHOWN, sections_in_words

FAKE = Path(__file__).parent / "fake_dogear.py"


@pytest.fixture
def commands(monkeypatch, tmp_path):
    monkeypatch.setattr(child, "dogear_command", lambda: [sys.executable, str(FAKE)])
    log = tmp_path / "commands.jsonl"
    monkeypatch.setenv("DOGEAR_FAKE_LOG", str(log))
    return log


def logged(log: Path, command: str) -> list[list[str]]:
    lines = log.read_text().splitlines() if log.exists() else []
    return [run for run in map(json.loads, lines) if run[0] == command]


@pytest.fixture
def running(tmp_path, commands):
    app = sample_app(tmp_path / "web")
    client = visitor(app)
    with client:
        yield app, client


def ready_book(app, behaviour="ok", **changes) -> str:
    """A book that has been read, whose file makes the stand-in behave as asked."""
    fields = {"state": "ready", "summary": sample_summary(), **changes}
    book_id = add_book(app.state.library, app.state.settings.dir, **fields)
    (app.state.library.folder(book_id) / "book.epub").write_text(behaviour)
    return book_id


def check(client, app, book_id, *states, **form):
    response = client.post(f"/books/{book_id}/words/check", data=form, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/books/{book_id}/words"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        found = app.state.library.words.get(book_id)
        if found is not None and found.state in (states or ("done",)):
            return found
        time.sleep(0.05)
    raise AssertionError(f"the check is {found}")


def page(client, book_id):
    response = client.get(f"/books/{book_id}/words")
    assert response.status_code == 200
    return response.text, lxml.html.fromstring(response.text)


def texts(tree, xpath):
    return [" ".join(e.text_content().split()) for e in tree.xpath(xpath)]


def save(client, book_id, words: dict[str, str], **expect):
    data = {}
    for number, (word, spelling) in enumerate(words.items(), start=1):
        data[f"word-{number}"] = word
        data[f"say-{number}"] = spelling
    return client.post(f"/books/{book_id}/words", data=data, follow_redirects=False)


def test_check_list_save_and_check_again(running, commands):
    app, client = running
    book_id = ready_book(app)
    html, tree = page(client, book_id)
    assert "<title>Check how words are said: The Strange Case" in html
    assert texts(tree, "//main//button") == ["Check the words"]
    assert app.state.library.words.get(book_id) is None  # a GET starts nothing

    check(client, app, book_id)
    html, tree = page(client, book_id)
    assert "<title>Words to check: The Strange Case" in html
    assert texts(tree, "//legend") == ["Utterson", "Lanyon", "Poole"]
    assert texts(tree, "//fieldset[@id='word-1']/p[@class='hint']") == ["In the book 131 times."]
    assert texts(tree, "//*[@role='status' and @id='status']") == [
        "Checked: 3 words the voice doesn't know."
    ]
    assert tree.xpath("//input[@id='say-1']/@value") == [""]
    assert tree.xpath("//button[@form='listen-1']/@type") == ["submit"]
    assert texts(tree, "//button[@form='listen-1']") == ["Listen to Utterson"]
    [listen_form] = tree.xpath("//form[@id='listen-1']")
    assert listen_form.get("action") == "/listen"
    assert dict(listen_form.form_values()) == {
        "text": "Utterson",
        "back": f"/books/{book_id}/words#word-1",
        "book": book_id,
    }
    [words] = logged(commands, "words")
    assert words[1:3] == [str(app.state.library.folder(book_id) / "book.epub"), "--json"]
    assert "--words" not in words and "--sections" not in words

    response = save(client, book_id, {"Utterson": " utter  son ", "Lanyon": "", "Poole": ""})
    assert response.status_code == 303
    assert response.headers["location"] == f"/books/{book_id}/words?saved=1"
    saved = client.get(f"/books/{book_id}/words?saved=1")
    tree = lxml.html.fromstring(saved.text)
    assert texts(tree, "//p[@class='notice']") == [
        "Saved. Choose Listen to hear a word the new way."
    ]
    assert tree.xpath("//input[@id='say-1']/@value") == ["utter son"]
    assert [(e.word, e.sounds_like) for e in app.state.page_words.entries()] == [
        ("Utterson", "utter son")
    ]

    # Listen, as the word's Listen form sends it, says the fixed version.
    listened = client.post("/listen", data=dict(listen_form.form_values()))
    assert "<p>Said: utter son</p>" in listened.text
    assert f'<a href="/books/{book_id}/words#word-1">Back to the words</a>' in listened.text

    # Checking again uses the page's word file, so the fixed word isn't listed.
    check(client, app, book_id)
    _, tree = page(client, book_id)
    assert texts(tree, "//legend") == ["Lanyon", "Poole"]
    words = logged(commands, "words")[-1]
    assert words[words.index("--words") + 1] == str(app.state.settings.words_file)


def test_emptying_a_saved_spelling_removes_it(running):
    app, client = running
    book_id = ready_book(app)
    check(client, app, book_id)
    save(client, book_id, {"Utterson": "utter son", "Lanyon": "lan yon"})
    save(client, book_id, {"Utterson": "", "Lanyon": "lan yun"})
    assert [(e.word, e.sounds_like) for e in app.state.page_words.entries()] == [
        ("Lanyon", "lan yun")
    ]


def test_a_words_page_opened_earlier_keeps_spellings_saved_since(running):
    app, client = running
    book_id = ready_book(app)
    check(client, app, book_id)
    path = f"/books/{book_id}/words"
    save(client, book_id, {"Utterson": "utter son", "Lanyon": "lan yon"})
    earlier = save_form(client, path)
    assert [earlier[f"was-{n}"] for n in (1, 2, 3)] == ["utter son", "lan yon", ""]
    # Meanwhile, the Pronunciations page changes one word and removes another.
    app.state.page_words.save({"Utterson": "utter sun", "Lanyon": ""})
    response = client.post(path, data={**earlier, "say-3": "pool"}, follow_redirects=False)
    assert response.headers["location"] == f"{path}?saved=1"
    assert [(e.word, e.sounds_like) for e in app.state.page_words.entries()] == [
        ("Poole", "pool"),
        ("Utterson", "utter sun"),
    ]
    page = changed_elsewhere(
        client, path, "Utterson", "otter son", lambda: app.state.page_words.save({"Utterson": ""})
    )
    tree = lxml.html.fromstring(page)
    assert tree.xpath("//*[@role='alert']//a/@href") == ["#say-1"]
    assert texts(tree, "//p[@id='say-1-error']") == [
        "Error: Utterson was changed in another window since this page was opened. It now "
        "has no spelling. Check it and save again."
    ]
    assert tree.xpath("//input[@id='say-1']/@aria-describedby") == ["say-1-about say-1-error"]
    assert [(e.word, e.sounds_like) for e in app.state.page_words.entries()] == [("Poole", "pool")]


def test_a_book_with_choices_checks_its_sections_with_its_voice(running, commands):
    app, client = running
    choices = replace(CHOSEN, voice="bf_emma", sections=(2, 3))
    book_id = ready_book(app, state="done", choices=choices)
    html, _ = page(client, book_id)
    assert "the words in sections 2 and 3 that the voice" in html
    check(client, app, book_id)
    words = logged(commands, "words")[-1]
    assert words[words.index("--sections") + 1] == "2-3"
    assert words[words.index("--voice") + 1] == "bf_emma"
    assert "--language" not in words
    _, tree = page(client, book_id)
    assert texts(tree, "//fieldset[@id='word-1']/p[@class='hint']") == [
        "In the checked sections 131 times."
    ]
    # And Listen says the book's words with its voice.
    assert tree.xpath("//form[@id='listen-1']/input[@name='book']/@value") == [book_id]


def test_a_book_marked_in_another_language_is_checked_as_english_when_asked(running, commands):
    app, client = running
    book_id = ready_book(app, summary=replace(sample_summary(), language="fr"))
    html, tree = page(client, book_id)
    assert 'the language with the code "fr"' in html
    assert texts(tree, "//main//button") == ["Check the words as English"]
    assert tree.xpath("//form[@action and .//button]//input[@name='english']/@value") == ["yes"]
    check(client, app, book_id, english="yes")
    words = logged(commands, "words")[-1]
    assert words[words.index("--language") + 1] == "en"


def test_saving_errors_keep_what_was_typed_and_link_to_each_field(tmp_path, commands):
    config = sample_config(tmp_path / "web")
    config["web"]["max_spelling_chars"] = 12
    app = sample_app(tmp_path / "web", config)
    with visitor(app) as client:
        book_id = ready_book(app)
        check(client, app, book_id)
        response = save(
            client,
            book_id,
            {"Utterson": "utter son utter son", "Lanyon": "lan\nyon", "Poole": "pool"},
        )
    assert response.status_code == 400
    tree = lxml.html.fromstring(response.text)
    assert tree.xpath("//title/text()")[0].startswith("Error: Words to check: ")
    [summary] = tree.xpath("//*[@role='alert']")
    assert [(a.get("href"), a.text_content()) for a in summary.xpath(".//a")] == [
        ("#say-1", "How to say Utterson can be at most 12 characters long."),
        ("#say-2", "How to say Lanyon can't have line breaks or other control characters."),
    ]
    assert tree.xpath("//input[@id='say-1']/@value") == ["utter son utter son"]
    assert tree.xpath("//input[@id='say-3']/@value") == ["pool"]
    assert tree.xpath("//input[@id='say-1']/@aria-describedby") == ["say-1-about say-1-error"]
    assert texts(tree, "//p[@id='say-1-error']") == [
        "Error: How to say Utterson can be at most 12 characters long."
    ]
    assert app.state.page_words.entries() == []  # nothing saved, not even Poole


def test_a_full_word_list_is_an_error_on_the_first_new_word(tmp_path, commands):
    config = sample_config(tmp_path / "web")
    config["web"]["max_pronunciations"] = 1
    app = sample_app(tmp_path / "web", config)
    with visitor(app) as client:
        book_id = ready_book(app)
        check(client, app, book_id)
        assert save(client, book_id, {"Utterson": "utter son"}).status_code == 303
        response = save(client, book_id, {"Utterson": "utter son", "Lanyon": "lan yon"})
    assert response.status_code == 400
    tree = lxml.html.fromstring(response.text)
    assert tree.xpath("//*[@role='alert']//a/@href") == ["#say-2"]
    assert "holds at most 1 words" in response.text.replace("&#39;", "'")


def test_a_word_with_exact_phonemes_is_not_changed_by_the_page(running):
    app, client = running
    words_file = app.state.settings.words_file
    words_file.write_text('[words]\nPoole = { phonemes = "pˈul" }\n', encoding="utf-8")
    book_id = ready_book(app, "manywords")
    check(client, app, book_id)
    response = save(client, book_id, {"Utterson": "utter son", "POOLE": "pool"})
    assert response.status_code == 400
    assert "Poole has exact phonemes" in response.text
    assert lxml.html.fromstring(response.text).xpath("//*[@role='alert']//a/@href") == ["#say-2"]
    assert save(client, book_id, {"Utterson": "utter son", "POOLE": ""}).status_code == 303
    assert [(e.word, e.phonemes) for e in app.state.page_words.entries()] == [
        ("Poole", "pˈul"),
        ("Utterson", None),
    ]


def test_the_list_shows_the_most_frequent_and_says_how_many_more(running):
    app, client = running
    book_id = ready_book(app, "manywords")
    found = check(client, app, book_id)
    assert (len(found.words), found.total) == (60, 60)
    html, tree = page(client, book_id)
    assert len(tree.xpath("//fieldset[@class='word']")) == WORDS_SHOWN
    assert "10 more words aren&#39;t listed. Each comes up 51 times or fewer." in html
    assert tree.xpath("//a[text()='Show all 60 words']/@href") == [f"/books/{book_id}/words/all"]


def test_a_book_whose_words_the_voice_knows(running):
    app, client = running
    book_id = ready_book(app, "nowords")
    check(client, app, book_id)
    html, tree = page(client, book_id)
    assert "<title>No words to check: " in html
    assert "The voice knows every word in the whole book" in html
    assert not tree.xpath("//fieldset") and not tree.xpath("//script")
    assert texts(tree, "//main//button") == ["Check again"]


@pytest.mark.parametrize(
    ("behaviour", "error", "hint"),
    [
        ("unreadable", "This book is locked with DRM.", "Use a copy without DRM."),
        ("badwords", "Dogear couldn't understand the list of this book's words", "a bug"),
    ],
)
def test_a_check_that_fails_says_why_and_can_be_tried_again(running, behaviour, error, hint):
    app, client = running
    book_id = ready_book(app, behaviour)
    found = check(client, app, book_id, "failed")
    assert error in found.error and hint in found.hint
    html, tree = page(client, book_id)
    assert "<title>Couldn&#39;t check words: " in html
    assert texts(tree, "//h1") == ["Dogear couldn't check the words"]
    assert texts(tree, "//main//button") == ["Check again"]
    (app.state.library.folder(book_id) / "book.epub").write_text("ok")
    check(client, app, book_id)


def test_waiting_for_a_check_works_without_javascript(running):
    app, client = running
    book_id = ready_book(app, "slowwords")
    check(client, app, book_id, "checking")
    html, tree = page(client, book_id)
    assert "<title>Checking words: " in html
    assert texts(tree, "//*[@role='status']")[0].startswith("Checking the words.")
    assert tree.xpath("//a[@id='check-progress']/@href") == [f"/books/{book_id}/words"]
    assert tree.xpath("//script/@src") == ["/static/progress.js"]
    status = client.get(f"/books/{book_id}/words/status").json()
    assert status["state"] == "checking"
    assert status["title"].startswith("Checking words: ")
    # A second check while one runs changes nothing.
    client.post(f"/books/{book_id}/words/check")
    assert app.state.library.words.ahead(book_id) is None

    # Deleting the book stops its check.
    client.post(f"/books/{book_id}/delete")
    assert app.state.library.words.get(book_id) is None


def test_a_book_not_read_yet_has_no_words_page(running):
    app, client = running
    book_id = add_book(app.state.library, app.state.settings.dir, state="reading")
    response = client.get(f"/books/{book_id}/words", follow_redirects=False)
    assert response.headers["location"] == f"/books/{book_id}"
    response = client.post(f"/books/{book_id}/words/check", follow_redirects=False)
    assert response.headers["location"] == f"/books/{book_id}"
    assert app.state.library.words.get(book_id) is None


def test_the_choose_and_done_pages_link_to_the_words_page(running):
    app, client = running
    ready = ready_book(app)
    done = ready_book(app, state="done", choices=CHOSEN)
    for book_id, path in ((ready, "choose"), (done, "")):
        tree = lxml.html.fromstring(client.get(f"/books/{book_id}/{path}".rstrip("/")).text)
        assert tree.xpath("//a[text()='check how words are said']/@href") == [
            f"/books/{book_id}/words"
        ]


def test_the_words_pages_need_the_cookie_and_dogears_own_forms(running):
    app, client = running
    book_id = ready_book(app)
    stranger = visitor(app, signed_in=False)
    assert stranger.get(f"/books/{book_id}/words").status_code == 403
    assert stranger.get(f"/books/{book_id}/words/status").status_code == 403
    assert stranger.post(f"/books/{book_id}/words/check").status_code == 403
    elsewhere = {"Origin": "http://evil.example"}
    assert client.post(f"/books/{book_id}/words/check", headers=elsewhere).status_code == 403
    response = client.post(
        f"/books/{book_id}/words", data={"word-1": "Hyde", "say-1": "x"}, headers=elsewhere
    )
    assert response.status_code == 403
    assert app.state.library.words.get(book_id) is None
    assert app.state.page_words.entries() == []


def test_only_numbered_word_fields_count(running):
    app, client = running
    book_id = ready_book(app)
    data = {
        "word-1": "Hyde",
        "say-1": "hide",
        "word-x": "Poole",
        "say-x": "pool",
        "word-99999999": "Lanyon",
        "say-99999999": "lan yon",
        f"word-{app.state.settings.words_listed + 1}": "Carew",
        f"say-{app.state.settings.words_listed + 1}": "ca roo",
        "word-²": "Enfield",
        "say-²": "en field",
    }
    assert client.post(f"/books/{book_id}/words", data=data).status_code == 200
    assert [e.word for e in app.state.page_words.entries()] == ["Hyde"]


def test_check_for_and_the_words_child_json():
    record = BookRecord(
        "A" * 16,
        "x.epub",
        ".epub",
        0,
        "ready",
        summary=sample_summary(),
        choices=replace(CHOSEN, sections=(1, 2, 3, 4)),
    )
    assert check_for(record) == WordCheck("waiting", (), False, "af_heart")
    english = replace(record, choices=replace(CHOSEN, sections=(1,), as_english=True))
    assert check_for(english) == WordCheck("waiting", (1,), True, "af_heart")
    done = parse_words(b'{"words": [{"word": "Hyde", "count": 2}]}', WordCheck("checking"), 9)
    assert (done.state, done.total, [w.word for w in done.words]) == ("done", 1, ["Hyde"])
    bad = parse_words(b'{"words": [{"word": "Hyde"}]}', WordCheck("checking"), 9)
    assert bad.state == "failed" and "word 1 has no count" in bad.error


def test_sections_in_words():
    assert sections_in_words(()) == "the whole book"
    assert sections_in_words((3,)) == "section 3"
    assert sections_in_words((1, 2, 3)) == "sections 1 to 3"
    assert sections_in_words((7, 1, 2, 3, 5)) == "sections 1 to 3, 5 and 7"
    assert sections_in_words((2, 3)) == "sections 2 and 3"
    assert sections_in_words((1, 2, 3, 7, 8)) == "sections 1 to 3, 7 and 8"


def test_no_page_holds_inline_script_or_style(running):
    app, client = running
    book_id = ready_book(app)
    check(client, app, book_id)
    html, _ = page(client, book_id)
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert "style=" not in html


# Saved checks, and every word the check kept


def restarted(app):
    """The app again, as after a restart: a new library on the same folder."""
    return sample_app(app.state.settings.dir, config=app.state.config)


def test_a_check_is_saved_with_the_book_and_still_there_after_a_restart(running):
    app, client = running
    book_id = ready_book(app, "manywords")
    found = check(client, app, book_id)
    saved = app.state.library.folder(book_id) / CHECK_FILE
    if os.name != "nt":
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert [p.name for p in saved.parent.iterdir() if p.name.startswith(".")] == []
    again = restarted(app)
    assert again.state.library.words.get(book_id) == found
    tree = lxml.html.fromstring(visitor(again).get(f"/books/{book_id}/words").text)
    assert len(tree.xpath("//fieldset[@class='word']")) == WORDS_SHOWN
    assert texts(tree, "//*[@role='status' and @id='status']") == [
        "Checked: 60 words the voice doesn't know."
    ]


def test_a_check_dogear_was_closed_during_says_so_after_a_restart(tmp_path):
    app = sample_app(tmp_path / "web")  # its queues never start, so the check waits
    book_id = ready_book(app)
    visitor(app).post(f"/books/{book_id}/words/check")
    assert app.state.library.words.get(book_id).state == "waiting"
    found = restarted(app).state.library.words.get(book_id)
    assert (found.state, found.error, found.hint) == ("failed", CLOSED_ERROR, CHECK_AGAIN)
    # Saved as failed, so it says the same after another restart.
    assert restarted(app).state.library.words.get(book_id) == found


def test_a_check_stopped_with_the_server_is_gone_after_a_restart(tmp_path, commands):
    app = sample_app(tmp_path / "web")
    with visitor(app) as client:
        book_id = ready_book(app, "slowwords")
        check(client, app, book_id, "checking")
    assert not (app.state.library.folder(book_id) / CHECK_FILE).exists()
    assert restarted(app).state.library.words.get(book_id) is None


SAVED_CHECK = (
    '{"state": "done", "sections": [], "as_english": false, "voice": "", '
    '"words": [{"word": "Hyde", "count": 2}], "total": 1, "error": "", "hint": ""}'
)


@pytest.mark.parametrize(
    "saved",
    [
        "not json",
        '{"state": "done"}',
        SAVED_CHECK.replace('"done"', '"lost"'),
        SAVED_CHECK.replace('"count": 2', '"count": 0'),
        SAVED_CHECK.replace('"sections": []', '"sections": [true]'),
        SAVED_CHECK.replace('"total": 1', '"total": 0'),
        SAVED_CHECK.replace('"voice": ""', '"voice": 3'),
        SAVED_CHECK.replace('"as_english": false', '"as_english": "no"'),
    ],
)
def test_a_saved_check_that_cant_be_read_is_left_out(tmp_path, caplog, saved):
    app = sample_app(tmp_path / "web")
    book_id = ready_book(app)
    (app.state.library.folder(book_id) / CHECK_FILE).write_text(saved, encoding="utf-8")
    assert restarted(app).state.library.words.get(book_id) is None
    assert "Ignoring the word check" in caplog.text


def test_a_saved_check_is_read_back_whole(tmp_path):
    app = sample_app(tmp_path / "web")
    book_id = ready_book(app)
    (app.state.library.folder(book_id) / CHECK_FILE).write_text(SAVED_CHECK, encoding="utf-8")
    found = restarted(app).state.library.words.get(book_id)
    assert found == WordCheck("done", words=(UnknownWord("Hyde", 2),), total=1)


def test_the_setting_bounds_the_words_kept_and_listed(tmp_path, commands):
    config = sample_config(tmp_path / "web")
    config["web"]["max_words_listed"] = 55
    app = sample_app(tmp_path / "web", config=config)
    with visitor(app) as client:
        book_id = ready_book(app, "manywords")
        found = check(client, app, book_id)
        assert (len(found.words), found.total) == (55, 60)
        html, tree = page(client, book_id)
        assert "10 more words aren&#39;t listed." in html
        assert tree.xpath("//a[text()='Show the 55 most frequent words']/@href") == [
            f"/books/{book_id}/words/all"
        ]
        html = client.get(f"/books/{book_id}/words/all").text
        assert "5 more words aren&#39;t listed. Each comes up 46 times or fewer." in html
    config["web"]["max_words_listed"] = 20  # lowered since: the saved check is cut to it
    again = sample_app(tmp_path / "web", config=config)
    kept = again.state.library.words.get(book_id)
    assert (len(kept.words), kept.total) == (20, 60)
    tree = lxml.html.fromstring(visitor(again).get(f"/books/{book_id}/words").text)
    assert len(tree.xpath("//fieldset[@class='word']")) == 20
    assert not tree.xpath("//a[starts-with(text(), 'Show')]")


def test_every_word_can_be_listed_heard_and_saved(running):
    app, client = running
    book_id = ready_book(app, "manywords")
    check(client, app, book_id)
    path = f"/books/{book_id}/words/all"
    html = client.get(path).text
    tree = lxml.html.fromstring(html)
    assert "<title>All words to check: The Strange Case" in html
    assert len(tree.xpath("//fieldset[@class='word']")) == 60
    assert "aren&#39;t listed" not in html
    assert tree.xpath("//a[text()='Show only the 50 most frequent words']/@href") == [
        f"/books/{book_id}/words"
    ]
    [listen] = tree.xpath("//form[@id='listen-55']")
    assert dict(listen.form_values())["back"] == f"{path}#word-55"
    form = save_form(client, path)
    assert form["word-55"] == "Name54"
    form["say-55"] = "name fifty four"
    response = client.post(path, data=form, follow_redirects=False)
    assert response.headers["location"] == f"{path}?saved=1"
    assert [(e.word, e.sounds_like) for e in app.state.page_words.entries()] == [
        ("Name54", "name fifty four")
    ]
    assert "Saved. Choose Listen" in client.get(f"{path}?saved=1").text


def test_a_word_changed_elsewhere_is_caught_on_the_all_words_page(running):
    app, client = running
    book_id = ready_book(app, "manywords")
    check(client, app, book_id)
    path = f"/books/{book_id}/words/all"
    html = changed_elsewhere(
        client, path, "Name57", "name", lambda: app.state.page_words.save({"Name57": "nayme"})
    )
    assert "Name57 was changed in another window since this page was opened." in html
    assert [(e.word, e.sounds_like) for e in app.state.page_words.entries()] == [
        ("Name57", "nayme")
    ]
    tree = lxml.html.fromstring(html)
    assert tree.xpath("//form[@method='post' and @novalidate]/@action") == [path]


def test_the_all_words_page_needs_the_cookie_and_dogears_own_forms(running):
    app, client = running
    book_id = ready_book(app)
    stranger = visitor(app, signed_in=False)
    assert stranger.get(f"/books/{book_id}/words/all").status_code == 403
    response = client.post(
        f"/books/{book_id}/words/all",
        data={"word-1": "Hyde", "say-1": "x"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert app.state.page_words.entries() == []
