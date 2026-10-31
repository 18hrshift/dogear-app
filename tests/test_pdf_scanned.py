"""A scanned PDF read with OCR by itself, as any command that reads a book does: through
`dogear outline`, stopped and carried on, reused, turned off, and what it says when
Tesseract or a language pack is missing. Tests that need the real Tesseract say so."""

import copy

import pytest
from conftest import require_tool
from pdf_builder import novel, scanned, scanned_words
from pdfplumber.utils.exceptions import PdfminerException
from pypdf import PdfReader, PdfWriter
from test_ocr_cache import TESSERACT, FakeOcr

from dogear import cli
from dogear.document import PageMarker
from dogear.errors import (
    ExtractionError,
    OcrLanguageError,
    ScannedPdfError,
    ToolFailedError,
    ToolNotFoundError,
)
from dogear.extract import extract_book, pdf
from dogear.extract.pdf import AUTO_OCR_OFF, OCR_HINT
from dogear.ocr import cache, page
from dogear.ocr.cache import ALREADY_READ

FAST = "[ocr]\ndpi = 150\n"  # Tesseract reads these big, clear pages well at 150


@pytest.fixture
def fake(monkeypatch):
    ocr = FakeOcr()
    monkeypatch.setattr(cache, "ocr_page", ocr)
    monkeypatch.setattr(cache, "find_tesseract", lambda config, settings: TESSERACT)
    return ocr


@pytest.fixture
def scanned_novel(tmp_path):
    """Two chapters, a page each, scanned."""
    text = novel(tmp_path / "novel-text.pdf", paragraphs=4, running_header=False)
    return scanned(text, tmp_path / "novel.pdf", dpi=200)


def fast_settings(tmp_path):
    path = tmp_path / "fast.toml"
    path.write_text(FAST)
    return path


def test_outline_reads_a_scanned_pdf_with_ocr_and_reuses_it(
    tmp_path, scanned_novel, capsys, monkeypatch
):
    require_tool("tesseract")
    settings = fast_settings(tmp_path)
    assert cli.main(["outline", str(scanned_novel), "--config", str(settings)]) == 0
    out, err = capsys.readouterr()
    assert "Title: novel" in out  # from the scanned file's own name
    assert "2. The Pool of Tears: main text" in out
    assert err.splitlines()[0] == (
        "This PDF is scanned, so Dogear is reading its 3 pages with OCR, which takes a "
        "while: often a few seconds a page."
    )
    assert "Finished reading 3 pages with OCR." in err

    read = []
    monkeypatch.setattr(cache, "ocr_page", lambda *args, **kwargs: read.append(args))
    assert cli.main(["outline", str(scanned_novel), "--config", str(settings)]) == 0
    again, err = capsys.readouterr()
    assert again == out
    assert err.splitlines() == [ALREADY_READ]
    assert read == []


def test_a_read_stopped_at_page_two_carries_on_from_page_two(
    tmp_path, scanned_novel, capsys, monkeypatch
):
    require_tool("tesseract")
    settings = fast_settings(tmp_path)
    real = cache.ocr_page
    read = []
    stopped = []

    def stop_at_page_two(document, index, *args, **kwargs):
        if index == 1 and not stopped:
            stopped.append(index)
            raise KeyboardInterrupt  # Ctrl+C
        read.append(index)
        real(document, index, *args, **kwargs)

    monkeypatch.setattr(cache, "ocr_page", stop_at_page_two)
    assert cli.main(["text", str(scanned_novel), "--config", str(settings)]) == 130
    err = capsys.readouterr().err
    assert "Stopped reading with OCR at page 2 of 3." in err
    assert err.splitlines()[-1] == "Stopped."
    assert read == [0]

    assert cli.main(["text", str(scanned_novel), "--config", str(settings)]) == 0
    out, err = capsys.readouterr()
    assert "Dogear read 1 of the 3 pages before, so it carries on from page 2." in err
    assert read == [0, 1, 2]
    assert "Alice was beginning to get very tired" in out


def test_with_auto_ocr_off_a_scanned_pdf_stops_as_before(tmp_path, config, fake):
    scan = scanned_words(tmp_path / "scan.pdf", ["ONE"], dpi=20)
    config["pdf"]["auto_ocr"] = False
    with pytest.raises(ScannedPdfError) as caught:
        extract_book(scan, config)
    assert "is probably scanned page images" in caught.value.message
    assert caught.value.hint == OCR_HINT
    assert fake.read == []
    config["pdf"]["auto_ocr"] = True
    assert extract_book(scan, config).sections
    assert fake.read == [0]


