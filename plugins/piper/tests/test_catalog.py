"""The pinned list of Piper voices: every file pinned, the languages Dogear will need, and
only voices whose training data anyone may use."""

import re

import pytest
from dogear.errors import VoiceError
from dogear.output.language_tag import language_tag

from dogear_piper.catalog import QUALITY, catalog, piper_voices, voice_files

SHA256 = re.compile(r"[0-9a-f]{64}")
ENTRIES = list(catalog()["voices"].items())


@pytest.mark.parametrize(("name", "entry"), ENTRIES, ids=[name for name, _ in ENTRIES])
def test_every_voice_is_pinned_and_described(name, entry):
    for kind in ("model", "config"):
        assert SHA256.fullmatch(entry[kind]["sha256"])
        assert entry[kind]["size"] > 0
    assert language_tag(entry["language"]) == entry["language"]
    # Piper's names start with the language and region, as en_US.
    assert name.split("-")[0] == entry["language"].replace("-", "_")
    assert name.endswith(f"-{entry['quality']}")
    assert entry["quality"] in QUALITY
    assert entry.get("gender", "") in ("", "female", "male")
    assert entry["path"] == "/".join(
        [entry["language"][:2], name.split("-")[0], name.split("-")[1], entry["quality"]]
    )
    assert entry["data"].endswith(".")


def test_every_file_comes_from_one_pinned_revision():
    assert re.fullmatch(
        r"https://huggingface\.co/rhasspy/piper-voices/resolve/[0-9a-f]{40}", catalog()["base_url"]
    )


def test_english_both_sides_of_the_atlantic_and_three_more_languages():
    languages = {voice.language for voice in piper_voices()}
    assert {"en-US", "en-GB", "fr-FR", "de-DE", "es-ES"} <= languages


def test_no_voice_comes_from_data_licensed_for_research_or_non_commercial_use():
    """en_US-lessac's recordings are licensed for research only, and en_US-ryan's for
    non-commercial use, so neither they nor voices fine-tuned from them are here."""
    for name, entry in ENTRIES:
        assert not re.search(r"lessac|ryan|hfc_|semaine", name)
        assert "lessac" not in entry["data"].lower()
        assert re.search(r"public domain|CC0|CC BY", entry["data"])


def test_best_first_means_higher_quality_first():
    order = list(QUALITY)
    ranks = [order.index(entry["quality"]) for _, entry in ENTRIES]
    assert ranks == sorted(ranks, reverse=True)


def test_a_voice_is_two_files_in_the_piper_folder(tmp_path):
    files = voice_files("en_GB-cori-high", tmp_path)
    base = f"{catalog()['base_url']}/en/en_GB/cori/high"
    assert files.model.url == f"{base}/en_GB-cori-high.onnx"
    assert files.config.url == f"{base}/en_GB-cori-high.onnx.json"
    assert files.model.path == tmp_path / "piper" / "en_GB-cori-high.onnx"
    assert files.config.path == tmp_path / "piper" / "en_GB-cori-high.onnx.json"
    entry = catalog()["voices"]["en_GB-cori-high"]
    assert (files.model.sha256, files.model.size) == (
        entry["model"]["sha256"],
        entry["model"]["size"],
    )


def test_an_unknown_voice_has_a_hint(tmp_path):
    with pytest.raises(VoiceError, match="no voice called 'en_US-nobody-high'") as err:
        voice_files("en_US-nobody-high", tmp_path)
    assert "dogear voices" in err.value.hint


def test_every_voice_says_its_sample_rate_and_the_pace_is_plausible():
    for name, entry in catalog()["voices"].items():
        assert entry["sample_rate"] in (16000, 22050), name
    assert 100 <= catalog()["words_per_minute"] <= 300
