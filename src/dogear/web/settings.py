"""The web page's settings: `[web]` in the config, with this run's command-line options."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dogear.config import Config
from dogear.errors import ConfigError
from dogear.paths import web_dir

LOCAL_HOST = "127.0.0.1"
LAN_HOST = "0.0.0.0"  # every network interface


@dataclass(frozen=True)
class WebSettings:
    dir: Path  # books, what's made from them, and the access key
    port: int
    lan: bool  # listen on every interface, for phones on the same network
    show_network_address: bool  # with lan, a link with the address this computer finds
    max_upload_bytes: int
    read_timeout_seconds: float
    config_file: Path | None  # passed on to every child, so books use the same settings
    word_limits: WordLimits
    words_listed: int  # the most words a book's word check keeps and lists

    @property
    def listen_host(self) -> str:
        return LAN_HOST if self.lan else LOCAL_HOST

    @property
    def books_dir(self) -> Path:
        return self.dir / "books"

    @property
    def incoming_dir(self) -> Path:
        """Where an upload is written until it is complete."""
        return self.dir / "incoming"

    @property
    def words_file(self) -> Path:
        """The page's own pronunciation file (page_words.py)."""
        return self.dir / "words.toml"

    @property
    def listen_dir(self) -> Path:
        """Recordings made for Listen (listening.py)."""
        return self.dir / "listen"


@dataclass(frozen=True)
class WordLimits:
    """How much the page's own pronunciation file holds."""

    entries: int
    word_chars: int
    spelling_chars: int


def web_settings(
    config: Config,
    *,
    port: int | None = None,
    lan: bool = False,
    config_file: Path | None = None,
) -> WebSettings:
    web = config["web"]
    chosen_port = web["port"] if port is None else port
    if not 1 <= chosen_port <= 65535:
        raise ConfigError(
            f"The port must be a number from 1 to 65535, got {chosen_port}.",
            hint="8765 is the usual one. Choose another with --port, or set web.port.",
        )
    if web["max_upload_mb"] <= 0:
        raise ConfigError(f"web.max_upload_mb must be more than 0, got {web['max_upload_mb']}.")
    for name in (
        "read_timeout_seconds",
        "max_pronunciations",
        "max_word_chars",
        "max_spelling_chars",
        "max_words_listed",
    ):
        if web[name] <= 0:
            raise ConfigError(f"web.{name} must be more than 0, got {web[name]}.")
    return WebSettings(
        dir=web_dir(config).resolve(),
        port=chosen_port,
        lan=lan,
        show_network_address=web["show_network_address"],
        max_upload_bytes=web["max_upload_mb"] * 1024 * 1024,
        read_timeout_seconds=float(web["read_timeout_seconds"]),
        config_file=config_file.resolve() if config_file is not None else None,
        word_limits=WordLimits(
            entries=web["max_pronunciations"],
            word_chars=web["max_word_chars"],
            spelling_chars=web["max_spelling_chars"],
        ),
        words_listed=web["max_words_listed"],
    )