def test_without_tesseract_it_says_how_to_install_it_or_turn_ocr_off(tmp_path, config):
    scan = scanned_words(tmp_path / "scan.pdf", ["ONE"], dpi=20)
    config["tools"]["tesseract"] = "no-such-tesseract"
    with pytest.raises(ToolNotFoundError) as caught:
        extract_book(scan, config)
    assert caught.value.message == (
        "This PDF is scanned, and reading it needs Tesseract OCR. tools.tesseract is set to "
        "'no-such-tesseract', but that program wasn't found."
    )
    assert "Install Tesseract OCR" in caught.value.hint
    assert caught.value.hint.endswith(AUTO_OCR_OFF)
    assert "auto_ocr = false under [pdf]" in AUTO_OCR_OFF


def test_a_missing_language_pack_is_named(tmp_path, config):
    require_tool("tesseract")
    scan = scanned_words(tmp_path / "scan.pdf", ["ONE"], dpi=20)
    config["ocr"]["language"] = "qqq"
    with pytest.raises(OcrLanguageError, match="language pack for 'qqq'"):
        extract_book(scan, config)


def test_a_page_tesseract_cannot_read_is_named(tmp_path, config, monkeypatch):
    require_tool("tesseract")
    scan = scanned_words(tmp_path / "scan.pdf", ["ONE", "TWO"], dpi=20)
    real = page.read_image
    calls = []

    def second_fails(tesseract, image, out_base, **options):
        calls.append(image)
        if len(calls) == 2:
            raise ToolFailedError("tesseract failed (exit code 1):\nError in pixReadMem")
        return real(tesseract, image, out_base, **options)

    monkeypatch.setattr(page, "read_image", second_fails)
    with pytest.raises(ToolFailedError) as caught:
        extract_book(scan, config)
    assert caught.value.message.startswith("Tesseract couldn't read page 2. tesseract failed")
    assert caught.value.hint.startswith("Check that page 2 shows properly in a PDF reader.")


def test_a_scan_with_no_words_on_it_says_so(tmp_path, config, fake):
    scan = scanned_words(tmp_path / "blank.pdf", ["", ""], dpi=20)
    fake.text = ""
    with pytest.raises(ExtractionError) as caught:
        extract_book(scan, config)
    assert caught.value.message == (
        "Dogear read this scanned PDF with OCR, but found almost no text (about 0 "
        "characters a page)."
    )


def test_kept_text_that_cant_be_read_blames_the_cache_not_the_pdf(
    tmp_path, config, fake, monkeypatch
):
    """Before, the error said the PDF was damaged, and the same text was used every time."""
    scan = scanned_words(tmp_path / "scan.pdf", ["ONE", "TWO"], dpi=20)
    layer = cache.text_layer(scan, config, print)
    real = pdf._pages

    def damaged(path, name, labels, settings):
        if path == layer:
            raise PdfminerException("bad content stream")
        return real(path, name, labels, settings)

    monkeypatch.setattr(pdf, "_pages", damaged)
    with pytest.raises(ExtractionError) as caught:
        extract_book(scan, config)
    assert caught.value.message == (
        "The text Dogear read from scan.pdf with OCR was damaged in its cache folder "
        "(bad content stream)."
    )
    assert caught.value.hint == (
        "The damage is in Dogear's own copy of the text, not in scan.pdf. Run the same "
        "command again: Dogear reads the PDF with OCR again."
    )
    assert not layer.exists()
    monkeypatch.setattr(pdf, "_pages", real)
    assert extract_book(scan, config).sections
    assert fake.read == [0, 1, 0, 1]


def test_the_title_bookmarks_and_page_numbers_come_from_the_scan(tmp_path, config, fake):
    scan = scanned_words(tmp_path / "scan.pdf", ["ONE", "TWO", "THREE", "FOUR"], dpi=20)
    writer = PdfWriter(clone_from=PdfReader(scan))
    writer.add_metadata({"/Title": "A Scanned Book", "/Author": "Ann Author"})
    writer.add_outline_item("Opening", 0)
    writer.add_outline_item("Middle", 2)
    writer.set_page_label(0, 3, style="/r")  # i to iv
    with (tmp_path / "labelled.pdf").open("wb") as file:
        writer.write(file)
    book = extract_book(tmp_path / "labelled.pdf", copy.deepcopy(config))
    assert book.metadata.title == "A Scanned Book"
    assert book.metadata.authors == ["Ann Author"]
    assert [section.title for section in book.sections] == ["Opening", "Middle"]
    assert book.source_path == tmp_path / "labelled.pdf"
    labels = [b.label for s in book.sections for b in s.blocks if isinstance(b, PageMarker)]
    assert labels == ["i", "ii", "iii", "iv"]
