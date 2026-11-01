"""The "Choose what to make" form: what it offers, what starts out chosen, and checking
what comes back. Defaults come from the config, or from the book's last choices."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dogear.book_summary import BookSummary
from dogear.config import Config
from dogear.errors import DogearError, VoiceError
from dogear.language_match import is_english, most_specific
from dogear.make import FORMATS
from dogear.voice.cloud import CloudVoice, cloud_voices, sends_text, text_goes_to
from dogear.voice.engines import (
    ChosenVoice,
    Engine,
    configured_voice,
    describe_voice,
    find_voice,
    installed,
)
from dogear.voice.language_voices import (
    BEST,
    LanguageVoices,
    mapped_voices,
    other_languages,
    voices_for,
    voices_for_run,
)
from dogear.web.books import Choices
from dogear.web.forms import every, first

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormatInfo:
    name: str  # as `dogear make --format` takes it
    title: str
    description: str


FORMAT_INFO = {
    info.name: info
    for info in (
        FormatInfo(
            "m4b",
            "M4B audiobook",
            "One file with chapters, for audiobook apps on a phone or computer.",
        ),
        FormatInfo(
            "mp3",
            "MP3 folder",
            "A folder of MP3 files, one for each section, with a playlist. Plays almost "
            "anywhere, including older players.",
        ),
        FormatInfo(
            "epub",
            "EPUB talking book",
            "The text and the audio together, each sentence highlighted as it's read, for "
            "reading apps such as Thorium Reader.",
        ),
        FormatInfo(
            "daisy",
            "DAISY talking book",
            "For DAISY players and apps, such as a Victor Reader Stream, which move by "
            "heading and by print page.",
        ),
    )
}

# Digits in the longest section number the form takes: more than reading.MAX_SECTIONS.
MAX_SECTION_DIGITS = 5

# Speeds offered, in words. The configured speed is added if it isn't one of these.
SPEEDS: tuple[tuple[float, str], ...] = (
    (0.8, "Slower"),
    (0.9, "A little slower"),
    (1.0, "Natural"),
    (1.1, "Faster"),
)


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    sends_to: str = ""  # for a voice, the computer it sends the book's text to, if another


@dataclass(frozen=True)
class VoiceGroup:
    """One engine's voices, shown together under its name."""

    label: str
    options: tuple[Option, ...]
    for_book_language: bool = False  # the voices that speak the book's language, first


@dataclass(frozen=True)
class Picked:
    """What the form shows as chosen: the defaults, or what was sent back."""

    formats: frozenset[str]
    voice: str
    speed: str
    sections: frozenset[int]
    as_english: bool


def format_options() -> list[FormatInfo]:
    return list(FORMAT_INFO.values())


def voice_options(config: Config, summary: BookSummary | None = None) -> list[VoiceGroup]:
    """Each installed engine's voices, best first. A value names the engine as well as
    the voice ("kokoro:af_heart"), so it stays clear whatever other engines have. Voices
    that send the book's text off this computer are offered only when the settings allow
    them ([voice] allow_cloud), and say where the text goes. For a book in another
    language than English, the voices that speak it come first, in a group of their own."""
    engines = []
    for engine in installed().engines:
        try:
            engines.append((engine, _engine_options(engine, config)))
        except DogearError as e:
            # Its settings are wrong: `dogear make` would say so too. The rest still work.
            log.warning("The %s voices aren't offered: %s %s", engine.title, e.message, e.hint)
    offered = {option.value: option for _, options in engines for option in options}
    own = [c for c in language_voices(summary, config) if c.spec in offered] if summary else []
    groups = []
    if own and summary is not None:
        groups.append(
            VoiceGroup(
                f"Voices for this book's language ({summary.language})",
                tuple(
                    Option(
                        c.spec,
                        f"{offered[c.spec].label} ({c.engine.title})",
                        offered[c.spec].sends_to,
                    )
                    for c in own
                ),
                for_book_language=True,
            )
        )
    first = {c.spec for c in own}
    for engine, options in engines:
        options = tuple(option for option in options if option.value not in first)
        if options:
            groups.append(VoiceGroup(f"{engine.title} voices", options))
    return groups


def _engine_options(engine: Engine, config: Config) -> tuple[Option, ...]:
    options = []
    for voice in engine.voices_in(config):
        host = text_goes_to(ChosenVoice(engine, voice), config)
        if host is not None and not config["voice"]["allow_cloud"]:
            continue
        label = describe_voice(voice) + (f" ({sends_text(host)})" if host else "")
        options.append(Option(f"{engine.name}:{voice.name}", label, host or ""))
    return tuple(options)


def voice_values(config: Config) -> set[str]:
    return {option.value for group in voice_options(config) for option in group.options}


def cloud_voices_for_run(spec: str, config: Config) -> list[CloudVoice]:
    """Every voice a book made with `spec` as its voice may use that sends the book's
    text off this computer, with where: the chosen voice, and any voice [voice.languages]
    or other_languages = "best" would give another language (voices_for_run, as
    `dogear make` checks). `spec` is one of `voice_values`."""
    return cloud_voices(voices_for_run(config, find_voice(spec, config)), config)


def offers_cloud_voices(groups: list[VoiceGroup]) -> bool:
    return any(option.sends_to for group in groups for option in group.options)


def book_in_english(summary: BookSummary) -> bool:
    """Whether the book is in English, as far as it says: a book that doesn't say is."""
    return is_english(summary.language)


def language_voices(summary: BookSummary, config: Config) -> list[ChosenVoice]:
    """The installed voices that speak the book's language with these settings, best
    first; none for a book in English or one that doesn't say."""
    if is_english(summary.language):
        return []
    return voices_for(summary.language or "", config)


