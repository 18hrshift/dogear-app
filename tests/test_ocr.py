"""`dogear ocr`, and what it shares with reading a scanned PDF by itself: reading one page
with Tesseract, the checks before it, and the progress sentences."""

import copy
import logging
import re
from hashlib import sha256
from pathlib import Path

import pdfplumber
import pytest
from conftest import require_tool
from pdf_builder import scanned_words
from PIL import Image
from pypdf import PdfWriter
from test_ocr_cache import TESSERACT

from dogear import cli
from dogear.errors import (
    ConfigError,
    DogearError,
    ExtractionError,
    OcrLanguageError,
    ToolNotFoundError,
)
from dogear.ocr import default_output_path, ocr_pdf, page
from dogear.ocr.page import open_pdf, page_dpi, page_pixels
from dogear.ocr.progress import OcrProgress
from dogear.ocr.settings import MIN_DPI, OcrSettings
from dogear.ocr.tesseract import (
    NO_FOLDER,
    LanguageListing,
    find_tesseract,
    language_data,
    read_listing,
)

WORDS = ["ACCESSIBLE BOOKS", "LISTEN CLOSELY"]


def text_of(path):
    with pdfplumber.open(str(path)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def images_of(path):
    with pdfplumber.open(str(path)) as pdf:
        return [len(page.images) for page in pdf.pages]


def test_ocr_adds_a_text_layer_over_each_page(tmp_path, config):
    require_tool("tesseract")
    scan = scanned_words(tmp_path / "scan.pdf", WORDS)
    assert all(not t.strip() for t in text_of(scan))

    lines = []
    out = tmp_path / "scan_ocr.pdf"
    assert ocr_pdf(scan, out, config, lines.append) == 2

    pages = text_of(out)
    assert len(pages) == 2
    for page_text, words in zip(pages, WORDS, strict=True):
        assert words in page_text.upper()
    assert images_of(out) == [1, 1]  # the pictures of the pages stay, under the text
    assert lines[0] == (
        "Reading the 2 pages of scan.pdf with OCR, which takes a while: often a few seconds a page."
    )
    assert lines[1].startswith("Read 1 of 2 pages with OCR.")
    assert lines[2] == "Finished reading 2 pages with OCR."


def test_the_ocr_command_saves_next_to_the_scan(tmp_path, capsys):
    require_tool("tesseract")
    scan = scanned_words(tmp_path / "scan.pdf", WORDS[:1])
    assert cli.main(["ocr", str(scan), "--dpi", "100"]) == 0
    out = tmp_path / "scan_ocr.pdf"
    assert WORDS[0] in text_of(out)[0].upper()
    assert f"Saved 1 page with a text layer to {out}" in capsys.readouterr().out


def test_refuses_to_overwrite_input(tmp_path, config):
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(b"%PDF-1.4")
    with pytest.raises(DogearError, match="overwrite"):
        ocr_pdf(scan, scan, config, print)


def test_damaged_pdf_is_an_extraction_error(tmp_path, config):
    require_tool("tesseract")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(ExtractionError, match="Couldn't open"):
        ocr_pdf(bad, tmp_path / "out.pdf", config, print)


def test_missing_tesseract_names_the_fix(tmp_path, config):
    custom = copy.deepcopy(config)
    custom["tools"]["tesseract"] = "no-such-tesseract"
    with pytest.raises(ToolNotFoundError) as err:
        ocr_pdf(tmp_path / "a.pdf", tmp_path / "b.pdf", custom, print)
    assert "no-such-tesseract" in err.value.message


def test_a_missing_language_pack_is_named_with_how_to_install_it(config):
    require_tool("tesseract")
    config["ocr"]["language"] = "eng+qqq"
    with pytest.raises(OcrLanguageError) as err:
        find_tesseract(config, OcrSettings.from_config(config))
    assert "language pack for 'qqq'" in err.value.message
    assert "sudo apt install tesseract-ocr-qqq" in err.value.hint
    assert "ocr.language" in err.value.hint


def test_the_installed_languages_pass(config):
    require_tool("tesseract")
    tesseract = find_tesseract(config, OcrSettings.from_config(config))
    assert tesseract.version.startswith("tesseract")
    assert re.fullmatch(r"eng=[0-9a-f]{64}", tesseract.language_data)


def test_the_language_listing_names_its_folder_when_tesseract_says():
    five = 'List of available languages in "/usr/share/tessdata/" (2):\neng\nosd\n'
    assert read_listing(five) == LanguageListing(Path("/usr/share/tessdata/"), {"eng", "osd"})
    older = "List of available languages (2):\neng\nosd\n"
    assert read_listing(older) == LanguageListing(None, {"eng", "osd"})


def test_the_language_data_is_each_files_sha256(tmp_path):
    (tmp_path / "eng.traineddata").write_bytes(b"one")
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "Latin.traineddata").write_bytes(b"two")
    listing = LanguageListing(tmp_path, {"eng", "script/Latin", "fra"})
    found = language_data(listing, ["eng", "script/Latin"])
    assert found == f"eng={sha256(b'one').hexdigest()}+script/Latin={sha256(b'two').hexdigest()}"
    (tmp_path / "eng.traineddata").write_bytes(b"a newer pack")
    assert language_data(listing, ["eng", "script/Latin"]) != found
    assert language_data(listing, ["fra"]) == "fra=unreadable"
    assert language_data(LanguageListing(None, {"eng"}), ["eng"]) == NO_FOLDER


