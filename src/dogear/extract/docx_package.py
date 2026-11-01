"""Open a Word document (.docx) and find its parts: the text, styles, lists, and notes.

A .docx file is a ZIP archive read in memory (see `zip_archive`). Its parts are found the
way Word finds them, through relationship files: `_rels/.rels` names the main document
(usually `word/document.xml`) and the document's own relationships name its styles,
numbering, footnotes, endnotes, and settings. Links that leave the archive are ignored.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from dogear.errors import ExtractionError
from dogear.extract.zip_archive import ZipArchive

# WordprocessingML as Word and LibreOffice save it ("Transitional"), as {namespace}
# prefixes to join with a local name: W + "p" is a paragraph.
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
STRICT_W = "{http://purl.oclc.org/ooxml/wordprocessingml/main}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
VML = "{urn:schemas-microsoft-com:vml}"
OFFICE = "{urn:schemas-microsoft-com:office:office}"
MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
RELATIONSHIPS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

PACKAGE_RELS = "_rels/.rels"
DAMAGED_HINT = (
    "The file may be damaged or only partly downloaded. Open it in Word or LibreOffice "
    "and save it again as a Word document (.docx)."
)
NOT_ZIP_HINT = (
    "Older Word files (.doc) and files with a password aren't ZIP archives. Open it in "
    "Word or LibreOffice and save it as a Word document (.docx) with no password."
)


class DocxArchive(ZipArchive):
    """Read-only access to the parts of a .docx file, as `ZipArchive` describes."""

    def __init__(self, path: Path, *, max_member_bytes: int) -> None:
        super().__init__(
            path,
            max_member_bytes=max_member_bytes,
            kind="a Word document",
            damaged_hint=DAMAGED_HINT,
            limit_setting="docx.max_member_mb",
            not_zip_hint=NOT_ZIP_HINT,
        )


@dataclass(frozen=True)
class DocxParts:
    """Archive paths of the parts Dogear reads. Only the document is required."""

    document: str
    core: str | None = None  # docProps/core.xml: title, author, language
    styles: str | None = None
    numbering: str | None = None
    footnotes: str | None = None
    endnotes: str | None = None
    settings: str | None = None


def find_parts(archive: DocxArchive) -> DocxParts:
    if not archive.has(PACKAGE_RELS):
        raise ExtractionError(
            f"{archive.name} isn't a Word document: it has none of the parts a .docx file holds.",
            hint=(
                "If it's another kind of document renamed to end in .docx, open it in Word "
                "or LibreOffice and save it as a Word document (.docx)."
            ),
        )
    package = _relationships(archive, "", PACKAGE_RELS)
    document = package.get("officeDocument")
    if document is None or not archive.has(document):
        raise ExtractionError(
            f"{archive.name} has no main document, so it has no text.", hint=DAMAGED_HINT
        )
    folder, name = posixpath.split(document)
    rels_path = posixpath.join(folder, "_rels", f"{name}.rels")
    related = _relationships(archive, document, rels_path) if archive.has(rels_path) else {}

    def present(path: str | None) -> str | None:
        return path if path is not None and archive.has(path) else None

    return DocxParts(
        document=document,
        core=present(package.get("core-properties")),
        styles=present(related.get("styles")),
        numbering=present(related.get("numbering")),
        footnotes=present(related.get("footnotes")),
        endnotes=present(related.get("endnotes")),
        settings=present(related.get("settings")),
    )


def check_document(root: etree._Element, archive: DocxArchive, part: str) -> etree._Element:
    """The <w:body> of the main document `root`, after checking it's a kind Dogear reads."""
    if root.tag == f"{STRICT_W}document":
        raise ExtractionError(
            f"{archive.name} is saved as a Strict Open XML document, which Dogear can't read yet.",
            hint="Open it in Word or LibreOffice and save it as a Word document (.docx).",
        )
    body = root.find(f"{W}body")
    if root.tag != f"{W}document" or body is None:
        raise ExtractionError(
            f"{part} in {archive.name} isn't a Word document's text.", hint=DAMAGED_HINT
        )
    return body


def _relationships(archive: DocxArchive, source: str, rels_path: str) -> dict[str, str]:
    """Relationship type (its last word, such as "styles") -> archive path of the target.

    The first relationship of each type wins. Targets outside the archive are left out.
    """
    found: dict[str, str] = {}
    for rel in archive.parse(rels_path).iter(f"{RELATIONSHIPS}Relationship"):
        if (rel.get("TargetMode") or "").lower() == "external":
            continue
        kind = (rel.get("Type") or "").rstrip("/").rpartition("/")[2]
        target = _resolve(source, rel.get("Target") or "")
        if kind and target is not None:
            found.setdefault(kind, target)
    return found


def _resolve(source: str, target: str) -> str | None:
    """The archive path `target` names, relative to the part `source` ("" for the package)."""
    if not target or "://" in target:
        return None
    if target.startswith("/"):
        path = posixpath.normpath(target.lstrip("/"))
    else:
        path = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    if path in (".", "..") or path.startswith("../"):
        return None
    return path
