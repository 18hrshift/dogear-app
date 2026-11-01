"""Read an EPUB (2 or 3) into a `dogear.document.Book`.

The archive is read in memory with `zipfile` and every XML file goes through
`safe_xml.parse_xml`, so a book can't make Dogear touch the network or other files.
Sections come from the table of contents (see `epub_sections`); the cover image from the
package (see `epub_cover`).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from dogear.config import Config
from dogear.document import Book
from dogear.errors import ConfigError, ExtractionError
from dogear.extract.epub_cover import read_cover
from dogear.extract.epub_nav import read_navigation
from dogear.extract.epub_package import (
    XHTML_MEDIA_TYPE,
    EpubArchive,
    find_package_path,
    read_encrypted_paths,
    read_package,
)
from dogear.extract.epub_sections import read_sections
from dogear.extract.note_links import link_notes

DRM_HINT = (
    "Dogear needs a DRM-free copy. Many stores and public-domain libraries sell or give "
    "away DRM-free EPUBs, and libraries for print-disabled readers can provide accessible "
    "copies."
)


def extract(path: Path, config: Config) -> Book:
    """Read the EPUB at `path`."""
    toc_depth = _toc_depth(config)
    with EpubArchive(path, max_member_bytes=_max_member_bytes(config)) as archive:
        opf_path = find_package_path(archive)
        encrypted = read_encrypted_paths(archive)
        if opf_path in encrypted:
            raise _drm_error(archive.name)
        package = read_package(archive, opf_path, fallback_title=path.stem)
        content = {i.path for i in package.manifest.values() if i.media_type == XHTML_MEDIA_TYPE}
        if content & encrypted:
            raise _drm_error(archive.name)
        nav = read_navigation(archive, package)
        note_markers: dict[str, str] = {}
        sections = read_sections(archive, package, nav, toc_depth, note_markers)
        cover = read_cover(archive, package, nav, encrypted)
    if not sections:
        raise ExtractionError(
            f"{path.name} has no readable text: every document in its reading order is empty.",
            hint="If it's a picture book or a scan, it has no text for Dogear to read aloud.",
        )
    link_notes(sections, note_markers)
    return Book(
        metadata=replace(package.metadata, cover=cover),
        sections=sections,
        source_path=path,
        source_format="epub",
    )


def _max_member_bytes(config: Config) -> int:
    limit_mb = config["epub"]["max_member_mb"]
    if limit_mb <= 0:
        raise ConfigError(f"epub.max_member_mb must be more than 0, got {limit_mb}.")
    return limit_mb * 1024 * 1024


def _toc_depth(config: Config) -> int:
    depth = config["epub"]["toc_depth"]
    if depth < 0:
        raise ConfigError(f"epub.toc_depth must be 0 or more, got {depth}.")
    return depth


def _drm_error(name: str) -> ExtractionError:
    return ExtractionError(
        f"{name} is protected by DRM (copy protection), so Dogear can't read its text.",
        hint=DRM_HINT,
    )
