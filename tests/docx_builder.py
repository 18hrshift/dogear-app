"""Write small synthetic Word documents (.docx) for tests.

`build_docx` writes a minimal valid package: [Content_Types].xml, the package and
document relationships, word/document.xml around the `body` given, and, unless told
otherwise, styles.xml (Normal, Title, Heading 1 to 3, and a table of contents style) and
numbering.xml (list 1 numbered, list 2 bulleted, list 3 with no number shown). Notes,
settings, and docProps/core.xml are added when given. Every part is checked with lxml
before it is written, so a broken fixture fails here rather than in the extractor.

The helpers (`p`, `r`, `table`, `drawing`, ...) return WordprocessingML as text, to be
combined into a body. Text arguments are inserted as raw XML.
"""

from __future__ import annotations

import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NAMESPACES = " ".join(
    f'xmlns:{prefix}="{uri}"'
    for prefix, uri in {
        "w": W_NS,
        "r": REL_NS,
        "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
        "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
        "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
        "v": "urn:schemas-microsoft-com:vml",
        "o": "urn:schemas-microsoft-com:office:office",
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
        "adec": "http://schemas.microsoft.com/office/drawing/2017/decorative",
    }.items()
)
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_DOCUMENT = f"{REL_NS}/officeDocument"
CORE_PROPERTIES = (
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
)

STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {NAMESPACES}>
  <w:docDefaults><w:rPrDefault><w:rPr><w:lang w:val="en-US"/></w:rPr></w:rPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>
    <w:basedOn w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/>
    <w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="TOC1"><w:name w:val="toc 1"/>
    <w:basedOn w:val="Normal"/></w:style>
  <w:style w:type="character" w:styleId="French"><w:name w:val="French"/>
    <w:rPr><w:lang w:val="fr-FR"/></w:rPr></w:style>
</w:styles>
"""

NUMBERING = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering {NAMESPACES}>
  <w:abstractNum w:abstractNumId="10">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/></w:lvl>
    <w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="20">
    <w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>
    <w:lvl w:ilvl="1"><w:numFmt w:val="lowerLetter"/></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="30">
    <w:lvl w:ilvl="0"><w:numFmt w:val="none"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="10"/></w:num>
  <w:num w:numId="2"><w:abstractNumId w:val="20"/></w:num>
  <w:num w:numId="3"><w:abstractNumId w:val="30"/></w:num>
</w:numbering>
"""


def r(
    text: str = "",
    *,
    lang: str | None = None,
    style: str | None = None,
    hidden: bool = False,
    extra: str = "",
) -> str:
    """A run of text. `extra` is raw run content after the text, such as "<w:br/>"."""
    props = ""
    if style:
        props += f'<w:rStyle w:val="{style}"/>'
    if hidden:
        props += "<w:vanish/>"
    if lang:
        props += f'<w:lang w:val="{lang}"/>'
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    t = f'<w:t xml:space="preserve">{text}</w:t>' if text else ""
    return f"<w:r>{rpr}{t}{extra}</w:r>"


def p(
    *content: str,
    style: str | None = None,
    num: tuple[int, int] | None = None,
    outline: int | None = None,
) -> str:
    """A paragraph. Plain strings in `content` become runs; strings starting with "<"
    are raw paragraph content (runs, hyperlinks, ...)."""
    props = ""
    if style:
        props += f'<w:pStyle w:val="{style}"/>'
    if num is not None:
        num_id, level = num
        props += f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/></w:numPr>'
    if outline is not None:
        props += f'<w:outlineLvl w:val="{outline}"/>'
    ppr = f"<w:pPr>{props}</w:pPr>" if props else ""
    runs = "".join(c if c.startswith("<") else r(c) for c in content)
    return f"<w:p>{ppr}{runs}</w:p>"


def heading(level: int, text: str) -> str:
    return p(text, style=f"Heading{level}")


def table(rows: Sequence[Sequence[str]], *, header_rows: int = 0) -> str:
    """A table. Each cell is raw cell content when it starts with "<", else one paragraph."""
    xml_rows = []
    for i, row in enumerate(rows):
        trpr = "<w:trPr><w:tblHeader/></w:trPr>" if i < header_rows else ""
        cells = "".join(f"<w:tc>{c if c.startswith('<') else p(c)}</w:tc>" for c in row)
        xml_rows.append(f"<w:tr>{trpr}{cells}</w:tr>")
    return f"<w:tbl><w:tblPr/>{''.join(xml_rows)}</w:tbl>"


