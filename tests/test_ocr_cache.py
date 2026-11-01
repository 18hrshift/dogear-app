"""Reading a scanned PDF with OCR into the cache folder: each page once, carrying on after
a stop, reused afterwards, and one run at a time. Tesseract is stood in for here, so these
run anywhere; tests/test_pdf_scanned.py runs the real one."""

import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path

import pdfplumber
import pytest
from pdf_builder import scanned_words
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from dogear.errors import ExtractionError
from dogear.file_lock import FileLock
from dogear.ocr import cache
from dogear.ocr.cache import ALREADY_READ, cache_key, discard, text_layer
from dogear.ocr.settings import OcrSettings
from dogear.ocr.tesseract import Tesseract

TESSERACT = Tesseract("tesseract", "tesseract 5.3.4")
# What the stand-in finds on page n: enough words for a page of text.
PAGE_TEXT = "Words on page {n}, and more after them, so the page has text to read."


class FakeOcr:
    """Stands in for ocr_page: writes a one-page PDF saying which page it is. With
    `stop_at`, a Ctrl+C comes while that page (from 0) is being read."""

    def __init__(self, stop_at: int | None = None, text: str = PAGE_TEXT) -> None:
        self.read: list[int] = []
        self.stop_at = stop_at
        self.text = text

    def __call__(self, document, index, output, tesseract, settings, *, text_only):
        assert text_only  # the picture of the page isn't kept
        assert tesseract == TESSERACT
        if index == self.stop_at:
            raise KeyboardInterrupt
        self.read.append(index)
        canvas = Canvas(str(output), pagesize=letter)
        canvas.setFont("Helvetica", 12)
        canvas.drawString(72, 700, self.text.format(n=index + 1))
        canvas.save()


@pytest.fixture
def fake(monkeypatch):
    ocr = FakeOcr()
    monkeypatch.setattr(cache, "ocr_page", ocr)
    monkeypatch.setattr(cache, "find_tesseract", lambda config, settings: TESSERACT)
    return ocr


@pytest.fixture
def scan(tmp_path):
    return scanned_words(tmp_path / "scan.pdf", ["ONE", "TWO", "THREE"], dpi=20)


@pytest.fixture
def config(config, tmp_path):
    config["render"]["cache_dir"] = str(tmp_path / "cache")
    return config


def texts(path):
    with pdfplumber.open(path) as pdf:
        return [(page.extract_text() or "").strip() for page in pdf.pages]


def folder_of(scan, config):
    key = cache_key(scan, OcrSettings.from_config(config), TESSERACT)
    return Path(config["render"]["cache_dir"]) / "ocr" / key


def test_each_page_is_read_once_and_the_text_is_kept(tmp_path, fake, scan, config):
    lines = []
    layer = text_layer(scan, config, lines.append)
    assert layer.parent.parent == tmp_path / "cache" / "ocr"
    assert texts(layer) == [PAGE_TEXT.format(n=n) for n in (1, 2, 3)]
    assert fake.read == [0, 1, 2]
    assert lines[0] == (
        "This PDF is scanned, so Dogear is reading its 3 pages with OCR, which takes a "
        "while: often a few seconds a page."
    )
    assert lines[1].startswith("Read 1 of 3 pages with OCR.")
    assert lines[-1] == "Finished reading 3 pages with OCR."
    assert sorted(p.name for p in layer.parent.iterdir()) == ["in-use.lock", "text-layer.pdf"]

    lines.clear()
    assert text_layer(scan, config, lines.append) == layer
    assert fake.read == [0, 1, 2]  # no OCR at all the second time
    assert lines == [ALREADY_READ]


