"""Rewrite one sentence of display text into what a voice engine should say.

Engines differ in how they read "Dr.", "3.14", or "1984", so Dogear spells these out
itself and every engine gets the same words. All of these rewrites are English: the
abbreviation tables in config, numbers (with num2words), currency, clock times, units,
and roman numerals. Text in another language is only tidied (plain quotes, dashes as
pauses, invisible characters taken out) and given that language's pronunciation
entries, then goes to its voice as written: other languages write numbers differently
("1,5" is one and a half in French), and their voices' engines read numbers, symbols,
and abbreviations by their own rules, as Piper's eSpeak NG does. Text with no language
is read as English, as Dogear reads a book that doesn't say.

The user's pronunciation files come first, before any of Dogear's own rewrites, so an
entry matches the book's spelling ("St. John", not "Saint John").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from num2words import num2words

from dogear.config import Config
from dogear.language_match import is_english
from dogear.pronunciations import PronunciationDictionary, dictionary_from_config
from dogear.speech.money import say_money
from dogear.speech.roman import heading_numerals, keyword_numerals
from dogear.speech.spoken import ENGLISH, Pair, keep_final_period, say_amount
from dogear.speech.times import say_times
from dogear.speech.units import say_units

_PUNCTUATION = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    " ": " ",
}
_EN_DASH_RANGE = re.compile(r"(?<=\d)\s*–\s*(?=\d)")  # 1990–1995 -> 1990 to 1995
_DASHES = re.compile(r"\s*[–—]\s*")
_MARKUP = re.compile(r"[#*_`~]")
# Any run of spaces, tabs, or line breaks. A line break inside a sentence (from <br/> in
# a book's colophon or a poem) is a space to the voice: misaki would otherwise join the
# words on either side into one unknown word.
_SPACES = re.compile(r"\s+")

# One pass over every number, most specific form first. The lookbehind keeps numbers
# inside words or version strings ("v1.2", "MP3") as they are.
_NUMBER = re.compile(
    r"""(?<![\w.,])(?:
        (?P<grouped>\d{1,3}(?:,\d{3})+)(?![\d])
      | (?P<decimal>\d+\.\d+)(?![\d])
      | (?P<ordinal>\d+)(?P<suffix>(?i:st|nd|rd|th))\b
      | (?P<int>\d+)(?![\d])
    )""",
    re.VERBOSE,
)
_YEAR_RANGE = range(1100, 2100)  # four-digit numbers read the way people say years
_LONGEST_CARDINAL = 999_999  # longer ungrouped numbers are usually codes: read digit by digit


@dataclass(frozen=True)
class SpeechRules:
    abbreviations: dict[str, str]
    before_name: dict[str, str]
    before_number: dict[str, str]
    roman_keywords: tuple[str, ...]
    time_suffixes: dict[str, str]
    currency: dict[str, Pair]
    currency_cents: dict[str, Pair]
    currency_scales: dict[str, str]
    units: dict[str, Pair]
    # The user's pronunciation files: sounds-like entries are applied here.
    dictionary: PronunciationDictionary = field(default_factory=PronunciationDictionary)

    @classmethod
    def from_config(cls, config: Config) -> SpeechRules:
        speech = config["speech"]
        return cls(
            abbreviations=dict(speech["abbreviations"]),
            before_name=dict(speech["abbreviations_before_name"]),
            before_number=dict(speech["abbreviations_before_number"]),
            roman_keywords=tuple(speech["roman_numeral_keywords"]),
            time_suffixes=dict(speech["time_suffixes"]),
            currency=_pairs(speech["currency"]),
            currency_cents=_pairs(speech["currency_cents"]),
            currency_scales=dict(speech["currency_scales"]),
            units=_pairs(speech["units"]),
            dictionary=dictionary_from_config(config),
        )

    def patterns(self) -> list[tuple[re.Pattern[str], str]]:
        def build(table: dict[str, str], context: str) -> list[tuple[re.Pattern[str], str]]:
            # Longest first, so "pp." wins over "p.". The lookbehind stops "p." matching
            # inside "pp." or a longer word.
            keys = sorted(table, key=len, reverse=True)
            return [(re.compile(rf"(?<![\w.]){re.escape(k)}{context}"), table[k]) for k in keys]

        return (
            build(self.before_name, r"(?=\s+[A-Z])")
            + build(self.before_number, r"(?=\s*\d)")
            + build(self.abbreviations, "")
        )


def normalize_for_speech(text: str, rules: SpeechRules, lang: str | None) -> str:
    """One sentence of body text, rewritten as the words a voice should say."""
    return _speak(_prepare(text, rules, lang), rules, lang)


def normalize_heading(text: str, rules: SpeechRules, lang: str | None) -> str:
    """Like `normalize_for_speech`, for a heading or section title. A roman numeral that
    numbers the heading is read as a number too: "IV." is "four.", and "CHAPTER I THE
    BEGINNING" is "CHAPTER one THE BEGINNING". Body text leaves both alone, since there
    a bare numeral may be a letter and a lone "I" before a word may be the pronoun.
    """
    text = _prepare(text, rules, lang)
    if is_english(lang):
        text = heading_numerals(text, rules.roman_keywords)
    return _speak(text, rules, lang)


def _prepare(text: str, rules: SpeechRules, lang: str | None) -> str:
    """Punctuation made plain, then the user's sounds-like entries for the language."""
    tidied = _normalize_punctuation(text, is_english(lang))
    return rules.dictionary.for_language(lang).say_sounds_like(tidied)


