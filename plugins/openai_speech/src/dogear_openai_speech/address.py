"""The service's address: checking `base_url`, and whether it is on this computer.

Only a server at localhost or a loopback address (127.0.0.1 and the rest of 127.0.0.0/8,
or [::1]) keeps the text on this computer. Anything else, a computer on the same network
included, is another computer, and a name that happens to lead back here counts as
another computer too: Dogear would rather warn when it needn't than not warn when it
should.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from dogear.errors import ConfigError

TABLE = "openai_speech"
LOCAL_NAMES = {"localhost"}


@dataclass(frozen=True)
class Address:
    base_url: str  # without a trailing slash
    host: str  # as the text's destination is named to the user, such as "api.openai.com"
    local: bool  # on this computer
    encrypted: bool  # https

    @property
    def speech_url(self) -> str:
        return f"{self.base_url}/audio/speech"


def parse_address(base_url: str) -> Address:
    """`base_url` checked, or a ConfigError that says what's wrong with it."""
    # An address with @ in it may hold a password, so it isn't repeated back.
    where = f"{TABLE}.base_url" + ("" if "@" in base_url else f" is '{base_url}'")
    example = 'such as "https://api.openai.com/v1" or "http://localhost:8880/v1"'
    try:
        parts = urlsplit(base_url.strip())
        port = parts.port  # a port that isn't a number raises here
    except ValueError:
        raise ConfigError(
            f"{where}, which isn't a web address.", hint=f"Use one {example}."
        ) from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(
            f"{where}, which isn't a web address starting with https:// or http://.",
            hint=f"Use one {example}.",
        )
    if parts.username is not None or parts.password is not None:
        raise ConfigError(  # not shown: it may be a password
            f"{TABLE}.base_url has a name or password in it.",
            hint=f"Take them out. Put the service's key in the environment variable that "
            f"{TABLE}.api_key_env names, never in a settings file.",
        )
    if parts.query or parts.fragment:
        raise ConfigError(
            f"{where}, which has a ? or # part.",
            hint="Use the address without it: Dogear adds /audio/speech to the end.",
        )
    host = parts.hostname.rstrip(".").lower()
    named = f"[{host}]" if ":" in host else host  # an IPv6 address, as a web address has it
    shown = named if port is None else f"{named}:{port}"
    base = parts._replace(path=parts.path.rstrip("/")).geturl()
    return Address(base, shown, is_this_computer(host), parts.scheme == "https")


def is_this_computer(host: str) -> bool:
    """Whether `host` (a name or an address, without brackets) is this computer."""
    if host in LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # a name: it could lead anywhere
