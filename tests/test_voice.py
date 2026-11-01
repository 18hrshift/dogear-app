"""The voice catalog and setting up the configured voice, with no real model or download."""

import re
import sys
import zipfile
from pathlib import Path

import pytest
from kokoro_fakes import use_fake_session, write_tokenizer, write_voice

from dogear.errors import PronunciationError, ToolNotFoundError, VoiceError
from dogear.voice import downloads as downloads_module
from dogear.voice import g2p as g2p_module
from dogear.voice import make_engine, prepare_voice
from dogear.voice.catalog import (
    catalog,
    downloaded_kokoro_file,
    downloaded_spacy_model_dir,
    kokoro_file,
    kokoro_files,
    kokoro_voice,
    kokoro_voices,
    spacy_model_dir,
    spacy_wheel,
)
from dogear.voice.engines import configured_voice
from dogear.voice.kokoro_plugin import prepare_kokoro, prepare_spacy

SHA256 = re.compile(r"[0-9a-f]{64}")

# misaki 0.9.4 loads its dictionaries with a deprecated importlib call.
pytestmark = pytest.mark.filterwarnings("ignore:open_text is deprecated:DeprecationWarning")


# The catalog.


@pytest.mark.parametrize("voice", kokoro_voices(), ids=lambda v: v.name)
def test_every_voice_is_pinned_and_described(voice):
    entry = catalog()["kokoro"]["voices"][voice.name]
    assert SHA256.fullmatch(entry["sha256"])
    assert voice.accent in ("American", "British")
    assert voice.gender in ("female", "male")
    assert re.fullmatch(r"[A-F][+-]?", voice.grade)
    # The name's first letter is the accent and the second the gender.
    assert voice.name[0] == ("b" if voice.british else "a")
    assert voice.name[1] == voice.gender[0]


def test_every_voice_file_is_different():
    hashes = [v["sha256"] for v in catalog()["kokoro"]["voices"].values()]
    assert len(set(hashes)) == len(hashes)


def test_every_other_download_is_pinned():
    kokoro = catalog()["kokoro"]
    entries = [*kokoro["models"].values(), kokoro["tokenizer"], catalog()["spacy"]]
    for entry in entries:
        assert SHA256.fullmatch(entry["sha256"])
        assert entry["size"] > 0


def test_kokoro_files_come_from_one_pinned_revision():
    base_url = catalog()["kokoro"]["base_url"]
    assert base_url.startswith("https://")
    assert re.search(r"/resolve/[0-9a-f]{40}$", base_url)


def test_default_voice_is_in_the_catalog(config):
    assert kokoro_voice(config["voice"]["voice"]).name == config["voice"]["voice"]


def test_default_model_build_is_in_the_catalog(config):
    assert config["kokoro"]["model"] in catalog()["kokoro"]["models"]


def test_default_voice_is_a_kokoro_voice(config):
    chosen = configured_voice(config)
    assert (chosen.engine.name, chosen.voice.name) == ("kokoro", config["voice"]["voice"])


def test_kokoro_file_locations(tmp_path):
    base_url = catalog()["kokoro"]["base_url"]
    model = kokoro_file("model", "quantized", tmp_path)
    assert model.url == f"{base_url}/onnx/model_quantized.onnx"
    assert model.path == tmp_path / "kokoro" / "onnx" / "model_quantized.onnx"
    assert model.sha256 == catalog()["kokoro"]["models"]["quantized"]["sha256"]
    voice = kokoro_file("voice", "bf_emma", tmp_path)
    assert voice.url == f"{base_url}/voices/bf_emma.bin"
    assert voice.path == tmp_path / "kokoro" / "voices" / "bf_emma.bin"
    assert voice.sha256 == catalog()["kokoro"]["voices"]["bf_emma"]["sha256"]
    tokenizer = kokoro_file("tokenizer", "", tmp_path)
    assert tokenizer.path == tmp_path / "kokoro" / "tokenizer.json"
    with pytest.raises(ValueError, match="unknown Kokoro file kind"):
        kokoro_file("vocoder", "", tmp_path)


def test_spacy_file_locations(tmp_path):
    wheel = spacy_wheel(tmp_path)
    assert wheel.url.endswith(".whl")
    assert wheel.path == tmp_path / "downloads" / "en_core_web_sm-3.8.0-py3-none-any.whl"
    assert spacy_model_dir(tmp_path) == tmp_path / "spacy" / "en_core_web_sm-3.8.0"


# Choosing and preparing the voice.

SYMBOLS = " .,!?;:'\"()abcdefhijklmnopqrstuvwxyzæðŋɑɔəɛɜɡɪɹʃʊʌʒʤʧθᵊˈˌAIOQWYTː"


