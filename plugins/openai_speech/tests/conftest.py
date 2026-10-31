"""Shared test setup for the speech service plugin. The tests run with the plugin installed
beside Dogear, so Dogear finds it through its entry point, as it would for a user. Every
service here is a fake one on this computer (fake_service.py): no test needs the network
or a key."""

import pytest
from dogear.config import default_config
from dogear.voice import engines
from fake_service import FakeService, running

PROXY_VARIABLES = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")


@pytest.fixture(autouse=True)
def _fresh_engine_list():
    engines.installed.cache_clear()
    yield
    engines.installed.cache_clear()


@pytest.fixture(autouse=True)
def _a_clean_environment(monkeypatch, tmp_path_factory):
    """Nothing a test does can reach the real voices, speech cache, or settings, and no
    key or proxy from the environment changes what it sees."""
    monkeypatch.delenv("DOGEAR_CONFIG", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for name in PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)
    home = tmp_path_factory.mktemp("home")
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))


@pytest.fixture
def service():
    """A fake speech service on this computer; `service.url` is its base_url."""
    fake = FakeService()
    with running(fake) as address:
        fake.url = f"{address}/v1"
        yield fake


@pytest.fixture
def config(service):
    """Dogear's settings, with the engine pointed at the fake service."""
    config = default_config()
    config["openai_speech"]["base_url"] = service.url
    return config


@pytest.fixture
def far_away(service, monkeypatch):
    """The fake service as if it were another computer: http://speech.invalid/v1, which
    reaches it as a web proxy. (.invalid names never lead anywhere real, so nothing could
    leave this computer even without the proxy.)"""
    monkeypatch.setenv("http_proxy", service.url.removesuffix("/v1"))
    return "http://speech.invalid/v1"
