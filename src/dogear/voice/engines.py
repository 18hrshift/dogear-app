"""Voice engines as plugins: what an engine gives Dogear, and finding the installed ones.

Every engine registers through the Python entry point group `dogear.engines`, Kokoro
included (in Dogear's own pyproject.toml), so there is one way in. The entry point's
name is the engine's name and its value is an object with the attributes and methods of
`EnginePlugin`. docs/voice-engines.md describes the interface for plugin authors.

An engine that fails to load, or doesn't give what the interface asks for, is set aside
as a `BrokenEngine` with a plain reason, which `dogear voices` reports. It never stops
another engine from working.
"""

from __future__ import annotations

import hashlib
import logging
import re
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib import metadata
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

import numpy as np

from dogear.config import Config, core_defaults
from dogear.errors import DogearError, VoiceError
from dogear.output.language_tag import language_tag
from dogear.voice.engine import SpeechPlan, VoiceEngine

if TYPE_CHECKING:
    from dogear.pronunciations import PronunciationDictionary

log = logging.getLogger(__name__)

GROUP = "dogear.engines"
OWN_PACKAGE = "dogear"  # engines from Dogear itself win a clash of names
ISSUES_URL = "https://github.com/18hrshift/dogear-app/issues"
_ENGINE_NAME = re.compile(r"[a-z][a-z0-9_]*")
_GENDERS = ("female", "male", "")

Report = Callable[[str], None]
T = TypeVar("T")


@dataclass(frozen=True)
class Voice:
    """One voice of an engine, as `dogear voices` and the web page list it."""

    name: str  # unique within its engine: what [voice] voice and --voice take
    language: str  # a BCP 47 tag, such as "en-US" or "fr-FR"
    description: str  # the language in words, with any accent: "British English"
    gender: str = ""  # "female", "male", or "" if not known
    quality: str = ""  # the engine's own grade, as words: "grade A", "high quality"


@dataclass(frozen=True)
class VoicePace:
    """What a voice's audio will be like, known without loading or downloading anything:
    enough to work out how much disk space a book needs before it is spoken."""

    sample_rate: int  # of the audio it makes
    words_per_minute: float  # about how many it says, at the configured speed


class EnginePlugin(Protocol):
    """What an engine's entry point gives Dogear."""

    name: str  # lowercase, as in [voice] engine; also names its settings table
    title: str  # for people: "Kokoro"
    license: str  # an SPDX expression for the engine's code and its voices
    settings: str  # TOML: the defaults of its [<name>] table, or "" for none
    # True only if the engine reads misaki's English phonemes, as Kokoro does: then
    # phoneme entries in pronunciation files apply to it, and so does `dogear words`.
    reads_misaki_phonemes: bool
    about_voices: str  # a sentence said before its voices: how they're ordered and graded

    def voices(self) -> Sequence[Voice]:
        """Every voice, best first."""
        ...

    def prepare(self, config: Config, voice: Voice, report: Report) -> None:
        """Make sure every file the voice needs is on disk and checked, downloading
        whatever is missing. Nothing else: no model is loaded."""
        ...

    def engine(
        self,
        config: Config,
        voice: Voice,
        pronunciations: PronunciationDictionary,
        report: Report,
    ) -> VoiceEngine:
        """The voice, ready to speak: prepare it, then load it."""
        ...

    def pace(self, config: Config, voice: Voice) -> VoicePace:
        """The voice's sample rate, and about how many words a minute it says at
        `[voice] speed`, with nothing loaded or downloaded. An estimate is fine; a
        generous one is better than a low one, as it sizes the disk space a book needs."""
        ...

    def sends_text_to(self, config: Config, voice: Voice) -> str | None:
        """Where the voice sends the text it speaks, with these settings: None if the text
        stays on this computer (a server on this computer counts), or else the name of the
        computer it goes to, such as "api.openai.com". Nothing is sent to find out.
        Dogear's core, not the engine, refuses a voice that sends text off this computer
        unless the user allows it, and says where the text goes first (cloud.py)."""
        ...