def test_another_language_data_folder_changes_what_tesseract_is_keyed_by(
    config, tmp_path, monkeypatch
):
    """A new eng.traineddata, or TESSDATA_PREFIX naming another folder, reads a scanned
    book again: before, only `tesseract --version` was in the cache key."""
    require_tool("tesseract")
    settings = OcrSettings.from_config(config)
    usual = find_tesseract(config, settings).language_data
    (tmp_path / "eng.traineddata").write_bytes(b"not really a language pack")
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    other = find_tesseract(config, settings).language_data
    assert other == f"eng={sha256(b'not really a language pack').hexdigest()}"
    assert other != usual


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("language", "eng; rm -rf", "Tesseract language codes"),
        ("language", "", "Tesseract language codes"),
        ("dpi", 50, "at least 70"),
        ("max_page_megapixels", 0, "more than 0"),
        ("max_page_side_pixels", 0, "from 1 to 32,767"),
        ("max_page_side_pixels", 40000, "from 1 to 32,767"),
    ],
)
def test_ocr_settings_are_checked(config, key, value, message):
    config["ocr"][key] = value
    with pytest.raises(ConfigError, match=message):
        OcrSettings.from_config(config)


def test_a_huge_page_is_drawn_at_a_lower_resolution(config):
    settings = OcrSettings.from_config(config)
    letter = (612, 792)
    assert page_dpi(*letter, settings) == 300
    poster = (72 * 100, 72 * 100)  # 100 inches square
    assert page_dpi(*poster, settings) == 100  # 10,000 by 10,000 pixels
    config["ocr"]["max_page_megapixels"] = 2
    smaller = OcrSettings.from_config(config)
    assert smaller.max_page_pixels == 2_000_000
    assert page_dpi(*letter, smaller) == 146  # 1,241 by 1,606 pixels


def test_a_long_page_is_drawn_no_longer_than_the_side_limit(config):
    settings = OcrSettings.from_config(config)
    scroll = (612, 72 * 100)  # 8.5 inches by 100
    assert page_dpi(*scroll, settings) == 300
    assert page_pixels(*scroll, 300) == (2550, 30001)  # rounded up, as pdfium does
    config["ocr"]["max_page_side_pixels"] = 15000
    shorter = OcrSettings.from_config(config)
    assert page_pixels(*scroll, 150) == (1275, 15001)  # one pixel too long...
    assert page_dpi(*scroll, shorter) == 149  # ...so one dpi less
    assert page_pixels(*scroll, 149) == (1267, 14901)


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (612, 792),
        (72 * 40, 72 * 40),
        (0.5, 4e7),  # 2 by 88.9 million pixels before, from rounding the width up
        (1.01 * 72 / 300, 72 * 100),
        (3e7, 0.01),
        (72 * 140, 72 * 140),
        (72 * 109, 72 * 109),
    ],
)
def test_the_picture_pdfium_draws_is_never_over_either_limit(config, width, height):
    """Counted the way pdfium makes it, each side rounded up, at the highest resolution
    that fits, and never under the least Tesseract reads."""
    for megapixels in (100, 2):
        config["ocr"]["max_page_megapixels"] = megapixels
        settings = OcrSettings.from_config(config)
        dpi = page_dpi(width, height, settings)
        if dpi is None:
            over = page_pixels(width, height, MIN_DPI)
            assert max(over) > settings.max_page_side or over[0] * over[1] > megapixels * 1e6
            continue
        assert MIN_DPI <= dpi <= settings.dpi
        w, h = page_pixels(width, height, dpi)
        assert max(w, h) <= settings.max_page_side and w * h <= settings.max_page_pixels
        if dpi < settings.dpi:
            w, h = page_pixels(width, height, dpi + 1)
            assert max(w, h) > settings.max_page_side or w * h > settings.max_page_pixels


