"""Guessing the language of text a book doesn't mark, for [language_detection].

Dogear uses py3langid (BSD-3-Clause): a naive Bayes classifier over byte n-grams, with
a model of 139 languages (4.4 MB) inside the package, so it runs offline. It gives the
same answer for the same text every time and takes a fraction of a millisecond for a
paragraph. The model is loaded only when detection is on: about a third of a second and
85 MB of memory.

Guessing is careful on purpose, since a wrong guess changes the voice mid-chapter:

- The identifier weighs every language it knows, not just the ones Dogear could read.
  Told to choose between only English and French, it calls German "English"; told to
  choose among all of them, it says it isn't sure.
- A guess is used only if it is a language some voice reads, the text has at least
  `min_letters` letters, and the identifier is at least `min_confidence` sure of it.
- Narration asks only about text whose language the book leaves to the book as a whole:
  a language marked on a passage, or on its chapter, is never second-guessed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cache

from dogear.config import Config
from dogear.errors import ConfigError

# The identifier: text in, (language, how sure it is from 0 to 1) out.
Identify = Callable[[str], tuple[str, float]]


@dataclass(frozen=True)
class DetectionSettings:
    enabled: bool
    min_letters: int
    min_confidence: float
    sentence_check_min_letters: int

    @classmethod
    def from_config(cls, config: Config) -> DetectionSettings:
        table = config["language_detection"]
        settings = cls(
            enabled=table["enabled"],
            min_letters=table["min_letters"],
            min_confidence=float(table["min_confidence"]),
            sentence_check_min_letters=table["sentence_check_min_letters"],
        )
        if settings.min_letters < 1:
            raise ConfigError(
                f"language_detection.min_letters is {settings.min_letters}, but it must be "
                "at least 1.",
                hint="Use 30 or more: shorter text is easily mistaken for another language.",
            )
        if not 0 < settings.min_confidence <= 1:
            raise ConfigError(
                f"language_detection.min_confidence is {settings.min_confidence}, but it "
                "must be more than 0 and at most 1.",
                hint="Use a value such as 0.9: a guess must be at least that sure to be used.",
            )
        if settings.sentence_check_min_letters < settings.min_letters:
            raise ConfigError(
                "language_detection.sentence_check_min_letters is less than min_letters, so "
                "every sentence it checks would be too short.",
                hint="Make it at least min_letters, such as 300.",
            )
        return settings


def check_detection_settings(config: Config) -> None:
    """Check [language_detection] as the settings load."""
    DetectionSettings.from_config(config)


class LanguageGuesser:
    """Guesses, among `languages` (first parts of tags, such as "fr"), the language of a
    piece of text, or says it can't tell."""

    def __init__(
        self,
        settings: DetectionSettings,
        languages: Iterable[str],
        identify: Identify | None = None,
    ) -> None:
        self.settings = settings
        self.languages = frozenset(languages)
        self._identify = identify or py3langid_identifier()

    def guess(self, text: str) -> str | None:
        """The language of `text`, if it is long enough and the guess sure enough."""
        if letters(text) < self.settings.min_letters:
            return None
        language, confidence = self._identify(text)
        if language in self.languages and confidence >= self.settings.min_confidence:
            return language
        return None

    def checks_sentences(self, text: str) -> bool:
        """Whether `text` is long enough to be checked a sentence at a time as well."""
        return letters(text) >= self.settings.sentence_check_min_letters


def language_guesser(config: Config, languages: Iterable[str]) -> LanguageGuesser | None:
    """A guesser for `languages` when [language_detection] is on and there is something
    to find: at least two languages, since with one every guess leaves the voice as it is."""
    settings = DetectionSettings.from_config(config)
    wanted = frozenset(languages)
    if not settings.enabled or len(wanted) < 2:
        return None
    return LanguageGuesser(settings, wanted)


def letters(text: str) -> int:
    return sum(c.isalpha() for c in text)


@cache
def py3langid_identifier() -> Identify:
    """py3langid's model, loaded once per process."""
    # Imported here, so only a run that detects languages loads the model.
    from py3langid.langid import MODEL_FILE, LanguageIdentifier

    identifier = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
    return identifier.classify
