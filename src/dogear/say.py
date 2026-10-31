"""`dogear say`: speak a short text into a WAV file, to hear how words come out.

Made for trying pronunciation entries: change an entry, say the sentence again, listen.
The text goes the way a book's does, through sentence splitting, speech normalization
(with the user's pronunciation files), the configured voice, and the renderer with its
sentence cache, so what you hear is what `dogear make` will say.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dogear.config import Config
from dogear.document import Paragraph, Section
from dogear.errors import DogearError
from dogear.narration import SectionNarration, Utterance
from dogear.paths import cache_dir
from dogear.render import render_book
from dogear.render.progress import Report
from dogear.render.voices import OneVoice
from dogear.speech.normalize import SpeechRules, normalize_for_speech
from dogear.speech.sentences import SentenceRules, split_sentences
from dogear.voice import make_engine
from dogear.voice.engines import configured_voice

_TITLE = "Said aloud"  # the one section's title; never spoken


@dataclass(frozen=True)
class Said:
    spoken: str  # one sentence, as the voice was given it
    voice_input: str  # what the engine read for it: for Kokoro, the phonemes


def say_text(text: str, output: Path, config: Config, report: Report) -> list[Said]:
    """Speak `text` into the WAV file `output`, the way `dogear make` would."""
    _check_output(output)
    # Said in the voice's own language: a French voice's text isn't read as English.
    utterances = spoken_sentences(text, config, configured_voice(config).voice.language)
    engine = make_engine(config, report)
    narration = SectionNarration(1, Section(_TITLE, blocks=[Paragraph(text.strip())]), utterances)
    work_root = cache_dir(config) / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_root) as work:
        # Progress lines ("Section 1 of 1 done") are noise for a few words.
        [chapter] = render_book(
            [narration], OneVoice(engine), config, Path(work), lambda line: None
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(chapter.path, output)
    return [Said(u.spoken, engine.plan(u.spoken).payload) for u in utterances]


def spoken_sentences(text: str, config: Config, lang: str | None = None) -> list[Utterance]:
    """The text, in `lang`, as sentences the voice says, with the usual pause after each."""
    rules = SpeechRules.from_config(config)
    split = SentenceRules.from_config(config)
    pause = config["narration"]["pauses"]["sentence_ms"]
    utterances = []
    for sentence in split_sentences(text, split):
        spoken = normalize_for_speech(sentence, rules, lang)
        if spoken:
            utterances.append(Utterance(sentence, spoken, pause, lang))
    if not utterances:
        raise DogearError(
            "There are no words to say in that text.",
            hint='Put the words in quotes, such as: dogear say "Utterson met Lanyon." -o test.wav',
        )
    return utterances


def _check_output(output: Path) -> None:
    if output.suffix.lower() != ".wav":
        raise DogearError(
            f"{output} doesn't end in .wav, but `dogear say` saves WAV audio.",
            hint=f"Use a name ending in .wav, such as {output.stem or 'test'}.wav.",
        )
    if output.is_dir():
        raise DogearError(
            f"{output} is a folder.", hint="Give a file name ending in .wav, such as test.wav."
        )
