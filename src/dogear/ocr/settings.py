"""The [ocr] settings, checked: how pages are drawn for Tesseract, and in which language."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dogear.config import Config
from dogear.errors import ConfigError

# One or more Tesseract language codes joined by "+", such as "eng", "eng+fra", "chi_sim",
# or a script such as "script/Latin".
_LANGUAGES = re.compile(r"[A-Za-z0-9_/-]+(?:\+[A-Za-z0-9_/-]+)*")
MIN_DPI = 70  # Tesseract's own lowest resolution; a page that needs less isn't read
MAX_SIDE_PIXELS = 32767  # Tesseract reads no picture wider or taller than this


@dataclass(frozen=True)
class OcrSettings:
    language: str  # as Tesseract takes it: "eng+fra"
    dpi: int
    max_page_pixels: int  # a page drawn bigger than this is drawn at a lower resolution
    max_page_side: int  # and so is one drawn wider or taller than this many pixels

    @classmethod
    def from_config(cls, config: Config) -> OcrSettings:
        table = config["ocr"]
        language, dpi, megapixels = table["language"], table["dpi"], table["max_page_megapixels"]
        side = table["max_page_side_pixels"]
        if not _LANGUAGES.fullmatch(language):
            raise ConfigError(
                f"Setting 'ocr.language' must be Tesseract language codes, got {language!r}.",
                hint='For example: language = "eng", or "eng+fra" for English and French.',
            )
        if dpi < MIN_DPI:
            raise ConfigError(f"Setting 'ocr.dpi' must be at least {MIN_DPI}, got {dpi}.")
        if megapixels <= 0:
            raise ConfigError(
                f"Setting 'ocr.max_page_megapixels' must be more than 0, got {megapixels}."
            )
        if not 1 <= side <= MAX_SIDE_PIXELS:
            raise ConfigError(
                f"Setting 'ocr.max_page_side_pixels' must be from 1 to {MAX_SIDE_PIXELS:,}, "
                f"got {side}.",
                hint=f"Tesseract reads no picture wider or taller than {MAX_SIDE_PIXELS:,} pixels.",
            )
        return cls(
            language=language,
            dpi=dpi,
            max_page_pixels=megapixels * 1_000_000,
            max_page_side=side,
        )

    @property
    def languages(self) -> list[str]:
        return self.language.split("+")
