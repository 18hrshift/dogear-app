"""`dogear words`: the words in a book the voice doesn't know, most frequent first.

These are the words misaki's dictionaries don't have, which the voice hands to the
espeak-ng program to guess. Names and made-up words are the usual ones: worth a listen
with `dogear say`, and an entry in a pronunciation file if they come out wrong. Words
the user's files already cover aren't listed. This needs spaCy's English model (about
13 MB) but not the Kokoro voice. It only applies to engines that read misaki's phonemes,
as Kokoro does: another engine has its own way of saying words it doesn't know.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dogear.config import Config
from dogear.errors import VoiceError
from dogear.extract import extract_book
from dogear.make import check_language, chosen_narrations, guesser_for, settle_language
from dogear.narration import narrate_book
from dogear.pronunciations import dictionary_from_config
from dogear.render.progress import Report
from dogear.voice.engines import ChosenVoice, configured_voice
from dogear.voice.kokoro_plugin import prepare_spacy
from dogear.voice.language_voices import LanguageVoices

_EDGES = re.compile(r"^\W+|\W+$")  # quotes, brackets, and punctuation around a word
_POSSESSIVE = re.compile(r"(?<=\w)['’]s$", re.IGNORECASE)


@dataclass(frozen=True)
class WordsRequest:
    book: Path
    sections: set[int] | None = None  # numbers from `dogear outline`; None means all
    language: str | None = None  # overrides the language the book declares


@dataclass(frozen=True)
class UnknownWord:
    word: str  # the spelling the book uses most
    count: int


def find_unknown_words(request: WordsRequest, config: Config, report: Report) -> list[UnknownWord]:
    """The book's unknown words, most frequent first, as the configured voice would meet them."""
    chosen = configured_voice(config)
    _check_reads_misaki(chosen)
    book = extract_book(request.book, config, report)
    if request.language:
        book.metadata.language = request.language
    choices = LanguageVoices.from_config(config, chosen)
    settle_language(book, choices, report)
    check_language(book, choices)
    guesser = guesser_for(config, choices, report)
    narrations = chosen_narrations(
        narrate_book(book, config, guesser), request.sections, len(book.sections)
    )
    british = chosen.voice.language.lower() == "en-gb"
    dictionary = dictionary_from_config(config)
    spacy_model = prepare_spacy(config, report)

    from dogear.voice.g2p import count_unknown_words  # loads misaki and spaCy: slow

    # Only what this voice reads: a passage another voice reads is that voice's business.
    spoken = [
        u.spoken
        for n in narrations
        for u in n.utterances
        if u.spoken and choices.voice_for(u.lang).spec == chosen.spec
    ]
    return rank_words(count_unknown_words(spoken, spacy_model, british, dictionary))


def _check_reads_misaki(chosen: ChosenVoice) -> None:
    """The words come from misaki's dictionaries, so they are only the words a voice
    doesn't know if the voice reads misaki's phonemes, as Kokoro's do."""
    if chosen.engine.plugin.reads_misaki_phonemes:
        return
    raise VoiceError(
        f"Dogear can't list the words {chosen.voice.name} doesn't know: it lists the words "
        f"missing from the Kokoro voice's dictionaries, and {chosen.voice.name} is a "
        f"{chosen.engine.title} voice, which works out how to say words another way.",
        hint="Listen to the book's names with the voice instead, or check its words with a "
        "Kokoro voice, such as af_heart.",
    )


def rank_words(counts: Counter[str]) -> list[UnknownWord]:
    """One entry per word, as a pronunciation file would list it: whatever its capitals,
    without punctuation around it, and with "Utterson's" counted as "Utterson". Most
    frequent first; ties in alphabetical order. Lone punctuation isn't a word to list."""
    totals: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = {}
    for word, count in counts.items():
        base = _POSSESSIVE.sub("", _EDGES.sub("", word))
        if not base:
            continue
        key = base.casefold()
        totals[key] += count
        spellings.setdefault(key, Counter())[base] += count
    ranked = sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))
    return [UnknownWord(spellings[key].most_common(1)[0][0], count) for key, count in ranked]


def words_to_json(words: list[UnknownWord]) -> dict[str, Any]:
    """What `dogear words --json` prints: {"words": [{"word": ..., "count": ...}, ...]},
    most frequent first."""
    return {"words": [{"word": w.word, "count": w.count} for w in words]}


def words_from_json(data: Any) -> list[UnknownWord]:
    """Words in `words_to_json`'s shape, checked one by one. Raises ValueError naming the
    first one that is missing or wrong."""
    if not isinstance(data, dict) or not isinstance(data.get("words"), list):
        raise ValueError("there is no list of words")
    words = []
    for number, item in enumerate(data["words"], start=1):
        word = item.get("word") if isinstance(item, dict) else None
        count = item.get("count") if isinstance(item, dict) else None
        if not isinstance(word, str) or not word.strip():
            raise ValueError(f"word {number} has no text")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError(f"word {number} has no count, or it isn't a whole number above 0")
        words.append(UnknownWord(word, count))
    return words
