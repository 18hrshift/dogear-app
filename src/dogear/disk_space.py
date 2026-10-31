"""Whether a `dogear make` run fits on disk, checked before anything is spoken.

A long book takes gigabytes, and running out of space hours in wastes the hours. So the
run's needs are estimated first, from how long its audio will be (its words at the
voice's pace) and the rates in `[audio]`:

- The speech cache keeps every sentence as 16-bit mono WAV at the voice's sample rate
  (about 170 MB an hour at 24 kHz). Every sentence is counted as new, and its pauses too,
  so a run that carries on from an earlier one is asked for more than it will use.
- The run's work folder holds each section's audio twice, as spoken and with its loudness
  evened out, in the same WAV, and, for talking books, the MP3s they share.
- What is saved: the M4B at `m4b_bitrate`, the MP3 folder (see `mp3_ceiling`), and the
  EPUB and DAISY talking books at `talking_book_bitrate`.

The cache and the work folder are on the cache folder's disk, and what is saved on the
output folder's. Each disk's needs, raised by `output.space_margin_percent`, are compared
with its free space. When both are on one disk, they add up.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dogear.config import Config
from dogear.errors import ConfigError, DiskSpaceError
from dogear.sizes import describe_size
from dogear.voice import VoicePace

log = logging.getLogger(__name__)

WAV_BYTES_PER_SAMPLE = 2  # 16-bit mono, as the speech cache and the work folder keep it
TALKING_BOOKS = ("epub", "daisy")
_BITRATE = re.compile(r"(\d+(?:\.\d+)?)([kKM]?)")
_SCALE = {"": 1, "k": 1_000, "K": 1_000, "M": 1_000_000}

Kind = Literal["cache", "output"]
WHAT = {"cache": "the speech cache and the work folder", "output": "the book"}


@dataclass(frozen=True)
class Need:
    """Space a run needs in one folder."""

    kind: Kind
    folder: Path
    size: int  # bytes


@dataclass(frozen=True)
class Disk:
    id: int  # the same for every folder on the disk
    total: int  # bytes
    free: int  # bytes this user can write


def space_needed(
    words: int, formats: list[str], cache: Path, output: Path, pace: VoicePace, config: Config
) -> list[Need]:
    """The space a run that says `words` words and makes `formats` needs: in the cache
    folder, and in the output folder."""
    seconds = words / pace.words_per_minute * 60
    audio = config["audio"]
    wav = pace.sample_rate * WAV_BYTES_PER_SAMPLE * seconds
    talking = bits_per_second(audio, "talking_book_bitrate")
    rates = {
        "m4b": bits_per_second(audio, "m4b_bitrate"),
        "mp3": mp3_ceiling(pace.sample_rate),
        "epub": talking,
        "daisy": talking,
    }
    # The speech cache, the work folder's two copies, and the talking books' MP3s.
    work = 3 * wav + (_bytes(talking, seconds) if set(formats) & set(TALKING_BOOKS) else 0)
    saved = sum(_bytes(rates[f], seconds) for f in formats)
    return [Need("cache", cache, round(work)), Need("output", output, round(saved))]


def check_space(needs: list[Need], config: Config) -> None:
    """Raise DiskSpaceError if a disk has less free space than is needed on it, with the
    margin."""
    margin = config["output"]["space_margin_percent"]
    if margin < 0:
        raise ConfigError(
            f"output.space_margin_percent is {margin}, but it can't be negative.",
            hint="Use 0 to check the estimate as it is, or a margin such as 20.",
        )
    disks: dict[int, tuple[Disk, list[Need]]] = {}
    for need in needs:
        disk = disk_of(need.folder)
        disks.setdefault(disk.id, (disk, []))[1].append(need)
    for disk, on_disk in disks.values():
        if disk.total == 0:
            # Some network and virtual disks report no size at all.
            log.info("Can't tell how much space is free in %s.", on_disk[0].folder)
            continue
        wanted = round(sum(n.size for n in on_disk) * (1 + margin / 100))
        if wanted > disk.free:
            raise _too_small(on_disk, wanted, disk.free)


def disk_of(folder: Path) -> Disk:
    """The disk `folder` is on, or will be on once it's made."""
    existing = _existing(folder)
    usage = shutil.disk_usage(existing)
    return Disk(os.stat(existing).st_dev, usage.total, usage.free)


def bits_per_second(audio: dict[str, Any], name: str) -> float:
    """An `[audio]` bitrate, written as ffmpeg takes it, such as "128k", in bits a second."""
    value = audio[name]
    match = _BITRATE.fullmatch(value.strip())
    if not match or float(match[1]) <= 0:
        raise ConfigError(
            f"audio.{name} is '{value}', which isn't a bitrate.",
            hint='Write it the way ffmpeg takes it, in kilobits a second, such as "64k".',
        )
    return float(match[1]) * _SCALE[match[2]]


def mp3_ceiling(sample_rate: int) -> int:
    """The MP3 folder's estimate, in bits a second. Variable-bitrate MP3 has no fixed rate,
    so it is the most an MP3 can hold at the voice's sample rate: 320 kbps from 32 kHz up
    (MPEG-1), 160 below (MPEG-2 and 2.5). That is conservative: at the default quality,
    the voice's 24 kHz speech comes to about 60 kbps."""
    return 320_000 if sample_rate >= 32_000 else 160_000


def _too_small(on_disk: list[Need], wanted: int, available: int) -> DiskSpaceError:
    uses = " and ".join(f"{WHAT[n.kind]} (in {n.folder})" for n in on_disk)
    kinds = {n.kind for n in on_disk}
    ways = ["Free up space on that disk, or make fewer sections or formats at a time."]
    if "output" in kinds:
        ways.append("Or save the book on another disk, with --output-dir.")
    if "cache" in kinds:
        ways.append(
            "Or keep the speech cache on another disk: set cache_dir under [render] in a "
            "settings file."
        )
    return DiskSpaceError(
        f"This book needs about {describe_size(wanted)} of free disk space, for {uses}, "
        f"but that disk has {describe_size(available)} free.",
        hint=" ".join(ways),
    )


def _bytes(bits_per_second: float, seconds: float) -> float:
    return bits_per_second / 8 * seconds


def _existing(folder: Path) -> Path:
    """`folder`, or the nearest folder above it that exists: it may not be made yet."""
    folder = folder.resolve()
    while not folder.exists():
        folder = folder.parent
    return folder
