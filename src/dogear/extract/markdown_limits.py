"""Bounds on the work of reading a Markdown file, so a small file can't fill the memory.

markdown-it-py keeps a few numbers for every line of a file, and a Python object for every
piece it reads the file into (a token: each paragraph, heading, list item, table cell,
stretch of text, line break, emphasis, link). Both can grow far past the file's size: a
blank line is one byte, and a table row is filled out to its header's width, so a 1.6 MB
table of 2,000 columns took over 1 GB. So the lines are counted before the file is
parsed, against [markdown] max_lines, and the tokens as markdown-it makes them, against
[markdown] max_pieces: the parse stops at the first one past it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from markdown_it.token import Token

from dogear.config import Config
from dogear.errors import ConfigError, ExtractionError

BUILT_TO_EXHAUST = "The file may be damaged or built to exhaust memory."


@dataclass(frozen=True)
class MarkdownLimits:
    max_lines: int
    max_pieces: int

    @classmethod
    def from_config(cls, config: Config) -> MarkdownLimits:
        table = config["markdown"]
        for name in ("max_lines", "max_pieces"):
            if table[name] <= 0:
                raise ConfigError(f"markdown.{name} must be more than 0, got {table[name]}.")
        return cls(max_lines=table["max_lines"], max_pieces=table["max_pieces"])


def check_lines(text: str, what: str, limits: MarkdownLimits) -> None:
    """Raise ExtractionError if `text`, the Markdown file `what`, has too many lines."""
    lines = text.count("\n") + 1
    if lines > limits.max_lines:
        raise ExtractionError(
            f"{what} has {lines:,} lines, more than the {limits.max_lines:,} Dogear reads "
            "in a Markdown file.",
            hint=f"{BUILT_TO_EXHAUST} If it's a genuine book, raise markdown.max_lines in "
            "your config file.",
        )


def count_pieces(md: MarkdownIt, what: str, limits: MarkdownLimits) -> None:
    """Make `md` stop with an ExtractionError once a parse has made more than
    limits.max_pieces tokens, block and inline together. Use a new MarkdownIt for each
    file: the count is kept with it."""
    budget = _Budget(what, limits.max_pieces)

    def count_blocks(state: StateCore) -> None:
        state.tokens = _Counted(budget, state.tokens)

    def count_inline(state: StateCore) -> None:
        # Each inline token's children are filled by the "inline" rule, which follows.
        for token in state.tokens:
            if token.type == "inline":
                token.children = _Counted(budget, token.children or ())

    md.core.ruler.before("block", "dogear_count_blocks", count_blocks)
    md.core.ruler.after("block", "dogear_count_inline", count_inline)


class _Budget:
    def __init__(self, what: str, most: int) -> None:
        self.what = what
        self.most = most
        self.made = 0

    def spend(self, count: int = 1) -> None:
        self.made += count
        if self.made > self.most:
            raise ExtractionError(
                f"{self.what} reads into more than {self.most:,} pieces (paragraphs, table "
                "cells, stretches of text, and the like), more than Dogear reads in a "
                "Markdown file.",
                hint=f"{BUILT_TO_EXHAUST} A table's rows are filled out to its header's "
                "width, so a very wide header makes many cells. If it's a genuine book, "
                "raise markdown.max_pieces in your config file.",
            )


class _Counted(list[Token]):
    """A token list that counts each token added to it against the budget. markdown-it's
    block and inline parsers add every token they make with append."""

    def __init__(self, budget: _Budget, tokens: Iterable[Token]) -> None:
        super().__init__(tokens)
        self._budget = budget
        budget.spend(len(self))

    def append(self, token: Token) -> None:
        self._budget.spend()
        super().append(token)

    def extend(self, tokens: Iterable[Token]) -> None:
        added = list(tokens)
        self._budget.spend(len(added))
        super().extend(added)
