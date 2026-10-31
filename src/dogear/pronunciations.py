"""The user's pronunciation dictionaries: TOML files saying how to read names and words.

A file has one [words] table. Each key is a word, or several, as the book writes it.
Its value is either the spelling the voice should read instead, or (for experts) the
exact phonemes, in the symbols of misaki, Kokoro's phonemizer:

    [words]
    Utterson = "utter son"             # sounds like: the voice reads this spelling instead
    "St. John" = "sin jun"             # several words, in quotes
    Lanyon = { phonemes = "lˈænjən" }  # exact phonemes

Sounds-like entries are applied in speech normalization, so they work for any voice and
show in `dogear text --spoken`. They apply to English text only, since a spelling that
sounds right read as English sounds wrong read as French, unless the entry lists its
languages: `Mme = { sounds_like = "madame", languages = ["fr"] }`. Phoneme entries are
pinned by the Kokoro voice's G2P,
and only to plain words: Dogear spells out abbreviations, numbers, and symbols before
the voice sees the text, so a phoneme entry written with them would never be found.
Files are read in order; when two list the same word, the later file wins. People write
these by hand, so every problem is reported with the file and the entry.
"""

from __future__ import annotations

import tomllib
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dogear.config import Config
from dogear.errors import PronunciationError
from dogear.language_match import ENGLISH, same_language
from dogear.output.language_tag import language_tag
from dogear.speech.word_match import WordMatch, WordMatcher

_TABLE = "words"
_PHONEMES = "phonemes"
_SOUNDS_LIKE = "sounds_like"
_LANGUAGES = "languages"
DEFAULT_LANGUAGES = (ENGLISH,)  # what a sounds-like entry applies to unless it says
_QUOTES_HINT = (
    "A word with spaces, dots, apostrophes, or letters other than A to Z needs quotes, "
    'such as "St. John" = "sin jun".'
)


@dataclass(frozen=True)
class Pronunciation:
    word: str  # as the file writes it, with its spacing tidied
    sounds_like: str | None  # the spelling the voice reads instead; or
    phonemes: str | None  # the exact phonemes. Exactly one of the two is set.
    source: Path  # the file it came from
    # The languages whose text a sounds-like entry applies to, as BCP 47 tags; any
    # tag of the same language matches ("en" covers en-GB).
    languages: tuple[str, ...] = DEFAULT_LANGUAGES

    def where(self) -> str:
        """The entry and its file, for messages."""
        return f"'{self.word}' in {self.source}"

    def applies_to(self, lang: str | None) -> bool:
        """Whether the entry applies to text in `lang` (None: English, as Dogear reads it)."""
        return any(same_language(tag, lang) for tag in self.languages)


class PronunciationDictionary:
    """Entries from every pronunciation file, found in text as `word_match` describes."""

    def __init__(self, entries: Iterable[Pronunciation] = ()) -> None:
        self.entries = tuple(entries)
        self._matcher = WordMatcher([entry.word for entry in self.entries])
        self._by_language: dict[str, PronunciationDictionary] = {}

    def find(self, text: str) -> Iterator[tuple[WordMatch, Pronunciation]]:
        for match in self._matcher.finditer(text):
            yield match, self.entries[match.index]

    def with_phonemes(self) -> PronunciationDictionary:
        return PronunciationDictionary(e for e in self.entries if e.phonemes is not None)

    def for_language(self, lang: str | None) -> PronunciationDictionary:
        """The entries that apply to text in `lang`. Phoneme entries apply to English."""
        key = (lang or ENGLISH).lower()
        if key not in self._by_language:
            self._by_language[key] = PronunciationDictionary(
                e for e in self.entries if e.applies_to(lang)
            )
        return self._by_language[key]

    def say_sounds_like(self, text: str) -> str:
        """`text` with each sounds-like entry's word replaced by its spelling.

        A word that starts with a capital keeps it ("Utterson" -> "Utter son"), so rules
        that look for a name, such as "Mr." becoming "Mister", still find one. Phoneme
        entries are matched too, so the longest entry wins whichever kind it is, but left
        as written: the voice engine pins their phonemes later.
        """

        def replace(match: WordMatch) -> str:
            entry = self.entries[match.index]
            if entry.sounds_like is None:
                return text[match.start : match.end]
            said = entry.sounds_like
            if text[match.start].isupper():
                said = said[0].upper() + said[1:]
            return said + match.possessive  # "Utterson's" -> "Utter son's"

        return self._matcher.sub(text, replace)


