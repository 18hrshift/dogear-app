"""Small helpers for reading XHTML documents inside a book archive."""

from __future__ import annotations

import posixpath
import re
from urllib.parse import unquote, urlsplit

from lxml import etree

OPS_NS = "http://www.idpf.org/2007/ops"
XML_NS = "http://www.w3.org/XML/1998/namespace"
EPUB_TYPE = f"{{{OPS_NS}}}type"
XML_LANG = f"{{{XML_NS}}}lang"

# HTML's whitespace characters. A no-break space is deliberately not one of them.
_SPACE_RUN = re.compile(r"[ \t\n\r\f]+")


def local_name(el: etree._Element) -> str:
    """Tag name without its namespace, such as 'p' for '{http://...xhtml}p'."""
    return str(el.tag).rpartition("}")[2].lower()


def semantics(el: etree._Element) -> frozenset[str]:
    """The element's epub:type tokens plus its DPUB-ARIA roles without 'doc-'.

    `role="doc-footnote"` and `epub:type="footnote"` mean the same thing, so both come
    out as "footnote". A document parsed as HTML, not XML, has no namespaces, so there
    the attribute is named "epub:type" as written.
    """
    tokens = set((el.get(EPUB_TYPE) or el.get("epub:type") or "").split())
    tokens.update(role.removeprefix("doc-") for role in (el.get("role") or "").split())
    return frozenset(tokens)


def lang_of(el: etree._Element, inherited: str | None) -> str | None:
    """The element's own xml:lang or lang, else the language it inherits.

    In a document parsed as HTML, xml:lang is an attribute named "xml:lang" as written.
    """
    lang = (el.get(XML_LANG) or el.get("xml:lang") or el.get("lang") or "").strip()
    return lang or inherited


def collapse(text: str) -> str:
    """Runs of whitespace as single spaces, trimmed at both ends."""
    return _SPACE_RUN.sub(" ", text).strip()


def plain_text(el: etree._Element) -> str:
    """All the text inside `el`, whitespace collapsed."""
    return collapse("".join(el.itertext()))


def resolve_href(base_path: str, href: str) -> tuple[str, str] | None:
    """(archive path, fragment) for an href in the file at `base_path`.

    Hrefs are URL-decoded and normalized. None means the href can't name a file in the
    book: it has a scheme (http:, mailto:), or its path escapes the archive.
    """
    parts = urlsplit(href.strip())
    if parts.scheme or parts.netloc:
        return None
    fragment = unquote(parts.fragment)
    if not parts.path:
        return base_path, fragment
    joined = posixpath.join(posixpath.dirname(base_path), unquote(parts.path))
    path = posixpath.normpath(joined)
    if path in (".", "..") or path.startswith(("/", "../")):
        return None
    return path, fragment
