"""Build PDF test fixtures at test time with reportlab, so no PDF files are committed.

Sample prose is from Lewis Carroll, "Alice's Adventures in Wonderland" (1865), which is
in the public domain. Source: Project Gutenberg eBook #11,
https://www.gutenberg.org/ebooks/11 (chapter I, "Down the Rabbit-Hole").
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 72
TEXT_WIDTH = PAGE_WIDTH - 2 * MARGIN
FONT = "Times-Roman"
BOLD_FONT = "Times-Bold"
BODY_SIZE = 11
LEADING = 14
INDENT = 18
HEADING_SIZE = 22
TOP = PAGE_HEIGHT - MARGIN

ALICE = (
    "Alice was beginning to get very tired of sitting by her sister on the bank, and of "
    "having nothing to do: once or twice she had peeped into the book her sister was "
    "reading, but it had no pictures or conversations in it, “and what is the use of a "
    "book,” thought Alice “without pictures or conversations?”",
    "So she was considering in her own mind (as well as she could, for the hot day made her "
    "feel very sleepy and stupid), whether the pleasure of making a daisy-chain would be "
    "worth the trouble of getting up and picking the daisies, when suddenly a White Rabbit "
    "with pink eyes ran close by her.",
    "There was nothing so very remarkable in that; nor did Alice think it so very much out "
    "of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be late!”",
    "The rabbit-hole went straight on like a tunnel for some way, and then dipped suddenly "
    "down, so suddenly that Alice had not a moment to think about stopping herself before "
    "she found herself falling down a very deep well.",
    "Either the well was very deep, or she fell very slowly, for she had plenty of time as "
    "she went down to look about her and to wonder what was going to happen next.",
)
CHAPTER_TITLES = (
    "Down the Rabbit-Hole",
    "The Pool of Tears",
    "A Caucus-Race and a Long Tale",
    "The Rabbit Sends in a Little Bill",
)
RUNNING_HEADER = "ALICE’S ADVENTURES IN WONDERLAND"

# Header or footer text for a 0-based page index: one line, several lines, or None.
PageText = Callable[[int], str | Sequence[str] | None]
# Running lines are set in small type, this far apart.
RUNNING_SIZE = 9
RUNNING_LEADING = 11
# Footnotes too.
NOTE_SIZE = 8
NOTE_LEADING = 10
# In text given to the builder, "[^1]" is a note reference: "1" set small and raised.
MARKER = re.compile(r"\[\^([^\]]+)\]")
# How a reference is set, as shares of the text size.
MARKER_SCALE = 0.65
MARKER_RISE = 0.35


def unmarked(text: str) -> str:
    """Text as a reader should get it back: with its note references taken out."""
    return MARKER.sub("", text)


def marked(text: str, after: str, label: str) -> str:
    """`text` with a reference to note `label` right after the first `after` in it."""
    at = text.index(after) + len(after)
    return f"{text[:at]}[^{label}]{text[at:]}"


class PdfBuilder:
    """Lays out lines and paragraphs page by page, like a very small typesetter.

    `style` is "indent" (first lines indented) or "gap" (a blank line between paragraphs).
    `header` and `footer` get the 0-based page index and return the text to print, if any:
    a line, or several lines, top to bottom.
    """

    def __init__(
        self,
        path: Path,
        *,
        title: str = "",
        author: str = "",
        lang: str | None = None,
        user_password: str | None = None,
        owner_password: str | None = None,
        style: str = "indent",
        header: PageText | None = None,
        footer: PageText | None = None,
    ) -> None:
        encrypt = None
        if user_password is not None or owner_password is not None:
            encrypt = StandardEncryption(user_password or "", ownerPassword=owner_password)
        self.path = path
        self._canvas = Canvas(str(path), pagesize=letter, lang=lang, encrypt=encrypt)
        # Always set: reportlab otherwise writes "untitled" and "anonymous".
        self._canvas.setTitle(title)
        self._canvas.setAuthor(author)
        self._style = style
        self._header = header
        self._footer = footer
        self.page = 0  # 0-based index of the page being drawn
        self._y = TOP
        self._drawn = False
        self._bookmarks = 0

    def line(
        self, text: str, *, x: float = MARGIN, size: float = BODY_SIZE, bold: bool = False
    ) -> None:
        if self._y < MARGIN:
            self.new_page()
        self._draw(text, x, self._y, size, BOLD_FONT if bold else FONT)
        self._y -= LEADING if size == BODY_SIZE else size * 1.3
        self._drawn = True

    def _draw(self, text: str, x: float, y: float, size: float, font: str) -> None:
        """One line of text, with any "[^1]" note references set small and raised."""
        for i, piece in enumerate(MARKER.split(text)):
            reference = i % 2 == 1
            piece_size = size * MARKER_SCALE if reference else size
            self._canvas.setFont(font, piece_size)
            self._canvas.drawString(x, y + (size * MARKER_RISE if reference else 0), piece)
            x += stringWidth(piece, font, piece_size)

    def space(self, points: float) -> None:
        self._y -= points

    def heading(self, text: str, *, size: float = HEADING_SIZE, bold: bool = False) -> None:
        self.line(text, size=size, bold=bold)
        self.space(LEADING)

    def paragraph(
        self, text: str, *, x: float = MARGIN, width: float = TEXT_WIDTH, indent: bool = True
    ) -> None:
        """`indent=False` sets the first line at the margin, as text carried on from the
        last page or column is."""
        first = INDENT if self._style == "indent" and indent else 0
        for i, line in enumerate(wrap(text, [width - first], width)):
            self.line(line, x=x + (first if i == 0 else 0))
        self._end_paragraph()

    def columns(
        self,
        columns: Sequence[Sequence[str]],
        *,
        gutter: float = 24,
        drop: float = 0,
        carried: bool = False,
    ) -> None:
        """Paragraphs side by side in columns, starting at the current line; the text after
        them starts below the longest. Each column after the first is set `drop` points
        lower, so its lines fall between the first column's. With `carried`, each column
        after the first opens with text carried on from the column before, not indented.
        Nothing here turns a page."""
        width = (TEXT_WIDTH - gutter * (len(columns) - 1)) / len(columns)
        top = bottom = self._y
        for i, texts in enumerate(columns):
            self._y = top - (drop if i else 0)
            for n, text in enumerate(texts):
                indent = not (carried and i and n == 0)
                self.paragraph(text, x=MARGIN + i * (width + gutter), width=width, indent=indent)
            bottom = min(bottom, self._y)
        self._y = bottom

    @staticmethod
    def column_width(count: int, gutter: float = 24) -> float:
        return (TEXT_WIDTH - gutter * (count - 1)) / count

    def drop_cap_paragraph(self, text: str, *, size: float = 40, beside: int = 3) -> None:
        """A paragraph whose first letter is a drop cap set beside its first lines."""
        cap, rest = text[0], text[1:]
        cap_width = stringWidth(cap, FONT, size) + 4
        self._canvas.setFont(FONT, size)
        self._canvas.drawString(MARGIN, self._y - (beside - 1) * LEADING, cap)
        for i, line in enumerate(wrap(rest, [TEXT_WIDTH - cap_width] * beside)):
            self.line(line, x=MARGIN + (cap_width if i < beside else 0))
        self._end_paragraph()

    def _end_paragraph(self) -> None:
        if self._style == "gap":
            self.space(LEADING)

    def image(self) -> None:
        """A grey square, like a scanned page with no text layer."""
        self._canvas.drawInlineImage(Image.new("L", (40, 40), 128), MARGIN, 200, 400, 400)
        self._drawn = True

    def figure(
        self,
        width: float = 200,
        height: float = 150,
        *,
        x: float = MARGIN,
        caption: str | None = None,
        caption_size: float = 9,
    ) -> None:
        """A grey picture in the flow of text, at the current line, then its caption."""
        if self._y - height < MARGIN:
            self.new_page()
        top = self._y + BODY_SIZE  # where the current line's letters would reach
        self._canvas.drawInlineImage(Image.new("L", (8, 8), 96), x, top - height, width, height)
        self._y = top - height - LEADING
        self._drawn = True
        if caption is not None:
            self.line(caption, x=x, size=caption_size)

    def footnotes(self, notes: Sequence[tuple[str | None, str]], *, raised: bool = False) -> None:
        """Notes at the foot of the current page, set small under a short rule, as
        (marker, text) pairs. A None marker carries on the last note of the page before.
        Markers are set "1 " on the line, or as raised references with `raised`."""
        lines: list[str] = []
        for marker, text in notes:
            for i, line in enumerate(wrap(text, size=NOTE_SIZE)):
                if marker is not None and i == 0:
                    line = f"[^{marker}]{line}" if raised else f"{marker} {line}"
                lines.append(line)
        y = MARGIN + NOTE_LEADING * (len(lines) - 1)  # the first line's baseline
        rule = y + NOTE_LEADING
        assert self._y > rule + LEADING, "the page's text runs into its notes"
        self._canvas.line(MARGIN, rule, MARGIN + 120, rule)
        for line in lines:
            self._draw(line, MARGIN, y, NOTE_SIZE, FONT)
            y -= NOTE_LEADING
        self._drawn = True

    def bookmark(self, title: str, level: int = 0) -> None:
        """A bookmark to the current page. Each gets its own key, or titles overwrite."""
        key = f"bookmark{self._bookmarks}"
        self._bookmarks += 1
        self._canvas.bookmarkPage(key)
        self._canvas.addOutlineEntry(title, key, level=level)

    def page_label(self, style: str, start: int = 1) -> None:
        """Number pages from here on: style is ARABIC, ROMAN_LOWER, ROMAN_UPPER, ..."""
        self._canvas.addPageLabel(self.page, style=style, start=start)

    def new_page(self) -> None:
        # Headers stack up from the last line and footers down from the first, so the
        # gap between them and the text stays the same however many lines they have.
        self._canvas.setFont(FONT, RUNNING_SIZE)
        header = _lines_of(self._header(self.page) if self._header else None)
        for i, text in enumerate(reversed(header)):
            y = PAGE_HEIGHT - 45 + i * RUNNING_LEADING
            self._canvas.drawCentredString(PAGE_WIDTH / 2, y, text)
        footer = _lines_of(self._footer(self.page) if self._footer else None)
        for i, text in enumerate(footer):
            self._canvas.drawCentredString(PAGE_WIDTH / 2, 40 - i * RUNNING_LEADING, text)
        self._canvas.showPage()
        self.page += 1
        self._y = TOP
        self._drawn = False

    def save(self) -> Path:
        if self._drawn:
            self.new_page()
        self._canvas.save()
        return self.path


def _lines_of(text: str | Sequence[str] | None) -> Sequence[str]:
    if not text:
        return ()
    return (text,) if isinstance(text, str) else text


def wrap(
    text: str,
    first_widths: Sequence[float] = (),
    width: float = TEXT_WIDTH,
    size: float = BODY_SIZE,
) -> list[str]:
    """Greedy line breaking in the body font. The first lines can be set narrower."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        limit = first_widths[len(lines)] if len(lines) < len(first_widths) else width
        if current and stringWidth(unmarked(candidate), FONT, size) > limit:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def scanned(source: Path, path: Path, *, dpi: int = 150) -> Path:
    """`source`, a PDF with text, as a scanner makes it: each page a picture of the page,
    with no text layer. Pillow writes the PDF, one picture a page, at the page's size."""
    import pypdfium2

    with pypdfium2.PdfDocument(str(source)) as document:
        pictures = [page.render(scale=dpi / 72).to_pil().convert("L") for page in document]
    pictures[0].save(path, "PDF", resolution=dpi, save_all=True, append_images=pictures[1:])
    return path


