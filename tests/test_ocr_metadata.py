"""The PDF saved by `dogear ocr` keeps the book's navigation and language."""

import logging

import pytest
from conftest import require_tool
from pdf_builder import scanned_words
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, Fit, NameObject, TextStringObject
from reportlab.pdfgen.canvas import Canvas

from dogear import ocr
from dogear.document import PageMarker
from dogear.extract import extract_book


def labelled_scan(tmp_path):
    scan = scanned_words(tmp_path / "scan.pdf", ["ONE", "TWO", "THREE", "FOUR"], dpi=100)
    writer = PdfWriter(clone_from=scan)
    writer.add_metadata({"/Title": "A Navigable Book", "/Author": "Ann Author"})
    writer.root_object[NameObject("/Lang")] = TextStringObject("fr-CA")
    opening = writer.add_outline_item("Opening", 0)
    story = writer.add_outline_item("The Story", 1, parent=opening)
    writer.add_outline_item("A Scene", 2, parent=story, fit=Fit.xyz(10, 700, 1.25))
    writer.add_outline_item("Ending", 3)
    writer.set_page_label(0, 1, style="/r")
    writer.set_page_label(2, 3, style="/D", start=1)
    source = tmp_path / "labelled.pdf"
    writer.write(source)
    return source


@pytest.fixture
def fake_ocr(monkeypatch):
    def page(document, index, output, tesseract, settings, *, text_only):
        assert not text_only
        canvas = Canvas(str(output))
        canvas.drawString(72, 700, f"Words on page {index + 1}, enough text for Dogear to read.")
        canvas.save()

    monkeypatch.setattr(ocr, "ocr_page", page)
    monkeypatch.setattr(ocr, "find_tesseract", lambda *args: object())


def test_saved_ocr_pdf_keeps_nested_bookmarks_page_labels_and_language(tmp_path, config, fake_ocr):
    source = labelled_scan(tmp_path)
    out = tmp_path / "out.pdf"
    assert ocr.ocr_pdf(source, out, config, print) == 4
    result = PdfReader(out)
    assert result.metadata.title == "A Navigable Book"
    assert result.metadata.author == "Ann Author"
    assert result.root_object["/Lang"] == "fr-CA"
    assert result.page_labels == ["i", "ii", "1", "2"]
    first, children, last = result.outline
    child, grandchildren = children
    [scene] = grandchildren
    assert [item.title for item in (first, child, scene, last)] == [
        "Opening",
        "The Story",
        "A Scene",
        "Ending",
    ]
    assert [result.get_destination_page_number(item) for item in (first, child, scene, last)] == [
        0,
        1,
        2,
        3,
    ]
    assert scene.typ == "/XYZ"
    assert (scene.left, scene.top, scene.zoom) == (10, 700, 1.25)

    book = extract_book(out, config)
    assert book.metadata.language == "fr-CA"
    assert [section.title for section in book.sections] == ["Opening", "Ending"]
    assert [
        block.label
        for section in book.sections
        for block in section.blocks
        if isinstance(block, PageMarker)
    ] == ["i", "ii", "1", "2"]


def test_real_ocr_keeps_the_print_page_labels_and_language(tmp_path, config):
    require_tool("tesseract")
    source = labelled_scan(tmp_path)
    out = tmp_path / "out.pdf"
    config["ocr"]["dpi"] = 150
    ocr.ocr_pdf(source, out, config, print)
    result = PdfReader(out)
    assert result.page_labels == ["i", "ii", "1", "2"]
    assert result.root_object["/Lang"] == "fr-CA"
    assert result.get_destination_page_number(result.outline[-1]) == 3
    assert "FOUR" in result.pages[3].extract_text().upper()


def test_ocr_does_not_copy_active_catalog_entries(tmp_path, config, fake_ocr):
    source = labelled_scan(tmp_path)
    writer = PdfWriter(clone_from=source)
    action = DictionaryObject(
        {
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert('untrusted')"),
        }
    )
    writer.root_object[NameObject("/OpenAction")] = action
    writer.root_object[NameObject("/AA")] = DictionaryObject({NameObject("/WC"): action})
    writer.add_attachment("note.txt", b"untrusted attachment")
    active = tmp_path / "active.pdf"
    writer.write(active)
    out = tmp_path / "out.pdf"
    ocr.ocr_pdf(active, out, config, print)
    result = PdfReader(out)
    assert "/OpenAction" not in result.root_object
    assert "/AA" not in result.root_object
    assert "/Names" not in result.root_object
    assert result.outline


def test_damaged_page_labels_are_reported_without_losing_other_metadata(
    tmp_path, config, fake_ocr, caplog
):
    source = labelled_scan(tmp_path)
    writer = PdfWriter(clone_from=source)
    writer.root_object[NameObject("/PageLabels")] = TextStringObject("damaged")
    damaged = tmp_path / "damaged.pdf"
    writer.write(damaged)
    out = tmp_path / "out.pdf"
    with caplog.at_level(logging.WARNING):
        ocr.ocr_pdf(damaged, out, config, print)
    assert any("page labels" in line.lower() for line in caplog.messages)
    result = PdfReader(out)
    assert result.metadata.title == "A Navigable Book"
    assert result.root_object["/Lang"] == "fr-CA"
    assert result.outline[-1].title == "Ending"


def test_empty_and_custom_print_page_labels_are_kept(tmp_path, config, fake_ocr):
    source = labelled_scan(tmp_path)
    writer = PdfWriter(clone_from=source)
    writer.set_page_label(0, 0, prefix="", style=None)
    writer.set_page_label(1, 2, prefix="Appendix-", style="/A", start=3)
    writer.set_page_label(3, 3, prefix="Foldout", style=None)
    custom = tmp_path / "custom.pdf"
    writer.write(custom)
    out = tmp_path / "out.pdf"
    ocr.ocr_pdf(custom, out, config, print)
    assert PdfReader(out).page_labels == ["", "Appendix-C", "Appendix-D", "Foldout"]


def test_an_owner_password_does_not_hide_book_metadata(tmp_path, config, fake_ocr):
    source = labelled_scan(tmp_path)
    writer = PdfWriter(clone_from=source)
    writer.encrypt(user_password="", owner_password="owner")
    protected = tmp_path / "protected.pdf"
    writer.write(protected)
    out = tmp_path / "out.pdf"
    ocr.ocr_pdf(protected, out, config, print)
    result = PdfReader(out)
    assert result.metadata.title == "A Navigable Book"
    assert result.page_labels == ["i", "ii", "1", "2"]
    assert result.root_object["/Lang"] == "fr-CA"
    assert result.get_destination_page_number(result.outline[-1]) == 3