# Methods an engine may give as well, when it has what they ask for:
#   configured_voices(config) -> Sequence[Voice]: the voices with these settings, for an
#     engine whose voices are set in its settings table, as a speech service's are. Then
#     `voices()` gives the voices of its default settings. Nothing is sent to list them.
#   pinned_files(config) -> Sequence[RemoteFile]: every file it downloads (for any of its
#     voices), pinned by SHA-256, where each lives on disk, for `dogear download --check`.
OPTIONAL_METHODS = ("configured_voices", "pinned_files")


@dataclass(frozen=True)
class Engine:
    """An installed engine that loaded and gave what the interface asks for."""

    plugin: EnginePlugin
    voices: tuple[Voice, ...]
    settings: dict[str, Any]
    package: str  # the package it came from, such as "dogear-piper 0.1.0"
    report_to: str  # where to report its bugs: a web address, or its package's name

    @property
    def name(self) -> str:
        return self.plugin.name

    @property
    def title(self) -> str:
        return self.plugin.title

    def voice(self, name: str, config: Config | None = None) -> Voice | None:
        return next((v for v in self.voices_in(config) if v.name == name), None)

    def voices_in(self, config: Config | None) -> tuple[Voice, ...]:
        """The voices with these settings: `voices`, unless the engine's voices are set in
        its settings (`configured_voices`). With no settings, the defaults' voices."""
        configured = getattr(self.plugin, "configured_voices", None)
        if config is None or configured is None:
            return self.voices
        voices = tuple(run_plugin(self, "listing its voices", lambda: configured(config)))
        try:
            _check_voices(voices)
        except _NotAnEngine as e:
            raise VoiceError(
                f"The {self.title} voice engine can't use the voices its settings give. {e}",
                hint=f"Check the voices in the [{self.name}] table of your settings file.",
            ) from None
        return voices


@dataclass(frozen=True)
class BrokenEngine:
    """An installed engine that can't be used, and why, in a sentence."""

    name: str
    package: str
    problem: str


@dataclass(frozen=True)
class Installed:
    engines: tuple[Engine, ...] = ()  # sorted by name
    broken: tuple[BrokenEngine, ...] = ()

    def get(self, name: str) -> Engine | None:
        return next((e for e in self.engines if e.name == name), None)

    def broken_named(self, name: str) -> BrokenEngine | None:
        return next((b for b in self.broken if b.name == name), None)


@dataclass(frozen=True)
class ChosenVoice:
    engine: Engine
    voice: Voice

    @property
    def spec(self) -> str:
        """'kokoro:af_heart': what --voice takes to name this voice in any company."""
        return f"{self.engine.name}:{self.voice.name}"


# Finding the installed engines.


def _entry_points() -> Iterable[metadata.EntryPoint]:
    """Every registered engine. Tests replace this to choose which engines are installed."""
    return metadata.entry_points(group=GROUP)


@cache
def installed() -> Installed:
    """The installed engines, loaded once per process."""
    core_tables = set(core_defaults())
    engines: dict[str, Engine] = {}
    broken: list[BrokenEngine] = []
    entries = sorted(_entry_points(), key=lambda e: (e.name, _package_name(e) != OWN_PACKAGE))
    for entry in entries:
        package = _package(entry)
        if entry.name in engines:
            broken.append(
                BrokenEngine(
                    entry.name,
                    package,
                    f"{engines[entry.name].package} already has a voice engine of that name.",
                )
            )
            continue
        loaded = _load(entry, package, core_tables)
        if isinstance(loaded, Engine):
            engines[entry.name] = loaded
        else:
            log.info("Voice engine %s (%s) set aside: %s", entry.name, package, loaded.problem)
            broken.append(loaded)
    return Installed(tuple(engines.values()), tuple(broken))


