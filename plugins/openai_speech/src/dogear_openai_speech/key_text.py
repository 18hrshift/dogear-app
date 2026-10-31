"""Keeping the service's key out of everything Dogear says.

A key is checked when it is read from the environment (`check_key`): one with a line
break, another control character, a space, or a character outside Latin-1 can't go in a
request header, and Python's own error for it repeats the header, key and all. As well,
every message the client builds passes through `hide_key`, which takes out the key, each
part of it, and the ways Python writes it out, so nothing that slips past the check can
show it either.
"""

from __future__ import annotations

import re
import unicodedata

from dogear.errors import VoiceError

from dogear_openai_speech.address import TABLE

HIDDEN = "(your key)"
SHORTEST_PART = 4  # parts of a key shorter than this aren't hidden: they'd match too much
_BREAKS = re.compile(r"[\s\x00-\x1f\x7f-\x9f]+")


def check_key(key: str, variable: str) -> None:
    """Raise VoiceError if `key`, from the environment variable `variable`, can't be sent
    in a request header. The message names the variable and the kind of character, never
    the key."""
    problem = _problem(key)
    if problem is None:
        return
    raise VoiceError(
        f"The key in the environment variable {variable} has {problem}, which a key can't "
        "have, so Dogear can't send it.",
        hint=f"Set {variable} again to the key alone, copied from the service, on one line. "
        "If it holds more than one key, keep only the one for this service. To use a key "
        f"from another variable, name it with api_key_env under [{TABLE}].",
    )


def _problem(key: str) -> str | None:
    for character in key:
        if character in "\r\n":
            return "a line break in it"
        if unicodedata.category(character) == "Cc":
            return "a control character in it"
        if character.isspace():
            return "a space in it"
        if ord(character) > 0xFF:
            return "a character outside Latin-1 in it, such as a curly quote"
    return None


def hide_key(text: str, key: str) -> str:
    """`text` with the key, every part of it between spaces or control characters, and
    the way Python's error messages write it out, each replaced by "(your key)"."""
    if not key or not text:
        return text
    forms = {key, repr(key)[1:-1], *_byte_forms(key)}
    forms.update(part for part in _BREAKS.split(key) if len(part) >= SHORTEST_PART)
    for form in sorted(forms, key=len, reverse=True):
        if form:
            text = text.replace(form, HIDDEN)
    return text


def _byte_forms(key: str) -> list[str]:
    """The key as Python writes it out as bytes, b'...', without the b'' around it, in
    the encodings a header may use."""
    forms = []
    for encoding in ("latin-1", "utf-8"):
        try:
            forms.append(repr(key.encode(encoding))[2:-1])
        except UnicodeEncodeError:
            continue
    return forms
