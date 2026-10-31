"""Checking that a book fits on disk before `dogear make` speaks it.

Free space is faked here, apart from one end-to-end run of the web page's real child.
"""

import html
import shutil
import sys
from pathlib import Path

import pytest
from conftest import require_tool
from web_samples import CHOSEN, add_book, sample_app, sample_summary, visitor

from dogear import disk_space
from dogear.disk_space import (
    Disk,
    Need,
    bits_per_second,
    check_space,
    mp3_ceiling,
    space_needed,
)
from dogear.errors import ConfigError, DiskSpaceError, VoiceError
from dogear.voice import VoicePace, voice_pace
from dogear.voice.catalog import catalog
from dogear.web import child
from dogear.web.making import make_book

HOUR = VoicePace(sample_rate=24_000, words_per_minute=150)  # 9,000 words is an hour
WORDS_AN_HOUR = 9_000
MB = 1_000_000
FIXTURE = Path(__file__).parent / "fixtures"
JEKYLL = FIXTURE / "robert-louis-stevenson_the-strange-case-of-dr-jekyll-and-mr-hyde.epub"


def needs(config, formats, tmp_path, words=WORDS_AN_HOUR):
    cache, output = tmp_path / "cache", tmp_path / "out"
    found = space_needed(words, formats, cache, output, HOUR, config)
    return {n.kind: n.size for n in found}


# The estimate


def test_an_hour_of_speech_cache_is_about_170_mb(config, tmp_path):
    wav_hour = 24_000 * 2 * 3600
    assert wav_hour == pytest.approx(173 * MB, rel=0.01)
    # The cache, and the work folder's two copies of the audio.
    assert needs(config, ["m4b"], tmp_path)["cache"] == 3 * wav_hour


def test_the_length_comes_from_the_words_and_the_pace(config, tmp_path):
    one_hour = needs(config, ["m4b"], tmp_path)
    two_hours = needs(config, ["m4b"], tmp_path, words=2 * WORDS_AN_HOUR)
    assert two_hours["cache"] == 2 * one_hour["cache"]
    assert two_hours["output"] == 2 * one_hour["output"]


def test_each_format_is_counted_at_its_own_rate(config, tmp_path):
    hour = 3600 / 8  # bytes in an hour, per bit a second
    assert needs(config, ["m4b"], tmp_path)["output"] == round(128_000 * hour)
    assert needs(config, ["mp3"], tmp_path)["output"] == round(160_000 * hour)
    assert needs(config, ["epub"], tmp_path)["output"] == round(64_000 * hour)
    assert needs(config, ["m4b", "daisy"], tmp_path)["output"] == round(192_000 * hour)


def test_talking_books_add_their_mp3s_to_the_work_folder(config, tmp_path):
    hour = 3600 / 8
    plain = needs(config, ["m4b", "mp3"], tmp_path)["cache"]
    assert needs(config, ["epub", "daisy"], tmp_path)["cache"] == plain + round(64_000 * hour)


def test_changing_the_audio_settings_changes_the_estimate(config, tmp_path):
    before = needs(config, ["m4b", "epub"], tmp_path)
    config["audio"]["m4b_bitrate"] = "64k"
    config["audio"]["talking_book_bitrate"] = "32k"
    after = needs(config, ["m4b", "epub"], tmp_path)
    assert after["output"] == before["output"] / 2
    assert after["cache"] < before["cache"]


def test_mp3_is_estimated_at_the_most_an_mp3_holds_at_the_voices_rate():
    assert mp3_ceiling(24_000) == 160_000
    assert mp3_ceiling(44_100) == 320_000


@pytest.mark.parametrize(
    ("text", "rate"), [("128k", 128_000), ("64K", 64_000), ("1.5M", 1_500_000), ("96000", 96_000)]
)
def test_bitrates_are_read_as_ffmpeg_writes_them(text, rate):
    assert bits_per_second({"m4b_bitrate": text}, "m4b_bitrate") == rate


@pytest.mark.parametrize("text", ["fast", "128 kbps", "", "0k", "-64k"])
def test_a_bitrate_that_isnt_one_is_a_settings_error(text):
    with pytest.raises(ConfigError, match="audio.m4b_bitrate is .*, which isn't a bitrate"):
        bits_per_second({"m4b_bitrate": text}, "m4b_bitrate")


def test_the_voices_pace_follows_its_speed(config):
    kokoro = catalog()["kokoro"]
    pace = voice_pace(config)
    assert pace.sample_rate == kokoro["sample_rate"]
    assert pace.words_per_minute == pytest.approx(kokoro["words_per_minute"] * 0.9)
    config["voice"]["speed"] = 1.8
    assert voice_pace(config).words_per_minute == pytest.approx(2 * pace.words_per_minute)
    config["voice"]["speed"] = 0
    with pytest.raises(VoiceError, match="must be more than 0"):
        voice_pace(config)


# The check