@pytest.fixture
def models(tmp_path, config):
    """Points models.dir at an empty folder and returns it."""
    folder = tmp_path / "models"
    config["models"]["dir"] = str(folder)
    return folder


def install_voice(models_dir, voice="af_heart", build="full"):
    """Puts stand-ins for every file the voice needs where Dogear looks for them."""
    model = kokoro_file("model", build, models_dir).path
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"not a real model")
    write_tokenizer(kokoro_file("tokenizer", "", models_dir).path, SYMBOLS)
    write_voice(kokoro_file("voice", voice, models_dir).path, rows=510)
    spacy_dir = spacy_model_dir(models_dir)
    spacy_dir.mkdir(parents=True, exist_ok=True)
    (spacy_dir / "config.cfg").write_text("[nlp]", encoding="utf-8")


@pytest.fixture
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("tests must not download anything")

    monkeypatch.setattr(downloads_module.urllib.request, "urlopen", fail)


@pytest.fixture
def fake_downloads(monkeypatch):
    """Replaces ensure_files: records what was asked for and writes stand-in files."""
    requested = []

    def fake_ensure_files(files, config, report):
        requested.append([f.path.name for f in files])
        for remote in files:
            remote.path.parent.mkdir(parents=True, exist_ok=True)
            if remote.path.suffix == ".whl":
                model_dir = catalog()["spacy"]["model_dir"]
                with zipfile.ZipFile(remote.path, "w") as wheel:
                    wheel.writestr(f"{model_dir}/config.cfg", "[nlp]")
            else:
                remote.path.write_bytes(b"stand-in")

    monkeypatch.setattr("dogear.voice.kokoro_plugin.ensure_files", fake_ensure_files)
    return requested


def test_kokoro_voice():
    voice = kokoro_voice("bf_emma")
    assert (voice.name, voice.accent, voice.british) == ("bf_emma", "British", True)
    assert not kokoro_voice("af_heart").british


def test_unknown_voice_has_a_hint():
    with pytest.raises(VoiceError, match="no voice called 'af_nobody'") as err:
        kokoro_voice("af_nobody")
    assert "dogear voices" in err.value.hint


@pytest.mark.parametrize(
    ("section", "key", "value", "message", "hint"),
    [
        ("voice", "engine", "piper", "voice.engine is 'piper'", "kokoro"),
        ("voice", "voice", "af_nobody", "no voice called 'af_nobody'", "dogear voices"),
        ("kokoro", "model", "q8f16", "kokoro.model is 'q8f16'", "full, quantized"),
    ],
)
def test_bad_voice_settings_fail_before_any_download(
    config, models, fake_downloads, section, key, value, message, hint
):
    config[section][key] = value
    with pytest.raises(VoiceError, match=message) as err:
        prepare_voice(config, print)
    assert hint in err.value.hint
    assert fake_downloads == []


def test_first_run_downloads_every_file_and_unpacks_spacy(config, models, fake_downloads):
    setup = prepare_kokoro(config, print)
    assert fake_downloads == [
        ["model.onnx", "tokenizer.json", "af_heart.bin", "en_core_web_sm-3.8.0-py3-none-any.whl"]
    ]
    assert setup.voice.name == "af_heart"
    assert setup.model.path == models / "kokoro" / "onnx" / "model.onnx"
    assert setup.tokenizer.path == models / "kokoro" / "tokenizer.json"
    assert setup.voice_file.path == models / "kokoro" / "voices" / "af_heart.bin"
    assert (spacy_model_dir(models) / "config.cfg").is_file()
    assert not spacy_wheel(models).path.exists()


def test_unpacked_spacy_model_is_not_downloaded_again(config, models, fake_downloads):
    setup = prepare_kokoro(config, print)
    prepare_kokoro(config, print)
    assert len(fake_downloads) == 1  # everything is there: nothing asked for
    setup.model.path.unlink()
    prepare_kokoro(config, print)
    assert fake_downloads[1] == ["model.onnx", "tokenizer.json", "af_heart.bin"]


def test_nothing_is_downloaded_when_every_file_is_there(config, models, no_network):
    install_voice(models)
    lines = []
    prepare_kokoro(config, lines.append)
    assert lines == []


def test_voice_and_model_settings_choose_the_files(config, models, fake_downloads):
    config["voice"]["voice"] = "bm_george"
    config["kokoro"]["model"] = "quantized"
    setup = prepare_kokoro(config, print)
    assert setup.voice.british
    assert setup.model.path.name == "model_quantized.onnx"
    assert setup.voice_file.path.name == "bm_george.bin"
    assert fake_downloads[0][:3] == ["model_quantized.onnx", "tokenizer.json", "bm_george.bin"]


