"""The [openai_speech] settings, read from Dogear's loaded config and checked.

Dogear has already checked each setting's type against settings.toml; this checks what
the values mean, with a message and a hint for each mistake. The service's key is read
from the environment variable `api_key_env` names, only when a voice is made, and is never
kept in the settings, a cache key, or a message.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from dogear.config import Config
from dogear.errors import ConfigError, VoiceError
from dogear.output.language_tag import language_tag
from dogear.voice.engines import Voice

from dogear_openai_speech.address import TABLE, Address, parse_address
from dogear_openai_speech.key_text import check_key

FORMATS = ("wav", "pcm")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Settings:
    address: Address
    model: str
    voices: tuple[str, ...]
    language: str
    language_name: str
    api_key_env: str  # "" for no key
    instructions: str
    response_format: str
    sample_rate: int
    max_input_chars: int
    words_per_minute: float
    timeout_seconds: float
    retries: int
    retry_wait_seconds: float
    requests_per_minute: int

    def voice_list(self) -> list[Voice]:
        return [Voice(name, self.language, self.language_name) for name in self.voices]


def read_settings(config: Config) -> Settings:
    """The engine's settings, checked: a ConfigError says what's wrong and what to do."""
    table: dict[str, Any] = config[TABLE]
    settings = Settings(
        address=parse_address(table["base_url"]),
        model=_text(table, "model", 'such as "gpt-4o-mini-tts"'),
        voices=_voices(table["voices"]),
        language=_language(table["language"]),
        language_name=_text(table, "language_name", 'such as "American English"'),
        api_key_env=_env_name(table["api_key_env"]),
        instructions=table["instructions"].strip(),
        response_format=_one_of(table, "response_format", FORMATS),
        sample_rate=_whole(table, "sample_rate", 8000, 192_000),
        max_input_chars=_whole(table, "max_input_chars", 1, 1_000_000),
        words_per_minute=_more_than_zero(table, "words_per_minute"),
        timeout_seconds=_more_than_zero(table, "timeout_seconds"),
        retries=_whole(table, "retries", 0, 10),
        retry_wait_seconds=_whole(table, "retry_wait_seconds", 0, 600),
        requests_per_minute=_whole(table, "requests_per_minute", 0, 100_000),
    )
    if settings.api_key_env and not settings.address.local and not settings.address.encrypted:
        raise ConfigError(
            f"{TABLE}.base_url starts with http://, so the key in {settings.api_key_env} "
            f"would cross the network to {settings.address.host} unencrypted.",
            hint="Use the service's https:// address. For a server on another computer of "
            f'your own that needs no key, set api_key_env = "" under [{TABLE}].',
        )
    return settings


def api_key(settings: Settings) -> str:
    """The key from the environment, or "" for none, checked to be one a request can carry
    (key_text.check_key). A service on another computer with `api_key_env` set must have
    one: a VoiceError says how to give it."""
    if not settings.api_key_env:
        return ""
    key = os.environ.get(settings.api_key_env, "").strip()
    if key:
        check_key(key, settings.api_key_env)
        return key
    if settings.address.local:
        return key  # a server on this computer usually takes none
    raise VoiceError(
        f"The environment variable {settings.api_key_env} has no key in it, so Dogear can't "
        f"use the service at {settings.address.host}.",
        hint=f"Set {settings.api_key_env} to your key for the service before running Dogear "
        f"(the plugin's README shows how). If the key is in another variable, name it with "
        f'api_key_env under [{TABLE}]. For a service that takes no key, set api_key_env = "".',
    )


def _text(table: dict[str, Any], name: str, example: str) -> str:
    value = table[name].strip()
    if not value:
        raise ConfigError(f"{TABLE}.{name} is empty.", hint=f"Give it a value, {example}.")
    return value


def _voices(names: list[str]) -> tuple[str, ...]:
    if not names:
        raise ConfigError(
            f"{TABLE}.voices is empty, so there is no voice to use.",
            hint='List the service\'s voices by their names, such as voices = ["alloy"].',
        )
    for name in names:
        if not name or name != name.strip() or ":" in name or " " in name:
            raise ConfigError(
                f"{TABLE}.voices has '{name}', and a voice's name needs no spaces or colons.",
                hint="Write each voice the way the service names it.",
            )
    if len(set(names)) != len(names):
        raise ConfigError(f"{TABLE}.voices lists a voice twice.", hint="List each voice once.")
    return tuple(names)


def _language(tag: str) -> str:
    checked = language_tag(tag)
    if checked != tag:
        raise ConfigError(
            f"{TABLE}.language is '{tag}', which isn't a language tag.",
            hint='Use a BCP 47 tag, such as "en-US" or "en-GB".',
        )
    return tag


def _env_name(name: str) -> str:
    if name and not _ENV_NAME.fullmatch(name):
        raise ConfigError(
            f"{TABLE}.api_key_env is '{name}', which isn't the name of an environment variable.",
            hint='Give the variable\'s name, such as "OPENAI_API_KEY", never the key itself.',
        )
    return name


def _one_of(table: dict[str, Any], name: str, allowed: tuple[str, ...]) -> str:
    value = table[name]
    if value not in allowed:
        raise ConfigError(
            f"{TABLE}.{name} is '{value}'.", hint=f"Use one of: {', '.join(allowed)}."
        )
    return value


def _whole(table: dict[str, Any], name: str, least: int, most: int) -> Any:
    value = table[name]
    if not least <= value <= most:
        raise ConfigError(
            f"{TABLE}.{name} is {value}.", hint=f"Use a number from {least:,} to {most:,}."
        )
    return value


def _more_than_zero(table: dict[str, Any], name: str) -> float:
    value = table[name]
    if value <= 0:
        raise ConfigError(f"{TABLE}.{name} is {value}, but it must be more than 0.")
    return float(value)
