"""The character sets an HTML file may declare, as browsers read them: the labels of the
WHATWG Encoding Standard (https://encoding.spec.whatwg.org/#names-and-labels), each with
the Python codec that decodes it.

Only these labels count. Python's own codec names are not looked up, since they include
codecs that aren't text at all (base64, zlib, rot13) and ones browsers refuse (UTF-7).
As browsers do, a page that declares Latin-1 or ASCII is read as Windows-1252, and one
that declares UTF-16 in a <meta> tag, which was just read as ASCII, as UTF-8.
The standard's "replacement" encodings (ISO-2022-KR, HZ, and the like) and
x-user-defined are left out: a page that declares one is read as if it declared nothing.
"""

from __future__ import annotations

_LABELS: dict[str, tuple[str, ...]] = {
    "utf-8": (
        "unicode-1-1-utf-8 unicode11utf8 unicode20utf8 utf-8 utf8 x-unicode20utf8 "
        # A <meta> that says UTF-16 was readable as ASCII, so it is wrong: UTF-8, as browsers.
        "csunicode iso-10646-ucs-2 ucs-2 unicode unicodefeff unicodefffe utf-16 utf-16be "
        "utf-16le"
    ).split(),
    "cp866": "866 cp866 csibm866 ibm866".split(),
    "iso8859_2": (
        "csisolatin2 iso-8859-2 iso-ir-101 iso8859-2 iso88592 iso_8859-2 iso_8859-2:1987 l2 latin2"
    ).split(),
    "iso8859_3": (
        "csisolatin3 iso-8859-3 iso-ir-109 iso8859-3 iso88593 iso_8859-3 iso_8859-3:1988 l3 latin3"
    ).split(),
    "iso8859_4": (
        "csisolatin4 iso-8859-4 iso-ir-110 iso8859-4 iso88594 iso_8859-4 iso_8859-4:1988 l4 latin4"
    ).split(),
    "iso8859_5": (
        "csisolatincyrillic cyrillic iso-8859-5 iso-ir-144 iso8859-5 iso88595 iso_8859-5 "
        "iso_8859-5:1988"
    ).split(),
    "iso8859_6": (
        "arabic asmo-708 csiso88596e csiso88596i csisolatinarabic ecma-114 iso-8859-6 "
        "iso-8859-6-e iso-8859-6-i iso-ir-127 iso8859-6 iso88596 iso_8859-6 iso_8859-6:1987"
    ).split(),
    "iso8859_7": (
        "csisolatingreek ecma-118 elot_928 greek greek8 iso-8859-7 iso-ir-126 iso8859-7 "
        "iso88597 iso_8859-7 iso_8859-7:1987 sun_eu_greek"
    ).split(),
    "iso8859_8": (
        "csiso88598e csisolatinhebrew hebrew iso-8859-8 iso-8859-8-e iso-ir-138 iso8859-8 "
        "iso88598 iso_8859-8 iso_8859-8:1988 visual csiso88598i iso-8859-8-i logical"
    ).split(),
    "iso8859_10": "csisolatin6 iso-8859-10 iso-ir-157 iso8859-10 iso885910 l6 latin6".split(),
    "iso8859_13": "iso-8859-13 iso8859-13 iso885913".split(),
    "iso8859_14": "iso-8859-14 iso8859-14 iso885914".split(),
    "iso8859_15": "csisolatin9 iso-8859-15 iso8859-15 iso885915 iso_8859-15 l9".split(),
    "iso8859_16": ("iso-8859-16",),
    "koi8_r": "cskoi8r koi koi8 koi8-r koi8_r".split(),
    "koi8_u": "koi8-ru koi8-u".split(),
    "mac_roman": "csmacintosh mac macintosh x-mac-roman".split(),
    "cp874": "dos-874 iso-8859-11 iso8859-11 iso885911 tis-620 windows-874".split(),
    "cp1250": "cp1250 windows-1250 x-cp1250".split(),
    "cp1251": "cp1251 windows-1251 x-cp1251".split(),
    "cp1252": (
        "ansi_x3.4-1968 ascii cp1252 cp819 csisolatin1 ibm819 iso-8859-1 iso-ir-100 "
        "iso8859-1 iso88591 iso_8859-1 iso_8859-1:1987 l1 latin1 us-ascii windows-1252 "
        "x-cp1252"
    ).split(),
    "cp1253": "cp1253 windows-1253 x-cp1253".split(),
    "cp1254": (
        "cp1254 csisolatin5 iso-8859-9 iso-ir-148 iso8859-9 iso88599 iso_8859-9 "
        "iso_8859-9:1989 l5 latin5 windows-1254 x-cp1254"
    ).split(),
    "cp1255": "cp1255 windows-1255 x-cp1255".split(),
    "cp1256": "cp1256 windows-1256 x-cp1256".split(),
    "cp1257": "cp1257 windows-1257 x-cp1257".split(),
    "cp1258": "cp1258 windows-1258 x-cp1258".split(),
    "mac_cyrillic": "x-mac-cyrillic x-mac-ukrainian".split(),
    # The standard's GBK is decoded as GB18030, which it is a part of.
    "gb18030": (
        "chinese csgb2312 csiso58gb231280 gb2312 gb_2312 gb_2312-80 gbk iso-ir-58 x-gbk gb18030"
    ).split(),
    "big5hkscs": "big5 big5-hkscs cn-big5 csbig5 x-x-big5".split(),
    "euc_jp": "cseucpkdfmtjapanese euc-jp x-euc-jp".split(),
    "iso2022_jp": "csiso2022jp iso-2022-jp".split(),
    "cp932": "csshiftjis ms932 ms_kanji shift-jis shift_jis sjis windows-31j x-sjis".split(),
    "cp949": (
        "cseuckr csksc56011987 euc-kr iso-ir-149 korean ks_c_5601-1987 ks_c_5601-1989 "
        "ksc5601 ksc_5601 windows-949"
    ).split(),
}

CODECS = {label: codec for codec, labels in _LABELS.items() for label in labels}


def codec_for_label(label: str) -> str | None:
    """The Python codec for a declared character set, such as "utf-8" for "UTF8", or None
    if browsers don't know the label."""
    return CODECS.get(label.strip().lower())
