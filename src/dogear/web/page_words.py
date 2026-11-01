"""The web page's own pronunciation file: words.toml in the web folder.

People fix words on the page (a book's "Check how words are said" page, and the
Pronunciations page) and never need to open this file. Every `make`, `words`, and `say`
the server runs gets it with --words (`option`), and `dogear` reads --words files after
the ones in the user's settings, so the page's entries win.

The page adds, changes, and removes sounds-like entries. Phoneme entries, which only
someone editing the file by hand can add, are kept as they are. Every word and spelling
is checked here before it is added or changed, with the bounds in [web]; any entry can
be removed. A save can say what the page showed for each word; then, if one of them was
changed since (in another window, say), it saves nothing rather than undo that change.

The file is written whole or not at all, owner-only like the rest of the web folder, and
read back through pronunciations.py before it replaces the old one, so a file `dogear`
can't read is never left in place.
"""

from __future__ import annotations

import errno
import threading
import unicodedata
from collections.abc import Mapping
from pathlib import Path

from dogear.errors import DogearError, PronunciationError
from dogear.pronunciation_writer import pronunciation_file_text
from dogear.pronunciations import (
    DEFAULT_LANGUAGES,
    Pronunciation,
    read_pronunciation_file,
    word_key,
)
from dogear.web.atomic import FileInUse, write_atomically
from dogear.web.private import private_folder
from dogear.web.settings import WordLimits

HEADER = (
    "Dogear's web page keeps this file: the words fixed on its Pronunciations page.\n"
    "Saving on the page writes it again. Phoneme entries written here by hand are kept."
)
BUG_HINT = "This is a bug in Dogear. Please report it."
# What writing to a full disk, or past a disk quota, fails with.
DISK_FULL = {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}


