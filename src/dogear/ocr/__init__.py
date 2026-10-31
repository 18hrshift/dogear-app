"""Reading scanned PDFs with OCR (Tesseract), so the PDF extractor can read them.

Each page is drawn as a picture, and Tesseract turns it into a one-page PDF with the
page's words as invisible text in the right positions and sizes, so chapter detection by
font size and layout still works on the result. `page.py` reads one page; `cache.py`
reads a whole scanned PDF for `dogear outline`, `make`, and the rest, keeping each page
so a stopped run carries on; this module is `dogear ocr`, which saves a copy of the PDF
with its pictures and a text layer over them.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from dogear.book_summary import count
from dogear.config import Config
from dogear.errors import DogearError
from dogear.ocr.page import ocr_page, open_pdf
from dogear.ocr.progress import TAKES_A_WHILE, OcrProgress
from dogear.ocr.settings import OcrSettings
from dogear.ocr.tesseract import find_tesseract
from dogear.render.progress import Report

log = logging.getLogger(__name__)


def default_output_path(input_pdf: Path) -> Path:
    return input_pdf.with_name(f"{input_pdf.stem}_ocr.pdf")


def ocr_pdf(input_pdf: Path, output_pdf: Path, config: Config, report: Report) -> int:
    """OCR every page of `input_pdf` into `output_pdf`, each page's picture with its text
    over it. Returns the page count."""
    if input_pdf.resolve() == output_pdf.resolve():
        raise DogearError(
            "The OCR output would overwrite the original PDF.", hint="Choose another output file."
        )
    settings = OcrSettings.from_config(config)
    tesseract = find_tesseract(config, settings)
    source = open_pdf(input_pdf)
    writer = PdfWriter()
    total = len(source)
    report(f"Reading the {count(total, 'page')} of {input_pdf.name} with OCR, {TAKES_A_WHILE}.")
    progress = OcrProgress(total, 0, report)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(total):
                page_pdf = Path(tmp) / f"page{index + 1:05d}.pdf"
                ocr_page(source, index, page_pdf, tesseract, settings, text_only=False)
                writer.append(str(page_pdf))
                progress.page_done()
            _copy_metadata(input_pdf, writer)
            output_pdf.parent.mkdir(parents=True, exist_ok=True)
            partial = output_pdf.with_name(f"{output_pdf.name}.{os.getpid()}.part")
            with partial.open("wb") as f:
                writer.write(f)
            os.replace(partial, output_pdf)
    finally:
        source.close()
    log.info("OCR finished: %d pages written to %s", total, output_pdf)
    return total


def _copy_metadata(input_pdf: Path, writer: PdfWriter) -> None:
    try:
        metadata = PdfReader(str(input_pdf)).metadata
    except Exception as e:  # metadata is a nicety; a broken info dict shouldn't stop OCR
        log.warning("Couldn't copy metadata from %s: %s", input_pdf.name, e)
        return
    if metadata:
        writer.add_metadata({k: str(v) for k, v in metadata.items()})
