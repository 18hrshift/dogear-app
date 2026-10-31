"""Speaking narrations into WAV files through the sentence cache, with a fake voice."""

import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from dogear.document import Section
from dogear.language_match import primary
from dogear.narration import SectionNarration, Utterance
from dogear.render import render_book
from dogear.render.cache import SpeechCache
from dogear.render.progress import Progress
from dogear.render.voices import OneVoice
from dogear.render.wav import WavWriter, read_wav, to_pcm16, trim_edges, write_wav
from dogear.voice.engine import SpeechPlan

RATE = 8000
TONE_PER_CHAR = 80  # samples of sound per character of text: 10 ms
PADDING = 800  # samples of silence the fake voice puts around each sentence: 100 ms


class ToneEngine:
    """A voice that says each sentence as a square wave sized by its length, padded with
    silence the way real voices pad theirs. Counts what it renders."""

    sample_rate = RATE

    def __init__(self) -> None:
        self.rendered: list[str] = []

    def plan(self, text: str) -> SpeechPlan:
        return SpeechPlan(key=hashlib.sha256(text.encode()).hexdigest(), payload=text)

    def render(self, plan: SpeechPlan) -> np.ndarray:
        self.rendered.append(plan.payload)
        tone = np.where(np.arange(len(plan.payload) * TONE_PER_CHAR) % 2, 0.5, -0.5)
        silence = np.zeros(PADDING)
        return np.concatenate([silence, tone, silence]).astype(np.float32)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def narration(number: int, title: str, *utterances: Utterance) -> SectionNarration:
    return SectionNarration(number, Section(title), list(utterances))


def said(text: str, pause_ms: int = 0) -> Utterance:
    return Utterance(text, text, pause_ms)


def pause(ms: int) -> Utterance:
    return Utterance("", "", ms)


@pytest.fixture
def config(config, tmp_path):
    config["render"]["cache_dir"] = str(tmp_path / "cache")
    return config


@pytest.fixture
def work_dir(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def render(narrations, config, work_dir, engine=None, lines=None, voices=None):
    """render_book with a fake clock, and `voices` or one voice, `engine` (by default a
    ToneEngine). Returns the chapters and the Progress used."""
    voices = voices or OneVoice(engine or ToneEngine())
    lines = [] if lines is None else lines
    progress = Progress(
        total_words=sum(n.words() for n in narrations),
        total_sections=len(narrations),
        every_seconds=config["render"]["progress_every_seconds"],
        report=lines.append,
        clock=Clock(),
    )
    chapters = render_book(narrations, voices, config, work_dir, lines.append, progress)
    return chapters, progress


def samples(path) -> np.ndarray:
    data, rate = read_wav(path)
    assert rate == RATE
    return data


def kept(config) -> int:
    """Samples of edge silence trimming keeps on each side."""
    return RATE * config["render"]["edge_silence_keep_ms"] // 1000


def frames(ms: int) -> int:
    return RATE * ms // 1000


# render_book


def test_one_wav_per_narration(config, work_dir):
    narrations = [
        narration(1, "The Storm", pause(500), said("Rain.", 300)),
        narration(3, "The Calm", said("Sun.", 300)),
    ]
    chapters, _ = render(narrations, config, work_dir)
    assert [(c.title, c.path, c.sample_rate) for c in chapters] == [
        ("The Storm", work_dir / "001.wav", RATE),
        ("The Calm", work_dir / "002.wav", RATE),
    ]
    for chapter in chapters:
        with wave.open(str(chapter.path), "rb") as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)


def test_silences_are_the_configured_length(config, work_dir):
    [chapter], _ = render([narration(1, "Quiet", pause(250), pause(1000))], config, work_dir)
    audio = samples(chapter.path)
    assert len(audio) == frames(250) + frames(1000)
    assert not audio.any()


def test_sentences_are_trimmed_then_followed_by_their_pause(config, work_dir):
    [chapter], _ = render(
        [narration(1, "T", pause(500), said("Hi.", 300), said("Bye.", 700))], config, work_dir
    )
    audio = samples(chapter.path)
    hi = len("Hi.") * TONE_PER_CHAR + 2 * kept(config)
    bye = len("Bye.") * TONE_PER_CHAR + 2 * kept(config)
    assert len(audio) == frames(500) + hi + frames(300) + bye + frames(700)
    # The opening pause and trimmed padding are silent; the tone is where expected.
    start = frames(500) + kept(config)
    assert not audio[:start].any()
    assert np.all(np.abs(audio[start : start + 3 * TONE_PER_CHAR]) == to_pcm16(np.array([0.5]))[0])
    assert not audio[-(frames(700) + kept(config)) :].any()


def test_marks_say_where_each_utterance_starts(config, work_dir):
    [chapter], _ = render(
        [narration(1, "T", pause(500), said("Hi.", 300), said("Bye.", 700))], config, work_dir
    )
    hi = len("Hi.") * TONE_PER_CHAR + 2 * kept(config)
    bye = len("Bye.") * TONE_PER_CHAR + 2 * kept(config)
    starts = [0, frames(500), frames(500) + hi + frames(300)]
    assert chapter.marks == (*starts, starts[-1] + bye + frames(700))
    assert chapter.marks[-1] == len(samples(chapter.path))


