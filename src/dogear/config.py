"""Load settings: packaged defaults, overridden by an optional user TOML file.

`defaults.toml` is the only place Dogear's own default values live. Each installed voice
engine adds the defaults of its own table, named after it (Kokoro's [kokoro] comes from
`voice/kokoro.toml`), and they are checked the same way. Code reads settings from the
dict this module returns instead of keeping its own copies.
"""

from __future__ import annotations

import copy
import os
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from dogear.errors import ConfigError

# Tables whose keys are user-chosen (word lists), so any key is allowed, and what each
# value must be: text, or a pair of texts for singular and plural ("mile", "miles").
WordTableValues = Literal["text", "pair"]
FREEFORM_TABLES: dict[tuple[str, str], WordTableValues] = {
    ("speech", "abbreviations"): "text",
    ("speech", "abbreviations_before_name"): "text",
    ("speech", "abbreviations_before_number"): "text",
    ("speech", "time_suffixes"): "text",
    ("speech", "currency"): "pair",
    ("speech", "currency_cents"): "pair",
    ("speech", "currency_scales"): "text",
    ("speech", "units"): "pair",
    # Language tags, such as "fr", to voices, such as "piper:fr_FR-mls-medium". Which
    # engines and voices are installed is checked by `dogear.voice.languages`.
    ("voice", "languages"): "text",
}
# Tables of words that hold one table per language as well, such as [narration.words.fr]:
# a key that is a language tag may be added, holding some of the table's own words.
LANGUAGE_WORD_TABLES: set[tuple[str, ...]] = {("narration", "words")}
# What each item must be in lists that are empty by default, where the default can't show it.
EMPTY_LIST_ITEMS: dict[tuple[str, ...], type] = {
    ("pronunciation", "files"): str,
}

Config = dict[str, Any]

# Environment variable naming a settings file to use when --config isn't given.
CONFIG_ENV = "DOGEAR_CONFIG"


def core_defaults() -> Config:
    """Dogear's own defaults, from defaults.toml, without any voice engine's."""
    text = resources.files("dogear").joinpath("defaults.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)


def default_config() -> Config:
    """Every default: Dogear's own, and each installed voice engine's table."""
    # Imported here: the engines module reads core_defaults, and loads the plugins.
    from dogear.voice.engines import engine_settings

    config = core_defaults()
    for name, settings in engine_settings().items():
        config[name] = copy.deepcopy(settings)
    return config


def load_config(path: Path | None = None) -> Config:
    """Defaults, with `path` merged on top after validation.

    With no `path`, the file named by the DOGEAR_CONFIG environment variable is used, if
    set: one settings file for every command, as a Docker image or a shortcut needs.
    """
    config = default_config()
    from_env = path is None and bool(os.environ.get(CONFIG_ENV))
    if from_env:
        path = Path(os.environ[CONFIG_ENV])
    if path is None:
        return config
    try:
        user = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            f"Config file not found: {path}",
            hint=f"The {CONFIG_ENV} environment variable names it. Fix the path or unset it."
            if from_env
            else "",
        ) from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(
            f"Config file {path} is not valid TOML: {e}",
            hint="Check the line it names. Strings need quotes; tables look like [pdf].",
        ) from None
    _check_engine_tables(config, user, path)
    merged = _merge(config, user, ())
    # Imported here: both modules read this one, and the voices check reads the
    # installed engines.
    from dogear.language_detection import check_detection_settings
    from dogear.voice.language_voices import check_language_voices

    check_language_voices(merged)
    check_detection_settings(merged)
    return merged


def _check_engine_tables(config: Config, user: Config, path: Path) -> None:
    """A table for a voice engine that is installed but couldn't be loaded gets its own
    message: "Unknown setting" would send the reader looking for a typing mistake."""
    from dogear.voice.engines import installed

    for key in user:
        broken = None if key in config else installed().broken_named(key)
        if broken is not None:
            raise ConfigError(
                f"{path} has settings for the voice engine '{key}' ({broken.package}), which "
                f"is installed but couldn't be loaded. {broken.problem}",
                hint=f"Reinstall it, or take the [{key}] table out of the file.",
            )


def _merge(base: Config, override: Config, where: tuple[str, ...]) -> Config:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        here = (*where, key)
        name = ".".join(here)
        if key not in base and where in LANGUAGE_WORD_TABLES and isinstance(value, dict):
            merged[key] = _language_words(name, base, value)
            continue
        if key not in base:
            known = ", ".join(sorted(base)) or "(none)"
            raise ConfigError(f"Unknown setting '{name}'.", hint=f"Known keys here: {known}.")
        default = base[key]
        if isinstance(default, dict):
            if not isinstance(value, dict):
                raise ConfigError(f"Setting '{name}' must be a table like [{name}].")
            if here in FREEFORM_TABLES:
                _check_word_table(name, value, FREEFORM_TABLES[here])
                merged[key] = {**default, **value}
            else:
                merged[key] = _merge(default, value, here)
        else:
            _check_type(here, default, value)
            merged[key] = value
    return merged


def _language_words(name: str, base: Config, table: dict[str, Any]) -> dict[str, str]:
    """A new language's table of words, such as [narration.words.it], checked against the
    table it sits in. A word it leaves out is the base table's own."""
    # Imported here: the module is small, but config.py is loaded by everything.
    from dogear.output.language_tag import language_tag

    language = name.rpartition(".")[2]
    if language_tag(language) != language:
        raise ConfigError(
            f"Unknown setting '{name}'.",
            hint="A table here is named after a language, as a BCP 47 tag such as it or pt-BR.",
        )
    words = {k: v for k, v in base.items() if isinstance(v, str)}
    for key, value in table.items():
        if key not in words:
            raise ConfigError(
                f"Unknown setting '{name}.{key}'.",
                hint=f"Known keys here: {', '.join(sorted(words))}.",
            )
        if not isinstance(value, str):
            raise ConfigError(f"'{name}.{key}' must be text in quotes, got {value!r}.")
    return {**words, **table}


def _check_word_table(name: str, table: dict[str, Any], values: WordTableValues) -> None:
    for k, v in table.items():
        if not k:
            raise ConfigError(f"'{name}' has an empty key (\"\"). Remove it or name a word.")
        if values == "text" and not isinstance(v, str):
            raise ConfigError(f"'{name}.{k}' must be text in quotes, got {v!r}.")
        if values == "pair" and not _is_pair(v):
            raise ConfigError(
                f"'{name}.{k}' must be two texts in brackets, singular then plural, got {v!r}.",
                hint='For example: "km" = ["kilometer", "kilometers"]',
            )


def _is_pair(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(isinstance(v, str) for v in value)


def _check_type(where: tuple[str, ...], default: Any, value: Any) -> None:
    name = ".".join(where)
    expected = type(default)
    if expected is float:
        ok = isinstance(value, int | float) and not isinstance(value, bool)
    elif expected is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, expected)
    if not ok:
        raise ConfigError(
            f"Setting '{name}' must be {expected.__name__}, got {type(value).__name__} ({value!r})."
        )
    if expected is list:
        item = type(default[0]) if default else EMPTY_LIST_ITEMS.get(where)
        if item and not all(isinstance(v, item) for v in value):
            raise ConfigError(f"Every item in '{name}' must be {item.__name__}.")
