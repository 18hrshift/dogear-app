"""Speaking a narrated book: one WAV per section, built sentence by sentence.

Each sentence is said by the voice for its language (`voices.Voices`), planned by that
voice's engine, taken from the speech cache if it was made before, and rendered
otherwise. The cache keeps each sentence at its voice's own sample rate; one made at
another rate than the book's is resampled as it is written. Sentences and the pauses
between them are written straight to the section's WAV file, so memory use doesn't grow
with the book. Where each utterance starts is recorded as it is written, in samples of
the section's one rate, so talking books can sync text to it.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from dogear.config import Config
from dogear.narration import SectionNarration
from dogear.output import ChapterAudio
from dogear.paths import cache_dir
from dogear.render.cache import SpeechCache
from dogear.render.progress import Progress, Report
from dogear.render.resample import resample
from dogear.render.voices import Voices
from dogear.render.wav import WavWriter, to_pcm16, trim_edges
from dogear.voice.engine import VoiceEngine


def render_book(
    narrations: list[SectionNarration],
    voices: Voices,
    config: Config,
    work_dir: Path,
    report: Report,
    progress: Progress | None = None,
) -> list[ChapterAudio]:
    """One WAV per narrated section in `work_dir`, in order, at `voices.sample_rate`."""
    settings = config["render"]
    rate = voices.sample_rate
    caches: dict[int, SpeechCache] = {}  # one per voice sample rate
    progress = progress or Progress(
        total_words=sum(n.words() for n in narrations),
        total_sections=len(narrations),
        every_seconds=settings["progress_every_seconds"],
        report=report,
    )
    chapters = []
    for index, narration in enumerate(narrations, start=1):
        path = work_dir / f"{index:03d}.wav"
        title = narration.section.title
        marks = []
        with WavWriter(path, rate) as out:
            for utterance in narration.utterances:
                marks.append(out.frames)
                if utterance.spoken:
                    engine = voices.for_language(utterance.lang)
                    if engine.sample_rate not in caches:
                        root = cache_dir(config) / "speech"
                        caches[engine.sample_rate] = SpeechCache(root, engine.sample_rate)
                    samples = _speak(utterance.spoken, engine, caches[engine.sample_rate], progress)
                    trimmed = trim_edges(
                        samples,
                        engine.sample_rate,
                        settings["edge_silence_db"],
                        settings["edge_silence_keep_ms"],
                    )
                    out.audio(_at_rate(trimmed, engine.sample_rate, rate))
                    progress.check_in(index, title)
                out.silence(utterance.pause_ms)
            marks.append(out.frames)
        chapters.append(ChapterAudio(title, path, tuple(marks), rate))
        progress.section_done(index, title)
    return chapters


def _speak(text: str, engine: VoiceEngine, cache: SpeechCache, progress: Progress) -> np.ndarray:
    plan = engine.plan(text)
    words = len(text.split())
    cached = cache.get(plan.key)
    if cached is not None:
        progress.sentence(words, cached=True)
        return cached
    started = time.monotonic()
    samples = to_pcm16(engine.render(plan))
    cache.put(plan.key, samples)
    progress.sentence(words, cached=False, seconds=time.monotonic() - started)
    return samples


def _at_rate(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """16-bit `samples` at `to_rate`."""
    if from_rate == to_rate or not len(samples):
        return samples
    moved = resample(samples, from_rate, to_rate)
    return np.clip(np.rint(moved), -32768, 32767).astype(np.int16)
