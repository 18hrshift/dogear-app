"""The installer build's steps that run anywhere: programs, freezing, the bundled voice
(read back by Dogear as a frozen build would), and finding Inno Setup."""

import json
import os
import zipfile
from dataclasses import replace
from types import SimpleNamespace

import pytest
from dogear_build import BuildError, freeze, programs, voice, windows
from dogear_build.pins import load

from dogear import bundle
from dogear.voice import downloads as downloads_module
from dogear.voice.catalog import catalog
from dogear.voice.kokoro_plugin import prepare_kokoro, prepare_spacy

# Programs.


def test_program_names_per_platform():
    assert programs.program_names("linux-x86_64") == ["ffmpeg", "ffprobe", "espeak-ng"]
    assert programs.program_names("windows-x86_64") == [
        "ffmpeg.exe",
        "ffprobe.exe",
        "espeak-ng.exe",
    ]


def test_a_new_pin_means_building_again():
    pins = load()
    before = programs._fingerprint(pins, {})
    pins.sources["ffmpeg"] = replace(pins.sources["ffmpeg"], sha256="0" * 64)
    assert programs._fingerprint(pins, {}) != before
    assert programs._fingerprint(load(), {"minimum_os": "15.0"}) != before


def test_the_macos_minimum_comes_from_the_pins():
    settings = load().platform("macos-arm64")
    assert programs._environment(settings)["MACOSX_DEPLOYMENT_TARGET"] == settings["minimum_os"]
    other = programs._environment(load().platform("linux-x86_64"))
    assert other.get("MACOSX_DEPLOYMENT_TARGET") == os.environ.get("MACOSX_DEPLOYMENT_TARGET")


def test_windows_needs_msys2(monkeypatch, tmp_path):
    # An empty DOGEAR_MSYS2 (an unset CI output, say) means the usual folder.
    monkeypatch.setattr(programs, "os", SimpleNamespace(name="nt", environ={"DOGEAR_MSYS2": ""}))
    monkeypatch.setattr(programs, "MSYS2_DEFAULT", str(tmp_path / "no-msys64"))
    with pytest.raises(BuildError, match="MSYS2 wasn't found") as err:
        programs._bash()
    assert str(tmp_path / "no-msys64") in str(err.value)


# Freezing.


def test_pyinstaller_arguments(tmp_path, monkeypatch):
    args = freeze.arguments(tmp_path / "work", tmp_path / "dist")
    assert args[0] == str(freeze.ENTRY)
    assert args[args.index("--contents-directory") + 1] == freeze.CONTENTS
    assert args[args.index("--distpath") + 1] == str(tmp_path / "dist")
    assert "--onedir" in args and "--console" in args
    assert "--strip" not in args  # it broke numpy's OpenBLAS in the Linux container
    collected = [args[i + 1] for i, a in enumerate(args) if a == "--collect-data"]
    assert collected == freeze.COLLECT_DATA


def test_only_dogears_own_packages_and_metadata_are_frozen(tmp_path):
    """Kokoro is found through Dogear's entry points, so Dogear's metadata goes in. A
    voice engine plugin installed in the build's environment never does."""
    args = freeze.arguments(tmp_path / "work", tmp_path / "dist")

    def given(option):
        return [args[i + 1] for i, a in enumerate(args) if a == option]

    assert given("--copy-metadata") == ["dogear"]
    assert given("--collect-submodules") == ["dogear"]
    assert not any(a.startswith(("--collect-all", "--recursive-copy-metadata")) for a in args)


def test_the_entry_point_runs_the_dogear_command():
    text = freeze.ENTRY.read_text(encoding="utf-8")
    assert "from dogear.cli import main" in text
    assert "raise SystemExit(main())" in text


# The bundled voice, made by the build and read back by Dogear.


def stand_in_downloads(files, config, report):
    """ensure_files without the network: writes stand-ins, and a wheel holding a model."""
    for remote in files:
        remote.path.parent.mkdir(parents=True, exist_ok=True)
        if remote.path.suffix == ".whl":
            model_dir = catalog()["spacy"]["model_dir"]
            with zipfile.ZipFile(remote.path, "w") as wheel:
                for name in ("config.cfg", "LICENSE", "LICENSES_SOURCES"):
                    wheel.writestr(f"{model_dir}/{name}", name)
        elif not remote.path.exists():
            remote.path.write_bytes(b"stand-in")


@pytest.fixture
def built_voice(tmp_path, monkeypatch):
    """Runs the build's voice step into a fake frozen folder; returns that models folder."""
    monkeypatch.setattr(voice, "ensure_files", stand_in_downloads)
    monkeypatch.setattr(voice, "_check", lambda remote: None)  # stand-ins can't match
    internal = tmp_path / "frozen" / "_internal"
    models = internal / bundle.FOLDER / bundle.MODELS
    spacy_folder = voice.bundle_voice(tmp_path / "cache", models, print)
    return internal, models, spacy_folder


def test_the_build_bundles_the_default_voice_with_a_manifest(built_voice, config):
    _, models, spacy_folder = built_voice
    manifest = json.loads((models / bundle.MANIFEST).read_text(encoding="utf-8"))["files"]
    assert len(manifest) == 4
    voice_file = f"kokoro/voices/{config['voice']['voice']}.bin"
    assert manifest[voice_file]["sha256"] == catalog()["kokoro"]["voices"]["af_heart"]["sha256"]
    for relative in manifest:
        assert (models / relative).exists()
    assert (spacy_folder / "config.cfg").is_file()
    assert (
        manifest[spacy_folder.relative_to(models).as_posix()]["sha256"]
        == catalog()["spacy"]["sha256"]
    )


def test_dogear_frozen_uses_what_the_build_bundled(built_voice, config, tmp_path, monkeypatch):
    internal, models, spacy_folder = built_voice
    monkeypatch.setattr(bundle, "sys", SimpleNamespace(frozen=True, _MEIPASS=str(internal)))
    bundle._manifest.cache_clear()

    def no_download(*args, **kwargs):
        raise AssertionError("nothing should be downloaded")

    monkeypatch.setattr(downloads_module.urllib.request, "urlopen", no_download)
    config["models"]["dir"] = str(tmp_path / "user-models")
    setup = prepare_kokoro(config, print)
    for remote in (setup.model, setup.tokenizer, setup.voice_file):
        assert remote.path.is_relative_to(models)
    assert prepare_spacy(config, print) == spacy_folder
    bundle._manifest.cache_clear()


def test_a_cached_file_that_doesnt_match_stops_the_build(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"not the model")
    remote = downloads_module.RemoteFile("https://example.org/m", "0" * 64, 13, path)
    with pytest.raises(BuildError, match="Delete it and build again"):
        voice._check(remote)


# Inno Setup.


def test_a_missing_inno_setup_says_where_to_get_it(monkeypatch, tmp_path):
    monkeypatch.setattr(windows.shutil, "which", lambda name: None)
    monkeypatch.setattr(windows, "ISCC_FOLDERS", [str(tmp_path / "nowhere")])
    with pytest.raises(BuildError, match="jrsoftware.org"):
        windows.find_iscc()


def test_inno_setup_is_found_in_its_install_folder(monkeypatch, tmp_path):
    (tmp_path / "ISCC.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(windows.shutil, "which", lambda name: None)
    monkeypatch.setattr(windows, "ISCC_FOLDERS", [str(tmp_path)])
    assert windows.find_iscc() == str(tmp_path / "ISCC.exe")
