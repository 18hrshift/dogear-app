"""Writing a pronunciation file, in the format pronunciations.py reads.

Every word and every value is written as a TOML basic string ("..."), with quotes,
backslashes, and control characters escaped, so a word comes back exactly as it went in,
whatever it holds. Python's tomllib only reads TOML, so this writes the little that a
pronunciation file needs.
"""

from __future__ import annotations

from collections.abc import Iterable

from dogear.pronunciations import DEFAULT_LANGUAGES, Pronunciation

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def pronunciation_file_text(entries: Iterable[Pronunciation], comment: str = "") -> str:
    """A pronunciation file with these entries under [words], in the order given. Each
    line of `comment` goes at the top, after "# "."""
    lines = [f"# {line}".rstrip() for line in comment.splitlines()]
    lines.append("[words]")
    for entry in entries:
        if entry.phonemes is not None:
            value = f"{{ phonemes = {toml_string(entry.phonemes)} }}"
        elif entry.sounds_like is not None and entry.languages != DEFAULT_LANGUAGES:
            languages = ", ".join(toml_string(tag) for tag in entry.languages)
            value = (
                f"{{ sounds_like = {toml_string(entry.sounds_like)}, languages = [{languages}] }}"
            )
        elif entry.sounds_like is not None:
            value = toml_string(entry.sounds_like)
        else:
            raise ValueError(f"the entry {entry.word!r} has neither a spelling nor phonemes")
        lines.append(f"{toml_string(entry.word)} = {value}")
    return "\n".join(lines) + "\n"


def toml_string(text: str) -> str:
    """`text` as a TOML basic string, in quotes. Raises ValueError for a lone surrogate,
    which TOML (being UTF-8) can't hold."""
    escaped = []
    for c in text:
        code = ord(c)
        if c in _ESCAPES:
            escaped.append(_ESCAPES[c])
        elif code < 0x20 or code == 0x7F:
            escaped.append(f"\\u{code:04X}")
        elif 0xD800 <= code <= 0xDFFF:
            raise ValueError(f"{text!r} has a lone surrogate, which TOML can't hold")
        else:
            escaped.append(c)
    return '"' + "".join(escaped) + '"'