def dictionary_from_config(config: Config) -> PronunciationDictionary:
    """Every file in `pronunciation.files`, read in order."""
    return load_dictionary(pronunciation_files(config))


def pronunciation_files(config: Config) -> list[Path]:
    files = config["pronunciation"]["files"]
    if any(not f.strip() for f in files):
        raise PronunciationError(
            "pronunciation.files has an empty entry.",
            hint="Remove the empty quotes, or write a file's path between them.",
        )
    return [Path(f).expanduser() for f in files]


def load_dictionary(paths: Iterable[Path]) -> PronunciationDictionary:
    """The entries of every file, a later file's entry replacing an earlier one's."""
    entries: dict[str, Pronunciation] = {}
    for path in paths:
        for entry in read_pronunciation_file(path):
            entries[word_key(entry.word)] = entry
    return PronunciationDictionary(entries.values())


def read_pronunciation_file(path: Path) -> list[Pronunciation]:
    data = _load_toml(path)
    outside = [key for key in data if key != _TABLE]
    if outside:
        raise PronunciationError(
            f"{path} has '{outside[0]}' outside the [{_TABLE}] table.",
            hint=f"Put every entry below a line that says [{_TABLE}].",
        )
    if _TABLE not in data:
        raise PronunciationError(
            f"{path} has no [{_TABLE}] table, so it changes nothing.",
            hint=f"Start the file with a line that says [{_TABLE}], then add entries below "
            'it, one per line, such as: Utterson = "utter son"',
        )
    words = data[_TABLE]
    if not isinstance(words, dict):
        raise PronunciationError(
            f"In {path}, '{_TABLE}' must be a table: a line that says [{_TABLE}] with "
            "entries below it."
        )
    entries: dict[str, Pronunciation] = {}
    for key, value in words.items():
        entry = _entry(key, value, path)
        earlier = entries.get(word_key(entry.word))
        if earlier:
            raise PronunciationError(
                f"{path} lists '{earlier.word}' and '{entry.word}', which are the same word.",
                hint="Keep one of them. Capital letters don't matter when matching words.",
            )
        entries[word_key(entry.word)] = entry
    return list(entries.values())


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PronunciationError(
            f"Pronunciation file not found: {path}",
            hint="Check the file's name and folder. A path that doesn't start at a drive or "
            "/ is looked up from the folder you run Dogear in.",
        ) from None
    except UnicodeDecodeError:
        raise PronunciationError(
            f"Pronunciation file {path} isn't plain UTF-8 text.",
            hint="Save it as plain text with UTF-8 encoding, the usual choice in text editors.",
        ) from None
    except OSError as e:
        raise PronunciationError(
            f"Couldn't read pronunciation file {path}: {e.strerror or e}.",
            hint="Check that it's a file, not a folder, and that you can open it.",
        ) from None
    except tomllib.TOMLDecodeError as e:
        raise PronunciationError(
            f"Pronunciation file {path} is not valid TOML: {e}.",
            hint=f"Check the line it names. {_QUOTES_HINT}",
        ) from None


