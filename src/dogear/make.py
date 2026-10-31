"""Book file in, audiobook out: extract, narrate, speak, package.

Each step lives in its own module; this one only runs them in order and reports
progress as plain sentences.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dogear.config import Config
from dogear.disk_space import check_space, space_needed
from dogear.document import Book
from dogear.errors import DogearError, OutputFolderError, VoiceError
from dogear.extract import extract_book
from dogear.language_detection import DetectionSettings, LanguageGuesser, language_guesser
from dogear.language_match import is_english, primary
from dogear.narration import (
    SectionNarration,
    has_narration_words,
    narrate_book,
    narrate_title,
    opens_with_title,
)
from dogear.output import build_m4b, build_mp3_folder, normalize_chapters
from dogear.output.daisy202 import build_daisy202
from dogear.output.epub3 import build_epub3
from dogear.output.ffmpeg import check_ffmpeg
from dogear.output.filenames import safe_filename
from dogear.output.talking_audio import encode_talking_audio
from dogear.paths import cache_dir
from dogear.readalong import build_readalong
from dogear.render import render_book
from dogear.render.progress import Progress, Report
from dogear.render.work import work_folder
from dogear.voice import make_voice, voice_pace
from dogear.voice.book_voices import (
    BookVoices,
    VoiceUse,
    book_pace,
    unspoken_languages,
    voices_needed,
)
from dogear.voice.cloud import refuse_unless_allowed
from dogear.voice.language_voices import LanguageVoices, voices_for

# m4b: one audiobook file with chapters. mp3: a folder of MP3 files with a playlist.
# epub: an EPUB 3 talking book, text and audio in step. daisy: a DAISY 2.02 talking book.
FORMATS = ("m4b", "mp3", "epub", "daisy")


@dataclass(frozen=True)
class MakeRequest:
    book: Path
    output_dir: Path
    sections: set[int] | None = None  # numbers from `dogear outline`; None means all
    language: str | None = None  # overrides the language the book declares


def make_audiobook(request: MakeRequest, config: Config, report: Report) -> list[Path]:
    """Make the configured outputs for one book. Returns what was saved, in order."""
    formats = _formats(config)
    choices = LanguageVoices.from_config(config)
    # A voice that would send the text off this computer, unless allowed, stops the run
    # before the book is even read: any voice the run may use. make_voice says where the
    # text goes, just before it is sent.
    refuse_unless_allowed(choices.voices(), config)
    book = extract_book(request.book, config, report)
    if request.language:
        book.metadata.language = request.language
    settle_language(book, choices, report)
    check_language(book, choices)
    check_ffmpeg(config)
    outputs = _output_paths(book, request, formats)
    guesser = guesser_for(config, choices, report)
    narrations = chosen_narrations(
        narrate_book(book, config, guesser), request.sections, len(book.sections)
    )
    chosen = len(narrations)
    title_page = bool(narrations) and opens_with_title(narrations[0], book.metadata.title)
    if config["narration"]["announce_title"] and not title_page:
        narrations.insert(0, narrate_title(book, config))
    progress = Progress(
        total_words=sum(n.words() for n in narrations),
        total_sections=len(narrations),
        every_seconds=config["render"]["progress_every_seconds"],
        report=report,
    )
    title_track = " and a title track" if len(narrations) > chosen else ""
    report(
        f"Making an audiobook of {book.metadata.title}: {_count(chosen, 'section')}"
        f"{title_track}, about {_count(progress.total_words, 'word')}, "
        f"voice {config['voice']['voice']}."
    )
    uses = voices_needed(narrations, choices) or [VoiceUse(choices.main, (), 0)]
    report_voices(uses, narrations, choices, config, report)
    # Before the voices load (or download), so a book that won't fit stops at once.
    needs = space_needed(
        progress.total_words,
        formats,
        cache_dir(config),
        request.output_dir,
        book_pace(voice_pace(config, use.voice) for use in uses),
        config,
    )
    check_space(needs, config)
    voices = load_voices(config, choices, uses, report)
    readers = [use.voice for use in uses]  # named as the talking books' narrators
    with work_folder(cache_dir(config) / "work") as work:
        chapters = render_book(narrations, voices, config, work, report, progress)
        report(progress.summary())
        report("Evening out the volume across chapters.")
        chapters = normalize_chapters(chapters, work, config)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        if "m4b" in formats:
            report("Building the M4B audiobook file.")
            build_m4b(chapters, book.metadata, outputs["m4b"], config, normalized=True)
        if "mp3" in formats:
            report("Building the MP3 folder.")
            build_mp3_folder(chapters, book.metadata, outputs["mp3"], config, normalized=True)
        if "epub" in formats or "daisy" in formats:
            sections = build_readalong(narrations, chapters, book.metadata.language)
            sections = encode_talking_audio(sections, work, config)
            if "epub" in formats:
                report("Building the EPUB talking book.")
                build_epub3(sections, book.metadata, outputs["epub"], config, voices=readers)
            if "daisy" in formats:
                report("Building the DAISY talking book.")
                build_daisy202(sections, book.metadata, outputs["daisy"], config, voices=readers)
    return [outputs[f] for f in FORMATS if f in formats]


def _output_paths(book: Book, request: MakeRequest, formats: list[str]) -> dict[str, Path]:
    """Where each format is saved, checked before any speaking starts."""
    title, stem = book.metadata.title, request.book.stem
    names = {
        "m4b": safe_filename(title, ".m4b", fallback=stem),
        "mp3": safe_filename(f"{title} MP3", "", fallback=f"{stem} MP3"),
        "epub": safe_filename(f"{title} talking book", ".epub", fallback=f"{stem} talking book"),
        "daisy": safe_filename(f"{title} DAISY", "", fallback=f"{stem} DAISY"),
    }
    paths = {f: request.output_dir / names[f] for f in formats}
    source = request.book.resolve()
    for path in paths.values():
        if path.resolve() == source:
            raise OutputFolderError(
                f"Saving {path.name} would replace the book it was made from.",
                hint="Choose another folder with -o, or rename the book file first.",
            )
    return paths


def load_voices(
    config: Config, choices: LanguageVoices, uses: list[VoiceUse], report: Report
) -> BookVoices:
    """Load each voice the book needs, once, through `make_voice` like any other. Every
    one is among the voices the settings allow a run (`voices_for_run`), which can be
    checked before the book is read."""
    allowed = {c.spec for c in choices.voices()}
    engines = {}
    for use in uses:
        if use.voice.spec not in allowed:
            raise VoiceError(
                f"The voice {use.voice.spec} was chosen for a passage, but the settings don't "
                "give it to any language.",
                hint="This is a bug in Dogear. Please report it, saying what you ran.",
            )
        engines[use.voice.spec] = make_voice(config, use.voice, report)
    return BookVoices(choices, engines)


def settle_language(book: Book, choices: LanguageVoices, report: Report) -> None:
    """A book that doesn't say its language is read as English, unless the main voice
    speaks another: then it is taken to be in the main voice's language."""
    voice = choices.main.voice
    if book.metadata.language or is_english(voice.language):
        return
    book.metadata.language = primary(voice.language)
    report(
        f"This book doesn't say what language it's in, so Dogear takes it to be in "
        f"{book.metadata.language}, the language of the voice {voice.name} "
        f"({voice.description}). If that's wrong, use --language."
    )