def test_edge_silence_settings_change_the_trimming(config, work_dir):
    config["render"]["edge_silence_keep_ms"] = 20
    [chapter], _ = render([narration(1, "T", said("Hi."))], config, work_dir)
    assert len(samples(chapter.path)) == 3 * TONE_PER_CHAR + 2 * frames(20)

    config["render"]["edge_silence_db"] = -3.0  # louder than the fake voice ever gets
    [chapter], _ = render([narration(1, "T", said("Hi."))], config, work_dir)
    assert len(samples(chapter.path)) == 0


def test_a_second_run_takes_everything_from_the_cache(config, work_dir):
    narrations = [
        narration(1, "A", said("One.", 300), said("Two.", 300)),
        narration(2, "B", said("Three.")),
    ]
    first_engine = ToneEngine()
    [first, _], _ = render(narrations, config, work_dir, first_engine)
    assert first_engine.rendered == ["One.", "Two.", "Three."]
    first_audio = first.path.read_bytes()

    again = ToneEngine()
    [second, _], progress = render(narrations, config, work_dir, again)
    assert again.rendered == []
    assert second.path.read_bytes() == first_audio
    assert progress.summary() == (
        "Spoke 3 sentences (0 new, 3 already made in an earlier run) in less than a minute."
    )


def test_a_changed_sentence_is_the_only_one_rendered_again(config, work_dir):
    render([narration(1, "A", said("One."), said("Two."), said("Three."))], config, work_dir)
    engine = ToneEngine()
    _, progress = render(
        [narration(1, "A", said("One."), said("Two, revised."), said("Three."))],
        config,
        work_dir,
        engine,
    )
    assert engine.rendered == ["Two, revised."]
    assert progress.summary() == (
        "Spoke 3 sentences (1 new, 2 already made in an earlier run) in less than a minute."
    )


def test_a_repeated_sentence_is_rendered_once(config, work_dir):
    engine = ToneEngine()
    render([narration(1, "A", said("Yes."), said("No."), said("Yes."))], config, work_dir, engine)
    assert engine.rendered == ["Yes.", "No."]


def test_the_cache_lives_in_the_configured_folder(config, work_dir, tmp_path):
    engine = ToneEngine()
    render([narration(1, "A", said("Hello."))], config, work_dir, engine)
    key = engine.plan("Hello.").key
    assert (tmp_path / "cache" / "speech" / key[:2] / f"{key}.wav").is_file()


def test_partial_cache_files_are_never_used(config, work_dir, tmp_path):
    engine = ToneEngine()
    key = engine.plan("Hello.").key
    cache = SpeechCache(tmp_path / "cache" / "speech", RATE)
    folder = cache.path(key).parent
    folder.mkdir(parents=True)
    # Left behind by a run that crashed while writing: complete-looking, but unfinished.
    write_wav(folder / f"{key}.12345.part", np.ones(100, dtype=np.int16), RATE)
    render([narration(1, "A", said("Hello."))], config, work_dir, engine)
    assert engine.rendered == ["Hello."]


def test_progress_lines_come_from_the_render(config, work_dir):
    lines = []
    narrations = [narration(1, "A", said("One two.")), narration(2, "B", said("Three."))]
    _, progress = render(narrations, config, work_dir, lines=lines)
    assert lines == ["Section 1 of 2 done: A.", "Section 2 of 2 done: B."]
    assert progress.words_done == 3


def test_render_book_makes_its_own_progress(config, work_dir):
    lines = []
    render_book(
        [narration(1, "A", said("One."))], OneVoice(ToneEngine()), config, work_dir, lines.append
    )
    assert lines == ["Section 1 of 1 done: A."]


# More than one voice


class HalfRateTone(ToneEngine):
    """A second voice, at half the sample rate: the same tone, half as many samples."""

    sample_rate = RATE // 2

    def plan(self, text: str) -> SpeechPlan:
        return SpeechPlan(key=hashlib.sha256(f"half\n{text}".encode()).hexdigest(), payload=text)

    def render(self, plan: SpeechPlan) -> np.ndarray:
        return super().render(plan)[::2]


@dataclass
class ByLanguage:
    """Voices by the first part of a language tag; English for anything else."""

    engines: dict[str, object]

    @property
    def sample_rate(self) -> int:
        return max(e.sample_rate for e in self.engines.values())

    def for_language(self, lang):
        return self.engines.get(primary(lang), self.engines["en"])


def said_in(text: str, lang: str, pause_ms: int = 0) -> Utterance:
    return Utterance(text, text, pause_ms, lang)


@pytest.fixture
def two_voices():
    return ByLanguage({"en": ToneEngine(), "fr": HalfRateTone()})


