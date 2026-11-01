"""Shared test setup.

Tests that need an external program skip when it's missing on a developer machine.
CI lists the programs it installs in DOGEAR_REQUIRE_TOOLS (comma-separated); for those,
a missing program fails the run instead, so a broken runner can't quietly skip tests.
"""

import os
import shutil

import pytest
from engine_fakes import FakeEntryPoint, entry_point_for
from standalone_build import FakeBuild

from dogear.config import default_config
from dogear.voice import engines


def require_tool(program: str) -> str:
    path = shutil.which(program)
    if path:
        return path
    required = {t.strip() for t in os.environ.get("DOGEAR_REQUIRE_TOOLS", "").split(",")}
    if program in required:
        pytest.fail(f"{program} is required (DOGEAR_REQUIRE_TOOLS) but was not found on PATH")
    pytest.skip(f"{program} not installed")


@pytest.fixture(autouse=True)
def _no_settings_file_from_the_environment(monkeypatch):
    """A developer's own DOGEAR_CONFIG must not change what the tests see."""
    monkeypatch.delenv("DOGEAR_CONFIG", raising=False)


@pytest.fixture(autouse=True)
def _per_user_folders_in_a_temporary_home(monkeypatch, tmp_path_factory):
    """Every test gets its own empty home folder, so nothing it does (a CLI command run
    with default settings, say) can reach the real downloaded voice, speech cache, or web
    books. A stand-in spaCy model once leaked into the real models folder this way and
    broke every real `dogear make` until it was found."""
    home = tmp_path_factory.mktemp("home")
    for name in ("HOME", "USERPROFILE"):  # Path.home() on POSIX and on Windows
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))


@pytest.fixture(autouse=True)
def _only_dogears_own_voice_engines(monkeypatch):
    """The tests see Kokoro only, whatever plugins are installed where they run. A test
    that needs other engines installs stand-ins (see engine_fakes.py)."""
    found = list(engines._entry_points())
    own = [entry for entry in found if entry.dist and entry.dist.name == engines.OWN_PACKAGE]
    monkeypatch.setattr(engines, "_entry_points", lambda: own)
    engines.installed.cache_clear()
    yield
    engines.installed.cache_clear()


@pytest.fixture
def installed_engines(monkeypatch):
    """Call it with stand-in plugins (or entry points) to install them beside Kokoro."""
    own = list(engines._entry_points())

    def install(*items):
        entries = [i if isinstance(i, FakeEntryPoint) else entry_point_for(i) for i in items]
        monkeypatch.setattr(engines, "_entry_points", lambda: [*own, *entries])
        engines.installed.cache_clear()

    return install


@pytest.fixture
def config():
    return default_config()


@pytest.fixture
def standalone_build(tmp_path, monkeypatch) -> FakeBuild:
    """Dogear running as a standalone build, with an empty bundled folder to fill."""
    return FakeBuild(tmp_path / "standalone", monkeypatch)
