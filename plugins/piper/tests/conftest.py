"""Shared test setup for the Piper plugin. The tests run with the plugin installed beside
Dogear, so Dogear finds both Kokoro and Piper through their entry points, as it would
for a user. Onnxruntime is replaced in every test but the real-voice one."""

from pathlib import Path

import pytest
from dogear.config import default_config
from dogear.voice import engines
from piper_fakes import FakeSession


@pytest.fixture(autouse=True)
def _fresh_engine_list():
    engines.installed.cache_clear()
    yield
    engines.installed.cache_clear()


@pytest.fixture(autouse=True)
def _per_user_folders_in_a_temporary_home(monkeypatch, tmp_path_factory):
    """Nothing a test does can reach the real downloaded voices or speech cache."""
    monkeypatch.delenv("DOGEAR_CONFIG", raising=False)
    home = tmp_path_factory.mktemp("home")
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))


@pytest.fixture
def config():
    return default_config()


@pytest.fixture
def models(tmp_path, config) -> Path:
    """Points models.dir at an empty folder and returns it."""
    folder = tmp_path / "models"
    config["models"]["dir"] = str(folder)
    return folder


@pytest.fixture
def fake_session(monkeypatch) -> type[FakeSession]:
    import onnxruntime

    FakeSession.instances = []
    monkeypatch.setattr(onnxruntime, "InferenceSession", FakeSession)
    return FakeSession
