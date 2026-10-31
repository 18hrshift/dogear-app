"""Sizes in words, for messages and pages: '540 KB', '116 MB', '1.2 GB'."""

from __future__ import annotations


def describe_size(size: int) -> str:
    """'540 KB', '116 MB', '1.2 GB'."""
    for unit, scale in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size >= scale:
            amount = size / scale
            text = f"{amount:.1f}" if amount < 10 else f"{amount:,.0f}"
            return f"{text.removesuffix('.0')} {unit}"
    return f"{size} bytes" if size != 1 else "1 byte"
