"""Open an EPUB archive and read its package: metadata, manifest, reading order, and guide."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from dogear.document import Metadata
from dogear.errors import ExtractionError
from dogear.extract.xhtml import plain_text, resolve_href
from dogear.extract.zip_archive import ZipArchive

log = logging.getLogger(__name__)

CONTAINER_PATH = "META-INF/container.xml"
ENCRYPTION_PATH = "META-INF/encryption.xml"
DC_NS = "http://purl.org/dc/elements/1.1/"
OPF_MEDIA_TYPE = "application/oebps-package+xml"
XHTML_MEDIA_TYPE = "application/xhtml+xml"
NCX_MEDIA_TYPE = "application/x-dtbncx+xml"

DAMAGED_HINT = (
    "The file may be damaged or only partly downloaded. Download it again, or open it in "
    "Calibre and convert it to EPUB, which often repairs it."
)


class EpubArchive(ZipArchive):
    """Read-only access to the files inside an EPUB, as `ZipArchive` describes."""

    def __init__(self, path: Path, *, max_member_bytes: int) -> None:
        super().__init__(
            path,
            max_member_bytes=max_member_bytes,
            kind="an EPUB",
            damaged_hint=DAMAGED_HINT,
            limit_setting="epub.max_member_mb",
        )


@dataclass(frozen=True)
class ManifestItem:
    id: str
    href: str
    path: str | None  # archive path; None when the href leaves the book
    media_type: str
    properties: frozenset[str]


@dataclass(frozen=True)
class SpineRef:
    idref: str
    linear: bool


@dataclass
class Package:
    path: str  # archive path of the OPF file
    metadata: Metadata
    manifest: dict[str, ManifestItem]
    spine: list[SpineRef]
    nav_path: str | None  # EPUB 3 navigation document
    ncx_path: str | None  # EPUB 2 table of contents
    # Manifest id named by an EPUB 2 <meta name="cover" content="...">, if any.
    cover_id: str | None = None
    # EPUB 2 guide references as (type, archive path), in the order the book lists them.
    guide: list[tuple[str, str]] = field(default_factory=list)


def find_package_path(archive: EpubArchive) -> str:
    """Archive path of the OPF package file named in META-INF/container.xml."""
    if not archive.has(CONTAINER_PATH):
        raise ExtractionError(
            f"{archive.name} has no {CONTAINER_PATH}, so Dogear can't find the book inside it.",
            hint=DAMAGED_HINT,
        )
    rootfiles = archive.parse(CONTAINER_PATH).findall(".//{*}rootfile")
    preferred = [r for r in rootfiles if r.get("media-type") == OPF_MEDIA_TYPE]
    full_path = next((r.get("full-path") for r in preferred + rootfiles), None)
    target = resolve_href("", full_path) if full_path else None
    if target is None or not archive.has(target[0]):
        named = f" ({full_path})" if full_path else ""
        raise ExtractionError(
            f"The package file{named} that lists the contents of {archive.name} is missing.",
            hint=DAMAGED_HINT,
        )
    return target[0]


def read_encrypted_paths(archive: EpubArchive) -> frozenset[str]:
    """Archive paths that META-INF/encryption.xml says are encrypted or obfuscated."""
    if not archive.has(ENCRYPTION_PATH):
        return frozenset()
    paths = set()
    for ref in archive.parse(ENCRYPTION_PATH).iter("{*}CipherReference"):
        target = resolve_href("", ref.get("URI") or "")
        if target is not None:
            paths.add(target[0])
    return frozenset(paths)


def read_package(archive: EpubArchive, opf_path: str, fallback_title: str) -> Package:
    root = archive.parse(opf_path)
    manifest = _manifest(root, opf_path)
    spine_el = root.find("{*}spine")
    if spine_el is None:
        raise ExtractionError(
            f"{opf_path} in {archive.name} has no reading order (spine).", hint=DAMAGED_HINT
        )
    return Package(
        path=opf_path,
        metadata=_metadata(root, fallback_title),
        manifest=manifest,
        spine=_spine(spine_el),
        nav_path=next((i.path for i in manifest.values() if "nav" in i.properties), None),
        ncx_path=_ncx_path(spine_el, manifest),
        cover_id=_cover_id(root),
        guide=_guide(root, opf_path),
    )


def _metadata(root: etree._Element, fallback_title: str) -> Metadata:
    meta = root.find("{*}metadata")
    if meta is None:
        log.warning("The book has no metadata section; using the file name as its title.")
        return Metadata(title=fallback_title)

    def texts(name: str) -> list[str]:
        values = (plain_text(el) for el in meta.iter(f"{{{DC_NS}}}{name}"))
        return [v for v in values if v]

    def first(name: str) -> str | None:
        return next(iter(texts(name)), None)

    return Metadata(
        title=first("title") or fallback_title,
        authors=texts("creator"),
        language=first("language"),
        identifier=_identifier(root, meta),
        publisher=first("publisher"),
    )


def _identifier(root: etree._Element, meta: etree._Element) -> str | None:
    """The dc:identifier named by package@unique-identifier, else the first one."""
    found = [(el.get("id"), plain_text(el)) for el in meta.iter(f"{{{DC_NS}}}identifier")]
    found = [(id_, text) for id_, text in found if text]
    unique_id = root.get("unique-identifier")
    named = next((text for id_, text in found if unique_id and id_ == unique_id), None)
    return named or next((text for _, text in found), None)


def _cover_id(root: etree._Element) -> str | None:
    for el in root.iterfind("{*}metadata/{*}meta"):
        if (el.get("name") or "").strip().lower() == "cover":
            return (el.get("content") or "").strip() or None
    return None


def _guide(root: etree._Element, opf_path: str) -> list[tuple[str, str]]:
    refs = []
    for el in root.iterfind("{*}guide/{*}reference"):
        href = el.get("href")
        target = resolve_href(opf_path, href) if href else None
        if target is not None:
            refs.append(((el.get("type") or "").strip().lower(), target[0]))
    return refs


def _manifest(root: etree._Element, opf_path: str) -> dict[str, ManifestItem]:
    items = {}
    for el in root.iterfind("{*}manifest/{*}item"):
        item_id, href = el.get("id"), el.get("href")
        if not item_id or not href:
            log.warning("Ignored a manifest entry in %s with no id or href.", opf_path)
            continue
        target = resolve_href(opf_path, href)
        items[item_id] = ManifestItem(
            id=item_id,
            href=href,
            path=target[0] if target else None,
            media_type=(el.get("media-type") or "").strip().lower(),
            properties=frozenset((el.get("properties") or "").split()),
        )
    return items


def _spine(spine_el: etree._Element) -> list[SpineRef]:
    return [
        SpineRef(
            idref=el.get("idref") or "",
            linear=(el.get("linear") or "yes").strip().lower() != "no",
        )
        for el in spine_el.iterfind("{*}itemref")
    ]


def _ncx_path(spine_el: etree._Element, manifest: dict[str, ManifestItem]) -> str | None:
    named = manifest.get(spine_el.get("toc") or "")
    if named is not None:
        return named.path
    return next((i.path for i in manifest.values() if i.media_type == NCX_MEDIA_TYPE), None)
