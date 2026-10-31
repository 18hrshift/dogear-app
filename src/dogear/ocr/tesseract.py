"""Tesseract, the OCR program: finding it, checking it has the languages asked for, and
reading one page's picture with it.

What Tesseract reads a page into depends on its version and on its language data, the
`<language>.traineddata` files in the folder `tesseract --list-langs` names. The OCR
cache key covers both: the version, and the SHA-256 of each language's file, so a new
language pack, or TESSDATA_PREFIX pointing at another folder, reads a book again. Hashing
them takes about 10 ms for English's 4 MB file.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from dogear.config import Config
from dogear.errors import OcrLanguageError, ToolFailedError
from dogear.ocr.settings import OcrSettings
from dogear.tools import find_tool, run_tool

log = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 60  # for `tesseract --version` and `--list-langs`, which are quick
# The heading of `tesseract --list-langs` names the folder in Tesseract 4.1 and later:
# List of available languages in "/usr/share/tesseract-ocr/5/tessdata/" (2):
_FOLDER = re.compile(r'List of available languages in "(.+)" \(\d+\):')
NO_FOLDER = "folder not given"  # the language data, for a Tesseract that doesn't say


@dataclass(frozen=True)
class Tesseract:
    program: str
    version: str  # the first line of `tesseract --version`, such as "tesseract 5.3.4"
    # The SHA-256 of each ocr.language's traineddata file, "eng=<hex>", for the cache key.
    language_data: str = ""


@dataclass(frozen=True)
class LanguageListing:
    """What `tesseract --list-langs` says: where the language data is, and what is there."""

    folder: Path | None  # None for a Tesseract that doesn't say
    languages: set[str]


def find_tesseract(config: Config, settings: OcrSettings) -> Tesseract:
    """Tesseract, checked to have every language in ocr.language. Raises ToolNotFoundError
    if it isn't installed, and OcrLanguageError naming a language pack that's missing."""
    program = find_tool("tesseract", config)
    result = run_tool([program, "--version"], timeout=CHECK_TIMEOUT_SECONDS)
    lines = [line.strip() for line in (result.stdout + result.stderr).splitlines()]
    version = next((line for line in lines if line), "tesseract (version unknown)")
    listing = list_languages(program)
    missing = [language for language in settings.languages if language not in listing.languages]
    if missing:
        raise missing_language(missing[0], settings.language)
    return Tesseract(program, version, language_data(listing, settings.languages))


def list_languages(program: str) -> LanguageListing:
    """What `tesseract --list-langs` lists. Older versions print it on stderr."""
    result = run_tool([program, "--list-langs"], timeout=CHECK_TIMEOUT_SECONDS)
    return read_listing(result.stdout + "\n" + result.stderr)


def read_listing(text: str) -> LanguageListing:
    """The listing in `text`: a heading, then the languages, one a line."""
    found = _FOLDER.search(text)
    words = (line.strip() for line in text.splitlines())
    return LanguageListing(
        folder=Path(found.group(1)) if found else None,
        languages={word for word in words if word and " " not in word},
    )


def language_data(listing: LanguageListing, languages: list[str]) -> str:
    """Each language's traineddata file's SHA-256, as "eng=<hex>+fra=<hex>", or NO_FOLDER
    if Tesseract doesn't say where they are. A file Dogear can't read is "eng=unreadable":
    Tesseract says what's wrong with it when it reads a page."""
    if listing.folder is None:
        log.info(
            "Tesseract doesn't say where its language data is, so OCR read before is kept "
            "after a new language pack; a new version of Tesseract still reads it again."
        )
        return NO_FOLDER
    parts = []
    for language in languages:
        path = listing.folder / f"{language}.traineddata"
        try:
            parts.append(f"{language}={_sha256(path)}")
        except OSError:
            parts.append(f"{language}=unreadable")
    return "+".join(parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def missing_language(language: str, asked: str) -> OcrLanguageError:
    package = "tesseract-ocr-" + language.lower().replace("_", "-")
    return OcrLanguageError(
        f"Tesseract doesn't have the language pack for '{language}', which ocr.language "
        f"({asked!r}) asks for.",
        hint=f"Install it (Debian/Ubuntu: sudo apt install {package}; macOS: brew install "
        "tesseract-lang; Windows: run the Tesseract installer again and choose the language "
        "under Additional language data), or change ocr.language in your settings file.",
    )


def read_image(
    tesseract: Tesseract,
    image: Path,
    out_base: Path,
    *,
    dpi: int,
    settings: OcrSettings,
    text_only: bool,
) -> Path:
    """Read the picture of a page into a one-page PDF at `out_base`.pdf, which it returns:
    the page's words as invisible text where they are printed, over the picture unless
    `text_only`. The page is `image`'s size at `dpi`, so positions and font sizes match
    the page it was drawn from."""
    command = [tesseract.program, str(image), str(out_base), "-l", settings.language]
    command += ["--dpi", str(dpi)]
    if text_only:
        command += ["-c", "textonly_pdf=1"]
    try:
        run_tool([*command, "pdf"])
    except ToolFailedError as e:
        if "Failed loading language" in e.message:
            raise missing_language(settings.language, settings.language) from None
        raise
    return out_base.with_suffix(".pdf")