def test_each_utterance_is_said_by_its_languages_voice(config, work_dir, two_voices):
    narrations = [narration(1, "T", said_in("Hello.", "en"), said_in("Bonjour.", "fr-CA"))]
    [chapter], _ = render(narrations, config, work_dir, voices=two_voices)
    assert two_voices.engines["en"].rendered == ["Hello."]
    assert two_voices.engines["fr"].rendered == ["Bonjour."]
    assert chapter.sample_rate == RATE
    with wave.open(str(chapter.path), "rb") as w:
        assert w.getframerate() == RATE


def test_a_voice_at_another_rate_is_resampled_and_the_marks_stay_exact(
    config, work_dir, two_voices
):
    narrations = [
        narration(1, "T", pause(500), said_in("Hi.", "en", 300), said_in("Salut.", "fr", 700))
    ]
    [chapter], _ = render(narrations, config, work_dir, voices=two_voices)
    hi = len("Hi.") * TONE_PER_CHAR + 2 * kept(config)
    # Trimmed at its own rate (half the samples), then twice as many at the book's.
    salut = 2 * (len("Salut.") * TONE_PER_CHAR // 2 + 2 * (kept(config) // 2))
    assert chapter.marks == (
        0,
        frames(500),
        frames(500) + hi + frames(300),
        frames(500) + hi + frames(300) + salut + frames(700),
    )
    audio = samples(chapter.path)
    assert len(audio) == chapter.marks[-1]
    # The French tone is heard where its mark says, at the book's rate.
    start = chapter.marks[2] + kept(config) + 4
    assert np.abs(audio[start : start + len("Salut.") * TONE_PER_CHAR - 8]).min() > 1000


def test_each_voice_caches_at_its_own_rate(config, work_dir, two_voices):
    narrations = [narration(1, "T", said_in("Hi.", "en"), said_in("Salut.", "fr"))]
    render(narrations, config, work_dir, voices=two_voices)
    cached = sorted((Path(config["render"]["cache_dir"]) / "speech").rglob("*.wav"))
    assert sorted(read_wav(path)[1] for path in cached) == [RATE // 2, RATE]
    again = ByLanguage({"en": ToneEngine(), "fr": HalfRateTone()})
    _, progress = render(narrations, config, work_dir, voices=again)
    assert again.engines["en"].rendered == again.engines["fr"].rendered == []
    assert progress.cached_sentences == 2


# The speech cache


def test_cache_round_trip(tmp_path):
    cache = SpeechCache(tmp_path, RATE)
    data = np.array([0, 1, -1, 32767, -32768], dtype=np.int16)
    assert cache.get("ab" + "0" * 62) is None
    cache.put("ab" + "0" * 62, data)
    assert np.array_equal(cache.get("ab" + "0" * 62), data)
    assert [p.name for p in (tmp_path / "ab").iterdir()] == ["ab" + "0" * 62 + ".wav"]


def test_cache_ignores_audio_at_another_sample_rate(tmp_path):
    SpeechCache(tmp_path, RATE).put("cd" * 32, np.ones(10, dtype=np.int16))
    assert SpeechCache(tmp_path, 24000).get("cd" * 32) is None


# WAV helpers


def test_to_pcm16_scales_and_clips():
    floats = np.array([-2.0, -1.0, 0.0, 0.5, 1.0, 3.0], dtype=np.float32)
    assert to_pcm16(floats).tolist() == [-32767, -32767, 0, 16383, 32767, 32767]
    assert to_pcm16(floats).dtype == np.int16


def test_trim_edges_keeps_a_margin():
    quiet, loud = np.zeros(100, dtype=np.int16), np.full(10, 1000, dtype=np.int16)
    audio = np.concatenate([quiet, loud, quiet])
    trimmed = trim_edges(audio, sample_rate=1000, threshold_db=-50.0, keep_ms=20)
    assert len(trimmed) == 20 + 10 + 20


def test_trim_edges_margin_stops_at_the_ends():
    audio = np.concatenate([np.zeros(5), np.full(10, 1000), np.zeros(5)]).astype(np.int16)
    assert len(trim_edges(audio, 1000, -50.0, keep_ms=20)) == len(audio)


def test_trim_edges_counts_quiet_sound_as_silence():
    hum = np.full(100, 50, dtype=np.int16)  # about -56 dB
    assert len(trim_edges(hum, 1000, -50.0, 20)) == 0
    assert len(trim_edges(hum, 1000, -60.0, 20)) == 100


def test_trim_edges_of_silence_is_empty():
    assert len(trim_edges(np.zeros(500, dtype=np.int16), 1000, -50.0, 60)) == 0
    assert len(trim_edges(np.zeros(0, dtype=np.int16), 1000, -50.0, 60)) == 0


def test_wav_writer_appends_audio_and_silence(tmp_path):
    path = tmp_path / "out.wav"
    with WavWriter(path, RATE) as out:
        out.audio(np.full(10, 7, dtype=np.int16))
        out.silence(5)
        out.audio(np.full(3, -7, dtype=np.int16))
        assert out.frames == 10 + frames(5) + 3
    assert samples(path).tolist() == [7] * 10 + [0] * frames(5) + [-7] * 3


def test_read_wav_rejects_other_formats(tmp_path):
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(8))
    with pytest.raises(ValueError, match="not mono 16-bit"):
        read_wav(path)