def asks_about_language(summary: BookSummary, config: Config) -> bool:
    """Whether the form asks about the book's language: it says it's in another language
    than English, and the settings don't give that language a voice of its own (a voice
    under [voice.languages], or other_languages = "best" with one installed)."""
    if is_english(summary.language):
        return False
    if most_specific(mapped_voices(config), summary.language) is not None:
        return False
    return not (other_languages(config) == BEST and language_voices(summary, config))


def speaks_book_language(summary: BookSummary, config: Config, voice: str) -> bool:
    """Whether the book, in its own language, is read by a voice that speaks it, when
    `voice` is the main voice."""
    return LanguageVoices.from_config(config, main=find_voice(voice, config)).speaks(
        summary.language
    )


def speed_options(config: Config) -> list[Option]:
    usual = float(config["voice"]["speed"])
    speeds = list(SPEEDS)
    if all(abs(usual - speed) > 1e-9 for speed, _ in speeds):
        speeds = sorted([*speeds, (usual, "Your usual speed")])
    return [
        Option(
            _speed_text(speed),
            f"{words} ({_speed_text(speed)})"
            + (", the usual" if abs(speed - usual) < 1e-9 else ""),
        )
        for speed, words in speeds
    ]


def defaults(summary: BookSummary, config: Config, last: Choices | None) -> Picked:
    if last is not None:
        return Picked(
            formats=frozenset(last.formats),
            voice=_named_with_engine(last.voice, config),
            speed=_speed_text(last.speed),
            sections=frozenset(last.sections),
            as_english=last.as_english,
        )
    voice = _configured_voice(config)
    own = language_voices(summary, config)
    if own and asks_about_language(summary, config) and voice not in {c.spec for c in own}:
        voice = own[0].spec  # the best voice for the book's language
    return Picked(
        formats=frozenset(f for f in config["output"]["formats"] if f in FORMATS),
        voice=voice,
        speed=_speed_text(float(config["voice"]["speed"])),
        sections=frozenset(s.number for s in summary.sections),
        as_english=False,
    )


def check(
    fields: list[tuple[str, str]], summary: BookSummary, config: Config
) -> tuple[Picked, Choices | None, dict[str, str]]:
    """What was sent back, as shown on the form; the choices, if they're all right; and
    an error for each field that isn't, keyed by the field's name."""
    numbers = {s.number for s in summary.sections}
    sent_sections = [section_number(v) for v in every(fields, "section")]
    picked = Picked(
        formats=frozenset(f for f in every(fields, "format") if f in FORMATS),
        voice=_named_with_engine(first(fields, "voice"), config),
        speed=first(fields, "speed"),
        sections=frozenset(n for n in sent_sections if n in numbers),
        as_english=first(fields, "english") == "yes",
    )
    errors: dict[str, str] = {}
    asks = asks_about_language(summary, config)
    if picked.voice not in voice_values(config):
        errors["voice"] = "Choose a voice from the list."
    elif asks and not picked.as_english and not speaks_book_language(summary, config, picked.voice):
        if language_voices(summary, config):
            errors["voice"] = (
                f"The voice you chose doesn't speak {summary.language}, the language this "
                "book says it is in. Choose one of the voices for it, listed first, or choose "
                "Read it as English."
            )
        else:
            errors["english"] = (
                "None of Dogear's voices speaks this book's language. To make it anyway, "
                "choose Read it as English."
            )
    if not picked.formats:
        errors["format"] = "Choose at least one thing to make."
    if picked.speed not in {option.value for option in speed_options(config)}:
        errors["speed"] = "Choose a speed."
    if any(n not in numbers for n in sent_sections):
        # Only a changed or very old copy of the form sends these.
        errors["section"] = "Choose sections from the list."
    elif not picked.sections:
        errors["section"] = "Choose at least one section."
    if errors:
        return picked, None, errors
    choices = Choices(
        formats=tuple(f for f in FORMATS if f in picked.formats),
        voice=picked.voice,
        speed=float(picked.speed),
        sections=tuple(sorted(picked.sections)),
        as_english=picked.as_english and asks,
    )
    return picked, choices, {}


def form_fields(picked: Picked) -> list[tuple[str, str]]:
    """The choose form's fields, as `check` reads them, for what was picked."""
    fields = [("format", f) for f in FORMATS if f in picked.formats]
    fields += [("voice", picked.voice), ("speed", picked.speed)]
    fields += [("section", str(n)) for n in sorted(picked.sections)]
    return fields + ([("english", "yes")] if picked.as_english else [])


def _named_with_engine(voice: str, config: Config) -> str:
    """A voice as the form's values name it, with its engine. Books chosen before there
    were other engines saved the voice's name alone, such as "af_heart", and a page
    opened before then sends it that way too."""
    try:
        return find_voice(voice, config).spec
    except VoiceError:
        return voice  # no longer installed: nothing in the list starts out chosen


def _configured_voice(config: Config) -> str:
    try:
        return configured_voice(config).spec
    except VoiceError:
        return ""  # the settings name a voice that isn't installed


def section_number(value: str) -> int | None:
    """A section number as the form sends it, or None if it isn't one. Only ASCII digits,
    and not too many of them: int() would turn "²" into an error and a 5,000-digit
    string into a long wait."""
    if value.isascii() and value.isdecimal() and len(value) <= MAX_SECTION_DIGITS:
        return int(value)
    return None


def _speed_text(speed: float) -> str:
    return f"{speed:.2f}".rstrip("0").rstrip(".") if speed % 1 else f"{speed:.1f}"