def test_missing_espeak_is_reported_before_any_download(config, models, fake_downloads, tmp_path):
    config["tools"]["espeak_ng"] = str(tmp_path / "no-such-espeak-ng")
    with pytest.raises(ToolNotFoundError, match="espeak_ng") as err:
        make_engine(config, print)
    assert "eSpeak NG" in err.value.hint
    assert fake_downloads == []


@pytest.fixture
def ready_engine(config, models, monkeypatch, no_network):
    """Everything make_engine needs, faked: model files, spaCy, onnxruntime, espeak-ng."""
    import spacy

    install_voice(models)
    install_voice(models, voice="bf_emma")
    monkeypatch.setattr(spacy, "load", lambda path, enable: spacy.blank("en"))
    config["tools"]["espeak_ng"] = sys.executable  # found, but never run in these tests
    return use_fake_session(monkeypatch)


def test_make_engine_uses_the_configured_settings(config, models, ready_engine):
    config["voice"]["speed"] = 1.25
    config["kokoro"]["threads"] = 3
    engine = make_engine(config, print)
    assert engine.sample_rate == catalog()["kokoro"]["sample_rate"]
    [session] = ready_engine.instances
    assert session.model == str(models / "kokoro" / "onnx" / "model.onnx")
    assert session.options.intra_op_num_threads == 3
    assert session.providers == ["CPUExecutionProvider"]
    audio = engine.render(engine.plan("Hello there."))
    assert len(audio) > 0
    assert session.feeds[0]["speed"].tolist() == [1.25]


def test_make_engine_pronounces_by_the_voices_accent(config, ready_engine):
    american = make_engine(config, print).plan("Hello.").payload
    config["voice"]["voice"] = "bf_emma"
    british = make_engine(config, print).plan("Hello.").payload
    assert "O" in american  # misaki's American "oh" in "hello"
    assert "Q" in british  # and its British one
    assert american != british


def test_make_engine_keys_differ_by_voice(config, ready_engine):
    heart = make_engine(config, print).plan("Good morning.")
    config["voice"]["voice"] = "bf_emma"
    emma = make_engine(config, print).plan("Good morning.")
    assert heart.key != emma.key


# The user's pronunciation files.


def use_words(config, tmp_path, text, name="words.toml"):
    path = tmp_path / name
    path.write_text("[words]\n" + text, encoding="utf-8")
    config["pronunciation"]["files"] = [str(path)]
    return path


# spaCy's blank pipeline stands in for the model here. It tags nothing, so misaki would
# send a period right after a pinned word to espeak-ng; these sentences avoid that.


def test_make_engine_pins_phoneme_entries(config, ready_engine, tmp_path):
    use_words(config, tmp_path, 'Lanyon = { phonemes = "lˈænjən" }\n')
    assert make_engine(config, print).plan("Hello Lanyon").payload == "həlˈO lˈænjən"


def test_changing_a_phoneme_entry_changes_only_its_sentences_keys(config, ready_engine, tmp_path):
    use_words(config, tmp_path, 'Lanyon = { phonemes = "lˈænjən" }\n')
    first = make_engine(config, print)
    use_words(config, tmp_path, 'Lanyon = { phonemes = "lˈɑnjən" }\n')
    second = make_engine(config, print)
    assert first.plan("Lanyon said hello.").key != second.plan("Lanyon said hello.").key
    assert first.plan("Good morning.").key == second.plan("Good morning.").key


def test_a_bad_pronunciation_file_stops_before_any_download(
    config, models, fake_downloads, tmp_path
):
    config["tools"]["espeak_ng"] = sys.executable
    config["pronunciation"]["files"] = [str(tmp_path / "missing.toml")]
    with pytest.raises(PronunciationError, match="Pronunciation file not found"):
        make_engine(config, print)
    assert fake_downloads == []


def test_a_phoneme_the_voice_lacks_stops_the_engine(config, ready_engine, tmp_path):
    path = use_words(config, tmp_path, 'Gale = { phonemes = "gˈAl" }\n')  # keyboard g, not ɡ
    with pytest.raises(PronunciationError, match="'g' \\(latin small letter g\\)") as err:
        make_engine(config, print)
    assert f"'Gale' in {path}" in err.value.message
    assert ready_engine.instances == []  # stopped before loading the model


# A standalone build, which carries the default voice.


def voice_files(voice="af_heart", model_build="full"):
    """Where packaging/ puts a voice's files in a build, and their SHA-256 sums. Asked
    before anything is bundled, so the catalog answers with models-folder paths."""
    base = Path("models")
    remotes = [
        kokoro_file("model", model_build, base),
        kokoro_file("tokenizer", "", base),
        kokoro_file("voice", voice, base),
    ]
    files = {r.path.relative_to(base).as_posix(): r.sha256 for r in remotes}
    spacy_dir = spacy_model_dir(base).relative_to(base).as_posix()
    return files, spacy_dir


