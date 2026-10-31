"""Listen: `dogear say` children on their own queue, the recordings they leave, and the
pages and JSON that play them. Children are the stand-in (tests/fake_dogear.py)."""

import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from web_samples import CHOSEN, add_book, sample_app, sample_summary, visitor

from dogear.web import child, listening
from dogear.web.listen_pages import back_to
from dogear.web.listening import LISTENS_KEPT, MAX_LISTEN_CHARS, Listens

FAKE = Path(__file__).parent / "fake_dogear.py"
JSON = {"Accept": "application/json"}


@pytest.fixture
def commands(monkeypatch, tmp_path):
    monkeypatch.setattr(child, "dogear_command", lambda: [sys.executable, str(FAKE)])
    log = tmp_path / "commands.jsonl"
    monkeypatch.setenv("DOGEAR_FAKE_LOG", str(log))
    return log


def logged(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def running(tmp_path, commands):
    app = sample_app(tmp_path / "web")
    client = visitor(app)
    with client:
        yield app, client


def listen(client, text, **fields):
    """Listen without JavaScript: the form, then the page it goes to."""
    response = client.post("/listen", data={"text": text, **fields}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"]


def wait_until(app, listen_id, *states, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = app.state.listens.get(listen_id)
        if found is not None and found.state in states:
            return found
        time.sleep(0.05)
    raise AssertionError(f"listen {listen_id} isn't {states}")


def test_listen_without_javascript_shows_a_player_and_what_was_said(running, commands):
    app, client = running
    page_url = listen(client, "Utterson met him.", back="/pronunciations")
    page = client.get(page_url)
    assert page.status_code == 200
    listen_id = page_url.rsplit("/", 1)[1]
    assert "<title>Listen: Utterson met him. - Dogear</title>" in page.text
    assert (
        f'<audio controls preload="metadata" src="/listen/{listen_id}/audio" '
        'aria-label="Recording of Utterson met him.">'
    ) in page.text
    assert "autoplay" not in page.text
    assert "<p>Said: Utterson met him.</p>" in page.text
    assert f'<a href="/listen/{listen_id}/audio" download="Utterson met him.wav">' in page.text
    assert '<a href="/pronunciations">Back to Pronunciations</a>' in page.text

    audio = client.get(f"/listen/{listen_id}/audio")
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.headers["content-disposition"].startswith("inline;")
    assert audio.content[:4] == b"RIFF"
    assert audio.content == (app.state.settings.listen_dir / f"{listen_id}.wav").read_bytes()
    [say] = logged(commands)
    assert say[:3] == ["say", "-o", str(app.state.settings.listen_dir / f"{listen_id}.wav")]
    assert say[-2:] == ["--", "Utterson met him."]
    assert "--words" not in say  # the page hasn't saved a word yet


def test_listening_again_after_a_fix_uses_the_pages_words(running, commands):
    app, client = running
    first = listen(client, "Utterson")
    assert "<p>Said: Utterson</p>" in client.get(first).text
    app.state.page_words.save({"Utterson": "utter son"})
    second = listen(client, "Utterson")
    assert "<p>Said: utter son</p>" in client.get(second).text
    assert client.get(f"{second}/audio").content != client.get(f"{first}/audio").content
    say = logged(commands)[-1]
    words_file = str(app.state.settings.words_file)
    assert say[say.index("--words") + 1] == words_file
    assert say.index("--words") < say.index("--")


@pytest.mark.parametrize("text", ["--help", "-o /tmp/elsewhere.wav", "--words=x.toml"])
def test_text_that_looks_like_an_option_is_only_ever_text(running, commands, text):
    app, client = running
    page = client.get(listen(client, text)).text
    assert f"<p>Said: {text}</p>" in page
    say = logged(commands)[-1]
    assert say[-2:] == ["--", text]
    assert say.count("-o") == 1


def test_listen_with_javascript_answers_in_json(running):
    app, client = running
    response = client.post("/listen", data={"text": "Lanyon"}, headers=JSON)
    assert response.status_code == 202
    started = response.json()
    assert started["state"] in ("waiting", "saying", "done")
    listen_id = started["status_url"].split("/")[2]
    wait_until(app, listen_id, "done")
    status = client.get(started["status_url"]).json()
    assert status["state"] == "done"
    assert status["audio_url"] == f"/listen/{listen_id}/audio"
    assert status["said"] == ["Lanyon"]
    assert status["text"] == "Lanyon"  # listen.js names the player with it
    assert status["sentence"] == "Ready to play."
    assert client.get(status["audio_url"]).headers["content-type"] == "audio/wav"


def test_a_listen_that_fails_says_why(running):
    app, client = running
    page = client.get(listen(client, "fail now")).text
    assert "<title>Couldn&#39;t say it: fail now - Dogear</title>" in page
    assert "<strong>Error:</strong> The voice couldn&#39;t be loaded." in page
    assert "<strong>What to do:</strong> Run dogear download." in page
    listen_id = [i for i in app.state.listens._listens][-1]
    assert client.get(f"/listen/{listen_id}/audio").status_code == 404
    assert not (app.state.settings.listen_dir / f"{listen_id}.wav").exists()


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("", "Type a word or a sentence to say."),
        ("  ...  ", "Type a word or a sentence to say."),
        ("x" * (MAX_LISTEN_CHARS + 1), f"more than {MAX_LISTEN_CHARS} characters"),
        ("nul\x00here", "control characters"),
    ],
)
def test_what_cannot_be_said_is_refused(running, commands, text, why):
    app, client = running
    page = client.post("/listen", data={"text": text}, follow_redirects=False)
    assert page.status_code == 400
    assert why in page.text.replace("&#39;", "'")
    answer = client.post("/listen", data={"text": text}, headers=JSON)
    assert answer.status_code == 400
    assert why in answer.json()["error"]
    assert logged(commands) == []


def test_a_listen_never_waits_behind_a_book_being_made(running):
    app, client = running
    library = app.state.library
    book_id = add_book(
        library, app.state.settings.dir, state="ready", summary=sample_summary(), choices=CHOSEN
    )
    (library.folder(book_id) / "book.epub").write_text("slow")
    client.post(
        f"/books/{book_id}/choose",
        data={"format": "m4b", "voice": "af_heart", "speed": "0.9", "section": ["1", "2"]},
    )
    deadline = time.monotonic() + 30
    while library.get(book_id).state != "making":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    page = client.get(listen(client, "Poole")).text
    assert "<p>Said: Poole</p>" in page
    assert library.get(book_id).state == "making"
    client.post(f"/books/{book_id}/stop")


def test_listens_are_said_one_at_a_time_in_order(running, monkeypatch):
    app, client = running
    monkeypatch.setattr("dogear.web.listen_pages.WAIT_SECONDS", 0)
    first = listen(client, "slow one").rsplit("/", 1)[1]
    wait_until(app, first, "saying")
    second = listen(client, "Hyde").rsplit("/", 1)[1]
    status = client.get(f"/listen/{second}/status").json()
    assert (status["state"], status["sentence"]) == (
        "waiting",
        "Waiting: Dogear is saying 1 other thing first.",
    )
    page = client.get(f"/listen/{second}").text
    assert "<title>Getting it ready: Hyde - Dogear</title>" in page
    assert '<p id="status" class="status" role="status">Waiting: Dogear is saying 1' in page
    assert f'<a id="check-progress" href="/listen/{second}">Check progress</a>' in page
    assert '<script src="/static/progress.js" defer></script>' in page
    assert "<audio" not in page


def test_too_many_waiting_listens_are_refused(running, monkeypatch):
    app, client = running
    monkeypatch.setattr(listening, "MAX_WAITING", 2)
    monkeypatch.setattr("dogear.web.listen_pages.WAIT_SECONDS", 0)
    listen(client, "slow one")
    listen(client, "slow two")
    refused = client.post("/listen", data={"text": "Hyde"}, follow_redirects=False)
    assert refused.status_code == 503
    assert "still saying 2 other things" in refused.text


def test_only_the_latest_recordings_are_kept(running):
    app, client = running
    folder = app.state.settings.listen_dir
    ids = [listen(client, f"Word {n}").rsplit("/", 1)[1] for n in range(LISTENS_KEPT + 2)]
    assert sorted(p.stem for p in folder.iterdir()) == sorted(ids[2:])
    gone = client.get(f"/listen/{ids[0]}")
    assert gone.status_code == 404
    assert "This recording is gone" in gone.text
    assert client.get(f"/listen/{ids[0]}/audio").status_code == 404
    assert client.get(f"/listen/{ids[-1]}/audio").status_code == 200


def test_recordings_from_an_earlier_run_are_deleted_on_start(tmp_path):
    folder = tmp_path / "web" / "listen"
    folder.mkdir(parents=True)
    (folder / ("a" * 32 + ".wav")).write_bytes(b"RIFF")
    app = sample_app(tmp_path / "web")
    assert list(app.state.settings.listen_dir.iterdir()) == []
    if os.name != "nt":
        assert (folder.stat().st_mode & 0o777) == 0o700


def test_only_files_inside_the_listen_folder_are_served(tmp_path):
    app = sample_app(tmp_path / "web")
    listens: Listens = app.state.listens
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"secret")
    found = listens.add("Hyde", "/pronunciations")
    listens._listens[found.id] = listening.Listen(found.id, "Hyde", "/pronunciations", state="done")
    path = listens.folder / f"{found.id}.wav"
    if os.name != "nt":
        path.symlink_to(outside)
        assert listens.recording(found.id) is None
        path.unlink()
    path.write_bytes(b"RIFF")
    assert listens.recording(found.id) == path.resolve()
    for bad in ("../secret", "..%2Fsecret", found.id.upper(), found.id + "/../x", ""):
        assert listens.recording(bad) is None
    client = visitor(app)
    assert client.get("/listen/..%2F..%2Fsecret.wav/audio").status_code == 404


