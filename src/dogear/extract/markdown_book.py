"""Read a Markdown file (.md, .markdown) into a `dogear.document.Book`.

The file is rendered to HTML with markdown-it-py: CommonMark, plus tables,
strikethrough, and footnotes (`[^1]`). The HTML then goes through the HTML extractor
(`html_book`), so sections, notes, and language tags work as they do for HTML.

- HTML written in the Markdown is kept and read like the rest: it is parsed by the same
  safe HTML parser, so it can't fetch anything or reach other files.
- Footnotes are rendered with DPUB-ARIA roles (doc-noteref, doc-endnotes,
  doc-backlink), so a reference is tied to its note and the note's back link is never
  read.
- An image written with no description, `![](photo.jpg)`, has none. Markdown can't
  mark an image decorative: that needs HTML, `<img src="rule.png" alt="">`.
- YAML front matter gives the title, authors, and language (see `front_matter`).
  Without a title there, the title is the one level-1 heading, else the file name.
- A file with more lines or pieces than [markdown] max_lines and max_pieces allow is
  refused, so a small file can't fill the memory (see `markdown_limits`).

Markdown files are read as UTF-8 (or UTF-16 with a byte order mark), the encoding
Markdown tools expect.
"""

from __future__ import annotations

import codecs
import html
import logging
from collections.abc import Sequence
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin

from dogear.config import Config
from dogear.document import Book
from dogear.errors import ExtractionError
from dogear.extract.front_matter import FrontMatter, NotFrontMatter, read_front_matter
from dogear.extract.html_book import read_html
from dogear.extract.html_parse import parse_html
from dogear.extract.limited_read import read_limited
from dogear.extract.markdown_limits import MarkdownLimits, check_lines, count_pieces

log = logging.getLogger(__name__)

# Lists and quotes nested deeper than this are past what markdown-it reads: it stops
# reading the file there. No book nests this deep, so reaching it is an error.
MAX_NESTING = 100


def extract(path: Path, config: Config) -> Book:
    """Read the Markdown file at `path`."""
    limits = MarkdownLimits.from_config(config)
    text = _decode(read_limited(path, config, "markdown", "Markdown"), path.name)
    check_lines(text, path.name, limits)
    front, body = _render(text, path.name, limits)
    root = parse_html(_page(front, body), path.name)
    return read_html(root, path, source_format=path.suffix.lower().lstrip("."))


def _decode(data: bytes, what: str) -> str:
    codec = "utf-16" if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig"
    try:
        return data.decode(codec)
    except UnicodeDecodeError:
        raise ExtractionError(
            f"{what} isn't UTF-8 text, so Dogear can't tell which characters it holds.",
            hint="Open it in a text editor and save it with the encoding UTF-8, then try again.",
        ) from None


def _render(text: str, what: str, limits: MarkdownLimits) -> tuple[FrontMatter, str]:
    """The front matter and the HTML for the rest of `text`."""
    md = _markdown(what, limits, front_matter=True)
    env: dict = {}
    tokens = md.parse(text, env)
    front = FrontMatter()
    found = next((t for t in tokens if t.type == "front_matter"), None)
    if found is not None:
        try:
            front = read_front_matter(found.content, what)
        except NotFrontMatter:
            log.info("%s opens with --- lines that aren't front matter; reading them.", what)
            tokens = []  # the first parse's, let go before the second is made
            md, env = _markdown(what, limits, front_matter=False), {}
            tokens = md.parse(text, env)
    if any(t.level >= MAX_NESTING - 1 for t in tokens):
        raise ExtractionError(
            f"{what} nests lists or quotes more than {MAX_NESTING} levels deep, more than "
            "Dogear reads.",
            hint="The file may be damaged or built to exhaust memory.",
        )
    return front, md.renderer.render(tokens, md.options, env)


def _markdown(what: str, limits: MarkdownLimits, *, front_matter: bool) -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True, "maxNesting": MAX_NESTING})
    md.enable(["table", "strikethrough"])
    if front_matter:
        md.use(front_matter_plugin)
    md.use(footnote_plugin)
    count_pieces(md, what, limits)
    md.add_render_rule("image", _image)
    md.add_render_rule("footnote_ref", _footnote_ref)
    md.add_render_rule("footnote_block_open", _footnotes_open)
    md.add_render_rule("footnote_block_close", _footnotes_close)
    md.add_render_rule("footnote_open", _footnote_open)
    md.add_render_rule("footnote_anchor", _footnote_backlink)
    return md


def _page(front: FrontMatter, body: str) -> str:
    """A whole HTML document holding `body`, with the front matter in its <head>."""
    lang = f' lang="{html.escape(front.language)}"' if front.language else ""
    head = [f"<title>{html.escape(front.title)}</title>"] if front.title else []
    head += [f'<meta name="author" content="{html.escape(a)}">' for a in front.authors]
    return f"<!DOCTYPE html><html{lang}><head>{''.join(head)}</head><body>{body}</body></html>"


# Render rules. markdown-it calls each as a method of its renderer.


def _image(self, tokens: Sequence[Token], idx: int, options, env) -> str:
    """An <img>, with no alt attribute when the Markdown gives no description."""
    token = tokens[idx]
    alt = self.renderInlineAsText(token.children or [], options, env)
    if alt.strip():
        token.attrSet("alt", alt)
    else:
        token.attrs.pop("alt", None)
    return self.renderToken(tokens, idx, options, env)


def _note_number(self, tokens: Sequence[Token], idx: int, options, env) -> str:
    return self.rules["footnote_anchor_name"](tokens, idx, options, env)


def _footnote_ref(self, tokens: Sequence[Token], idx: int, options, env) -> str:
    number = _note_number(self, tokens, idx, options, env)
    sub_id = tokens[idx].meta.get("subId", -1)
    ref_id = f"fnref{number}" + (f":{sub_id}" if sub_id > 0 else "")
    return f'<a href="#fn{number}" id="{ref_id}" role="doc-noteref">{number}</a>'


def _footnotes_open(self, tokens: Sequence[Token], idx: int, options, env) -> str:
    # A <div>, not a <section>: a section of notes would mark the chapter it ends as
    # back matter (see epub_kind).
    return '<div role="doc-endnotes"><ol>\n'


def _footnotes_close(self, tokens: Sequence[Token], idx: int, options, env) -> str:
    return "</ol></div>\n"


def _footnote_open(self, tokens: Sequence[Token], idx: int, options, env) -> str:
    return f'<li id="fn{_note_number(self, tokens, idx, options, env)}">'


def _footnote_backlink(self, tokens: Sequence[Token], idx: int, options, env) -> str:
    number = _note_number(self, tokens, idx, options, env)
    return f' <a href="#fnref{number}" role="doc-backlink">↩</a>'