def scanned_words(path: Path, pages: Sequence[str], *, dpi: int = 150) -> Path:
    """A scanned PDF with these words in large type, one line a page."""
    source = path.with_name(f"{path.stem}-text.pdf")
    canvas = Canvas(str(source), pagesize=letter)
    for words in pages:
        canvas.setFont("Helvetica-Bold", 36)
        canvas.drawString(MARGIN, PAGE_HEIGHT / 2, words)
        canvas.showPage()
    canvas.save()
    return scanned(source, path, dpi=dpi)


def alice(count: int, start: int = 0) -> list[str]:
    """`count` paragraphs of sample prose, cycling through the passage."""
    return [ALICE[(start + i) % len(ALICE)] for i in range(count)]


def novel(
    path: Path,
    *,
    headings: Sequence[str | None] = CHAPTER_TITLES[:3],
    heading_size: float = HEADING_SIZE,
    bookmarks: Sequence[str] | None = None,
    paragraphs: int = 24,
    front: Sequence[str] = (),
    back: Sequence[str] = (),
    running_header: bool = True,
    chapter_header: bool = False,
    page_numbers: bool = True,
    **options: Any,
) -> Path:
    """A small book: front pages, one chapter per heading, then back-matter pages.

    Each chapter starts on a new page and runs about two pages. Pages other than section
    openings get a running header, with the chapter's heading on a second line if
    `chapter_header`; every page gets a page number at the bottom.
    """
    openings: set[int] = set()
    chapter: list[str] = []  # the heading of the chapter being set

    def header(page: int) -> Sequence[str] | None:
        if not running_header or page in openings:
            return None
        return [RUNNING_HEADER, *chapter[-1:]] if chapter_header else [RUNNING_HEADER]

    def footer(page: int) -> str | None:
        return str(page + 1) if page_numbers else None

    pdf = PdfBuilder(path, header=header, footer=footer, **options)
    for text in front:
        openings.add(pdf.page)
        pdf.paragraph(text)
        pdf.new_page()
    for n, heading in enumerate(headings):
        openings.add(pdf.page)
        if bookmarks is not None:
            pdf.bookmark(bookmarks[n])
        if heading is not None:
            pdf.heading(heading, size=heading_size)
            chapter.append(heading)
        for text in alice(paragraphs, start=n):
            pdf.paragraph(text)
        pdf.new_page()
    for title in back:
        openings.add(pdf.page)
        pdf.heading(title)
        for text in alice(3):
            pdf.paragraph(text)
        pdf.new_page()
    return pdf.save()