def test_a_stopped_read_carries_on_from_the_page_it_stopped_at(fake, scan, config):
    lines = []
    fake.stop_at = 1
    with pytest.raises(KeyboardInterrupt):
        text_layer(scan, config, lines.append)
    assert fake.read == [0]
    assert lines[-1] == (
        "Stopped reading with OCR at page 2 of 3. The pages read so far are kept, so running "
        "the same command again carries on from there."
    )

    lines.clear()
    fake.stop_at = None
    layer = text_layer(scan, config, lines.append)
    assert fake.read == [0, 1, 2]
    assert lines[1] == "Dogear read 1 of the 3 pages before, so it carries on from page 2."
    assert lines[2].startswith("Read 2 of 3 pages with OCR.")
    assert texts(layer)[1] == PAGE_TEXT.format(n=2)


def test_selected_pages_resume_and_have_their_own_cache(fake, scan, config):
    fake.stop_at = 2
    with pytest.raises(KeyboardInterrupt):
        text_layer(scan, config, print, pages=(0, 2))
    assert fake.read == [0]

    fake.stop_at = None
    selected = text_layer(scan, config, print, pages=(0, 2))
    assert fake.read == [0, 2]
    assert texts(selected) == [PAGE_TEXT.format(n=n) for n in (1, 3)]
    assert text_layer(scan, config, print, pages=(0, 2)) == selected
    assert fake.read == [0, 2]

    other = text_layer(scan, config, print, pages=(1,))
    whole = text_layer(scan, config, print)
    assert len({selected, other, whole}) == 3
    assert fake.read == [0, 2, 1, 0, 1, 2]
    assert texts(other) == [PAGE_TEXT.format(n=2)]


def test_the_cache_key_covers_the_file_the_ocr_settings_and_tesseract(scan, config, tmp_path):
    settings = OcrSettings.from_config(config)
    key = cache_key(scan, settings, TESSERACT)
    assert cache_key(scan, settings, TESSERACT) == key
    copy = tmp_path / "copy.pdf"
    shutil.copyfile(scan, copy)
    assert cache_key(copy, settings, TESSERACT) == key  # the bytes count, not the name
    changed = [
        cache_key(scan, replace(settings, language="eng+fra"), TESSERACT),
        cache_key(scan, replace(settings, dpi=200), TESSERACT),
        cache_key(scan, replace(settings, max_page_pixels=1), TESSERACT),
        cache_key(scan, replace(settings, max_page_side=1000), TESSERACT),
        cache_key(scan, settings, Tesseract("tesseract", "tesseract 5.4.0")),
        cache_key(scan, settings, Tesseract("tesseract", "tesseract 5.3.4", "eng=a new pack")),
    ]
    other = scanned_words(tmp_path / "other.pdf", ["ONE", "TWO", "FOUR"], dpi=20)
    changed.append(cache_key(other, settings, TESSERACT))
    assert len({key, *changed}) == 8


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("dpi", 200),
        ("language", "eng+fra"),
        ("max_page_megapixels", 50),
        ("max_page_side_pixels", 20000),
    ],
)
def test_changing_an_ocr_setting_reads_the_book_again(fake, scan, config, key, value):
    text_layer(scan, config, print)
    config["ocr"][key] = value
    text_layer(scan, config, print)
    assert fake.read == [0, 1, 2, 0, 1, 2]


def test_another_run_on_the_same_file_is_waited_for(fake, scan, config, monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "WAIT_POLL_SECONDS", 0.02)
    big = scanned_words(tmp_path / "big.pdf", [f"PAGE {n}" for n in range(12)], dpi=20)
    folder = folder_of(big, config)
    folder.mkdir(parents=True)
    other = FileLock.try_acquire(folder / "in-use.lock")  # another run, reading the book
    lines = []
    found = []
    waiter = threading.Thread(target=lambda: found.append(text_layer(big, config, lines.append)))
    waiter.start()
    try:
        until(lambda: lines)
        assert lines == [
            "Another Dogear is reading this PDF with OCR now. This one waits for it to "
            "finish, then uses the text it read."
        ]
        for n in range(1, 11):  # how far the other run has got
            (folder / f"page-{n:05d}.pdf").write_bytes(b"%PDF")
        until(lambda: len(lines) == 2)
        assert lines[1] == "The other Dogear has read 10 of 12 pages with OCR."
        shutil.copyfile(big, folder / "text-layer.pdf")  # it finished: a page for each page
    finally:
        other.release()
    waiter.join(10)
    assert found == [folder / "text-layer.pdf"]
    assert lines[-1] == ALREADY_READ
    assert fake.read == []


