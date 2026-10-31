"""The forms that give words their spellings: a book's words page and the Pronunciations
page. Each word on them is three fields, numbered from 1: `word-N`, the word (hidden);
`was-N`, the spelling the page was shown with (hidden, "" for none); and `say-N`, how to
say it ("Say it like"). An empty `say-N` removes the word's entry.

Only the words whose "Say it like" differs from `was-N` are saved. So a page opened
earlier, in another tab or through the browser's Back button, never undoes changes
saved since on words it leaves alone. If a word it does change was also changed since,
nothing is saved, and the page says so on that word's field.

Everything sent is checked here (page_words.py has the rules); the pages show what was
sent back, with an error on each field that can't be saved.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from dogear.pronunciations import Pronunciation, word_key
from dogear.web.page_words import (
    ChangedElsewhere,
    EntryError,
    PageWords,
    clean_spelling,
    clean_word,
    tidy,
)
from dogear.web.settings import WordLimits

MAX_NUMBER_DIGITS = 5
FIELDS_PER_WORD = 3  # word-N, was-N, say-N


@dataclass(frozen=True)
class SentWord:
    number: int
    word: str  # as sent
    typed: str  # what was in "Say it like"
    was: str | None  # the spelling the page was shown with; None if the form didn't say

    @property
    def field(self) -> str:
        return say_field(self.number)


@dataclass(frozen=True)
class WordItem:
    """A word on one of these forms, as its group shows it (_macros.html's word_group)."""

    number: int
    word: str
    about: str  # such as "In the book 131 times.", or ""
    value: str  # in "Say it like"
    was: str  # the spelling the page is shown with, sent back as `was-N`

    @property
    def field(self) -> str:
        return say_field(self.number)


@dataclass
class Changes:
    """What a form asks to save."""

    spellings: dict[str, str] = field(default_factory=dict)  # word: spelling, "" removes
    shown: dict[str, str] = field(default_factory=dict)  # word: its spelling on the page
    errors: dict[str, str] = field(default_factory=dict)  # field name: what's wrong


def say_field(number: int) -> str:
    """The name, and the id, of a word's "Say it like" field."""
    return f"say-{number}"


def form_limits(limits: WordLimits, words: int | None = None) -> tuple[int, int]:
    """The most bytes and fields a form listing `words` words (by default, as many as the
    word list holds) can send: each word and both spellings at their longest, every
    character percent-encoded (up to 9 bytes each)."""
    words = limits.entries if words is None else words
    per_word = 9 * (limits.word_chars + 2 * limits.spelling_chars) + FIELDS_PER_WORD * len(
        "&word-12345="
    )
    return words * per_word + 4096, FIELDS_PER_WORD * words + 16


def sent_words(fields: list[tuple[str, str]], most: int) -> list[SentWord]:
    """The words sent, numbered 1 to `most`, in order. Other fields are ignored."""
    sent: dict[str, dict[int, str]] = {"word": {}, "say": {}, "was": {}}
    for key, value in fields:
        kind, _, number_text = key.partition("-")
        number = _number(number_text)
        if number is None or not 1 <= number <= most or kind not in sent:
            continue
        sent[kind].setdefault(number, value)
    words, typed, was = sent["word"], sent["say"], sent["was"]
    return [SentWord(n, words[n], typed.get(n, ""), was.get(n)) for n in sorted(words)]


def spellings_to_save(sent: list[SentWord], limits: WordLimits) -> Changes:
    """The spellings a form asks to save: only the words whose "Say it like" changed.
    A word being given a spelling must pass the rules for new words; a word being
    removed is matched to its entry whatever it is, so any entry the page shows can be
    removed."""
    changes = Changes()
    for item in sent:
        typed = tidy(item.typed)
        if item.was is not None and typed == tidy(item.was):
            continue  # left as it was shown: nothing asked
        if typed:
            try:
                word = clean_word(item.word, limits)
            except EntryError as e:
                changes.errors[item.field] = f"{item.word.strip()} can't be saved: {e.message}"
                continue
            try:
                changes.spellings[word] = clean_spelling(item.typed, word, limits)
            except EntryError as e:
                changes.errors[item.field] = e.message
                continue
        else:
            word = tidy(item.word)
            if not word:
                continue  # no entry has an empty word
            changes.spellings[word] = ""
        if item.was is not None:
            changes.shown[word] = tidy(item.was)
    return changes


def save_sent(
    page_words: PageWords, sent: list[SentWord], limits: WordLimits
) -> tuple[list[SentWord], dict[str, str]]:
    """Save what a form asks for, all of it or none. Returns the words to show again and
    an error for each field that kept it from saving; no errors means everything asked
    for was saved. Raises DogearError if the word list can't be read or written."""
    changes = spellings_to_save(sent, limits)
    if changes.errors:
        return sent, changes.errors
    try:
        page_words.save(changes.spellings, changes.shown)
    except EntryError as e:
        return sent, {field_for(sent, e.word): e.message}
    except ChangedElsewhere as e:
        return changed_elsewhere(sent, e.words)
    return sent, {}


def changed_elsewhere(
    sent: list[SentWord], words: Mapping[str, str]
) -> tuple[list[SentWord], dict[str, str]]:
    """The words to show again after a save found `words` (word: its spelling now, ""
    for none) changed since the page was shown, and an error on each of their fields.
    Each keeps what was typed in it, and is now shown with its new spelling, so saving
    again saves what was typed."""
    errors: dict[str, str] = {}
    now_by_key = {word_key(word): now for word, now in words.items()}
    again = []
    for item in sent:
        now = now_by_key.get(word_key(tidy(item.word)))
        if now is None:
            again.append(item)
            continue
        says = f"It now says: {now}." if now else "It now has no spelling."
        errors[item.field] = (
            f"{tidy(item.word)} was changed in another window since this page was opened. "
            f"{says} Check it and save again."
        )
        again.append(replace(item, was=now))
    return again, errors


def saved_spellings(entries: list[Pronunciation]) -> dict[str, str]:
    """Each saved word's spelling, by word_key: "" for a word with phonemes instead."""
    return {word_key(e.word): e.sounds_like or "" for e in entries}


def shown_again(
    sent: list[SentWord],
    entries: list[Pronunciation],
    about: Callable[[str], str] = lambda word: "",
) -> list[WordItem]:
    """The words sent, to show again with what was typed. Each keeps the spelling its
    page was first shown with, so the next Save still saves only what changed; a word
    sent without one is shown with its saved spelling."""
    saved = saved_spellings(entries)
    return [
        WordItem(
            s.number,
            s.word,
            about(s.word),
            s.typed,
            s.was if s.was is not None else saved.get(word_key(tidy(s.word)), ""),
        )
        for s in sent
    ]


def field_for(sent: list[SentWord], word: str) -> str:
    """The "Say it like" field of `word`, or of the first word if it isn't one sent."""
    for item in sent:
        if word_key(tidy(item.word)) == word_key(word):
            return item.field
    return sent[0].field if sent else ""


def _number(text: str) -> int | None:
    if text.isascii() and text.isdecimal() and len(text) <= MAX_NUMBER_DIGITS:
        return int(text)
    return None