def _entry(key: str, value: Any, path: Path) -> Pronunciation:
    word = " ".join(key.split())
    if not word:
        raise PronunciationError(
            f'{path} has an entry with an empty word ("").',
            hint="Write the word between the quotes, or remove the entry.",
        )
    if not any(c.isalnum() for c in word):
        raise PronunciationError(
            f"The entry '{word}' in {path} has no letters or digits, so it can't match a word."
        )
    where = f"'{word}' in {path}"
    if isinstance(value, str):
        sounds_like = " ".join(value.split())
        if not sounds_like:
            raise PronunciationError(
                f"The entry {where} is empty.",
                hint="Write how the word sounds between the quotes, such as "
                'Utterson = "utter son".',
            )
        return Pronunciation(word, sounds_like=sounds_like, phonemes=None, source=path)
    if isinstance(value, dict):
        unknown = sorted(set(value) - {_PHONEMES, _SOUNDS_LIKE, _LANGUAGES})
        if unknown:
            raise PronunciationError(
                f"The entry {where} has '{unknown[0]}', which an entry can't have.",
                hint=f'An entry is a spelling in quotes, {{ {_SOUNDS_LIKE} = "...", '
                f'{_LANGUAGES} = ["fr"] }}, or {{ {_PHONEMES} = "..." }}. If the word has a dot '
                f"in it, put the whole word in quotes. {_QUOTES_HINT}",
            )
        if _SOUNDS_LIKE in value:
            return _sounds_like_table(value, word, where, path)
        phonemes = _phonemes(value, word, where)
        return Pronunciation(word, sounds_like=None, phonemes=phonemes, source=path)
    raise PronunciationError(
        f'The entry {where} must be a spelling in quotes, or {{ {_PHONEMES} = "..." }}, '
        f"not {value!r}.",
        hint='For example: Utterson = "utter son"',
    )


def _sounds_like_table(value: dict[str, Any], word: str, where: str, path: Path) -> Pronunciation:
    """An entry written as { sounds_like = "...", languages = [...] }."""
    if _PHONEMES in value:
        raise PronunciationError(
            f"The entry {where} has both {_SOUNDS_LIKE} and {_PHONEMES}.",
            hint="Keep one of them: a spelling to read instead, or the exact phonemes.",
        )
    spelling = value[_SOUNDS_LIKE]
    if not isinstance(spelling, str) or not spelling.strip():
        raise PronunciationError(
            f"The entry {where} needs its {_SOUNDS_LIKE} written in quotes.",
            hint=f'For example: Mme = {{ {_SOUNDS_LIKE} = "madame", {_LANGUAGES} = ["fr"] }}',
        )
    languages = value.get(_LANGUAGES, list(DEFAULT_LANGUAGES))
    if (
        not isinstance(languages, list)
        or not languages
        or not all(isinstance(t, str) and language_tag(t) == t for t in languages)
    ):
        raise PronunciationError(
            f"The entry {where} has {_LANGUAGES} = {languages!r}, which isn't a list of languages.",
            hint=f"List the languages it applies to as BCP 47 tags in quotes, such as "
            f'{_LANGUAGES} = ["fr"] or ["en", "fr"].',
        )
    return Pronunciation(
        word,
        sounds_like=" ".join(spelling.split()),
        phonemes=None,
        source=path,
        languages=tuple(languages),
    )


def _phonemes(value: dict[str, Any], word: str, where: str) -> str:
    if _LANGUAGES in value:
        raise PronunciationError(
            f"The entry {where} gives {_LANGUAGES}, but its phonemes are in the Kokoro "
            "voice's English symbols, so it applies to English only.",
            hint=f"Take out {_LANGUAGES}, or give the entry a {_SOUNDS_LIKE} spelling instead.",
        )
    phonemes = value.get(_PHONEMES)
    if not isinstance(phonemes, str) or not phonemes.strip():
        raise PronunciationError(
            f"The entry {where} needs its {_PHONEMES} written in quotes.",
            hint=f'For example: Lanyon = {{ {_PHONEMES} = "lˈænjən" }}',
        )
    # Matched in the text the voice is given, after Dogear's own rewrites.
    if not all(_is_letter(c) or c in " -'’" for c in word):
        raise PronunciationError(
            f"The phoneme entry {where} can only use letters, spaces, hyphens, and apostrophes.",
            hint="Dogear spells out abbreviations, numbers, and symbols before the voice reads "
            "them. Write the words the way `dogear text --spoken` shows them (such as "
            '"Saint John" for "St. John"), or give this entry a spelling in quotes instead.',
        )
    return " ".join(phonemes.split())


def _is_letter(c: str) -> bool:
    return unicodedata.category(c)[0] in "LM"  # letters, and accents written separately


def word_key(word: str) -> str:
    """What makes two entries the same word: case and apostrophe style don't count."""
    return word.replace("’", "'").casefold()