def check_language(book: Book, choices: LanguageVoices) -> None:
    """Stop before anything slow if no voice Dogear would use speaks the book's language."""
    language = book.metadata.language
    if choices.speaks(language):
        return
    found = voices_for(language or "", choices.config)
    if found:
        where = (
            f"To read it in {language}, choose one of its voices with --voice, such as "
            f"{found[0].spec}, or give the language a voice under [voice.languages] in your "
            "settings."
        )
    else:
        where = (
            f"No installed voice speaks {language}. Voice engine plugins, such as Piper, "
            "add voices in other languages: the README says how to install one."
        )
    raise VoiceError(
        f"This book is marked as being in '{language}', but the voice "
        f"{choices.main.voice.name} speaks {choices.main.voice.description}.",
        hint=f"{where} If the book is really in English, run the command again with --language en.",
    )


def guesser_for(config: Config, choices: LanguageVoices, report: Report) -> LanguageGuesser | None:
    """A language guesser, when [language_detection] is on and there is something to
    find: a voice for more than one language."""
    if not DetectionSettings.from_config(config).enabled:
        return None
    languages = choices.languages()
    guesser = language_guesser(config, languages)
    if guesser is None:
        report(
            f"Language detection is on, but every voice Dogear would use speaks "
            f"{', '.join(sorted(languages))}, so there's nothing to look for. Give another "
            "language a voice under [voice.languages] to look for it."
        )
    else:
        report(
            "Looking for passages the book doesn't mark as being in another language, "
            f"among: {', '.join(sorted(languages))}."
        )
    return guesser