@pytest.fixture
def disks(monkeypatch):
    """Fake disks, {folder: (disk id, free bytes)}, to fill and change. A folder is on the
    disk of the entry whose folder holds it."""
    found: dict[Path, tuple[int, int]] = {}

    def disk_of(folder: Path) -> Disk:
        for root, (disk_id, free) in found.items():
            if folder.is_relative_to(root):
                return Disk(disk_id, 10**12, free)
        raise AssertionError(f"no disk for {folder}")

    monkeypatch.setattr(disk_space, "disk_of", disk_of)
    return found


def test_a_disk_with_room_for_the_estimate_and_the_margin_passes(config, tmp_path, disks):
    disks[tmp_path] = (1, 181 * MB)  # 150 MB and 20% is 180 MB
    check_space([Need("cache", tmp_path, 100 * MB), Need("output", tmp_path, 50 * MB)], config)


def test_needs_on_one_disk_add_up(config, tmp_path, disks):
    disks[tmp_path] = (1, 179 * MB)
    found = [Need("cache", tmp_path / "cache", 100 * MB), Need("output", tmp_path / "out", 50 * MB)]
    with pytest.raises(DiskSpaceError) as error:
        check_space(found, config)
    assert error.value.message == (
        "This book needs about 172 MB of free disk space, for the speech cache and the work "
        f"folder (in {tmp_path / 'cache'}) and the book (in {tmp_path / 'out'}), but that "
        "disk has 171 MB free."
    )
    assert error.value.hint == (
        "Free up space on that disk, or make fewer sections or formats at a time. Or save "
        "the book on another disk, with --output-dir. Or keep the speech cache on another "
        "disk: set cache_dir under [render] in a settings file."
    )


def test_the_margin_setting_changes_what_fits(config, tmp_path, disks):
    disks[tmp_path] = (1, 100 * MB)
    found = [Need("output", tmp_path, 100 * MB)]
    config["output"]["space_margin_percent"] = 0
    check_space(found, config)
    config["output"]["space_margin_percent"] = 1
    with pytest.raises(DiskSpaceError):
        check_space(found, config)
    config["output"]["space_margin_percent"] = -1
    with pytest.raises(ConfigError, match="can't be negative"):
        check_space(found, config)


def test_each_disk_is_checked_for_its_own_needs(config, tmp_path, disks):
    cache, out = tmp_path / "cache", tmp_path / "out"
    found = [Need("cache", cache, 100 * MB), Need("output", out, 50 * MB)]
    disks[cache], disks[out] = (1, 200 * MB), (2, 10 * MB)
    with pytest.raises(DiskSpaceError) as error:
        check_space(found, config)
    assert error.value.message == (
        f"This book needs about 57 MB of free disk space, for the book (in {out}), but that "
        "disk has 9.5 MB free."
    )
    assert "--output-dir" in error.value.hint
    assert "cache_dir" not in error.value.hint
    disks[cache], disks[out] = (1, 10 * MB), (2, 200 * MB)
    with pytest.raises(DiskSpaceError) as error:
        check_space(found, config)
    assert f"for the speech cache and the work folder (in {cache}), but" in error.value.message
    assert "cache_dir under [render]" in error.value.hint
    assert "--output-dir" not in error.value.hint


def test_a_disk_that_reports_no_size_is_not_held_against_a_book(config, tmp_path, monkeypatch):
    monkeypatch.setattr(disk_space, "disk_of", lambda folder: Disk(1, 0, 0))
    check_space([Need("output", tmp_path, 100 * MB)], config)


def test_a_folder_not_made_yet_is_found_on_the_disk_it_will_be_on(tmp_path):
    folder = tmp_path / "new" / "books"
    assert disk_space.disk_of(folder).id == disk_space.disk_of(tmp_path).id
    assert not folder.exists()


# On the web page


def test_the_web_page_shows_the_childs_disk_space_error(tmp_path, monkeypatch):
    require_tool("ffmpeg")
    monkeypatch.setattr(child, "dogear_command", lambda: [sys.executable, "-m", "dogear"])
    settings_file = tmp_path / "settings.toml"
    # No disk has room for a book with this margin.
    settings_file.write_text("[output]\nspace_margin_percent = 1000000000000\n", "utf-8")
    app = sample_app(tmp_path / "web", config_file=settings_file)
    library = app.state.library
    book_id = add_book(
        library, tmp_path, "jekyll.epub", state="waiting", choices=CHOSEN, summary=sample_summary()
    )
    shutil.copy(JEKYLL, library.folder(book_id) / "book.epub")
    make_book(library.store, app.state.settings, book_id, child.Stopper())
    record = library.get(book_id)
    assert record.state == "failed"
    assert record.error.startswith("This book needs about ")
    assert "of free disk space" in record.error
    assert record.hint.startswith("Free up space on that disk")
    page = visitor(app).get(f"/books/{book_id}").text
    assert f"<strong>Error:</strong> {html.escape(record.error)}" in page
    assert html.escape(record.hint) in page