def engine_settings() -> dict[str, dict[str, Any]]:
    """Each installed engine's settings defaults, by its name: the tables `config.py`
    adds to Dogear's own defaults."""
    return {e.name: e.settings for e in installed().engines if e.settings}


def _load(entry: metadata.EntryPoint, package: str, core_tables: set[str]) -> Engine | BrokenEngine:
    try:
        plugin = entry.load()
    except Exception as e:  # a plugin's import can fail any way at all
        return BrokenEngine(entry.name, package, f"It failed to load: {_one_line(e)}.")
    try:
        return _checked(entry, plugin, package, core_tables)
    except _NotAnEngine as e:
        return BrokenEngine(entry.name, package, str(e))
    except Exception as e:
        return BrokenEngine(entry.name, package, f"It failed to list its voices: {_one_line(e)}.")


class _NotAnEngine(Exception):
    """An entry point whose object doesn't give what EnginePlugin asks for."""


def _checked(
    entry: metadata.EntryPoint, plugin: Any, package: str, core_tables: set[str]
) -> Engine:
    for attribute, kind in (
        ("name", str),
        ("title", str),
        ("license", str),
        ("settings", str),
        ("reads_misaki_phonemes", bool),
        ("about_voices", str),
    ):
        if not isinstance(getattr(plugin, attribute, None), kind):
            raise _NotAnEngine(
                f"It has no {attribute} ({kind.__name__}), so it isn't a voice engine."
            )
    for method in ("voices", "prepare", "engine", "pace", "sends_text_to"):
        if not callable(getattr(plugin, method, None)):
            raise _NotAnEngine(f"It has no {method} method, so it isn't a voice engine.")
    for method in OPTIONAL_METHODS:
        if hasattr(plugin, method) and not callable(getattr(plugin, method)):
            raise _NotAnEngine(f"Its {method} isn't a method.")
    if plugin.name != entry.name:
        raise _NotAnEngine(f"It calls itself '{plugin.name}' but is registered as '{entry.name}'.")
    if not _ENGINE_NAME.fullmatch(entry.name):
        raise _NotAnEngine(
            "Its name must be lowercase letters, digits, and underscores, starting with a letter."
        )
    if entry.name in core_tables:
        raise _NotAnEngine(f"Its name is taken by Dogear's own [{entry.name}] settings.")
    try:
        settings = tomllib.loads(plugin.settings)
    except tomllib.TOMLDecodeError as e:
        raise _NotAnEngine(f"Its settings aren't valid TOML: {e}.") from None
    voices = tuple(plugin.voices())
    _check_voices(voices)
    return Engine(plugin, voices, settings, package, _report_to(entry, package))


def _check_voices(voices: tuple[Voice, ...]) -> None:
    if not voices:
        raise _NotAnEngine("It has no voices.")
    names = set()
    for voice in voices:
        if not isinstance(voice, Voice):
            raise _NotAnEngine(f"It lists {voice!r}, which isn't a Voice.")
        if not voice.name or ":" in voice.name or voice.name != voice.name.strip():
            raise _NotAnEngine(f"Its voice '{voice.name}' needs a name without a colon or spaces.")
        if voice.name in names:
            raise _NotAnEngine(f"It lists the voice '{voice.name}' twice.")
        names.add(voice.name)
        if language_tag(voice.language) != voice.language:
            raise _NotAnEngine(
                f"Its voice '{voice.name}' has the language '{voice.language}', which isn't "
                "a BCP 47 tag such as en-US."
            )
        if voice.gender not in _GENDERS:
            raise _NotAnEngine(f"Its voice '{voice.name}' has the gender '{voice.gender}'.")


def _package_name(entry: metadata.EntryPoint) -> str:
    dist = getattr(entry, "dist", None)
    return dist.name if dist is not None else ""


def _package(entry: metadata.EntryPoint) -> str:
    dist = getattr(entry, "dist", None)
    if dist is None:
        return entry.value  # no package metadata: name the module instead
    return f"{dist.name} {dist.version}"


