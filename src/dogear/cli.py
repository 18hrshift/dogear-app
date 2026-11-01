"""The `dogear` command.

Output is written to be read by a screen reader as well as seen: plain sentences and
numbered lists, no tables, colors, or animations. Progress and errors go to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dogear import __version__
from dogear.book_summary import count, describe_section, summarize, summary_to_json
from dogear.config import Config, load_config
from dogear.document import (
    Book,
    Footnote,
    Heading,
    Image,
    ListBlock,
    PageMarker,
    Paragraph,
    SceneBreak,
    Section,
    Table,
    block_text,
)
from dogear.errors import DogearError
from dogear.extract import extract_book, format_names
from dogear.language_match import primary
from dogear.make import (
    MakeRequest,
    guesser_for,
    make_audiobook,
    parse_formats,
    parse_sections,
    settle_language,
)
from dogear.narration import narrate_book
from dogear.ocr import default_output_path, ocr_pdf
from dogear.pronunciations import dictionary_from_config
from dogear.say import say_text
from dogear.unknown_words import WordsRequest, find_unknown_words, words_to_json
from dogear.voice import prepare_voice
from dogear.voice.check import check_voice_files
from dogear.voice.cloud import ALLOW_OPTION, sends_text, text_goes_to
from dogear.voice.engines import ChosenVoice, choose_voice, describe_voice, installed
from dogear.voice.language_voices import LanguageVoices
from dogear.web.child import UTF8_OUTPUT_ENV
from dogear.web.server_watch import stop_with_server_when_asked

BOOK_HELP = f"the book file: {format_names('or')}"
SECTIONS_HELP = "only these sections from outline, such as 3 or 1-3,7"
VOICE_HELP = "voice name, from `dogear voices`, or engine:name such as kokoro:af_heart"
ALLOW_CLOUD_HELP = (
    "allow a voice that sends the text it speaks off this computer, for this run only "
    "(Dogear says where first)"
)


def main(argv: list[str] | None = None) -> int:
    _utf8_output_when_asked()
    stop_with_server_when_asked()
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        config = load_config(args.config)
        return args.run(args, config)
    except DogearError as e:
        print(f"Error: {e.message}", file=sys.stderr)
        if e.hint:
            print(f"What to do: {e.hint}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        if args.run is _make:
            # Spoken sentences, and pages of a scanned PDF read with OCR, are kept as
            # they're done. (A stopped OCR says so itself, for every command.)
            print(
                "What was done so far is saved. Run the same command again to carry on.",
                file=sys.stderr,
            )
        return 130


def _utf8_output_when_asked() -> None:
    """Write UTF-8 when `dogear serve` runs this as a child process and reads its output.

    It asks through an environment variable of its own because a frozen (installer)
    build ignores PYTHONIOENCODING, and Windows would otherwise write its ANSI code page.
    """
    if os.environ.get(UTF8_OUTPUT_ENV) != "1":
        return
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, help="settings file (TOML) to use")
    common.add_argument("-v", "--verbose", action="store_true", help="explain each step")
    word_files = argparse.ArgumentParser(add_help=False)
    word_files.add_argument(
        "--words",
        action="append",
        type=Path,
        metavar="FILE",
        help="a pronunciation file to use as well as any in your settings; repeat for more",
    )

    # --config and -v live on each command only: an argparse parent shared with the main
    # parser would reset them to None after the command name.
    parser = argparse.ArgumentParser(
        prog="dogear", description="Turn books into navigable audiobooks."
    )
    parser.add_argument("--version", action="version", version=f"dogear {__version__}")
    commands = parser.add_subparsers(title="commands", required=True, metavar="COMMAND")

    outline = commands.add_parser(
        "outline",
        parents=[common],
        help="list a book's sections",
        description="List a book's title, authors, and sections with word counts and print pages.",
    )
    outline.add_argument("book", type=Path, help=BOOK_HELP)
    outline.add_argument(
        "--json",
        action="store_true",
        help="print the same facts as JSON, for other programs to read",
    )
    outline.set_defaults(run=_outline)

    text = commands.add_parser(
        "text",
        parents=[common, word_files],
        help="show the text Dogear found",
        description="Show the text Dogear extracted, to check it before making audio.",
    )
    text.add_argument("book", type=Path, help=BOOK_HELP)
    text.add_argument("--section", type=int, metavar="N", help="only section N (from outline)")
    text.add_argument(
        "--spoken",
        action="store_true",
        help="show each sentence the way the voice will say it, and which voice says it",
    )
    text.add_argument("--voice", help=f"with --spoken: the main voice; {VOICE_HELP}")
    text.add_argument("--language", help="the book's language, if its own label is wrong")
    text.set_defaults(run=_text)

    make = commands.add_parser(
        "make",
        parents=[common, word_files],
        help="make an audiobook",
        description=(
            "Read a book aloud and save it as an M4B audiobook, a folder of MP3 files, an "
            "EPUB or DAISY talking book, or several of these. The first run downloads the "
            "voice (about 340 MB). Stopping part way is safe: run the same command again "
            "and it continues where it left off."
        ),
    )
    make.add_argument("book", type=Path, help=BOOK_HELP)
    make.add_argument(
        "-o", "--output-dir", type=Path, help="folder to save in (default: the book's folder)"
    )
    make.add_argument(
        "--format",
        metavar="LIST",
        help="what to make, separated by commas: m4b (one audiobook file), mp3 (a folder of "
        "MP3 files), epub (EPUB 3 talking book), daisy (DAISY 2.02 talking book), or all "
        "(default: from config)",
    )
    make.add_argument("--voice", help=VOICE_HELP)
    make.add_argument("--speed", type=float, help="speaking pace, such as 0.9 or 1.1")
    make.add_argument("--allow-cloud", action="store_true", help=ALLOW_CLOUD_HELP)
    make.add_argument("--sections", metavar="LIST", help=SECTIONS_HELP)
    make.add_argument("--language", help="the book's language, if its own label is wrong")
    make.set_defaults(run=_make)

    words = commands.add_parser(
        "words",
        parents=[common, word_files],
        help="list the words the voice doesn't know",
        description=(
            "List the words in a book that the voice doesn't know and has to guess, most "
            "frequent first: usually names. Listen to them with `dogear say`, and fix any "
            "that sound wrong in a pronunciation file. Words your pronunciation files "
            "already cover aren't listed. The first run downloads a small language model "
            "(about 13 MB), not the whole voice."
        ),
    )
    words.add_argument("book", type=Path, help=BOOK_HELP)
    words.add_argument("--sections", metavar="LIST", help=SECTIONS_HELP)
    words.add_argument("--language", help="the book's language, if its own label is wrong")
    words.add_argument("--voice", help="voice name, from `dogear voices` (accents differ)")
    words.add_argument(
        "--json",
        action="store_true",
        help="print the same list as JSON, for other programs to read",
    )
    words.set_defaults(run=_words)

    say = commands.add_parser(
        "say",
        parents=[common, word_files],
        help="say a few words into a WAV file",
        description=(
            "Say a short text with the voice, speed, and pronunciation files `dogear make` "
            "would use, and save it as a WAV file. Use it to hear how a word comes out "
            "before and after adding it to a pronunciation file."
        ),
    )
    say.add_argument("text", help="what to say, in quotes")
    say.add_argument(
        "-o", "--output", type=Path, required=True, help="the WAV file to save, such as test.wav"
    )
    say.add_argument("--voice", help=VOICE_HELP)
    say.add_argument("--speed", type=float, help="speaking pace, such as 0.9 or 1.1")
    say.add_argument("--allow-cloud", action="store_true", help=ALLOW_CLOUD_HELP)
    say.add_argument(
        "--phonemes",
        action="store_true",
        help="also show the phonemes the voice read, for writing phoneme entries",
    )
    say.set_defaults(run=_say)

    voices = commands.add_parser(
        "voices",
        parents=[common],
        help="list the voices",
        description=(
            "List the voices Dogear can use: every installed voice engine's, best first. "
            "Kokoro comes with Dogear; other engines are installed as plugins."
        ),
    )
    voices.set_defaults(run=_voices)

    download = commands.add_parser(
        "download",
        parents=[common],
        help="download the voice now, or check the voice files",
        description=(
            "Download the configured voice ahead of time, for example before going offline. "
            "`dogear make` does this by itself when needed. With --check, check every voice "
            "file Dogear has against its checksum instead, and download again any that are "
            "damaged."
        ),
    )
    download_what = download.add_mutually_exclusive_group()
    download_what.add_argument("--voice", help=VOICE_HELP)
    download_what.add_argument(
        "--check",
        action="store_true",
        help="check every voice file on disk, and download again any that are damaged",
    )
    download.set_defaults(run=_download)

    ocr = commands.add_parser(
        "ocr",
        parents=[common],
        help="save a scanned PDF with a text layer",
        description=(
            "Save a copy of a scanned PDF with a text layer, read with OCR (Tesseract), for "
            "other programs to read as well. Dogear's other commands read a scanned PDF "
            "with OCR by themselves."
        ),
    )
    ocr.add_argument("pdf", type=Path, help="the scanned PDF")
    ocr.add_argument("-o", "--output", type=Path, help="where to save (default: NAME_ocr.pdf)")
    ocr.add_argument("--language", help="Tesseract language, such as eng or eng+fra")
    ocr.add_argument("--dpi", type=int, help="scan resolution to read pages at")
    ocr.set_defaults(run=_ocr)

    serve = commands.add_parser(
        "serve",
        parents=[common],
        help="make books from a web browser",
        description=(
            "Start Dogear's web page, for making books in a web browser on this computer, "
            "or with --lan from a phone or another computer on the same network. Keep the "
            "window open while you use it, and press Ctrl+C to stop."
        ),
    )
    serve.add_argument("--port", type=int, help="the port to use (default: web.port in config)")
    serve.add_argument(
        "--lan",
        action="store_true",
        help="let phones and other computers on the same network use it, with the link",
    )
    serve.add_argument("--no-browser", action="store_true", help="don't open a web browser")
    serve.set_defaults(run=_serve)
    return parser


def _outline(args: argparse.Namespace, config: Config) -> int:
    summary = summarize(extract_book(args.book, config, _report))
    if args.json:
        # ASCII only, so any program can read it whatever the console's encoding.
        print(json.dumps(summary_to_json(summary), indent=2))
        return 0
    print(f"Title: {summary.title}")
    if summary.authors:
        print(f"By: {', '.join(summary.authors)}")
    if summary.language:
        print(f"Language: {summary.language}")
    print(
        f"{summary.source_format.upper()} file, {count(len(summary.sections), 'section')}, "
        f"{count(summary.words, 'word')}."
    )
    print()
    for section in summary.sections:
        print(f"{section.number}. {describe_section(section)}")
    return 0


def _text(args: argparse.Namespace, config: Config) -> int:
    _apply_voice_options(args, config)
    if args.spoken or args.words:
        _use_word_files(args, config)
    book = extract_book(args.book, config, _report)
    if args.language:
        book.metadata.language = args.language
    sections = _chosen_sections(book, args.section)
    if args.spoken:
        _print_spoken(book, config, {number for number, _ in sections})
        return 0
    for number, section in sections:
        print(f"Section {number}: {section.title}")
        print()
        for block in section.blocks:
            for line in _block_lines(block):
                print(line)
        print()
    return 0


def _print_spoken(book: Book, config: Config, wanted: set[int]) -> None:
    """Each section the way the voices will read it, one utterance per line. In a book
    with more than one language or voice, a line in brackets says where each starts."""
    choices = LanguageVoices.from_config(config)
    settle_language(book, choices, _report)
    guesser = guesser_for(config, choices, _report)
    narrated = {n.number: n for n in narrate_book(book, config, guesser)}
    paragraph_ms = config["narration"]["pauses"]["paragraph_ms"]
    readers = {
        (primary(u.lang), choices.voice_for(u.lang).spec)
        for n in narrated.values()
        for u in n.utterances
        if u.spoken
    }
    for number in sorted(wanted):
        print(f"Section {number}: {book.sections[number - 1].title}")
        print()
        if number not in narrated:
            print("Not read aloud: its notes are read where the text refers to them.")
        reading = None
        for utterance in narrated[number].utterances if number in narrated else []:
            if not utterance.spoken:
                continue
            reader = (primary(utterance.lang), choices.voice_for(utterance.lang).spec)
            if len(readers) > 1 and reader != reading:
                print(_reader_line(utterance.lang, choices))
                reading = reader
            print(utterance.spoken)
            if utterance.pause_ms >= paragraph_ms:
                print()
        print()


def _reader_line(lang: str | None, choices: LanguageVoices) -> str:
    """'[In fr, read by piper:fr_FR-mls-medium.]'"""
    voice = choices.voice_for(lang)
    language = primary(lang)
    if choices.speaks(lang):
        return f"[In {language}, read by {voice.spec}.]"
    return f"[In {language}, read by {voice.spec}, which doesn't speak it.]"


def _chosen_sections(book: Book, wanted: int | None) -> list[tuple[int, Section]]:
    numbered = list(enumerate(book.sections, start=1))
    if wanted is None:
        return numbered
    if not 1 <= wanted <= len(numbered):
        raise DogearError(
            f"There is no section {wanted}; this book has {len(numbered)}.",
            hint="Run `dogear outline` to see the section numbers.",
        )
    return [numbered[wanted - 1]]


def _block_lines(block) -> list[str]:
    match block:
        case PageMarker(label=label):
            return [f"[Print page {label}]"]
        case SceneBreak():
            return ["[Scene break]", ""]
        case Heading() | Paragraph():
            return [block.text, ""]
        case ListBlock(items=items):
            return [f"- {item}" for item in items] + [""]
        case Footnote(label=label, text=text):
            # The note id is a file path plus an anchor: noise when read aloud.
            return [(f"Note {label}: " if label else "Note: ") + text, ""]
        case Image(alt=alt, caption=caption):
            described = alt if alt else ("no description" if alt is None else "decorative")
            return [f"Image: {described}." + (f" Caption: {caption}" if caption else ""), ""]
        case Table():
            return [f"Table: {block_text(block)}", ""]
    raise TypeError(f"not a block: {block!r}")


def _make(args: argparse.Namespace, config: Config) -> int:
    _apply_voice_options(args, config)
    _use_word_files(args, config)
    if args.format:
        config["output"]["formats"] = parse_formats(args.format)
    request = MakeRequest(
        book=args.book,
        output_dir=args.output_dir or args.book.resolve().parent,
        sections=parse_sections(args.sections) if args.sections else None,
        language=args.language,
    )
    saved = make_audiobook(request, config, _report)
    for path in saved:
        print(f"Saved: {path}")
    return 0


def _words(args: argparse.Namespace, config: Config) -> int:
    _apply_voice_options(args, config)
    _use_word_files(args, config)
    request = WordsRequest(
        book=args.book,
        sections=parse_sections(args.sections) if args.sections else None,
        language=args.language,
    )
    unknown = find_unknown_words(request, config, _report)
    if args.json:
        # ASCII only, so any program can read it whatever the console's encoding.
        print(json.dumps(words_to_json(unknown), indent=2))
        return 0
    where = "these sections" if args.sections else "this book"
    if not unknown:
        print(f"The voice knows every word in {where}.")
        return 0
    print(
        f"{count(len(unknown), 'word')} in {where} the voice doesn't know, most frequent "
        "first. It guesses how to say them."
    )
    print()
    for number, word in enumerate(unknown, start=1):
        print(f"{number}. {word.word}, {count(word.count, 'time')}.")
    print()
    print(
        'To hear one, run: dogear say "the word in a sentence" -o test.wav. To fix one, add '
        "it to a pronunciation file (the README shows how) and pass the file with --words."
    )
    return 0


def _say(args: argparse.Namespace, config: Config) -> int:
    _apply_voice_options(args, config)
    _use_word_files(args, config)
    for said in say_text(args.text, args.output, config, _report):
        print(f"Said: {said.spoken}")
        if args.phonemes:
            print(f"Phonemes: {said.voice_input}")
    print(f"Saved: {args.output}")
    return 0


def _use_word_files(args: argparse.Namespace, config: Config) -> None:
    """Add the --words files after the configured ones, so their entries win, and read
    every file now, so a mistake in one is reported before anything slow."""
    added = [str(path) for path in args.words or []]
    config["pronunciation"]["files"] = [*config["pronunciation"]["files"], *added]
    dictionary_from_config(config)


def _voices(args: argparse.Namespace, config: Config) -> int:
    found = installed()
    using = (config["voice"]["engine"], config["voice"]["voice"])
    any_cloud = False
    for engine in found.engines:
        voices = engine.voices_in(config)
        print(
            f"{engine.title}, licensed {engine.plugin.license}: "
            f"{count(len(voices), 'voice')}. {engine.plugin.about_voices}"
        )
        print()
        for number, voice in enumerate(voices, start=1):
            host = text_goes_to(ChosenVoice(engine, voice), config)
            any_cloud = any_cloud or host is not None
            where = f" ({sends_text(host)})" if host else ""
            note = " This is the one Dogear uses now." if (engine.name, voice.name) == using else ""
            print(f"{number}. {describe_voice(voice)}{where}.{note}")
        print()
    if any_cloud:
        allowed = (
            "Your settings allow them: allow_cloud is true under [voice]."
            if config["voice"]["allow_cloud"]
            else f"Dogear uses one only if you allow it: add {ALLOW_OPTION} to `dogear make` "
            "or `dogear say`, or set allow_cloud = true under [voice] in your settings file."
        )
        print(
            "Voices marked as sending the book's text are spoken by a service on another "
            f"computer, and the text they speak is sent there. {allowed}"
        )
        print()
    for broken in found.broken:
        print(
            f"The voice engine {broken.name}, from {broken.package}, is installed but can't be "
            f"used, so its voices aren't listed. {broken.problem} Reinstall it, or uninstall it "
            "if you don't need it."
        )
        print()
    if not any(e.name == using[0] and e.voice(using[1], config) for e in found.engines):
        print(
            f"Your settings choose the voice {using[1]} of the engine {using[0]}, which isn't here."
        )
        print()
    print(
        "To use another voice, add --voice NAME to `dogear make`, or set voice and its engine "
        "under [voice] in your config. If two engines have a voice of the same name, put the "
        "engine's name and a colon first, such as kokoro:af_heart."
    )
    return 0


def _download(args: argparse.Namespace, config: Config) -> int:
    if args.check:
        check_voice_files(config, lambda line: print(line, flush=True), _report)
        return 0
    _apply_voice_options(args, config)
    chosen = prepare_voice(config, _report)
    host = text_goes_to(chosen, config)
    if host is None:
        print(f"The voice {chosen.voice.name} is ready to use offline.")
    else:
        print(
            f"The voice {chosen.voice.name} has nothing to download: it is spoken by a service "
            f"at {host}, so it needs the internet, and sends the text it speaks there."
        )
    return 0


def _apply_voice_options(args: argparse.Namespace, config: Config) -> None:
    if getattr(args, "allow_cloud", False):
        config["voice"]["allow_cloud"] = True
    if getattr(args, "voice", None):
        choose_voice(config, args.voice)
    if getattr(args, "speed", None) is not None:
        if not 0.5 <= args.speed <= 2.0:
            raise DogearError(
                f"A speed of {args.speed} is outside what the voice can do.",
                hint="Use a value from 0.5 to 2.0, such as 0.9 or 1.1.",
            )
        config["voice"]["speed"] = args.speed


def _report(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _ocr(args: argparse.Namespace, config: Config) -> int:
    if args.language:
        config["ocr"]["language"] = args.language
    if args.dpi:
        config["ocr"]["dpi"] = args.dpi
    output = args.output or default_output_path(args.pdf)
    pages = ocr_pdf(args.pdf, output, config, _report)
    print(f"Saved {count(pages, 'page')} with a text layer to {output}")
    return 0


def _serve(args: argparse.Namespace, config: Config) -> int:
    # Imported here so the other commands don't load the web server.
    from dogear.web.server import ServeOptions, serve

    options = ServeOptions(
        port=args.port,
        lan=args.lan,
        open_browser=not args.no_browser,
        config_file=args.config,
    )
    return serve(options, config)
