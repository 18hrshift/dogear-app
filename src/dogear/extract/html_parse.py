"""Decode and parse an HTML file from a book without touching the network or the disk.

Bytes are decoded the way browsers do: a byte order mark, else the character set the
file declares in its first 1024 bytes, if browsers know its label (html_encodings), else
UTF-8 if the bytes are valid UTF-8, else Windows-1252.

lxml's HTML parser (libxml2) then reads the text with the network off. HTML has no
external entities: the parser knows HTML's named entities itself and never expands an
entity a document declares. The parser's limits on nesting depth and text length stay
on. Where it recovers from ordinary mistakes (an unclosed <p>), a document that reaches
a limit fails with an error, never read in part.
"""

from __future__ import annotations

import codecs
import logging
import re

from lxml import etree

from dogear.errors import ExtractionError
from dogear.extract.html_encodings import codec_for_label

log = logging.getLogger(__name__)

HTML_HINT = (
    "Open it in a web browser to check that it shows the book, or save it again from the "
    "program that made it."
)
PRESCAN_BYTES = 1024  # how far browsers look for a declared character set
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_META_CHARSET = re.compile(rb"""<meta\s[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)
_XML_ENCODING = re.compile(rb"""^\s*<\?xml\s[^>]*?encoding\s*=\s*["']([A-Za-z0-9_.:-]+)""", re.I)
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")


def decode_html(data: bytes, what: str) -> str:
    """The text of `data`, an HTML file named `what` in messages."""
    for bom, codec in _BOMS:
        if data.startswith(bom):
            return _decode(data, codec, what)
    declared = _declared_codec(data[:PRESCAN_BYTES], what)
    if declared is not None:
        return _decode(data, declared, what)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        log.info("%s declares no character set and isn't UTF-8; reading it as Windows-1252.", what)
        return _decode(data, "cp1252", what)


def parse_html(text: str, what: str) -> etree._Element:
    """The root <html> element of `text`, an HTML document named `what` in messages."""
    if "\x00" in text:
        raise ExtractionError(
            f"{what} isn't an HTML file: it holds binary data, not text.", hint=HTML_HINT
        )
    parser = etree.HTMLParser(
        no_network=True, remove_comments=True, remove_pis=True, huge_tree=False
    )
    # lxml refuses text that still declares an encoding; it has been decoded already.
    text = _XML_DECLARATION.sub("", text, count=1)
    try:
        root = etree.fromstring(text, parser)
    except etree.XMLSyntaxError as e:
        raise ExtractionError(f"{what} can't be read as HTML ({e.msg}).", hint=HTML_HINT) from None
    fatal = [e for e in parser.error_log if e.level == etree.ErrorLevels.FATAL]
    if fatal:
        raise ExtractionError(
            f"{what} can't be read completely: {fatal[0].message.strip()}.",
            hint=(
                "The file nests its elements more deeply, or holds a longer stretch of "
                "text, than any book needs. It may be damaged or built to exhaust memory."
            ),
        )
    if root is None:
        raise ExtractionError(f"{what} is empty.", hint=HTML_HINT)
    return root


def _declared_codec(head: bytes, what: str) -> str | None:
    match = _META_CHARSET.search(head) or _XML_ENCODING.search(head)
    if match is None:
        return None
    label = match.group(1).decode("ascii")
    codec = codec_for_label(label)
    if codec is None:
        log.warning("%s says its text is in %r, which Dogear doesn't know; guessing.", what, label)
    return codec


def _decode(data: bytes, codec: str, what: str) -> str:
    """`data` decoded with `codec`, a text codec, with any bytes it can't read as
    replacement characters."""
    try:
        return data.decode(codec)
    except UnicodeError:
        log.warning(
            "Some bytes in %s aren't valid %s text; they are read as replacement characters.",
            what,
            codec,
        )
        return data.decode(codec, errors="replace")