def _report_to(entry: metadata.EntryPoint, package: str) -> str:
    """Where to report a bug in the engine: its package's issues page or home page."""
    if _package_name(entry) == OWN_PACKAGE:
        return ISSUES_URL
    dist = getattr(entry, "dist", None)
    urls = dist.metadata.get_all("Project-URL") or [] if dist is not None else []
    found = {}
    for url in urls:
        label, _, address = url.partition(",")
        found[label.strip().lower()] = address.strip()
    for label in ("issues", "bug tracker", "homepage", "source"):
        if found.get(label):
            return found[label]
    return f"the makers of {package}"


def _one_line(error: BaseException) -> str:
    text = " ".join(str(error).split())
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


# Choosing a voice.


def engine_named(name: str) -> Engine:
    """The installed engine called `name`, or a VoiceError saying why it can't be used."""
    found = installed()
    engine = found.get(name)
    if engine is not None:
        return engine
    broken = found.broken_named(name)
    if broken is not None:
        raise VoiceError(
            f"The voice engine '{name}' ({broken.package}) is installed, but Dogear can't "
            f"use it. {broken.problem}",
            hint="Reinstall it, or choose a voice from another engine: run `dogear voices`.",
        )
    names = ", ".join(e.name for e in found.engines) or "(none)"
    raise VoiceError(
        f"voice.engine is '{name}', which isn't installed.",
        hint=f"Use one of: {names}. Run `dogear voices` to list their voices.",
    )


def find_voice(spec: str, config: Config | None = None) -> ChosenVoice:
    """The voice `spec` names: "engine:voice", or a voice name only one engine has. With
    `config`, among the voices those settings give (see `Engine.voices_in`)."""
    engine_name, colon, voice_name = spec.strip().rpartition(":")
    if colon:
        engine = engine_named(engine_name)
        voice = engine.voice(voice_name, config)
        if voice is None:
            raise VoiceError(
                f"The {engine.title} voice engine has no voice called '{voice_name}'.",
                hint="Run `dogear voices` to list them.",
            )
        return ChosenVoice(engine, voice)
    matches = [ChosenVoice(e, v) for e in installed().engines if (v := e.voice(voice_name, config))]
    if not matches:
        raise VoiceError(
            f"There's no voice called '{voice_name}'.", hint="Run `dogear voices` to list them."
        )
    if len(matches) > 1:
        titles = " and ".join(m.engine.title for m in matches)
        raise VoiceError(
            f"More than one voice engine has a voice called '{voice_name}': {titles}.",
            hint=f"Write the engine's name first, such as {matches[0].spec}.",
        )
    return matches[0]


def configured_voice(config: Config) -> ChosenVoice:
    """The voice the settings choose: [voice] engine and [voice] voice."""
    engine = engine_named(config["voice"]["engine"])
    name = config["voice"]["voice"]
    voice = engine.voice(name, config)
    if voice is not None:
        return ChosenVoice(engine, voice)
    elsewhere = [e for e in installed().engines if e.voice(name, config)]
    if elsewhere:
        other = elsewhere[0]
        raise VoiceError(
            f"The voice {name} is a {other.title} voice, but voice.engine is '{engine.name}'.",
            hint=f'Set engine = "{other.name}" under [voice] as well, or use --voice '
            f"{other.name}:{name}.",
        )
    raise VoiceError(
        f"The {engine.title} voice engine has no voice called '{name}'.",
        hint="Run `dogear voices` to list them.",
    )


def choose_voice(config: Config, spec: str) -> ChosenVoice:
    """Find the voice `spec` names and set it, and its engine, in `config`."""
    chosen = find_voice(spec, config)
    config["voice"]["engine"] = chosen.engine.name
    config["voice"]["voice"] = chosen.voice.name
    return chosen


