"""An EPUB 3 talking book: the book's text and its audio in step (Media Overlays).

Each section becomes a content document (`epub3_text`), a SMIL file pairing its
elements with its audio (`epub3_smil`), and its MP3. The navigation document
(`epub3_nav`) and the package document (`epub3_package`, with the accessibility claims
from `epub3_a11y`) tie them together, and `epub3_container` zips them up.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from dogear.config import Config
from dogear.document import Metadata
from dogear.output.book_identity import book_uid, narrator
from dogear.output.cover import write_cover
from dogear.output.epub3_a11y import accessibility_metadata
from dogear.output.epub3_container import write_container
from dogear.output.epub3_nav import NAV_FILE, TextDocument, navigation_document
from dogear.output.epub3_package import PACKAGE_FILE, Item, Package, package_document
from dogear.output.epub3_package import serialize as serialize_package
from dogear.output.epub3_smil import media_overlay
from dogear.output.epub3_smil import serialize as serialize_smil
from dogear.output.epub3_text import content_document
from dogear.output.epub3_xhtml import STYLESHEET
from dogear.output.epub3_xhtml import serialize as serialize_xhtml
from dogear.output.language_tag import language_tag
from dogear.readalong.model import SectionText
from dogear.voice.engines import ChosenVoice

log = logging.getLogger(__name__)

# The class a reading app puts on the element being read aloud. epub3.css styles it.
ACTIVE_CLASS = "-epub-media-overlay-active"
# Dogear's voices speak only English, and `dogear make` refuses books marked as being in
# another language, so a book that names no usable language is in English.
FALLBACK_LANGUAGE = "en"
# The folder inside the zip that holds everything but the container's own files.
ROOT = "EPUB"
_XHTML = "application/xhtml+xml"
_IMAGE_TYPES = {".jpg": "image/jpeg", ".png": "image/png"}


def build_epub3(
    sections: list[SectionText],
    metadata: Metadata,
    path: Path,
    config: Config,
    voices: Sequence[ChosenVoice] = (),
) -> None:
    """Write the talking book to `path`, a .epub file. `voices` read it, the main voice
    first (none: the configured voice alone), and are named as its narrators.

    `sections` come from `readalong.build_readalong`, in reading order, with their audio
    already encoded by `talking_audio` (each `section.audio.path` is the MP3 to put in
    the book) and the syncs that tie their text to it, in seconds. The book is built
    next to `path` and moved into place at the end, so a failed run never leaves a
    broken book under the real name.
    """
    if not sections:
        raise ValueError("no sections to package")
    language = _book_language(metadata)
    width = max(3, len(str(len(sections))))
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".dogear-") as tmp:
        work = Path(tmp)
        book = _BookFiles()
        for position, section in enumerate(sections, start=1):
            book.add_section(section, f"s{position:0{width}d}", language)
        nav = navigation_document(book.documents, language)
        book.add(Item("nav", NAV_FILE, _XHTML, properties="nav"), serialize_xhtml(nav))
        book.add(Item("css", STYLESHEET, "text/css"), _stylesheet())
        cover = write_cover(metadata.cover, work)
        if cover:
            suffix = cover.path.suffix
            href, media_type = f"cover{suffix}", _IMAGE_TYPES[suffix]
            book.add(Item("cover-image", href, media_type, properties="cover-image"), cover.path)
        voice = narrator(config, voices)
        package = Package(
            identifier=book_uid(metadata),
            title=metadata.title.strip() or sections[0].title,
            authors=tuple(name.strip() for name in metadata.authors if name.strip()),
            language=language,
            publisher=(metadata.publisher or "").strip() or None,
            modified=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            narrator=voice,
            active_class=ACTIVE_CLASS,
            overlays=tuple(book.overlays),
            accessibility=tuple(accessibility_metadata(sections, metadata, voice)),
            items=tuple(book.items),
            spine=tuple(book.spine),
        )
        package_path = f"{ROOT}/{PACKAGE_FILE}"
        # First after the container's own files, where a reading app looks next.
        book.files.insert(0, (package_path, serialize_package(package_document(package))))
        partial = work / "book.epub"
        write_container(partial, package_path, book.files)
        os.replace(partial, path)
    log.info("Built %s with %d sections", path.name, len(sections))


@dataclass
class _BookFiles:
    """The files going into the book, with their manifest entries, as they're made."""

    files: list[tuple[str, bytes | Path]] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    spine: list[str] = field(default_factory=list)
    overlays: list[tuple[str, int]] = field(default_factory=list)
    documents: list[TextDocument] = field(default_factory=list)

    def add(self, item: Item, content: bytes | Path) -> None:
        self.files.append((f"{ROOT}/{item.href}", content))
        self.items.append(item)

    def add_section(self, section: SectionText, name: str, book_language: str) -> None:
        """A section's text, its SMIL, and its audio, all named after `name`."""
        audio = section.audio.path
        if audio.suffix.lower() != ".mp3":
            raise ValueError(
                f"Section {section.number}'s audio is {audio.name}; encode it with "
                "talking_audio first."
            )
        lang = language_tag(section.lang) or book_language
        text_href, smil_href, audio_href = f"{name}.xhtml", f"{name}.smil", f"audio/{name}.mp3"
        html = content_document(section, lang)
        overlay = media_overlay(section, text_href, audio_href, set(html.xpath("//@id")))
        overlay_id = f"{name}-overlay"
        self.add(Item(name, text_href, _XHTML, media_overlay=overlay_id), serialize_xhtml(html))
        self.add(Item(overlay_id, smil_href, "application/smil+xml"), serialize_smil(overlay.smil))
        self.add(Item(f"{name}-audio", audio_href, "audio/mpeg"), audio)
        self.spine.append(name)
        self.overlays.append((overlay_id, overlay.milliseconds))
        self.documents.append(TextDocument(section, text_href, lang))


def _book_language(metadata: Metadata) -> str:
    tag = language_tag(metadata.language)
    if metadata.language and tag is None:
        log.warning(
            "The book's language, '%s', isn't a language code Dogear recognizes, so the "
            "EPUB talking book is marked as English (%s).",
            metadata.language,
            FALLBACK_LANGUAGE,
        )
    return tag or FALLBACK_LANGUAGE


def _stylesheet() -> bytes:
    return resources.files("dogear.output").joinpath("epub3.css").read_bytes()
