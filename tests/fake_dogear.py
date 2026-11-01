"""A stand-in for the `dogear` command, run by the web page's tests as its child process.

What it does depends on the first word of the book file:

    ok          outline lists three sections; make prints progress and saves what it's
                asked; words lists WORDS, less any the --words files have
    unreadable  outline and words fail with `Error:` and `What to do:` lines
    fail        make fails with `Error:` and `What to do:` lines
    slow        make prints a line, then waits (for a test to stop it)
    slowread    outline waits (for a test to stop it), then lists three sections
    scanread    outline reports reading SCAN_PAGES pages with OCR, one every SCAN_SECONDS,
                then lists three sections
    slowwords   words waits (for a test to stop it)
    crash       make fails with a traceback and no `Error:` line
    manywords   words lists 60 words
    nowords     words lists none
    badwords    words prints something that isn't JSON

`say` writes the text after `--` into a small WAV file, with the spellings of the --words
files in place, and prints `Said:` and `Saved:` lines. Text that starts with "fail" fails
with `Error:` and `What to do:` lines; text that starts with "slow" waits to be stopped.

When DOGEAR_FAKE_LOG names a file, each run adds its arguments to it as a JSON list.
When DOGEAR_FAKE_PIDS names a file, each run adds its process id to it, on a line.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import tomllib
import wave
from pathlib import Path

from dogear.web.server_watch import stop_with_server_when_asked

TITLE = "Fake Book"
SECTIONS = ["Title Page", "One", "Two"]
WORDS = [("Utterson", 131), ("Lanyon", 40), ("Poole", 12)]
SCAN_PAGES = 6
SCAN_SECONDS = 0.4
SCAN_START = (
    "This PDF is scanned, so Dogear is reading its 6 pages with OCR, which takes a while: "
    "often a few seconds a page."
)


def main(argv: list[str]) -> int:
    stop_with_server_when_asked()  # as the real `dogear` does
    log = os.environ.get("DOGEAR_FAKE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as file:
            file.write(json.dumps(argv) + "\n")
    pids = os.environ.get("DOGEAR_FAKE_PIDS")
    if pids:
        with open(pids, "a", encoding="utf-8") as file:
            file.write(f"{os.getpid()}\n")
    if argv[0] == "say":
        return say(argv)
    command, book = argv[0], Path(argv[1])
    behaviour = book.read_text(encoding="utf-8").split()[0]
    if command == "outline":
        if behaviour == "unreadable":
            return fail()
        if behaviour == "slowread" and not wait_to_be_stopped():
            return 130
        if behaviour == "scanread":
            read_with_ocr()
        print(json.dumps(summary()))
        return 0
    if command == "words":
        return words(argv, behaviour)
    if behaviour == "fail":
        return fail()
    return make(argv, behaviour)


def wait_to_be_stopped() -> bool:
    """Waits a minute. False if it was stopped (Ctrl+C) meanwhile."""
    try:
        time.sleep(60)
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return False
    return True


def read_with_ocr() -> None:
    """What `dogear outline` reports while it reads a scanned PDF with OCR."""
    print(SCAN_START, file=sys.stderr, flush=True)
    for page in range(1, SCAN_PAGES + 1):
        time.sleep(SCAN_SECONDS)
        print(f"Read {page} of {SCAN_PAGES} pages with OCR.", file=sys.stderr, flush=True)


def word_files(argv: list[str]) -> list[Path]:
    options = argv[: argv.index("--")] if "--" in argv else argv
    return [Path(options[i + 1]) for i, arg in enumerate(options) if arg == "--words"]


def spellings(argv: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in word_files(argv):
        for word, value in tomllib.loads(path.read_text(encoding="utf-8"))["words"].items():
            if isinstance(value, str):
                found[word] = value
    return found


def words(argv: list[str], behaviour: str) -> int:
    if behaviour == "unreadable":
        return fail()
    if behaviour == "slowwords" and not wait_to_be_stopped():
        return 130
    if behaviour == "badwords":
        print("not JSON")
        return 0
    if behaviour == "manywords":
        listed = [(f"Name{n:02d}", 100 - n) for n in range(60)]
    elif behaviour == "nowords":
        listed = []
    else:
        known = {word.casefold() for word in spellings(argv)}
        listed = [(word, count) for word, count in WORDS if word.casefold() not in known]
    print(json.dumps({"words": [{"word": w, "count": c} for w, c in listed]}))
    return 0


def say(argv: list[str]) -> int:
    out = Path(argv[argv.index("-o") + 1])
    text = argv[argv.index("--") + 1]  # never anywhere else
    if text.startswith("slow") and not wait_to_be_stopped():
        return 130
    if text.startswith("fail"):
        print("Error: The voice couldn't be loaded.", file=sys.stderr)
        print("What to do: Run dogear download.", file=sys.stderr)
        return 1
    said = text
    for word, spelling in spellings(argv).items():
        said = re.sub(rf"\b{re.escape(word)}\b", spelling, said, flags=re.IGNORECASE)
    with wave.open(str(out), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(1)
        audio.setframerate(8000)
        audio.writeframes(said.encode("utf-8") * 20)
    print(f"Said: {said}")
    print(f"Saved: {out}")
    return 0


def fail() -> int:
    print("Error: This book is locked with DRM.", file=sys.stderr)
    print("What to do: Use a copy without DRM.", file=sys.stderr)
    return 1


def summary() -> dict:
    return {
        "title": TITLE,
        "authors": ["Ann Author"],
        "language": "en",
        "source_format": "epub",
        "words": 300,
        "sections": [
            {
                "number": n,
                "title": title,
                "kind": "front" if n == 1 else "body",
                "words": 100,
                "first_page": None if n == 1 else str(n),
                "last_page": None if n == 1 else str(n + 1),
                "images": 0,
                "notes": 1 if n == 2 else 0,
                "tables": 0,
            }
            for n, title in enumerate(SECTIONS, start=1)
        ],
    }


def make(argv: list[str], behaviour: str) -> int:
    out = Path(argv[argv.index("-o") + 1])
    formats = argv[argv.index("--format") + 1].split(",")
    print(f"Making an audiobook of {TITLE}: 3 sections, about 300 words.", file=sys.stderr)
    sys.stderr.flush()
    if behaviour == "crash":
        raise RuntimeError("a bug")
    if behaviour == "slow":
        try:
            time.sleep(60)
        except KeyboardInterrupt:  # the goodbye `dogear make` gives in a terminal
            print("Stopped.", file=sys.stderr)
            print(
                "What was done so far is saved. Run the same command again to carry on.",
                file=sys.stderr,
            )
            return 130
    for n, title in enumerate(SECTIONS, start=1):
        print(f"Section {n} of 3 done: {title}.", file=sys.stderr)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("m4b", "mp3", "epub", "daisy"):
        if name in formats:
            print(f"Saved: {save(out, name)}")
    return 0


def save(out: Path, name: str) -> Path:
    if name == "m4b":
        path = out / f"{TITLE}.m4b"
        path.write_bytes(b"m4b" * 1000)
    elif name == "epub":
        path = out / f"{TITLE} talking book.epub"
        path.write_bytes(b"epub" * 1000)
    else:
        path = out / f"{TITLE} {name.upper()}"
        path.mkdir(exist_ok=True)
        (path / "01 One.mp3").write_bytes(b"one" * 1000)
        (path / "02 Two.mp3").write_bytes(b"two" * 1000)
    return path


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