def test_listening_needs_the_access_cookie_and_dogears_own_forms(running):
    app, client = running
    listen_id = listen(client, "Hyde").rsplit("/", 1)[1]
    stranger = visitor(app, signed_in=False)
    for path in (
        f"/listen/{listen_id}",
        f"/listen/{listen_id}/status",
        f"/listen/{listen_id}/audio",
    ):
        response = stranger.get(path)
        assert response.status_code == 403
        assert "needs Dogear" in response.text
    assert stranger.post("/listen", data={"text": "Hyde"}).status_code == 403
    elsewhere = client.post(
        "/listen", data={"text": "Hyde"}, headers={"Origin": "http://evil.example"}
    )
    assert elsewhere.status_code == 403


def test_back_goes_only_to_dogears_own_pages():
    book = "/books/" + "A" * 16 + "/words"
    assert back_to(book) == book
    assert back_to(book + "#word-3") == book + "#word-3"
    assert back_to(book + "/all#word-55") == book + "/all#word-55"
    assert back_to("/pronunciations#try") == "/pronunciations#try"
    for elsewhere in (
        "https://evil.example/",
        "//evil.example/pronunciations",
        "/pronunciations/../help",
        "/books/x/words",
        book + "/other",
        book + "/all/../../../help",
        book + "#<script>",
        "",
    ):
        assert back_to(elsewhere) == "/pronunciations"


def test_a_books_listen_uses_the_books_voice_and_speed(running, commands):
    app, client = running
    book_id = add_book(
        app.state.library,
        app.state.settings.dir,
        state="done",
        summary=sample_summary(),
        choices=replace(CHOSEN, voice="bf_emma", speed=1.1),
    )
    page = client.get(listen(client, "Hyde", book=book_id, back=f"/books/{book_id}/words#word-1"))
    assert f'<a href="/books/{book_id}/words#word-1">Back to the words</a>' in page.text
    say = logged(commands)[-1]
    assert say[say.index("--voice") + 1] == "bf_emma"
    assert say[say.index("--speed") + 1] == "1.1"
    listen(client, "Hyde", book="not-a-book")
    assert "--voice" not in logged(commands)[-1]