def describe_voice(voice: Voice) -> str:
    """'af_heart: American English, female, grade A'"""
    parts = [voice.description, voice.gender, voice.quality]
    return f"{voice.name}: {', '.join(p for p in parts if p)}"


# Running an engine's code.


def run_plugin(engine: Engine, doing: str, action: Callable[[], T]) -> T:
    """`action()`, with an error Dogear didn't expect from the engine's code turned into a
    VoiceError that says which engine failed and where to report it. Errors the engine
    raises as DogearErrors already say what to do, so they pass through."""
    try:
        return action()
    except DogearError:
        raise
    except Exception as e:
        log.info("The %s voice engine failed while %s.", engine.title, doing, exc_info=True)
        raise VoiceError(
            f"The {engine.title} voice engine stopped while {doing}: {_one_line(e)}",
            hint=f"This is a bug in the voice engine ({engine.package}). Please report it to "
            f"{engine.report_to}, saying what you ran. Run it again with -v to see the details.",
        ) from e


@dataclass
class EngineVoice:
    """An engine's VoiceEngine as the renderer uses it. Each plan's cache key is made to
    cover the engine's name and the voice's on top of what the engine's own key covers
    (its model, settings, and input), so two engines can never share a cached sentence.
    The engine's own errors become VoiceErrors (see `run_plugin`), and so does anything
    it gives that isn't what the interface asks for: a plan that isn't a SpeechPlan, or
    audio that isn't one channel of finite samples."""

    chosen: ChosenVoice
    inner: VoiceEngine
    sample_rate: int = field(init=False)

    def __post_init__(self) -> None:
        rate = getattr(self.inner, "sample_rate", None)
        if not isinstance(rate, int) or isinstance(rate, bool) or rate <= 0:
            raise self._bug(f"gave the sample rate {_short(rate)}", "it is a whole number of Hz")
        self.sample_rate = rate

    def plan(self, text: str) -> SpeechPlan:
        plan = run_plugin(self.chosen.engine, "preparing a sentence", lambda: self.inner.plan(text))
        if not isinstance(plan, SpeechPlan):
            raise self._bug(f"gave {_short(plan)} as a sentence's plan", "plan gives a SpeechPlan")
        if not isinstance(plan.key, str) or not isinstance(plan.payload, str):
            raise self._bug(
                "gave a plan whose key or payload isn't text", "a SpeechPlan holds two strings"
            )
        identity = f"{self.chosen.engine.name}\n{self.chosen.voice.name}\n{plan.key}"
        return SpeechPlan(hashlib.sha256(identity.encode()).hexdigest(), plan.payload)

    def render(self, plan: SpeechPlan) -> np.ndarray:
        samples = run_plugin(
            self.chosen.engine, "speaking a sentence", lambda: self.inner.render(plan)
        )
        try:
            with np.errstate(over="ignore", invalid="ignore"):  # found just below instead
                audio = np.asarray(samples, dtype=np.float32)
        except Exception as e:  # an engine's audio could be anything at all
            raise self._bug(
                f"gave audio that isn't samples ({_one_line(e)})", "audio is a numpy array"
            ) from None
        if audio.ndim != 1:
            raise self._bug(f"gave audio of shape {audio.shape}", "audio is one channel of samples")
        if not np.isfinite(audio).all():
            raise self._bug(
                "gave audio with samples that aren't numbers (NaN) or are infinite",
                "every sample is a number from -1 to 1",
            )
        return audio

    def _bug(self, what: str, rule: str) -> VoiceError:
        engine = self.chosen.engine
        return VoiceError(
            f"The {engine.title} voice engine {what}.",
            hint=f"This is a bug in {engine.package}: {rule}. Please report it to "
            f"{engine.report_to}.",
        )


def _short(value: object) -> str:
    """A value an engine gave, for a message: None, a number, or else its type."""
    if value is None or isinstance(value, (bool, int, float)):
        return repr(value)
    return f"a {type(value).__name__}"
