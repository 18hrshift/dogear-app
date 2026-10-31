"""Read a Word document's styles: which are headings, the title, lists, and languages.

A paragraph's formatting comes from its style, and each style can be based on another
(w:basedOn), so every question is asked of the whole chain, the paragraph's own style
first. A heading is a paragraph whose chain holds an outline level (w:outlineLvl 0 to 8,
which is what puts it in Word's navigation pane), or a built-in heading style ("heading
1", style id "Heading1"). So a renamed or translated heading style still counts: Word
keeps both the outline level and the English built-in name whatever the style is called
on screen ("Überschrift 1" has style id "berschrift1" but name "heading 1").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

from dogear.extract.docx_package import W

# A chain longer than this is a loop or a damaged file; Word itself allows far fewer.
MAX_CHAIN = 64
BODY_TEXT_LEVEL = 9  # w:outlineLvl 9 means "body text": explicitly not a heading
_HEADING_NAME = re.compile(r"heading ([1-9])")
_HEADING_ID = re.compile(r"Heading([1-9])")
_TOC_NAME = re.compile(r"toc [1-9]|toc heading")
NO_LANGUAGE = "x-none"  # what Word writes for "no language"


@dataclass(frozen=True)
class Style:
    style_id: str
    name: str  # lower case, such as "heading 1"
    based_on: str | None
    outline_level: int | None  # w:outlineLvl, 0 for the top level
    numbering: tuple[str, int | None] | None  # (w:numId, w:ilvl) from the style's w:numPr
    lang: str | None


class Styles:
    def __init__(self, root: etree._Element | None) -> None:
        self._styles: dict[str, Style] = {}
        self.default_paragraph: str | None = None
        self.default_lang: str | None = None
        if root is None:
            return
        for el in root.iter(f"{W}style"):
            style = _style(el)
            if style is None:
                continue
            self._styles.setdefault(style.style_id, style)
            if el.get(f"{W}type") == "paragraph" and on(el, f"{W}default", default=False):
                self.default_paragraph = self.default_paragraph or style.style_id
        defaults = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")
        if defaults is not None:
            self.default_lang = run_lang(defaults)

    def chain(self, style_id: str | None) -> list[Style]:
        """The style and the styles it is based on, its own first."""
        chain: list[Style] = []
        seen: set[str] = set()
        while style_id and style_id not in seen and len(chain) < MAX_CHAIN:
            seen.add(style_id)
            style = self._styles.get(style_id)
            if style is None:
                break
            chain.append(style)
            style_id = style.based_on
        return chain

    def heading_level(self, style_id: str | None) -> int | None:
        """1 to 9 for a heading style, else None.

        A style the document names but doesn't define counts by its id alone.
        """
        chain = self.chain(style_id)
        if not chain and style_id and (match := _HEADING_ID.fullmatch(style_id)):
            return int(match.group(1))
        for style in chain:
            if style.outline_level is not None:
                return heading_for_outline(style.outline_level)
            if match := _HEADING_NAME.fullmatch(style.name) or _HEADING_ID.fullmatch(
                style.style_id
            ):
                return int(match.group(1))
        return None

    def is_title(self, style_id: str | None) -> bool:
        chain = self.chain(style_id)
        if not chain:
            return style_id == "Title"
        return any(s.name == "title" or s.style_id == "Title" for s in chain)

    def is_contents(self, style_id: str | None) -> bool:
        """Whether the style is one of a table of contents' ("toc 1", "TOC Heading")."""
        return any(_TOC_NAME.fullmatch(s.name) for s in self.chain(style_id))

    def numbering(self, style_id: str | None) -> tuple[str, int | None] | None:
        return next((s.numbering for s in self.chain(style_id) if s.numbering), None)

    def lang(self, style_id: str | None) -> str | None:
        return next((s.lang for s in self.chain(style_id) if s.lang), None)


def heading_for_outline(level: int) -> int | None:
    """The heading level (1 to 9) for a w:outlineLvl, or None for body text."""
    return level + 1 if 0 <= level < BODY_TEXT_LEVEL else None


def on(el: etree._Element, attribute: str = f"{W}val", *, default: bool = True) -> bool:
    """A WordprocessingML on/off value: true unless it says 0, false, or off."""
    value = el.get(attribute)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "off")


def run_lang(rpr: etree._Element) -> str | None:
    """The language a run's properties (w:rPr) give its text, if they give one.

    Right-to-left and complex-script text (w:rtl, w:cs) is in the w:bidi language.
    """
    lang = rpr.find(f"{W}lang")
    if lang is None:
        return None
    complex_script = any(
        (flag := rpr.find(f"{W}{name}")) is not None and on(flag) for name in ("rtl", "cs")
    )
    value = lang.get(f"{W}bidi") if complex_script else None
    value = (value or lang.get(f"{W}val") or "").strip()
    return value if value and value.lower() != NO_LANGUAGE else None


def paragraph_numbering(ppr: etree._Element | None) -> tuple[str, int | None] | None:
    """(w:numId, w:ilvl) from a paragraph's or style's w:pPr, if it has w:numPr."""
    num_pr = ppr.find(f"{W}numPr") if ppr is not None else None
    if num_pr is None:
        return None
    num_id = _val(num_pr.find(f"{W}numId"))
    level = _int(_val(num_pr.find(f"{W}ilvl")))
    if num_id is None and level is None:
        return None
    return (num_id or "", level)


def outline_level(ppr: etree._Element | None) -> int | None:
    return _int(_val(ppr.find(f"{W}outlineLvl"))) if ppr is not None else None


def _style(el: etree._Element) -> Style | None:
    style_id = (el.get(f"{W}styleId") or "").strip()
    if not style_id:
        return None
    ppr = el.find(f"{W}pPr")
    rpr = el.find(f"{W}rPr")
    return Style(
        style_id=style_id,
        name=(_val(el.find(f"{W}name")) or "").strip().lower(),
        based_on=_val(el.find(f"{W}basedOn")),
        outline_level=outline_level(ppr),
        numbering=paragraph_numbering(ppr),
        lang=run_lang(rpr) if rpr is not None else None,
    )


def _val(el: etree._Element | None) -> str | None:
    return el.get(f"{W}val") if el is not None else None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