def until(condition, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_what_a_killed_run_left_behind_is_cleared(fake, scan, config, tmp_path):
    folder = folder_of(scan, config)
    (folder / "work-abc123").mkdir(parents=True)
    (folder / "work-abc123" / "page.png").write_bytes(b"picture")
    (folder / "text-layer.pdf.4242.part").write_bytes(b"half")
    text_layer(scan, config, print)
    assert sorted(p.name for p in folder.iterdir()) == ["in-use.lock", "text-layer.pdf"]


def test_a_page_damaged_in_the_cache_is_read_again(fake, scan, config, tmp_path):
    folder = folder_of(scan, config)
    folder.mkdir(parents=True)
    (folder / "page-00002.pdf").write_bytes(b"not a pdf")
    with pytest.raises(ExtractionError) as caught:
        text_layer(scan, config, print)
    assert caught.value.message.startswith(
        "The text Dogear read from page 2 with OCR was damaged in its cache folder"
    )
    assert caught.value.hint == "Try again: Dogear reads that page again."
    assert fake.read == [0, 2]
    layer = text_layer(scan, config, print)
    assert fake.read == [0, 2, 1]
    assert texts(layer)[1] == PAGE_TEXT.format(n=2)


@pytest.mark.parametrize(
    ("damage", "problem"),
    [
        (b"", "it couldn't be opened"),
        (b"%PDF-1.4 and then nothing", "it couldn't be opened"),
        ("one page", "it has 1 page, not 3"),
    ],
)
def test_a_text_layer_damaged_in_the_cache_is_made_again(
    fake, scan, config, tmp_path, damage, problem
):
    """Before, a damaged text-layer.pdf was used every time, and the error blamed the PDF."""
    layer = text_layer(scan, config, print)
    if damage == "one page":
        shutil.copyfile(scanned_words(tmp_path / "one.pdf", ["ONE"], dpi=20), layer)
    else:
        layer.write_bytes(damage)
    lines = []
    assert text_layer(scan, config, lines.append) == layer
    assert lines[0].startswith(
        "The text Dogear kept from reading this PDF with OCR before was damaged in its cache "
        f"folder ({problem}"
    )
    assert lines[0].endswith("), so Dogear makes it again.")
    assert fake.read == [0, 1, 2, 0, 1, 2]
    assert texts(layer) == [PAGE_TEXT.format(n=n) for n in (1, 2, 3)]


def test_a_damaged_text_layer_is_made_again_from_the_pages_still_kept(fake, scan, config):
    fake.stop_at = 2
    with pytest.raises(KeyboardInterrupt):
        text_layer(scan, config, print)
    folder = folder_of(scan, config)
    (folder / "text-layer.pdf").write_bytes(b"not a pdf")  # as if damaged after a join
    fake.stop_at = None
    layer = text_layer(scan, config, print)
    assert fake.read == [0, 1, 2]  # pages 1 and 2 came from the cache folder
    assert texts(layer) == [PAGE_TEXT.format(n=n) for n in (1, 2, 3)]


def test_a_damaged_text_layer_is_discarded_unless_another_run_is_working(fake, scan, config):
    layer = text_layer(scan, config, print)
    other = FileLock.try_acquire(layer.parent / "in-use.lock")
    try:
        assert not discard(layer)  # it may be making it again already
        assert layer.exists()
    finally:
        other.release()
    assert discard(layer)
    assert not layer.exists()