def drawing(descr: str | None = None, *, title: str | None = None, decorative: bool = False) -> str:
    """A run holding a picture, with the alt text Word keeps in wp:docPr."""
    attrs = ""
    if descr is not None:
        attrs += f' descr="{escape(descr)}"'
    if title is not None:
        attrs += f' title="{escape(title)}"'
    ext = (
        '<a:extLst><a:ext uri="{C183D7F6-B498-43B3-948B-1728B52AA6E4}">'
        '<adec:decorative val="1"/></a:ext></a:extLst>'
        if decorative
        else ""
    )
    return (
        "<w:r><w:drawing><wp:inline>"
        f'<wp:docPr id="1" name="Picture 1"{attrs}>{ext}</wp:docPr>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:blipFill><a:blip r:embed="rIdImage"/></pic:blipFill></pic:pic>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )


def text_box(*paragraphs: str) -> str:
    """A run holding a text box shape with `paragraphs` in it, as Word 2010 and later
    write one: a DrawingML shape, with a VML copy as the fallback."""
    content = f"<w:txbxContent>{''.join(paragraphs)}</w:txbxContent>"
    return (
        "<w:r><mc:AlternateContent>"
        '<mc:Choice Requires="wps"><w:drawing><wp:anchor>'
        '<wp:docPr id="2" name="Text Box 2"/>'
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        f"<wps:wsp><wps:txbx>{content}</wps:txbx></wps:wsp>"
        "</a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>"
        f"<mc:Fallback><w:pict><v:shape><v:textbox>{content}</v:textbox></v:shape></w:pict>"
        "</mc:Fallback></mc:AlternateContent></w:r>"
    )


def footnote_ref(note_id: int, *, custom: str | None = None) -> str:
    if custom is not None:
        return (
            f'<w:r><w:footnoteReference w:customMarkFollows="1" w:id="{note_id}"/>'
            f"<w:t>{custom}</w:t></w:r>"
        )
    return f'<w:r><w:footnoteReference w:id="{note_id}"/></w:r>'


def endnote_ref(note_id: int) -> str:
    return f'<w:r><w:endnoteReference w:id="{note_id}"/></w:r>'


def notes_xml(kind: str, notes: Mapping[int, str]) -> str:
    """footnotes.xml or endnotes.xml: Word's two separator notes, then `notes` (id -> raw
    paragraphs, or plain text for one paragraph opening with the note's own mark)."""
    mark = f"<w:r><w:{kind}Ref/></w:r>"
    items = [
        f'<w:{kind} w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:{kind}>',
        f'<w:{kind} w:type="continuationSeparator" w:id="0"><w:p><w:r>'
        f"<w:continuationSeparator/></w:r></w:p></w:{kind}>",
    ]
    for note_id, content in notes.items():
        paragraphs = content if content.startswith("<") else p(mark, " " + content)
        items.append(f'<w:{kind} w:id="{note_id}">{paragraphs}</w:{kind}>')
    root = f"w:{kind}s"
    return f'<?xml version="1.0" encoding="UTF-8"?><{root} {NAMESPACES}>{"".join(items)}</{root}>'


def core_xml(
    title: str | None = None, creator: str | None = None, language: str | None = None
) -> str:
    fields = ""
    if title is not None:
        fields += f"<dc:title>{title}</dc:title>"
    if creator is not None:
        fields += f"<dc:creator>{creator}</dc:creator>"
    if language is not None:
        fields += f"<dc:language>{language}</dc:language>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        f'xmlns:dc="http://purl.org/dc/elements/1.1/">{fields}</cp:coreProperties>'
    )


def build_docx(
    path: Path,
    body: str,
    *,
    title: str | None = "Test Document",
    creator: str | None = "Ada Writer",
    language: str | None = None,
    core: bool = True,
    styles: str | None = STYLES,
    numbering: str | None = NUMBERING,
    footnotes: Mapping[int, str] | None = None,
    endnotes: Mapping[int, str] | None = None,
    settings: str | None = None,
    sect_pr: str = "",
    document: str | None = None,  # the whole of word/document.xml, instead of `body`
    files: Mapping[str, str | bytes] | None = None,  # archive path -> content, added last
    rels: bool = True,  # False leaves out _rels/.rels
) -> Path:
    parts: dict[str, str] = {}
    if document is None:
        document = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:document {NAMESPACES}><w:body>{body}{sect_pr}</w:body></w:document>"
        )
    related = {}
    for name, content, kind in (
        ("styles.xml", styles, "styles"),
        ("numbering.xml", numbering, "numbering"),
        ("footnotes.xml", notes_xml("footnote", footnotes) if footnotes else None, "footnotes"),
        ("endnotes.xml", notes_xml("endnote", endnotes) if endnotes else None, "endnotes"),
        ("settings.xml", settings, "settings"),
    ):
        if content is not None:
            parts[f"word/{name}"] = content
            related[name] = f"{REL_NS}/{kind}"
    package_rels = {"word/document.xml": OFFICE_DOCUMENT}
    if core:
        parts["docProps/core.xml"] = core_xml(title, creator, language)
        package_rels["docProps/core.xml"] = CORE_PROPERTIES
    parts["word/document.xml"] = document
    parts["word/_rels/document.xml.rels"] = _rels(related)
    if rels:
        parts["_rels/.rels"] = _rels(package_rels)
    for content in parts.values():
        etree.fromstring(content.encode())  # a broken fixture fails here
    parts["[Content_Types].xml"] = CONTENT_TYPES
    contents: dict[str, str | bytes] = {**parts, **(files or {})}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in contents.items():
            zf.writestr(name, content)
    return path


def settings_xml(footnote_format: str | None = None, endnote_format: str | None = None) -> str:
    props = ""
    if footnote_format:
        props += f'<w:footnotePr><w:numFmt w:val="{footnote_format}"/></w:footnotePr>'
    if endnote_format:
        props += f'<w:endnotePr><w:numFmt w:val="{endnote_format}"/></w:endnotePr>'
    return f'<?xml version="1.0" encoding="UTF-8"?><w:settings {NAMESPACES}>{props}</w:settings>'


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""


def _rels(targets: Mapping[str, str]) -> str:
    items = "".join(
        f'<Relationship Id="rId{i}" Type="{kind}" Target="{target}"/>'
        for i, (target, kind) in enumerate(targets.items(), start=1)
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PACKAGE_REL}">'
        f"{items}</Relationships>"
    )
