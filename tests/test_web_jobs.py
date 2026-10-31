"""Reading and making books in child processes, one at a time, with a stand-in `dogear`
(tests/fake_dogear.py): the queue, stopping, starting again, failures, restarts."""

import errno
import io
import json
import logging
import os
import signal
import sys
import time
import zipfile
from dataclasses import replace
from pathlib import Path

import fake_dogear
import pytest
from web_samples import CHOSEN, add_book, sample_app, sample_config, sample_summary, visitor

from dogear.web import child, making, progress
from dogear.web.books import CLOSED_ERROR, BookRecord, Choices
from dogear.web.library import UNEXPECTED, Library
from dogear.web.making import make_args, saved_outputs, section_spec
from dogear.web.reading import INTERRUPTED_ERROR
from dogear.web.settings import web_settings

FAKE = Path(__file__).parent / "fake_dogear.py"


@pytest.fixture
def commands(monkeypatch, tmp_path):
    """Children run the stand-in. Returns the file where it logs each command."""
    monkeypatch.setattr(child, "dogear_command", lambda: [sys.executable, str(FAKE)])
    log = tmp_path / "commands.jsonl"
    monkeypatch.setenv("DOGEAR_FAKE_LOG", str(log))
    return log


def logged(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def pids(monkeypatch, tmp_path):
    """Returns a function that waits for the stand-in's next run and gives its process id."""
    path = tmp_path / "pids"
    monkeypatch.setenv("DOGEAR_FAKE_PIDS", str(path))
    seen = 0

    def next_pid(timeout: float = 30) -> int:
        nonlocal seen
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = path.read_text().split() if path.exists() else []
            if len(found) > seen:
                seen += 1
                return int(found[seen - 1])
            time.sleep(0.05)
        raise AssertionError("the child didn't start")

    return next_pid


@pytest.fixture
def running(tmp_path, commands):
    """The app with its queues running, and a signed-in visitor."""
    app = sample_app(tmp_path / "web")
    client = visitor(app)
    with client:
        yield app, client


def upload(client, behaviour: str, name: str = "book.epub") -> str:
    response = client.post(
        "/books", files={"book": (name, behaviour.encode(), "")}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return response.headers["location"].rsplit("/", 1)[1]


def wait_for(app, book_id: str, *states: str, timeout: float = 30) -> BookRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = app.state.library.get(book_id)
        if record is not None and record.state in states:
            return record
        time.sleep(0.05)
    raise AssertionError(f"book {book_id} is {record.state if record else 'gone'}, not {states}")


def wait_until_idle(worker, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while worker.running() is not None:
        assert time.monotonic() < deadline, f"the worker is still running {worker.running()}"
        time.sleep(0.05)


def choose(client, book_id: str, **fields) -> None:
    data = {
        "format": ["m4b"],
        "voice": "kokoro:af_heart",
        "speed": "0.9",
        "section": ["1", "2", "3"],
    }
    data.update(fields)
    response = client.post(f"/books/{book_id}/choose", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text


def set_behaviour(app, book_id: str, behaviour: str) -> None:
    record = app.state.library.get(book_id)
    (app.state.library.folder(book_id) / record.source_name).write_text(behaviour)


def test_a_book_is_read_chosen_made_and_downloaded(running, commands):
    app, client = running
    book_id = upload(client, "ok")
    wait_for(app, book_id, "ready")
    page = client.get(f"/books/{book_id}")
    assert page.url.path == f"/books/{book_id}/choose"
    assert "1. Title Page: front matter, 100 words." in page.text
    assert "2. One: main text, 100 words, print pages 2 to 3, 1 note." in page.text
    choose(client, book_id, format=["m4b", "mp3"], speed="1.0", section=["2", "3"])
    record = wait_for(app, book_id, "done")
    assert [o.format for o in record.outputs] == ["m4b", "mp3"]
    assert app.state.library.progress(book_id).lines[-1].text == "Section 3 of 3 done: Two."

    page = client.get(f"/books/{book_id}").text
    assert "<title>Ready: Fake Book - Dogear</title>" in page
    assert "M4B audiobook: Fake Book.m4b</a> (2.9 KB)" in page
    assert "MP3 folder: Fake Book MP3.zip</a> (5.9 KB, a zip file of a folder)" in page
    assert str(app.state.library.folder(book_id) / "made") in page

    m4b = client.get(f"/books/{book_id}/files/0")
    assert m4b.content == b"m4b" * 1000
    assert m4b.headers["content-type"] == "audio/mp4"
    assert m4b.headers["content-disposition"] == "attachment; filename*=utf-8''Fake%20Book.m4b"
    folder = client.get(f"/books/{book_id}/files/1")
    with zipfile.ZipFile(io.BytesIO(folder.content)) as archive:
        assert archive.namelist() == ["Fake Book MP3/01 One.mp3", "Fake Book MP3/02 Two.mp3"]
        assert all(i.compress_type == zipfile.ZIP_STORED for i in archive.infolist())

    outline, make = logged(commands)
    assert outline[0] == "outline" and outline[2] == "--json"
    assert make[0] == "make"
    assert make[make.index("--format") + 1] == "m4b,mp3"
    assert make[make.index("--voice") + 1] == "kokoro:af_heart"
    assert make[make.index("--speed") + 1] == "1"
    assert make[make.index("--sections") + 1] == "2-3"
    assert Path(make[make.index("-o") + 1]) == app.state.library.folder(book_id) / "made"


def test_every_section_chosen_means_no_sections_option(running, commands):
    app, client = running
    book_id = upload(client, "ok")
    wait_for(app, book_id, "ready")
    choose(client, book_id)
    wait_for(app, book_id, "done")
    assert "--sections" not in logged(commands)[-1]


def test_a_book_dogear_cannot_read_says_why(running):
    app, client = running
    book_id = upload(client, "unreadable")
    record = wait_for(app, book_id, "unreadable")
    assert record.error == "This book is locked with DRM."
    page = client.get(f"/books/{book_id}").text
    assert "<title>Couldn&#39;t read: book.epub - Dogear</title>" in page
    assert "<strong>Error:</strong> This book is locked with DRM." in page
    assert "<strong>What to do:</strong> Use a copy without DRM." in page


def test_a_book_dogear_could_not_read_can_be_read_again(running):
    app, client = running
    book_id = upload(client, "unreadable")
    wait_for(app, book_id, "unreadable")
    page = client.get(f"/books/{book_id}").text
    assert f'<form method="post" action="/books/{book_id}/read">' in page
    assert "Read again</button>" in page
    set_behaviour(app, book_id, "ok")  # say, Calibre was installed in the meantime
    response = client.post(f"/books/{book_id}/read", follow_redirects=False)
    assert response.headers["location"] == f"/books/{book_id}"
    record = wait_for(app, book_id, "ready")
    assert (record.error, record.hint) == ("", "")
    assert record.summary.title == "Fake Book"


def test_only_a_book_dogear_could_not_read_is_read_again(running, commands):
    app, client = running
    book_id = upload(client, "ok")
    wait_for(app, book_id, "ready")
    assert "Read again" not in client.get(f"/books/{book_id}/choose").text
    client.post(f"/books/{book_id}/read")
    time.sleep(0.3)
    assert app.state.library.get(book_id).state == "ready"
    assert [c[0] for c in logged(commands)] == ["outline"]


@pytest.mark.skipif(os.name == "nt", reason="signals are for Linux and macOS")
def test_a_read_stopped_by_something_else_is_no_bug_and_can_be_read_again(running, pids):
    """Something other than the server ends the child (a person, the system running out
    of memory): the book wasn't the problem, so it can simply be read again."""
    app, client = running
    book_id = upload(client, "slowread")
    os.kill(pids(), signal.SIGKILL)
    record = wait_for(app, book_id, "unreadable")
    assert (record.error, record.hint) == (INTERRUPTED_ERROR, "Choose Read again.")
    assert "This is a bug" not in client.get(f"/books/{book_id}").text
    set_behaviour(app, book_id, "ok")
    client.post(f"/books/{book_id}/read")
    wait_for(app, book_id, "ready")


def test_reading_shows_its_progress_and_outlasts_the_timeout_while_it_reports(tmp_path, commands):
    """The stand-in reads a "scanned PDF" for 2.4 seconds, reporting every 0.4, and the
    timeout is 1 second: it counts from the latest progress line, so a long read that
    keeps reporting, as reading with OCR does, is never cut short."""
    config = sample_config(tmp_path / "web")
    config["web"]["read_timeout_seconds"] = 1
    app = sample_app(tmp_path / "web", config)
    with visitor(app) as client:
        book_id = upload(client, "scanread")
        deadline = time.monotonic() + 30
        while len(status(client, book_id)["lines"]) < 2:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        page = client.get(f"/books/{book_id}").text
        record = wait_for(app, book_id, "ready")
    assert f"<li>{fake_dogear.SCAN_START}</li>" in page
    assert 'role="status">Read ' in page  # the latest line, for screen readers
    assert "Stop reading this book</button>" in page
    assert record.summary.title == "Fake Book"
    lines = [line.text for line in app.state.library.progress(book_id).lines]
    assert lines == [fake_dogear.SCAN_START] + [
        f"Read {n} of 6 pages with OCR." for n in range(1, 7)
    ]


def test_a_read_that_goes_quiet_for_the_timeout_is_stopped(tmp_path, commands):
    config = sample_config(tmp_path / "web")
    config["web"]["read_timeout_seconds"] = 1.5
    app = sample_app(tmp_path / "web", config)
    with visitor(app) as client:
        book_id = upload(client, "slowread")
        record = wait_for(app, book_id, "unreadable")
    assert record.error == (
        "Dogear went 1.5 seconds without any progress reading this book, so it stopped."
    )
    assert record.hint.startswith("Choose Read again.")


def test_a_read_stopped_from_its_page_stays_stopped_until_read_again(running, pids):
    app, client = running
    book_id = upload(client, "slowread")
    pids()  # being read
    assert "Stop reading this book</button>" in client.get(f"/books/{book_id}").text
    response = client.post(f"/books/{book_id}/stop", follow_redirects=False)
    assert response.headers["location"] == f"/books/{book_id}"
    assert app.state.library.reader.wait_until_not_running(book_id, 30)
    assert app.state.library.get(book_id).state == "read_stopped"
    page = client.get(f"/books/{book_id}").text
    assert "<h1>Reading this book stopped</h1>" in page
    assert "Read again</button>" in page
    assert "Stopped.</li>" not in page  # the child's goodbye for a terminal isn't kept

    set_behaviour(app, book_id, "ok")
    client.post(f"/books/{book_id}/read")
    assert wait_for(app, book_id, "ready").summary.title == "Fake Book"


def test_read_again_right_after_stop_waits_for_the_stopped_read_to_end(running, pids):
    app, client = running
    book_id = upload(client, "slowread")
    pids()
    client.post(f"/books/{book_id}/stop")
    set_behaviour(app, book_id, "ok")
    client.post(f"/books/{book_id}/read")  # while the stopped child may still be ending
    assert wait_for(app, book_id, "ready").summary.title == "Fake Book"


def test_a_book_being_read_when_the_server_stops_is_read_again_when_it_starts(
    tmp_path, commands, pids
):
    app = sample_app(tmp_path / "web")
    with visitor(app) as client:
        book_id = upload(client, "slowread")
        pids()  # being read
    # Closing stopped the child; the book isn't unreadable, and no bug is reported.
    assert app.state.library.get(book_id).state == "reading"
    restarted = sample_app(tmp_path / "web")
    set_behaviour(restarted, book_id, "ok")
    with visitor(restarted):
        wait_for(restarted, book_id, "ready")


def test_a_full_disk_for_progress_lines_does_not_stop_the_book(running, monkeypatch, caplog):
    app, client = running
    book_id = upload(client, "ok")
    wait_for(app, book_id, "ready")

    def full(path, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(progress, "write_atomically", full)
    with caplog.at_level(logging.WARNING, logger="dogear.web.progress"):
        choose(client, book_id)
        wait_for(app, book_id, "done")
    warnings = [r.getMessage() for r in caplog.records if r.name == "dogear.web.progress"]
    folder = app.state.library.folder(book_id)
    assert warnings == [
        f"Couldn't save the progress lines in {folder}: [Errno 28] No space left on device"
    ]
    assert app.state.library.progress(book_id).lines[-1].text == "Section 3 of 3 done: Two."


def test_a_bug_while_making_ends_the_child_before_the_job_ends(running, monkeypatch, pids):
    """Otherwise the child would run on with nothing watching it: Stop couldn't reach it,
    and Start again would start a second child for the same book."""
    app, client = running
    book_id = upload(client, "slow")
    pids()  # reading it
    wait_for(app, book_id, "ready")

    def broken(stream):
        yield next(child.read_lines(stream))  # the child is under way
        raise RuntimeError("a bug in the server")

    monkeypatch.setattr(making, "read_lines", broken)
    choose(client, book_id)
    maker = pids()
    record = wait_for(app, book_id, "failed")
    assert record.error == UNEXPECTED
    # The child was ended before the job failed the book; the worker lets go of the job
    # a moment after that, so it is waited for rather than caught mid-step.
    if os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.kill(maker, 0)  # ended, and waited for
    wait_until_idle(app.state.library.maker)

    monkeypatch.setattr(making, "read_lines", child.read_lines)
    set_behaviour(app, book_id, "ok")
    client.post(f"/books/{book_id}/again")
    wait_for(app, book_id, "done")


def test_a_failed_make_says_why_and_start_again_runs_it_again(running, commands):
    app, client = running
    book_id = upload(client, "fail")
    wait_for(app, book_id, "ready")
    choose(client, book_id)
    record = wait_for(app, book_id, "failed")
    assert (record.error, record.hint) == (
        "This book is locked with DRM.",
        "Use a copy without DRM.",
    )
    page = client.get(f"/books/{book_id}").text
    assert "<h1>Dogear couldn&#39;t make this book</h1>" in page
    assert "Start again</button>" in page

    set_behaviour(app, book_id, "ok")
    response = client.post(f"/books/{book_id}/again", follow_redirects=False)
    assert response.status_code == 303
    wait_for(app, book_id, "done")
    first, second = [c for c in logged(commands) if c[0] == "make"]
    assert first == second


def test_a_crash_is_a_problem_dogear_did_not_expect(running):
    app, client = running
    book_id = upload(client, "crash")
    wait_for(app, book_id, "ready")
    choose(client, book_id)
    record = wait_for(app, book_id, "failed")
    assert record.error == "Dogear stopped because of a problem it didn't expect."
    lines = app.state.library.progress(book_id).lines
    assert any(line.text.startswith("RuntimeError: a bug") for line in lines)


def status(client, book_id):
    response = client.get(f"/books/{book_id}/status")
    assert response.status_code == 200
    return response.json()


def test_books_are_made_one_at_a_time_in_order(running):
    app, client = running
    first, second = upload(client, "slow"), upload(client, "slow")
    wait_for(app, first, "ready")
    wait_for(app, second, "ready")
    choose(client, first)
    wait_for(app, first, "making")
    choose(client, second)
    assert status(client, second)["sentence"] == "Waiting: 1 book ahead of this one."
    page = client.get(f"/books/{second}").text
    assert '<p id="status" class="status" role="status">Waiting: 1 book ahead' in page
    assert f'<a id="check-progress" href="/books/{second}">Check progress</a>' in page
    assert 'data-continues="waiting making"' in page

    client.post(f"/books/{first}/stop")
    assert wait_for(app, first, "stopped")
    wait_for(app, second, "making")
    assert status(client, second)["sentence"].startswith("Making: ")

    client.post(f"/books/{second}/stop")
    wait_for(app, second, "stopped")


def test_stopping_a_waiting_book_takes_it_out_of_the_queue(running):
    app, client = running
    first, second = upload(client, "slow"), upload(client, "ok")
    wait_for(app, first, "ready")
    wait_for(app, second, "ready")
    choose(client, first)
    wait_for(app, first, "making")
    choose(client, second)
    client.post(f"/books/{second}/stop")
    assert app.state.library.get(second).state == "stopped"
    client.post(f"/books/{first}/stop")
    wait_for(app, first, "stopped")
    time.sleep(0.3)
    assert app.state.library.get(second).state == "stopped"


def test_stop_then_start_again(running):
    app, client = running
    book_id = upload(client, "slow")
    wait_for(app, book_id, "ready")
    choose(client, book_id)
    record = wait_for(app, book_id, "making")
    assert status(client, book_id)["state"] == "making"
    client.post(f"/books/{book_id}/stop")
    record = wait_for(app, book_id, "stopped")
    page = client.get(f"/books/{book_id}").text
    assert "<title>Stopped: Fake Book - Dogear</title>" in page
    assert "Choose Start again to carry on." in page
    set_behaviour(app, book_id, "ok")
    client.post(f"/books/{book_id}/again")
    record = wait_for(app, book_id, "done")
    assert record.choices == Choices(("m4b",), "kokoro:af_heart", 0.9, (1, 2, 3))


@pytest.mark.parametrize("how", ["stop", "server closing"])
def test_once_stopped_the_childs_goodbye_for_a_terminal_is_not_shown(tmp_path, commands, how):
    """`dogear make` says "Run the same command again to carry on" when stopped. The page
    has its own words for that (Start again), so nothing the child says after the server
    asked it to stop is kept."""
    app = sample_app(tmp_path / "web")
    with visitor(app) as client:
        book_id = upload(client, "slow")
        wait_for(app, book_id, "ready")
        choose(client, book_id)
        deadline = time.monotonic() + 30
        while not status(client, book_id)["lines"]:  # the child is under way
            assert time.monotonic() < deadline
            time.sleep(0.05)
        if how == "stop":
            client.post(f"/books/{book_id}/stop")
            wait_for(app, book_id, "stopped")
    restarted = sample_app(tmp_path / "web") if how == "server closing" else app
    client = visitor(restarted)
    assert status(client, book_id)["lines"] == [
        "Making an audiobook of Fake Book: 3 sections, about 300 words."
    ]
    page = client.get(f"/books/{book_id}").text
    assert "Stopped.</li>" not in page
    assert "Run the same command again" not in page
    assert "Choose Start again to carry on." in page


def test_the_status_says_what_the_page_needs(running):
    app, client = running
    book_id = upload(client, "ok")
    wait_for(app, book_id, "ready")
    choose(client, book_id)
    wait_for(app, book_id, "done")
    assert status(client, book_id) == {
        "state": "done",
        "title": "Ready: Fake Book - Dogear",
        "sentence": "Your talking book is ready.",
        "first": 0,
        "lines": [
            "Making an audiobook of Fake Book: 3 sections, about 300 words.",
            "Section 1 of 3 done: Title Page.",
            "Section 2 of 3 done: One.",
            "Section 3 of 3 done: Two.",
        ],
    }


def test_deleting_a_book_being_made_stops_it_and_removes_its_folder(running):
    app, client = running
    book_id = upload(client, "slow")
    wait_for(app, book_id, "ready")
    choose(client, book_id)
    wait_for(app, book_id, "making")
    folder = app.state.library.folder(book_id)
    page = client.get(f"/books/{book_id}/delete").text
    assert "Deleting it stops that first." in page
    response = client.post(f"/books/{book_id}/delete", follow_redirects=False)
    assert response.headers["location"] == "/?deleted=1"
    assert not folder.exists()
    assert app.state.library.get(book_id) is None
    assert 'role="status">The book was deleted.</p>' in client.get("/?deleted=1").text
    assert client.get(f"/books/{book_id}").status_code == 404


def test_closing_the_server_stops_the_book_being_made(tmp_path, commands):
    app = sample_app(tmp_path / "web")
    with visitor(app) as client:
        book_id = upload(client, "slow")
        wait_for(app, book_id, "ready")
        choose(client, book_id)
        wait_for(app, book_id, "making")
    assert app.state.library.get(book_id).state == "stopped"


def test_children_get_the_servers_config_file(tmp_path, commands, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings_file = Path("dogear.toml")
    settings_file.write_text("")
    app = sample_app(tmp_path / "web", config_file=settings_file)
    with visitor(app) as client:
        book_id = upload(client, "ok")
        wait_for(app, book_id, "ready")
        choose(client, book_id)
        wait_for(app, book_id, "done")
    outline, make = logged(commands)
    expected = ["--config", str(tmp_path / "dogear.toml")]
    assert outline[-2:] == expected
    assert make[-2:] == expected


def test_making_uses_the_pages_word_file_once_there_is_one(running, commands):
    app, client = running
    book_id = upload(client, "ok")
    wait_for(app, book_id, "ready")
    choose(client, book_id)
    wait_for(app, book_id, "done")
    assert "--words" not in logged(commands)[-1]

    app.state.page_words.save({"Utterson": "utter son"})
    client.post(
        f"/books/{book_id}/choose",
        data={"format": "m4b", "voice": "af_heart", "speed": "0.9", "section": ["2"]},
    )
    wait_for(app, book_id, "done")
    make = logged(commands)[-1]
    words_file = app.state.settings.words_file
    assert make[make.index("--words") + 1] == str(words_file)
    assert words_file.parent == app.state.settings.dir


def test_after_a_restart_books_being_made_show_as_stopped(tmp_path, commands):
    config = sample_config(tmp_path)
    settings = web_settings(config)
    library = Library(settings)
    folder = tmp_path / "x"
    folder.mkdir()
    making = add_book(library, folder, state="making", choices=CHOSEN, summary=sample_summary())
    waiting = add_book(library, folder, state="waiting", choices=CHOSEN, summary=sample_summary())
    done = add_book(library, folder, state="done", choices=CHOSEN, summary=sample_summary())
    reading = add_book(library, folder, "ok.epub", state="reading")
    (library.folder(reading) / "book.epub").write_text("ok")

    app = sample_app(tmp_path, config)
    restarted = app.state.library
    for book_id in (making, waiting):
        record = restarted.get(book_id)
        assert (record.state, record.error) == ("stopped", CLOSED_ERROR)
        assert record.choices == CHOSEN
    assert restarted.get(done).state == "done"
    with visitor(app):
        wait_for(app, reading, "ready")


def test_make_args_and_section_lists(tmp_path):
    record = BookRecord(
        id="A" * 16,
        original_name="x.epub",
        extension=".epub",
        added=0,
        state="waiting",
        summary=sample_summary(),
        choices=Choices(("m4b", "daisy"), "bf_emma", 1.1, (1, 2, 4)),
    )
    args = make_args(record, tmp_path)
    assert args == [
        "make",
        str(tmp_path / "book.epub"),
        "-o",
        str(tmp_path / "made"),
        "--format",
        "m4b,daisy",
        "--voice",
        "bf_emma",
        "--speed",
        "1.1",
        "--sections",
        "1-2,4",
    ]
    record = replace(
        record, choices=Choices(("m4b",), "af_heart", 0.9, (1, 2, 3, 4), as_english=True)
    )
    assert make_args(record, tmp_path)[-2:] == ["--language", "en"]
    assert "--sections" not in make_args(record, tmp_path)
    assert section_spec((7, 1, 2, 3, 5, 6, 10)) == "1-3,5-7,10"
    assert section_spec((4,)) == "4"


def test_only_outputs_inside_the_made_folder_count(tmp_path):
    record = BookRecord("A" * 16, "x.epub", ".epub", 0, "making", choices=CHOSEN)
    made = tmp_path / "made"
    made.mkdir()
    (made / "a.m4b").write_text("x")
    (made / "a MP3").mkdir()
    outside = tmp_path / "elsewhere.m4b"
    outside.write_text("x")
    good = saved_outputs(record, tmp_path, [str(made / "a.m4b"), str(made / "a MP3")])
    assert [(o.format, o.path) for o in good] == [("m4b", "made/a.m4b"), ("mp3", "made/a MP3")]
    assert saved_outputs(record, tmp_path, [str(outside), str(made / "a MP3")]) is None
    assert saved_outputs(record, tmp_path, [str(made / "a.m4b")]) is None
    assert saved_outputs(record, tmp_path, [str(made / "a.m4b"), str(made / "gone")]) is None
