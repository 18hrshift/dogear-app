"""Read a Word document's list definitions (numbering.xml): is a list numbered or bulleted?

A list paragraph names a numbering instance (w:numId) and a level (w:ilvl, 0 to 8). The
instance points to an abstract definition (w:abstractNum) whose levels each have a
format: "bullet", "none", or a kind of number ("decimal", "lowerRoman", ...). An
instance can override a level's format, and an abstract definition can take its levels
from a list style (w:numStyleLink), which names another instance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from lxml import etree

from dogear.extract.docx_package import W
from dogear.extract.docx_styles import Styles

log = logging.getLogger(__name__)

MAX_LINKS = 8  # list styles linking to list styles; more is a loop or a damaged file


@dataclass
class _Abstract:
    formats: dict[int, str] = field(default_factory=dict)  # level -> w:numFmt
    style_link: str | None = None  # w:numStyleLink: take the levels from this list style


class Numbering:
    def __init__(self, root: etree._Element | None, styles: Styles) -> None:
        self._styles = styles
        self._abstracts: dict[str, _Abstract] = {}
        # w:numId -> (w:abstractNumId, the formats it overrides by level)
        self._instances: dict[str, tuple[str, dict[int, str]]] = {}
        if root is None:
            return
        for el in root.iter(f"{W}abstractNum"):
            abstract_id = el.get(f"{W}abstractNumId")
            if abstract_id is not None:
                link = el.find(f"{W}numStyleLink")
                self._abstracts.setdefault(
                    abstract_id,
                    _Abstract(_formats(el), link.get(f"{W}val") if link is not None else None),
                )
        for el in root.iter(f"{W}num"):
            num_id = el.get(f"{W}numId")
            abstract = el.find(f"{W}abstractNumId")
            if num_id is None or abstract is None:
                continue
            overrides = {}
            for override in el.iter(f"{W}lvlOverride"):
                overrides.update(_formats(override))
            self._instances.setdefault(num_id, (abstract.get(f"{W}val") or "", overrides))

    def ordered(self, num_id: str, level: int) -> bool | None:
        """True for a numbered list level, False for bullets, None for no list at all.

        w:numId 0 means "no numbering"; so does a level whose format is "none" (an indent
        with nothing shown) and a numId the document doesn't define.
        """
        if num_id == "0":
            return None
        fmt = self._format(num_id, level, links=0)
        if fmt is None:
            log.debug("List %s has no level %d; reading its paragraphs as text.", num_id, level)
            return None
        if fmt == "none":
            return None
        return fmt != "bullet"

    def _format(self, num_id: str, level: int, links: int) -> str | None:
        instance = self._instances.get(num_id)
        if instance is None or links > MAX_LINKS:
            return None
        abstract_id, overrides = instance
        if level in overrides:
            return overrides[level]
        abstract = self._abstracts.get(abstract_id)
        if abstract is None:
            return None
        if level in abstract.formats:
            return abstract.formats[level]
        if abstract.style_link:
            linked = self._styles.numbering(abstract.style_link)
            if linked and linked[0] != num_id:
                return self._format(linked[0], level, links + 1)
        return None


def _formats(el: etree._Element) -> dict[int, str]:
    """Level -> number format for the w:lvl elements directly in `el`."""
    formats = {}
    for lvl in el.findall(f"{W}lvl"):
        fmt = lvl.find(f"{W}numFmt")
        try:
            level = int(lvl.get(f"{W}ilvl") or "")
        except ValueError:
            continue
        if fmt is not None:
            formats[level] = (fmt.get(f"{W}val") or "decimal").strip()
    return formats