class EntryError(Exception):
    """A word or spelling the page can't save. `message` says why, in words for the page;
    `word` is the word it's about, when it's about one."""

    def __init__(self, message: str, word: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.word = word


class ChangedElsewhere(Exception):
    """Words whose spelling was changed since the page that sends them was shown.
    `words` has each word as sent, with its spelling now ("" if it has none)."""

    def __init__(self, words: dict[str, str]) -> None:
        super().__init__(", ".join(words))
        self.words = words


def words_option(path: Path) -> list[str]:
    """--words with the page's file (WebSettings.words_file), for a child command that
    takes it; nothing until the page has saved a word."""
    return ["--words", str(path)] if path.is_file() else []


def clean_word(text: str, limits: WordLimits) -> str:
    """A word as the book writes it, with its spacing tidied the way pronunciations.py
    tidies it. Raises EntryError if it can't be saved."""
    _one_line(text, "The word")
    word = tidy(text)
    if not word:
        raise EntryError("Type the word as the book writes it.")
    if len(word) > limits.word_chars:
        raise EntryError(f"A word can be at most {limits.word_chars} characters long.")
    if not any(c.isalnum() for c in word):
        raise EntryError("A word needs at least one letter or number.")
    return word


def clean_spelling(text: str, word: str, limits: WordLimits) -> str:
    """How to say `word`, tidied: "" when it's empty. Raises EntryError if it can't be
    saved."""
    _one_line(text, f"How to say {word}")
    spelling = tidy(text)
    if len(spelling) > limits.spelling_chars:
        raise EntryError(
            f"How to say {word} can be at most {limits.spelling_chars} characters long.",
            word,
        )
    return spelling


def tidy(text: str) -> str:
    """`text` with its spacing tidied, as pronunciations.py tidies words and spellings."""
    return " ".join(text.split())


def _one_line(text: str, what: str) -> None:
    # A tab pasted in is only space; line breaks and other control characters aren't text.
    if any(c != "\t" and unicodedata.category(c) in ("Cc", "Cs") for c in text):
        raise EntryError(f"{what} can't have line breaks or other control characters.")


class PageWords:
    """The page's pronunciation file, read afresh whenever it's needed. Saves happen one
    at a time, each a whole new file."""

    def __init__(self, path: Path, limits: WordLimits) -> None:
        self.path = path
        self.limits = limits
        self._lock = threading.Lock()

    def entries(self) -> list[Pronunciation]:
        """Every entry, in alphabetical order; none until the first save. Raises
        PronunciationError if the file was changed by hand into one `dogear` can't read."""
        if not self.path.exists():
            return []
        return sorted(read_pronunciation_file(self.path), key=lambda e: word_key(e.word))

    def save(self, spellings: Mapping[str, str], shown: Mapping[str, str] | None = None) -> None:
        """Give each word its spelling, or remove the word's entry if its spelling is "".
        A word given a spelling has been through clean_word and clean_spelling; a word
        being removed is matched to whichever entry it is. Words match as in any
        pronunciation file, whatever their capitals.

        `shown` has, for some of the words, the spelling the page showed when it was
        opened ("" for none). If one of them has another spelling now, and not the one
        asked for, someone changed it since: nothing is saved, and ChangedElsewhere names
        each such word. Raises EntryError for a word with a phoneme entry, or a new word
        when the file is full. Saves all or nothing."""
        shown = shown or {}
        with self._lock:
            before = {word_key(e.word): e for e in self.entries()}
            after = dict(before)
            added = ""
            changed: dict[str, str] = {}
            for word, spelling in spellings.items():
                key = word_key(word)
                existing = before.get(key)
                if existing is not None and existing.phonemes is not None:
                    if not spelling:
                        continue  # nothing to change: it has no spelling to remove
                    raise EntryError(
                        f"{existing.word} has exact phonemes in Dogear's word list, written "
                        "there by hand. The page doesn't change those.",
                        word,
                    )
                now = (existing.sounds_like or "") if existing is not None else ""
                if word in shown and now not in (shown[word], spelling):
                    changed[word] = now
                    continue
                if spelling:
                    languages = existing.languages if existing is not None else DEFAULT_LANGUAGES
                    after[key] = Pronunciation(word, spelling, None, self.path, languages)
                    if key not in before:
                        added = added or word
                else:
                    after.pop(key, None)
            if changed:
                raise ChangedElsewhere(changed)
            if len(after) > self.limits.entries and len(after) > len(before):
                raise EntryError(
                    f"Dogear's word list is full: it holds at most {self.limits.entries} "
                    "words. Remove words you no longer need on the Pronunciations page "
                    "first, or raise web.max_pronunciations in a settings file.",
                    added,
                )
            if _same(before, after):
                return
            self._write(sorted(after.values(), key=lambda e: word_key(e.word)))

    def _write(self, entries: list[Pronunciation]) -> None:
        expected = [_facts(e) for e in entries]

        def read_back(written: Path) -> None:
            try:
                found = [_facts(e) for e in read_pronunciation_file(written)]
            except PronunciationError as e:
                raise DogearError(
                    f"Dogear couldn't save its word list: the new file didn't read back "
                    f"({e.message}).",
                    hint=BUG_HINT,
                ) from None
            if found != expected:
                raise DogearError(
                    "Dogear couldn't save its word list: the new file didn't read back as "
                    "it was written.",
                    hint=BUG_HINT,
                )

        data = pronunciation_file_text(entries, HEADER).encode("utf-8")
        try:
            private_folder(self.path.parent, tighten=False)
            write_atomically(self.path, data, check=read_back)
        except FileInUse:
            raise DogearError(
                f"Dogear couldn't save its word list: another program had the pronunciation "
                f"file open ({self.path}).",
                hint="Try saving again in a moment.",
            ) from None
        except OSError as e:
            full = e.errno in DISK_FULL
            raise DogearError(
                f"Dogear couldn't save its word list in {self.path.parent}: {e.strerror or e}.",
                hint="Check that the disk isn't full, then try again."
                if full
                else "Check that Dogear can still write to that folder, then try again.",
            ) from None


def _same(before: dict[str, Pronunciation], after: dict[str, Pronunciation]) -> bool:
    def facts(entries: dict[str, Pronunciation]) -> dict[str, tuple]:
        return {k: _facts(e) for k, e in entries.items()}

    return facts(before) == facts(after)


def _facts(entry: Pronunciation) -> tuple:
    """What an entry says, without the file it came from."""
    return (entry.word, entry.sounds_like, entry.phonemes, entry.languages)