def _speak(text: str, rules: SpeechRules, lang: str | None) -> str:
    if is_english(lang):
        text = _english_passes(text, rules)
        for pattern, expansion in rules.patterns():
            text = pattern.sub(lambda m, e=expansion: keep_final_period(m, e), text)
        text = _NUMBER.sub(_speak_number, text)
    text = _MARKUP.sub("", text)
    return _SPACES.sub(" ", text).strip()


def _english_passes(text: str, rules: SpeechRules) -> str:
    # Roman numerals go first: they become digits that the abbreviation and number passes
    # then read. With "Vol." as a keyword, "Vol. IV" -> "Vol. 4" -> "volume four".
    text = keyword_numerals(text, rules.roman_keywords)
    text = say_times(text, rules.time_suffixes)
    text = say_money(text, rules.currency, rules.currency_cents, rules.currency_scales)
    return say_units(text, rules.units)


def _pairs(table: dict[str, list[str]]) -> dict[str, Pair]:
    return {key: (singular, plural) for key, (singular, plural) in table.items()}


def _normalize_punctuation(text: str, english: bool) -> str:
    # Invisible formatting characters (word joiners, zero-width spaces, soft hyphens) help
    # page layout but can trip a voice engine.
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    for char, replacement in _PUNCTUATION.items():
        text = text.replace(char, replacement)
    if english:
        text = _EN_DASH_RANGE.sub(" to ", text)
    else:
        text = _EN_DASH_RANGE.sub("-", text)  # a range, as the language's voice reads one
    return _DASHES.sub(", ", text)  # a dash is a pause; a comma is the safest pause cue


def _speak_number(match: re.Match[str]) -> str:
    if amount := match.group("grouped") or match.group("decimal"):
        return say_amount(amount, ENGLISH)
    if ordinal := match.group("ordinal"):
        return num2words(int(ordinal), lang=ENGLISH, to="ordinal")
    return _speak_integer(match.group("int"))


def _speak_integer(digits: str) -> str:
    value = int(digits)
    if (digits.startswith("0") and len(digits) > 1) or value > _LONGEST_CARDINAL:
        return " ".join(num2words(int(d), lang=ENGLISH) for d in digits)
    if len(digits) == 4 and value in _YEAR_RANGE:
        return num2words(value, lang=ENGLISH, to="year")
    return num2words(value, lang=ENGLISH)