def bundle_voice(build):
    files, spacy_dir = voice_files()
    for relative, sha256 in files.items():
        build.add_model(relative, sha256)
    build.add_model_folder(spacy_dir, catalog()["spacy"]["sha256"])
    return files


@pytest.fixture
def downloaded(monkeypatch):
    """The real ensure_files, choosing what's missing; the download itself is faked."""
    names = []

    def fake_download(remote, timeout, report):
        names.append(remote.path.name)
        remote.path.parent.mkdir(parents=True, exist_ok=True)
        remote.path.write_bytes(b"stand-in")

    monkeypatch.setattr(downloads_module, "download", fake_download)
    return names


def test_a_standalone_build_speaks_its_voice_without_downloading(
    config, models, standalone_build, no_network
):
    bundle_voice(standalone_build)
    lines = []
    setup = prepare_kokoro(config, lines.append)
    assert lines == []
    for remote in (setup.model, setup.tokenizer, setup.voice_file):
        assert remote.path.is_relative_to(standalone_build.folder)
    assert prepare_spacy(config, print).is_relative_to(standalone_build.folder)
    assert not models.exists()  # nothing written to the user's folder


def test_a_standalone_build_downloads_other_voices_to_the_user_folder(
    config, models, standalone_build, downloaded
):
    bundle_voice(standalone_build)
    config["voice"]["voice"] = "bf_emma"
    setup = prepare_kokoro(config, print)
    assert downloaded == ["bf_emma.bin"]
    assert setup.voice_file.path == models / "kokoro" / "voices" / "bf_emma.bin"
    assert setup.model.path.is_relative_to(standalone_build.folder)


def test_a_bundled_file_that_isnt_the_pinned_one_is_downloaded_instead(
    config, models, standalone_build, downloaded
):
    files = bundle_voice(standalone_build)
    [tokenizer] = [relative for relative in files if relative.endswith("tokenizer.json")]
    standalone_build.add_model(tokenizer, "0" * 64)  # say, from an older catalog
    setup = prepare_kokoro(config, print)
    assert downloaded == ["tokenizer.json"]
    assert setup.tokenizer.path == models / "kokoro" / "tokenizer.json"


def test_the_bundled_espeak_ng_is_told_where_its_data_is(
    config, models, standalone_build, monkeypatch, no_network
):
    import spacy

    install_voice(models)
    espeak = standalone_build.add_program("espeak-ng")
    monkeypatch.setattr(spacy, "load", lambda path, enable: spacy.blank("en"))
    use_fake_session(monkeypatch)
    made = []
    real_g2p = g2p_module.EnglishG2P

    def recording_g2p(*args, **kwargs):
        made.append((args, kwargs))
        return real_g2p(*args, **kwargs)

    monkeypatch.setattr(g2p_module, "EnglishG2P", recording_g2p)
    make_engine(config, print)
    [(args, kwargs)] = made
    assert args[2] == str(espeak)
    assert kwargs["espeak_data"] == espeak.parent


def test_prepare_spacy_downloads_only_the_spacy_model(config, models, fake_downloads):
    folder = prepare_spacy(config, print)
    assert folder == spacy_model_dir(models)
    assert (folder / "config.cfg").is_file()
    assert fake_downloads == [["en_core_web_sm-3.8.0-py3-none-any.whl"]]
    assert prepare_spacy(config, print) == folder
    assert len(fake_downloads) == 1  # nothing more to fetch


def test_spacys_unpacked_model_is_pinned_too():
    assert SHA256.fullmatch(catalog()["spacy"]["model_sha256"])


def test_kokoro_files_lists_every_build_the_tokenizer_and_every_voice():
    kokoro = catalog()["kokoro"]
    listed = kokoro_files()
    assert [name for kind, name in listed if kind == "model"] == list(kokoro["models"])
    assert ("tokenizer", "") in listed
    assert [name for kind, name in listed if kind == "voice"] == list(kokoro["voices"])


def test_downloaded_files_are_never_the_bundled_copies(tmp_path, standalone_build):
    voice = catalog()["kokoro"]["voices"]["af_heart"]["sha256"]
    bundled = standalone_build.add_model("kokoro/voices/af_heart.bin", voice)
    assert kokoro_file("voice", "af_heart", tmp_path).path == bundled
    downloaded = downloaded_kokoro_file("voice", "af_heart", tmp_path)
    assert downloaded.path == tmp_path / "kokoro" / "voices" / "af_heart.bin"
    folder = standalone_build.add_model_folder(
        "spacy/en_core_web_sm-3.8.0", catalog()["spacy"]["sha256"]
    )
    assert spacy_model_dir(tmp_path) == folder
    assert downloaded_spacy_model_dir(tmp_path) == tmp_path / "spacy" / "en_core_web_sm-3.8.0"
