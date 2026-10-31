"""Which voice reads which language: [voice.languages] and [voice] other_languages.

Every passage has a language: the one the book marks on it, its chapter's, the book's,
or one that language detection found. Its voice is, in order:

1. the voice [voice.languages] gives the most specific tag that matches it ("fr-CA",
   then "fr");
2. the main voice ([voice] voice), if it speaks that language;
3. with other_languages = "best", the best installed voice for the language;
4. otherwise the main voice, which then reads a language it doesn't speak, as it always
   has.

A passage with no language at all is the main voice's. Nothing here loads or downloads
a voice: it only chooses among the voices the installed engines list with these settings
(`Engine.voices_in`), so a speech service's voices are the ones its settings name.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from dogear.config import Config
from dogear.errors import ConfigError, VoiceError
from dogear.language_match import lookup_keys, most_specific, primary, same_language
from dogear.output.language_tag import language_tag
from dogear.voice.cloud import text_goes_to
from dogear.voice.engines import ChosenVoice, Engine, configured_voice, find_voice, installed

MAIN = "main"  # other_languages: the main voice reads them
BEST = "best"  # other_languages: the best installed voice for each, if any
OTHER_LANGUAGES = (MAIN, BEST)


@dataclass(frozen=True)
class LanguageVoices:
    main: ChosenVoice
    mapped: dict[str, ChosenVoice]  # language tag, as lookup_keys writes it -> its voice
    best_for_others: bool
    # The settings: they give each engine's voices, and with "best", a voice that sends
    # text off this computer is never chosen that way, only when the settings name it.
    config: Config = field(compare=False, repr=False)

    @classmethod
    def from_config(cls, config: Config, main: ChosenVoice | None = None) -> LanguageVoices:
        """The voices the settings give each language, around `main` (by default the
        configured voice)."""
        return cls(
            main=main or configured_voice(config),
            mapped=mapped_voices(config),
            best_for_others=other_languages(config) == BEST,
            config=config,
        )

    def voice_for(self, lang: str | None) -> ChosenVoice:
        """The voice that reads a passage in `lang`."""
        return self.speaker(lang) or self.main

    def speaker(self, lang: str | None) -> ChosenVoice | None:
        """A voice that speaks `lang`, by the order above, or None if there is none and
        the main voice would read it without speaking it."""
        if not lang:
            return self.main
        mapped = most_specific(self.mapped, lang)
        if mapped is not None:
            return mapped
        if same_language(self.main.voice.language, lang):
            return self.main
        if self.best_for_others:
            return best_voice(lang, prefer=self.main.engine, config=self.config)
        return None

    def speaks(self, lang: str | None) -> bool:
        return self.speaker(lang) is not None

    def voices(self) -> list[ChosenVoice]:
        """Every voice a run with these settings may use, whatever the book: the main
        voice first, then each [voice.languages] voice, then, with other_languages =
        "best", each voice that may be chosen for a language. Each once."""
        found = {self.main.spec: self.main}
        for chosen in self.mapped.values():
            found.setdefault(chosen.spec, chosen)
        if self.best_for_others:
            # A passage's tag picks the voice for that exact tag, else for its language,
            # so asking for every tag the voices have, and every language, finds them all.
            tags = {v.language for e in installed().engines for v in e.voices_in(self.config)}
            for tag in sorted(tags | {primary(t) for t in tags}):
                chosen = self.speaker(tag)
                if chosen is not None:
                    found.setdefault(chosen.spec, chosen)
        return list(found.values())

    def languages(self) -> set[str]:
        """Every language (its first part, such as "fr") some voice here would read:
        what language detection looks for."""
        return {primary(c.voice.language) for c in self.voices()}


def voices_for_run(config: Config, main: ChosenVoice | None = None) -> list[ChosenVoice]:
    """Every voice a run with these settings may use (see `LanguageVoices.voices`),
    known before any book is read. `main` is the main voice, by default the configured
    one."""
    return LanguageVoices.from_config(config, main).voices()


def best_voice(
    lang: str, prefer: Engine | None = None, config: Config | None = None
) -> ChosenVoice | None:
    """The best installed voice for `lang`: one for its exact tag first (fr-CA), then one
    for its language (fr). Within each, the `prefer` engine's voices come first, then the
    other engines' in the order `dogear voices` lists them, each engine's best first.
    With `config`, a voice that would send text off this computer is never chosen: a
    cloud voice reads a language only when the settings name it."""
    engines = list(installed().engines)
    if prefer is not None:
        engines.sort(key=lambda e: e.name != prefer.name)  # stable: the rest keep their order
    voices = [ChosenVoice(e, v) for e in engines for v in e.voices_in(config)]
    if config is not None:
        voices = [c for c in voices if text_goes_to(c, config) is None]
    exact = [c for c in voices if c.voice.language.lower() == lang.lower()]
    same = [c for c in voices if same_language(c.voice.language, lang)]
    return next(iter(exact + same), None)


def voices_for(lang: str, config: Config) -> list[ChosenVoice]:
    """Every installed voice that speaks `lang` with these settings, best first, as
    `best_voice` ranks them."""
    voices = [ChosenVoice(e, v) for e in installed().engines for v in e.voices_in(config)]
    exact = [c for c in voices if c.voice.language.lower() == lang.lower()]
    return exact + [c for c in voices if same_language(c.voice.language, lang) and c not in exact]


def other_languages(config: Config) -> str:
    value = config["voice"]["other_languages"]
    if value not in OTHER_LANGUAGES:
        raise ConfigError(
            f"voice.other_languages is '{value}', which isn't one of: "
            f"{', '.join(OTHER_LANGUAGES)}.",
            hint=f'Use "{MAIN}" to read every language with the main voice, or "{BEST}" to '
            "read each with the best installed voice for it.",
        )
    return value


def mapped_voices(config: Config) -> dict[str, ChosenVoice]:
    """[voice.languages], each voice found among those these settings give (so a speech
    service's are the ones its own table lists) and checked to speak its language."""
    mapped: dict[str, ChosenVoice] = {}
    for tag, spec in config["voice"]["languages"].items():
        where = f"voice.languages.{tag}"
        if language_tag(tag) != tag:
            raise ConfigError(
                f"{where} isn't named after a language: '{tag}' isn't a BCP 47 tag such "
                "as fr or pt-BR.",
                hint="Name each entry after the language its voice reads, such as "
                'fr = "piper:fr_FR-mls-medium".',
            )
        key = lookup_keys(tag)[0]  # "FR" and "fra" are both "fr"
        if key in mapped:
            raise ConfigError(
                f"[voice.languages] lists the language {tag} twice, written two ways.",
                hint="Keep one of them.",
            )
        chosen = _found(where, spec, config)
        if not same_language(chosen.voice.language, tag):
            raise ConfigError(
                f"{where} is {chosen.spec}, which speaks {chosen.voice.language} "
                f"({chosen.voice.description}), not {tag}.",
                hint=_voices_hint(tag, config),
            )
        mapped[key] = chosen
    return mapped


def check_language_voices(config: Config) -> None:
    """Check [voice] other_languages and [voice.languages] as the settings load, so a
    mistake is found before any book is read."""
    other_languages(config)
    mapped_voices(config)


def _found(where: str, spec: str, config: Config) -> ChosenVoice:
    engine, colon, _ = spec.strip().rpartition(":")
    found = installed()
    if colon and found.get(engine) is None and found.broken_named(engine) is None:
        names = ", ".join(e.name for e in found.engines) or "(none)"
        raise ConfigError(
            f"{where} is '{spec}', but no voice engine called '{engine}' is installed.",
            hint=f"Install it, or take this line out of your settings. Installed engines: {names}.",
        )
    try:
        return find_voice(spec, config)
    except VoiceError as e:
        raise ConfigError(f"{where} is '{spec}'. {e.message}", hint=e.hint) from None


def _voices_hint(tag: str, config: Config) -> str:
    found = voices_for(tag, config)
    if not found:
        return f"No installed voice speaks {tag}. Run `dogear voices` to list them."
    return f"Voices for {tag}: {_names(found)}."


def _names(voices: Iterable[ChosenVoice]) -> str:
    return ", ".join(c.spec for c in voices)
