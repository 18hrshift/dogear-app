"""`dogear download --check`: every voice file on disk hashed against the catalog (or, for
another engine, against what it pins), and the damaged ones downloaded again.

The catalog is replaced by a small one whose files are served from `file://` URLs in
tmp_path, so nothing here uses the network or the real voice.
"""

import hashlib
import importlib
import io
import zipfile
from pathlib import Path

import pytest
from engine_fakes import FakePlugin, hum_voice

from dogear import cli
from dogear.errors import DogearError, VoiceError
from dogear.voice import check as check_module
from dogear.voice import downloads, spacy_model
from dogear.voice.check import CHECK_AGAIN, REINSTALL, check_voice_files
from dogear.voice.downloads import RemoteFile, folder_sha256

# The module, not the function of the same name that dogear.voice gives.
catalog_module = importlib.import_module("dogear.voice.catalog")

MODEL = b"model bytes " * 100
TOKENIZER = b'{"model": {"vocab": {"$": 0}}}'
HEART = b"heart voice"
EMMA = b"emma voice"
MODEL_DIR = "en_core_web_sm/en_core_web_sm-3.8.0"
SPACY_FILES = {"config.cfg": b"[nlp]", "vocab/strings.json": b"[]"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wheel_bytes() -> bytes:
    """The same bytes every time: each entry has a fixed date, not the time it's made."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as wheel:
        for name, data in SPACY_FILES.items():
            wheel.writestr(zipfile.ZipInfo(f"{MODEL_DIR}/{name}", (2024, 1, 1, 0, 0, 0)), data)
    return buffer.getvalue()


def spacy_folder_sha256(tmp_path: Path) -> str:
    folder = tmp_path / "expected-spacy"
    for name, data in SPACY_FILES.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    return folder_sha256(folder)


@pytest.fixture
def server(tmp_path):
    """Where the small catalog's files are served from."""
    root = tmp_path / "server"
    files = {
        "onnx/model.onnx": MODEL,
        "tokenizer.json": TOKENIZER,
        "voices/af_heart.bin": HEART,
        "voices/bf_emma.bin": EMMA,
        "en_core_web_sm-3.8.0-py3-none-any.whl": wheel_bytes(),
    }
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    return root


@pytest.fixture
def small_catalog(tmp_path, server, monkeypatch):
    wheel = wheel_bytes()
    voice = {"accent": "American", "gender": "female", "grade": "A"}
    data = {
        "kokoro": {
            "base_url": server.as_uri(),
            "sample_rate": 24000,
            "max_phonemes": 510,
            "tokenizer": {"path": "tokenizer.json", "sha256": sha256(TOKENIZER), "size": 100},
            "models": {
                "full": {"path": "onnx/model.onnx", "sha256": sha256(MODEL), "size": 10_000}
            },
            "voices": {
                "af_heart": {**voice, "sha256": sha256(HEART)},
                "bf_emma": {**voice, "accent": "British", "sha256": sha256(EMMA)},
            },
        },
        "spacy": {
            "url": (server / "en_core_web_sm-3.8.0-py3-none-any.whl").as_uri(),
            "sha256": sha256(wheel),
            "size": len(wheel),
            "model_dir": MODEL_DIR,
            "model_sha256": spacy_folder_sha256(tmp_path),
        },
    }
    for module in (catalog_module, check_module, spacy_model):
        monkeypatch.setattr(module, "catalog", lambda: data)
    return data


@pytest.fixture
def models(tmp_path, config, small_catalog):
    """An empty models folder that the config points at."""
    folder = tmp_path / "models"
    config["models"]["dir"] = str(folder)
    return folder


def install(models: Path, *voices: str) -> None:
    """The model, the tokenizer, the voices, and spaCy's unpacked model, all good."""
    for relative, data in {
        "kokoro/onnx/model.onnx": MODEL,
        "kokoro/tokenizer.json": TOKENIZER,
        **{f"kokoro/voices/{v}.bin": {"af_heart": HEART, "bf_emma": EMMA}[v] for v in voices},
        **{f"spacy/en_core_web_sm-3.8.0/{name}": data for name, data in SPACY_FILES.items()},
    }.items():
        (models / relative).parent.mkdir(parents=True, exist_ok=True)
        (models / relative).write_bytes(data)


def damage(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[0] ^= 1
    path.write_bytes(bytes(data))


def run_check(config) -> list[str]:
    lines: list[str] = []
    check_voice_files(config, lines.append, lines.append)
    return lines


def test_no_voice_files_says_where_they_go_and_how_to_get_them(config, models):
    lines = run_check(config)
    assert lines == [
        f"Dogear has no voice files yet. Downloads go into {models}.",
        "Run `dogear download` to get the voice now, or let `dogear make` do it.",
    ]


def test_every_voice_file_on_disk_is_checked_whatever_voice_it_is(config, models):
    install(models, "af_heart", "bf_emma")
    lines = run_check(config)
    assert lines[0].startswith(f"Checking 5 voice files in {models}, ")
    assert lines[1:] == [
        "1. Good: the Kokoro model (full build).",
        "2. Good: the Kokoro tokenizer.",
        "3. Good: the voice af_heart.",
        "4. Good: the voice bf_emma.",
        "5. Good: spaCy's English model.",
        "Every voice file is good.",
    ]


def test_a_damaged_file_is_downloaded_again(config, models):
    install(models, "af_heart", "bf_emma")
    voice = models / "kokoro/voices/af_heart.bin"
    damage(voice)
    lines = run_check(config)
    assert "3. Damaged: the voice af_heart. It doesn't match its checksum." in lines
    assert lines[-3:] == [
        "Downloading 1 damaged file again.",
        "Downloaded again, and good now: the voice af_heart.",
        "Every voice file is good now.",
    ]
    assert voice.read_bytes() == HEART


def test_a_damaged_spacy_model_is_unpacked_again_from_a_new_download(config, models):
    install(models, "af_heart")
    strings = models / "spacy/en_core_web_sm-3.8.0/vocab/strings.json"
    damage(strings)
    (strings.parent / "stray.txt").write_text("not part of the model", encoding="utf-8")
    lines = run_check(config)
    assert "4. Damaged: spaCy's English model. It doesn't match its checksum." in lines
    assert "Downloaded again, and good now: spaCy's English model." in lines
    assert strings.read_bytes() == SPACY_FILES["vocab/strings.json"]
    assert not (strings.parent / "stray.txt").exists()
    assert list((models / "downloads").iterdir()) == []  # the wheel is gone once unpacked


def test_a_wheel_left_from_an_unpack_that_stopped_is_checked_too(config, models):
    install(models)
    wheel = models / "downloads" / "en_core_web_sm-3.8.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"half a wheel")
    lines = run_check(config)
    assert "3. Damaged: spaCy's English model, as downloaded. It doesn't match its checksum." in (
        lines
    )
    assert wheel.read_bytes() == wheel_bytes()


def test_a_file_that_cant_be_downloaded_again_is_an_error_and_stays_as_it_was(
    config, models, server
):
    install(models, "af_heart")
    voice = models / "kokoro/voices/af_heart.bin"
    damage(voice)
    damaged = voice.read_bytes()
    (server / "voices/af_heart.bin").unlink()  # as if offline
    lines: list[str] = []
    with pytest.raises(DogearError) as error:
        check_voice_files(config, lines.append, lines.append)
    assert error.value.message == "1 voice file still damaged: the voice af_heart."
    assert error.value.hint == CHECK_AGAIN
    assert lines[-1].startswith("Still damaged: the voice af_heart. Couldn't download af_heart")
    assert voice.read_bytes() == damaged


def test_a_damaged_bundled_file_says_to_reinstall_and_is_left_alone(
    config, models, standalone_build, small_catalog
):
    sha = small_catalog["kokoro"]["voices"]["af_heart"]["sha256"]
    bundled = standalone_build.add_model("kokoro/voices/af_heart.bin", sha, b"not the voice")
    lines: list[str] = []
    with pytest.raises(DogearError) as error:
        check_voice_files(config, lines.append, lines.append)
    where = standalone_build.folder / "models"
    assert lines[0].startswith(f"Checking 1 voice file that came with Dogear, in {where}, ")
    assert lines[1] == "1. Damaged: the voice af_heart. It doesn't match its checksum."
    assert error.value.hint == REINSTALL
    assert bundled.read_bytes() == b"not the voice"


def test_bundled_and_downloaded_files_are_listed_apart(
    config, models, standalone_build, small_catalog
):
    install(models, "bf_emma")
    folder = standalone_build.add_model_folder(
        "spacy/en_core_web_sm-3.8.0", small_catalog["spacy"]["sha256"]
    )
    (folder / "vocab").mkdir()
    (folder / "vocab/strings.json").write_bytes(SPACY_FILES["vocab/strings.json"])
    lines = run_check(config)
    assert lines[0].startswith("Checking 1 voice file that came with Dogear")
    assert lines[1] == "1. Good: spaCy's English model."
    assert lines[2].startswith(f"Checking 4 voice files in {models}")
    assert lines[-1] == "Every voice file is good."


def test_big_files_say_how_the_check_is_going(config, models, monkeypatch):
    monkeypatch.setattr(downloads, "_REPORT_FROM", 1000)
    monkeypatch.setattr(downloads, "_CHUNK", 300)
    install(models)
    lines = run_check(config)
    assert lines[1:6] == [
        "model.onnx: 25% checked.",
        "model.onnx: 50% checked.",
        "model.onnx: 75% checked.",
        "model.onnx: 100% checked.",
        "1. Good: the Kokoro model (full build).",
    ]


def test_the_command_exits_non_zero_while_a_file_is_still_damaged(
    config, models, server, tmp_path, capsys
):
    install(models, "af_heart")
    damage(models / "kokoro/voices/af_heart.bin")
    (server / "voices/af_heart.bin").unlink()
    settings = tmp_path / "settings.toml"
    settings.write_text(f"[models]\ndir = {str(models)!r}\n", encoding="utf-8")
    assert cli.main(["download", "--check", "--config", str(settings)]) == 1
    output = capsys.readouterr()
    assert "Damaged: the voice af_heart." in output.out
    assert "Error: 1 voice file still damaged: the voice af_heart." in output.err
    assert f"What to do: {CHECK_AGAIN}" in output.err


def test_the_command_exits_zero_when_every_file_is_good(config, models, tmp_path, capsys):
    install(models, "af_heart")
    settings = tmp_path / "settings.toml"
    settings.write_text(f"[models]\ndir = {str(models)!r}\n", encoding="utf-8")
    assert cli.main(["download", "--check", "--config", str(settings)]) == 0
    assert capsys.readouterr().out.endswith("Every voice file is good.\n")


def test_check_and_voice_cant_be_given_together(capsys):
    with pytest.raises(SystemExit):
        cli.main(["download", "--check", "--voice", "bf_emma"])
    assert "not allowed with" in capsys.readouterr().err


# Other engines' files: the ones each pins with `pinned_files`.

PINNED = b"another engine's model"


class PinningEngine(FakePlugin):
    """An engine that pins one file of its own, in its folder of the models folder."""

    def pinned_files(self, config):
        models = Path(config["models"]["dir"])
        url = (models.parent / "server" / "pinned.bin").as_uri()
        return [RemoteFile(url, sha256(PINNED), len(PINNED), models / "pins" / "pinned.bin")]


@pytest.fixture
def pinning(installed_engines, server):
    (server / "pinned.bin").write_bytes(PINNED)
    plugin = PinningEngine("pins", "Pins", [hum_voice("one")])
    installed_engines(plugin)
    return plugin


def test_another_engines_files_are_checked_and_downloaded_again(config, models, pinning):
    install(models)
    pinned = models / "pins" / "pinned.bin"
    pinned.parent.mkdir()
    pinned.write_bytes(b"not it")
    lines = run_check(config)
    assert lines[0].startswith(f"Checking 4 voice files in {models}, ")
    assert "4. Damaged: the Pins file pinned.bin. It doesn't match its checksum." in lines
    assert "Downloaded again, and good now: the Pins file pinned.bin." in lines
    assert lines[-1] == "Every voice file is good now."
    assert pinned.read_bytes() == PINNED


def test_another_engines_files_not_on_disk_arent_listed(config, models, pinning):
    install(models)
    lines = run_check(config)
    assert lines[0].startswith(f"Checking 3 voice files in {models}, ")
    assert "pinned.bin" not in "\n".join(lines)


def test_an_engine_that_pins_something_else_is_a_bug_in_it(config, models, installed_engines):
    class Odd(FakePlugin):
        def pinned_files(self, config):
            return ["model.onnx"]

    installed_engines(Odd("odd", "Odd", [hum_voice("one")]))
    with pytest.raises(VoiceError, match="listed 'model.onnx' as a file it pins"):
        run_check(config)
