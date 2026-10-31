"""Book language as the three-letter ISO 639-2 code that MP4 and ID3 language fields hold.

Books carry BCP 47 tags such as "en" or "en-GB". MP4 stores a language as three letters
(ISO 639-2/T) and ffmpeg ignores anything else, so two-letter codes are looked up here.
The table also turns a three-letter code back into two letters, so a book marked "eng"
matches a voice marked "en-US".
"""

from __future__ import annotations

# ISO 639-1 code -> ISO 639-2/T code, every language that has a two-letter code.
_TWO_TO_THREE = dict(
    pair.split(":")
    for pair in """
    aa:aar ab:abk ae:ave af:afr ak:aka am:amh an:arg ar:ara as:asm av:ava ay:aym az:aze
    ba:bak be:bel bg:bul bh:bih bi:bis bm:bam bn:ben bo:bod br:bre bs:bos ca:cat ce:che
    ch:cha co:cos cr:cre cs:ces cu:chu cv:chv cy:cym da:dan de:deu dv:div dz:dzo ee:ewe
    el:ell en:eng eo:epo es:spa et:est eu:eus fa:fas ff:ful fi:fin fj:fij fo:fao fr:fra
    fy:fry ga:gle gd:gla gl:glg gn:grn gu:guj gv:glv ha:hau he:heb hi:hin ho:hmo hr:hrv
    ht:hat hu:hun hy:hye hz:her ia:ina id:ind ie:ile ig:ibo ii:iii ik:ipk io:ido is:isl
    it:ita iu:iku ja:jpn jv:jav ka:kat kg:kon ki:kik kj:kua kk:kaz kl:kal km:khm kn:kan
    ko:kor kr:kau ks:kas ku:kur kv:kom kw:cor ky:kir la:lat lb:ltz lg:lug li:lim ln:lin
    lo:lao lt:lit lu:lub lv:lav mg:mlg mh:mah mi:mri mk:mkd ml:mal mn:mon mr:mar ms:msa
    mt:mlt my:mya na:nau nb:nob nd:nde ne:nep ng:ndo nl:nld nn:nno no:nor nr:nbl nv:nav
    ny:nya oc:oci oj:oji om:orm or:ori os:oss pa:pan pi:pli pl:pol ps:pus pt:por qu:que
    rm:roh rn:run ro:ron ru:rus rw:kin sa:san sc:srd sd:snd se:sme sg:sag si:sin sk:slk
    sl:slv sm:smo sn:sna so:som sq:sqi sr:srp ss:ssw st:sot su:sun sv:swe sw:swa ta:tam
    te:tel tg:tgk th:tha ti:tir tk:tuk tl:tgl tn:tsn to:ton tr:tur ts:tso tt:tat tw:twi
    ty:tah ug:uig uk:ukr ur:urd uz:uzb ve:ven vi:vie vo:vol wa:wln wo:wol xh:xho yi:yid
    yo:yor za:zha zh:zho zu:zul
    """.split()
)

_THREE_TO_TWO = {three: two for two, three in _TWO_TO_THREE.items()}

# Codes that say "no particular language", which are better left out.
_NO_LANGUAGE = {"und", "mul", "zxx", "mis"}


def iso639_2(tag: str | None) -> str | None:
    """'en-GB' -> 'eng'. None when the tag is missing or names no known language."""
    if not tag:
        return None
    primary = tag.strip().split("-")[0].split("_")[0].lower()
    if len(primary) == 2:
        return _TWO_TO_THREE.get(primary)
    if len(primary) == 3 and primary.isascii() and primary.isalpha():
        return None if primary in _NO_LANGUAGE else primary
    return None


def two_letter(code: str) -> str:
    """'eng' -> 'en': a language code as its two-letter ISO 639-1 code where it has one,
    lowercase; any other code as it is, lowercase."""
    code = code.lower()
    return _THREE_TO_TWO.get(code, code)
