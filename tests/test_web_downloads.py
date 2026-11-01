"""Downloads: only files inside a book's made folder, and folders as cached zips."""

import errno
import io
import os
import threading
import time
import zipfile

import pytest
from web_samples import CHOSEN, add_book, sample_app, sample_summary, visitor

from dogear.sizes import describe_size
from dogear.web import making
from dogear.web.books import Output
from dogear.web.downloads import NotAvailable, output_path, zip_folder


@pytest.fixture
def app(tmp_path):
    return sample_app(tmp_path / "web")


def done_book(app, tmp_path, outputs):
    return add_book(
        app.state.library,
        tmp_path,
        state="done",
        choices=CHOSEN,
        summary=sample_summary(),
        outputs=outputs,
    )


@pytest.mark.parametrize(
    "path",
    ["book.epub", "book.json", "made/../book.json", "../../access-key.txt", "made", "/etc/passwd"],
)
def test_nothing_outside_the_made_folder_is_served(app, tmp_path, path):
    book_id = done_book(app, tmp_path, [Output("m4b", path)])
    folder = app.state.library.folder(book_id)
    with pytest.raises(NotAvailable):
        output_path(Output("m4b", path), folder)
    response = visitor(app).get(f"/books/{book_id}/files/0")
    assert response.status_code == 404
    assert "<h1>Page not found</h1>" in response.text


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need extra rights on Windows")
def test_a_link_out_of_the_made_folder_is_not_followed(app, tmp_path):
    book_id = done_book(app, tmp_path, [Output("m4b", "made/link.m4b")])
    made = app.state.library.folder(book_id) / "made"
    made.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    (made / "link.m4b").symlink_to(secret)
    assert visitor(app).get(f"/books/{book_id}/files/0").status_code == 404


def test_only_numbered_outputs_of_finished_books_are_served(app, tmp_path):
    book_id = done_book(app, tmp_path, [])
    client = visitor(app)
    assert client.get(f"/books/{book_id}/files/0").status_code == 404
    assert client.get(f"/books/{book_id}/files/-1").status_code == 404
    assert client.get(f"/books/{book_id}/files/x").status_code == 404
    assert client.get("/books/..%2F..%2Fx/files/0").status_code == 404


def test_a_folder_is_zipped_once_without_compression(app, tmp_path):
    book_id = done_book(app, tmp_path, [Output("daisy", "made/Book DAISY")])
    book_folder = app.state.library.folder(book_id)
    daisy = book_folder / "made" / "Book DAISY"
    (daisy / "sub").mkdir(parents=True)
    (daisy / "ncc.html").write_text("<html/>")
    (daisy / "sub" / "a.mp3").write_bytes(b"a" * 5000)
    (daisy / ".dogear.json").write_text("{}")  # Dogear's own record: not for the listener
    first = zip_folder(daisy, book_folder, "a1")
    assert first == book_folder / "zips" / "a1" / "Book DAISY.zip"
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["Book DAISY/ncc.html", "Book DAISY/sub/a.mp3"]
        assert {i.compress_type for i in archive.infolist()} == {zipfile.ZIP_STORED}
    made_at = first.stat().st_mtime_ns
    assert zip_folder(daisy, book_folder, "a1").stat().st_mtime_ns == made_at
    assert [p.name for p in first.parent.iterdir()] == ["Book DAISY.zip"]  # nothing partial
    app.state.library.store.update(book_id, run="a1")
    response = visitor(app).get(f"/books/{book_id}/files/0")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.content == first.read_bytes()


def mp3_book(app, tmp_path, run: str, content: bytes) -> str:
    book_id = done_book(app, tmp_path, [Output("mp3", "made/Book MP3")])
    app.state.library.store.update(book_id, run=run)
    mp3 = app.state.library.folder(book_id) / "made" / "Book MP3"
    mp3.mkdir(parents=True)
    (mp3 / "01.mp3").write_bytes(content)
    return book_id


def zipped(response) -> bytes:
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return archive.read("Book MP3/01.mp3")


def test_a_zip_of_an_earlier_make_is_never_sent_for_a_later_one(app, tmp_path):
    """A zip the last make left (Windows can't delete one being downloaded) is kept apart
    by the make it came from."""
    book_id = mp3_book(app, tmp_path, "aaaa", b"first make")
    client = visitor(app)
    assert zipped(client.get(f"/books/{book_id}/files/0")) == b"first make"
    folder = app.state.library.folder(book_id)
    (folder / "made" / "Book MP3" / "01.mp3").write_bytes(b"second make")
    app.state.library.store.update(book_id, run="bbbb")
    assert zipped(client.get(f"/books/{book_id}/files/0")) == b"second make"


def test_a_download_waiting_while_the_book_is_made_again_zips_nothing(app, tmp_path):
    """The download checked that the book was done, then waited for another zip. A new
    make started meanwhile: the folder is being rewritten, so nothing is zipped."""
    book_id = mp3_book(app, tmp_path, "aaaa", b"first make")
    store = app.state.library.store
    client = visitor(app)
    results = []
    with store.made_lock(book_id):  # another download is zipping it
        waiting = threading.Thread(
            target=lambda: results.append(client.get(f"/books/{book_id}/files/0"))
        )
        waiting.start()
        time.sleep(0.3)  # it has checked "done", and waits for the lock
        assert results == []
        store.update(book_id, state="making", run="bbbb")  # what a new make does first
    waiting.join(10)
    assert results[0].status_code == 404
    assert not (app.state.library.folder(book_id) / "zips").exists()


def test_a_make_waits_for_a_zip_being_made(app, tmp_path, monkeypatch):
    """A new make doesn't start rewriting the outputs while they're being zipped."""
    started = []

    def no_child(command):
        started.append(command)
        raise OSError(errno.ENOENT, "no child in this test")

    monkeypatch.setattr(making, "start_lines", no_child)
    book_id = mp3_book(app, tmp_path, "aaaa", b"first make")
    library = app.state.library
    library.store.update(book_id, state="stopped")
    library.start()
    try:
        with library.store.made_lock(book_id):  # a download is zipping it
            library.start_again(book_id)
            time.sleep(0.3)
            assert library.get(book_id).state == "waiting"
            assert started == []
        deadline = time.monotonic() + 10
        while not started:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert library.get(book_id).run != "aaaa"
    finally:
        library.close()


def test_a_missing_output_is_said_not_skipped(app, tmp_path):
    book_id = done_book(app, tmp_path, [Output("m4b", "made/Gone.m4b")])
    page = visitor(app).get(f"/books/{book_id}").text
    assert "M4B audiobook: Gone.m4b is no longer in its folder." in page


@pytest.mark.parametrize(
    ("size", "words"),
    [
        (1, "1 byte"),
        (999, "999 bytes"),
        (2048, "2 KB"),
        (3_000_000, "2.9 MB"),
        (121_634_816, "116 MB"),
        (1_288_490_189, "1.2 GB"),
    ],
)
def test_sizes_in_words(size, words):
    assert describe_size(size) == words
