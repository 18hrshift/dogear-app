"""Freeze Dogear with PyInstaller into a folder ("onedir"): the `dogear` program and the
Python it runs on.

A folder, not one file: a one-file build unpacks itself to a temporary folder on every
start, which for Dogear's ~500 MB would take seconds each time, and a child process
started by `dogear serve` would unpack it again.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dogear_build import BuildError
from dogear_build.fetch import Report

ENTRY = Path(__file__).resolve().parent.parent / "entry.py"
NAME = "dogear"
# PyInstaller's folder for everything but the program; sys._MEIPASS points here when
# frozen, and dogear.bundle looks for its folder inside it.
CONTENTS = "_internal"

# Packages PyInstaller can't follow by reading imports.
# Dogear: defaults.toml, catalog.toml, epub3.css. misaki: its dictionaries. py3langid: its
# language model, for [language_detection].
COLLECT_DATA = ["dogear", "misaki", "py3langid"]
COLLECT_SUBMODULES = ["dogear"]
# Package metadata to carry. Dogear finds its voice engines through entry points, which
# live in a package's metadata, so Dogear's own is needed for Kokoro. Only Dogear's: a
# voice engine plugin installed where the build runs is never carried.
COPY_METADATA = ["dogear"]
# Never needed at run time, but pulled in by hooks that collect whole packages.
EXCLUDE = ["spacy.tests", "thinc.tests", "srsly.tests", "tkinter", "pytest"]


def freeze(work: Path, dist: Path, report: Report) -> Path:
    """Run PyInstaller. Returns the frozen folder, dist/dogear, holding `dogear` and CONTENTS."""
    report("Freezing Dogear with PyInstaller.")
    try:
        subprocess.run([sys.executable, "-m", "PyInstaller", *arguments(work, dist)], check=True)
    except subprocess.CalledProcessError as e:
        raise BuildError(f"PyInstaller failed (exit code {e.returncode}).") from None
    return dist / NAME


def arguments(work: Path, dist: Path) -> list[str]:
    args = [
        str(ENTRY),
        *("--name", NAME),
        "--onedir",
        "--console",  # dogear is a command; on Windows its window is how you stop serve
        "--noconfirm",
        "--clean",
        *("--contents-directory", CONTENTS),
        *("--distpath", str(dist)),
        *("--workpath", str(work / "pyinstaller-work")),
        *("--specpath", str(work)),
    ]
    # Not --strip, though it would save about 150 MB on Linux: the strip in the manylinux
    # container broke numpy's bundled OpenBLAS ("ELF load command address/offset not
    # page-aligned"), as stripping libraries patchelf has edited can.
    for package in COLLECT_DATA:
        args += ["--collect-data", package]
    for package in COLLECT_SUBMODULES:
        args += ["--collect-submodules", package]
    for package in COPY_METADATA:
        args += ["--copy-metadata", package]
    for module in EXCLUDE:
        args += ["--exclude-module", module]
    return args
