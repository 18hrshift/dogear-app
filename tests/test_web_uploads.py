"""Uploading a book: what is accepted, the size limit as the file streams in, and where
the file ends up."""

import errno
import os

import lxml.html
import pytest
from web_samples import sample_app, sample_config, visitor

from dogear.web import uploads
from dogear.web.books import RECORD, BookStore


@pytest.fixture
def app(tmp_path):
    config = sample_config(tmp_path)
    config["web"]["max_upload_mb"] = 1
    return sample_app(tmp_path, config)


def books(app):
    return app.state.library.books()


def incoming(app):
    folder = app.state.settings.incoming_dir
    return sorted(folder.iterdir()) if folder.exists() else []


def test_a_book_is_saved_under_a_new_name_in_its_own_folder(app):
    client = visitor(app)
    response = client.post(
        "/books",
        files={"book": ("My Book.EPUB", b"ok, a book", "application/epub+zip")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    [book] = books(app)
    assert response.headers["location"] == f"/books/{book.id}"
    assert book.original_name == "My Book.EPUB"
    assert book.extension == ".epub"
    assert book.state == "reading"
    folder = app.state.library.folder(book.id)
    assert sorted(p.name for p in folder.iterdir()) == ["book.epub", RECORD]
    assert (folder / "book.epub").read_bytes() == b"ok, a book"
    assert incoming(app) == []


def test_the_original_name_is_shown_escaped(app):
    client = visitor(app)
    client.post("/books", files={"book": ('<b>bold & "fun".pdf', b"ok", "application/pdf")})
    home = client.get("/").text
    assert "&lt;b&gt;bold &amp; &#34;fun&#34;.pdf" in home
    assert "<b>bold" not in home


def test_a_path_in_the_name_is_dropped(app):
    client = visitor(app)
    client.post("/books", files={"book": ("..\\..\\evil/../x.epub", b"ok", "")})
    [book] = books(app)
    assert book.original_name == "x.epub"


@pytest.mark.parametrize("name", ["notes.txt", "book.epub.exe", "README", "book.doc", "x.odt"])
def test_only_books_dogear_reads_are_accepted(app, name):
    response = visitor(app).post("/books", files={"book": (name, b"ok", "text/plain")})
    assert response.status_code == 400
    assert "<title>Error: Your books - Dogear</title>" in response.text
    assert "Choose an EPUB, PDF, Word (DOCX), HTML, Markdown, MOBI, or AZW3 file." in response.text
    assert 'aria-describedby="book-file-hint book-file-error"' in response.text
    assert books(app) == []
    assert incoming(app) == []


@pytest.mark.parametrize(
    "name", ["Story.MOBI", "Notes.DOCX", "page.html", "page.htm", "doc.xhtml", "a.md"]
)
def test_every_kind_dogear_reads_is_accepted(app, name):
    response = visitor(app).post("/books", files={"book": (name, b"ok", "")})
    assert response.status_code == 200  # followed to the book's page
    [book] = books(app)
    assert book.original_name == name


def test_the_help_page_names_every_kind(app):
    page = visitor(app).get("/help").text
    assert "Dogear reads EPUB, PDF, Word (DOCX), HTML, Markdown, MOBI, and AZW3 files." in page


def test_no_file_chosen_is_an_error(app):
    response = visitor(app).post("/books", files={"book": ("", b"", "application/octet-stream")})
    assert response.status_code == 400
    assert "Choose a book file to upload." in response.text


def test_an_empty_file_is_an_error(app):
    response = visitor(app).post("/books", files={"book": ("empty.epub", b"", "")})
    assert response.status_code == 400
    assert "empty.epub is empty." in response.text
    assert incoming(app) == []


def test_a_form_that_is_not_an_upload_is_an_error(app):
    response = visitor(app).post("/books", data={"book": "x"})
    assert response.status_code == 400
    assert books(app) == []


def test_a_file_over_the_limit_is_refused_and_not_kept(app):
    big = b"x" * (1024 * 1024 + 1)
    response = visitor(app).post("/books", files={"book": ("big.pdf", big, "application/pdf")})
    assert response.status_code == 413
    assert "bigger than 1 MB" in response.text
    assert books(app) == []
    assert incoming(app) == []


def test_the_limit_holds_when_the_size_is_not_declared(app):
    """Content-Length isn't trusted: a chunked upload is counted as it arrives."""
    boundary = "xyz"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="book"; filename="big.epub"\r\n'
        "Content-Type: application/epub+zip\r\n\r\n"
    ).encode()

    def body():
        yield head
        for _ in range(40):
            yield b"x" * 65536  # 2.5 MB in all
        yield f"\r\n--{boundary}--\r\n".encode()

    response = visitor(app).post(
        "/books",
        content=body(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert books(app) == []
    assert incoming(app) == []


def test_a_file_just_under_the_limit_is_accepted(app):
    small = b"ok" + b"x" * (1024 * 1024 - 2)
    response = visitor(app).post("/books", files={"book": ("a.pdf", small, "application/pdf")})
    assert response.status_code == 200  # followed to the book's page
    assert len(books(app)) == 1


def test_the_upload_form_names_the_kinds_and_the_limit(app):
    page = visitor(app).get("/").text
    assert "An EPUB, PDF, Word (DOCX), HTML, Markdown, MOBI, or AZW3 file, up to 1 MB." in page
    assert '<label for="book-file">Book file (required)</label>' in page


def test_the_file_picker_lets_every_kind_of_book_be_chosen(app):
    """No accept list: iPhones grey out files whose extension iOS has no type for, such
    as MOBI and AZW3, and the server checks the name instead."""
    [picker] = lxml.html.fromstring(visitor(app).get("/").text).xpath("//input[@type='file']")
    assert picker.get("accept") is None
    for name in ("book.mobi", "book.azw3", "book.AZW"):
        response = visitor(app).post("/books", files={"book": (name, b"ok", "")})
        assert response.status_code == 200, name  # followed to the book's page


class FailingFile:
    """A file being written that fails the way a full disk does."""

    def __init__(self, real, fail_after: int, error: int, on_close: bool) -> None:
        self.real = real
        self.room = fail_after
        self.error = error
        self.on_close = on_close

    def write(self, data: bytes) -> int:
        if not self.on_close and len(data) > self.room:
            raise OSError(self.error, os.strerror(self.error))
        self.room -= len(data)
        return self.real.write(data)

    def close(self) -> None:
        self.real.close()
        if self.on_close:
            raise OSError(self.error, os.strerror(self.error))


@pytest.mark.parametrize("on_close", [False, True])
def test_a_full_disk_says_so_and_keeps_nothing(app, monkeypatch, on_close):
    real_open = uploads._open_new
    monkeypatch.setattr(
        uploads,
        "_open_new",
        lambda path: FailingFile(real_open(path), 1000, errno.ENOSPC, on_close),
    )
    response = visitor(app).post("/books", files={"book": ("big.epub", b"x" * 300_000, "")})
    assert response.status_code == 507
    assert "<title>Error: Your books - Dogear</title>" in response.text
    assert "has no room left on its disk" in response.text
    assert "Free some space on that computer, then upload the book again." in response.text
    assert 'aria-describedby="book-file-hint book-file-error"' in response.text
    assert books(app) == []
    assert incoming(app) == []


def test_a_folder_dogear_cannot_write_to_says_so(app, monkeypatch):
    def refuse(path):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(uploads, "_open_new", refuse)
    response = visitor(app).post("/books", files={"book": ("a.epub", b"ok", "")})
    assert response.status_code == 500
    assert "Dogear isn&#39;t allowed to save files in its web folder" in response.text
    assert books(app) == []


def test_a_record_that_cannot_be_saved_leaves_no_book_behind(app, monkeypatch):
    def full(self, record):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(BookStore, "_save", full)
    response = visitor(app).post("/books", files={"book": ("a.epub", b"ok", "")})
    assert response.status_code == 507
    assert "has no room left on its disk" in response.text
    assert books(app) == []
    assert incoming(app) == []
    assert list(app.state.settings.books_dir.iterdir()) == []


def test_other_disk_errors_name_the_cause():
    error = uploads.disk_problem(OSError(errno.EIO, "Input/output error"))
    assert error.status_code == 500
    assert error.message.startswith(
        "Dogear couldn't save the book on the computer it runs on (Input/output error)."
    )
    assert uploads.disk_problem(OSError(errno.EFBIG, "File too large")).status_code == 507
