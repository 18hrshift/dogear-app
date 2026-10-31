"""Matching BCP 47 language tags, the way Dogear chooses voices and words for a passage.

A tag's first part is its language ("fr" in "fr-CA"); the rest narrow it down. Two tags
are the same language when their first parts match, whatever case they're written in,
and a three-letter code matches its two-letter one ("eng" is "en"). Books write tags
with underscores too ("en_US"), which count as hyphens.
Settings tables keyed by tags ([voice.languages], [narration.words.fr]) are looked up
most specific first: "fr-CA" tries "fr-ca", then "fr".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

from dogear.output.languages import two_letter

T = TypeVar("T")

ENGLISH = "en"  # what Dogear takes a passage with no language at all to be


def primary(tag: str | None) -> str:
    """'fr-CA' -> 'fr', 'eng' -> 'en'. A missing tag is English, as Dogear reads a book
    that doesn't say."""
    return _parts(tag or ENGLISH)[0]


def same_language(a: str | None, b: str | None) -> bool:
    return primary(a) == primary(b)


def is_english(tag: str | None) -> bool:
    return primary(tag) == ENGLISH


def lookup_keys(tag: str) -> list[str]:
    """The keys a tag matches, most specific first: 'pt-BR' -> ['pt-br', 'pt']."""
    parts = _parts(tag)
    return ["-".join(parts[:n]) for n in range(len(parts), 0, -1)]


def most_specific(table: Mapping[str, T], tag: str | None) -> T | None:
    """The value of the most specific key in `table` that matches `tag`. Keys are
    matched as `lookup_keys` writes them, so "FR" or "fra" find "fr". None when none
    matches, or when there is no tag."""
    if not tag:
        return None
    keyed = {lookup_keys(key)[0]: value for key, value in table.items()}
    return next((keyed[key] for key in lookup_keys(tag) if key in keyed), None)


def _parts(tag: str) -> list[str]:
    """A tag's parts, lowercase, its language as two letters where it has them."""
    parts = tag.strip().replace("_", "-").lower().split("-")
    return [two_letter(parts[0]), *parts[1:]]