@pytest.mark.parametrize(
    ("width", "height", "inches"),
    [
        (3e6, 3e6, "41,666.7 by 41,666.7"),  # a 439-byte PDF drew a 1.7 GB picture before
        (1e8, 1e8, "1,388,888.9 by 1,388,888.9"),  # ran out of memory before
        (0.5, 4e7, "0 by 555,555.6"),
        (72 * 200, 72 * 200, "200 by 200"),  # would need 50 dpi
    ],
)
def test_a_page_too_big_to_draw_at_70_dpi_is_refused_by_number(
    tmp_path, config, monkeypatch, width, height, inches
):
    pdf = PdfWriter()
    pdf.add_blank_page(width=612, height=792)
    pdf.add_blank_page(width=width, height=height)
    path = tmp_path / "huge.pdf"
    pdf.write(path)
    monkeypatch.setattr(page, "_draw", lambda *args, **kwargs: pytest.fail("drawn"))
    settings = OcrSettings.from_config(config)
    document = open_pdf(path)
    try:
        with pytest.raises(ExtractionError) as error:
            page.ocr_page(document, 1, tmp_path / "page.pdf", TESSERACT, settings, text_only=True)
    finally:
        document.close()
    assert error.value.message.startswith(f"Page 2 is {inches} inches, too big to read with OCR")
    assert "less than 70 dpi, the least Tesseract reads" in error.value.message
    assert "ocr.max_page_megapixels" in error.value.hint
    assert not (tmp_path / "page.pdf").exists()


def test_a_page_over_the_size_limit_is_read_at_the_lower_resolution(
    tmp_path, config, monkeypatch, caplog
):
    """ocr.max_page_megapixels decides how big a picture a page makes, and Tesseract is
    told the resolution it was drawn at, so the text lands where it is printed."""
    scan = scanned_words(tmp_path / "scan.pdf", ["ONE"], dpi=20)
    seen = []

    def read_image(tesseract, image, out_base, *, dpi, settings, text_only):
        with Image.open(image) as picture:
            seen.append((dpi, picture.size, picture.mode))
        made = out_base.with_suffix(".pdf")
        made.write_bytes(b"%PDF-1.4 stand-in")
        return made

    monkeypatch.setattr(page, "read_image", read_image)
    document = open_pdf(scan)
    try:
        for megapixels in (100, 2):
            config["ocr"]["max_page_megapixels"] = megapixels
            settings = OcrSettings.from_config(config)
            with caplog.at_level(logging.WARNING, logger="dogear.ocr.page"):
                page.ocr_page(
                    document, 0, tmp_path / "page.pdf", TESSERACT, settings, text_only=True
                )
    finally:
        document.close()
    assert seen == [(300, (2550, 3301), "L"), (146, (1241, 1606), "L")]
    assert caplog.messages == [
        "Page 1 is 8.5 by 11 inches, too big to read at 300 dpi, so it is read at 146 dpi."
    ]
    assert (tmp_path / "page.pdf").read_bytes() == b"%PDF-1.4 stand-in"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("work-")] == []


def test_progress_comes_after_the_first_page_every_ten_and_at_the_end():
    lines = []
    now = [0.0]
    progress = OcrProgress(25, 0, lines.append, clock=lambda: now[0])
    for _ in range(25):
        now[0] += 6
        progress.page_done()
    assert lines == [
        "Read 1 of 25 pages with OCR. About 2 minutes left.",
        "Read 10 of 25 pages with OCR. About 2 minutes left.",
        "Read 20 of 25 pages with OCR. Less than a minute left.",
        "Finished reading 25 pages with OCR.",
    ]


def test_progress_counts_pages_read_before():
    lines = []
    now = [0.0]
    progress = OcrProgress(300, 120, lines.append, clock=lambda: now[0])
    now[0] += 4
    progress.page_done()
    assert lines == ["Read 121 of 300 pages with OCR. About 12 minutes left."]


def test_default_output_path(tmp_path):
    assert default_output_path(tmp_path / "My Book.pdf") == tmp_path / "My Book_ocr.pdf"
