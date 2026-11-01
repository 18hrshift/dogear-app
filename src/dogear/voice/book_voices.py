"""The voices one book is read with: which each passage needs, and the loaded voices.

`dogear make` works out, from every utterance's language, which voices the book needs
(`LanguageVoices` chooses them), loads each once for the run, and gives the renderer a
`BookVoices`. The book's audio is written at the highest sample rate among them, so no
voice loses any of its sound; the others are resampled up to it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from dogear.errors import VoiceError
from dogear.language_match import primary
from dogear.narration import SectionNarration
from dogear.voice.engine import VoiceEngine
from dogear.voice.engines import ChosenVoice, VoicePace
from dogear.voice.language_voices import LanguageVoices


@dataclass(frozen=True)
class VoiceUse:
    """One voice the book needs, and what it reads."""

    voice: ChosenVoice
    languages: tuple[str, ...]  # the languages of the passages it reads, as tagged
    utterances: int  # how many utterances with words it says


def voices_needed(
    narrations: Iterable[SectionNarration], choices: LanguageVoices
) -> list[VoiceUse]:
    """Every voice the narrations need, in the order they are first heard."""
    found: dict[str, ChosenVoice] = {}
    languages: dict[str, dict[str, None]] = {}
    counts: Counter[str] = Counter()
    for narration in narrations:
        for utterance in narration.utterances:
            if not utterance.spoken:
                continue
            chosen = choices.voice_for(utterance.lang)
            found.setdefault(chosen.spec, chosen)
            languages.setdefault(chosen.spec, {})[utterance.lang or ""] = None
            counts[chosen.spec] += 1
    return [
        VoiceUse(chosen, tuple(lang for lang in languages[spec] if lang), counts[spec])
        for spec, chosen in found.items()
    ]


def unspoken_languages(
    narrations: Iterable[SectionNarration], choices: LanguageVoices
) -> dict[str, int]:
    """Languages (first parts of tags) of utterances that no voice here speaks, so the
    main voice reads them anyway, with how many utterances each."""
    counts: Counter[str] = Counter()
    for narration in narrations:
        for utterance in narration.utterances:
            if utterance.spoken and not choices.speaks(utterance.lang):
                counts[primary(utterance.lang)] += 1
    return dict(counts)


def book_pace(paces: Iterable[VoicePace]) -> VoicePace:
    """What to plan disk space for when several voices read a book: the highest sample
    rate (the one the audio is written at) and the slowest pace (the longest audio)."""
    paces = list(paces)
    if not paces:
        raise ValueError("a book needs at least one voice")
    return VoicePace(max(p.sample_rate for p in paces), min(p.words_per_minute for p in paces))


@dataclass(frozen=True)
class BookVoices:
    """Each loaded voice, by its spec ("piper:fr_FR-mls-medium"), and which reads what."""

    choices: LanguageVoices
    engines: Mapping[str, VoiceEngine]

    def __post_init__(self) -> None:
        if not self.engines:
            raise ValueError("a book needs at least one voice")

    @property
    def sample_rate(self) -> int:
        return max(engine.sample_rate for engine in self.engines.values())

    def for_language(self, lang: str | None) -> VoiceEngine:
        chosen = self.choices.voice_for(lang)
        engine = self.engines.get(chosen.spec)
        if engine is None:  # voices_needed lists every voice the book's passages need
            raise VoiceError(
                f"The voice {chosen.spec}, for a passage in {lang}, wasn't loaded.",
                hint="This is a bug in Dogear. Please report it, saying what you ran.",
            )
        return engine