def report_voices(
    uses: list[VoiceUse],
    narrations: list[SectionNarration],
    choices: LanguageVoices,
    config: Config,
    report: Report,
) -> None:
    """Say which voice reads what, when more than the main voice does, and what is read
    without a voice or words of its own language."""
    if len(uses) > 1 or uses[0].voice.spec != choices.main.spec:
        for use in uses:
            voice = use.voice
            languages = " and ".join(use.languages) or "no marked language"
            report(
                f"The voice {voice.voice.name} ({voice.engine.title}, {voice.voice.description}) "
                f"reads {_count(use.utterances, 'sentence')} in {languages}."
            )
    for language, count in sorted(unspoken_languages(narrations, choices).items()):
        found = voices_for(language, config)
        offer = (
            f"Voices for {language}: {', '.join(c.spec for c in found)}. Give it one under "
            '[voice.languages] in your settings, or set other_languages = "best" under [voice].'
            if found
            else f"No installed voice speaks {language}."
        )
        report(
            f"{_count(count, 'sentence')} in {language} {'is' if count == 1 else 'are'} read by "
            f"{choices.main.voice.name}, "
            f"which speaks {choices.main.voice.description}. {offer}"
        )
    used = {primary(u.lang) for n in narrations for u in n.utterances if u.spoken}
    for language in sorted(used):
        if not has_narration_words(config, language):
            report(
                f"Dogear has no {language} words for the structure it reads aloud (such as "
                f"Image and Note), so it says them in English. Add a [narration.words.{language}] "
                "table to your settings to change that."
            )


def parse_formats(spec: str) -> list[str]:
    """'m4b' or 'm4b,daisy' as a list of formats; 'all' is every one."""
    names = [name.strip().lower() for name in spec.split(",") if name.strip()]
    if names == ["all"]:
        return list(FORMATS)
    unknown = [name for name in names if name not in FORMATS]
    if unknown or not names:
        raise DogearError(
            f"'{unknown[0] if unknown else spec}' isn't a format Dogear makes.",
            hint=f"Use one or more of {', '.join(FORMATS)}, separated by commas, or all.",
        )
    return list(dict.fromkeys(names))


def parse_sections(spec: str) -> set[int]:
    """'3' or '1-3,7' as a set of section numbers."""
    numbers: set[int] = set()
    for part in spec.split(","):
        first, dash, last = part.strip().partition("-")
        try:
            start = int(first)
            end = int(last) if dash else start
        except ValueError:
            raise DogearError(
                f"'{spec}' isn't a list of section numbers.",
                hint="Use numbers from `dogear outline`, such as 3 or 1-3,7.",
            ) from None
        if end < start:
            raise DogearError(
                f"'{part.strip()}' counts backwards.",
                hint=f"Write the lower number first: {end}-{start}.",
            )
        numbers.update(range(start, end + 1))
    return numbers


def chosen_narrations(
    narrations: list[SectionNarration], wanted: set[int] | None, total: int
) -> list[SectionNarration]:
    if wanted is None:
        return narrations
    outside = sorted(n for n in wanted if not 1 <= n <= total)
    if outside:
        raise DogearError(
            f"There is no section {outside[0]}; this book has {total}.",
            hint="Run `dogear outline` to see the section numbers.",
        )
    chosen = [n for n in narrations if n.number in wanted]
    if not chosen:
        raise DogearError(
            "Nothing in the chosen sections is read aloud.",
            hint="Pick other sections from `dogear outline`.",
        )
    return chosen


def _formats(config: Config) -> list[str]:
    formats = config["output"]["formats"]
    unknown = [f for f in formats if f not in FORMATS]
    if unknown or not formats:
        raise DogearError(
            f"output.formats is {formats}; each entry must be one of: {', '.join(FORMATS)}.",
            hint='For example: formats = ["m4b", "daisy"].',
        )
    return formats


def _count(n: int, noun: str) -> str:
    return f"{n:,} {noun}{'' if n == 1 else 's'}"
