# Voice engines

Dogear speaks through voice engines. Kokoro comes with Dogear. Others are plugins: Python
packages installed beside Dogear, which Dogear finds by themselves. This page records why
Piper is the first plugin, and why the first cloud plugin speaks OpenAI's speech API, then
describes the interface for anyone writing one.

## Decision: Piper is the first plugin (2026-10-30)

The roadmap's candidates, checked again on 2026-10-30 against the ground rules: CPU first,
offline, open weights, pip-installable, voice files that can be pinned by SHA-256, and no
non-commercial or custom-licensed voice as a default.

| Engine | License | On a laptop CPU | Languages | Verdict |
|---|---|---|---|---|
| Piper ([piper-tts] 1.8.0, 2026-09-04) | GPL-3.0-or-later code; voices tagged MIT, each trained on its own data | Fast: about 6 times faster than real time here (below) | 178 voices in 53 languages | **First plugin**, as a separate GPL package |
| OmniVoice | Apache-2.0 code, but CC-BY-NC weights ([model card][omnivoice]) | Speed figures are for GPUs only | 600+ | Ruled out: non-commercial weights |
| Qwen3-TTS | Apache-2.0 code and weights ([model card][qwen]) | 0.6B and 1.7B models; about 3 times slower than real time on 4 x86 cores through a third-party port ([test][qwen-cpu]) | 10 | Too slow on a CPU for now |
| Chatterbox | MIT ([chatterbox]) | Turbo measured at 13.4 CPU core-hours per hour of audio ([Picovoice][picovoice]); needs PyTorch | 23 (multilingual model) | Too slow on a CPU; it also watermarks every output |
| Pocket TTS (Kyutai) | MIT code, CC-BY-4.0 weights ([repository][pocket]) | About 0.37 core-hours per hour of audio ([Picovoice][picovoice]) | 6 or 7 | The best candidate for a second plugin; needs PyTorch, and its main weights are gated |

Also checked: Supertonic 3 (OpenRAIL-M weights, and its repository was archived on
2026-09-09: [supertonic]), NeuTTS (Air is English only and depends on the GPL
`phonemizer` package: [neutts]), KaniTTS (its codec is under NVIDIA's own model license:
[kani]), and sherpa-onnx 2.0 (Apache-2.0, and since 2.0.0 free of GPL code, but without
eSpeak NG it drops words its lexicon lacks: [sherpa-pr], [sherpa-oov]). Kokoro has had no
new weights since 2025.

Why Piper. It is the fastest of these on a CPU: on the Intel i7-8700K (6 cores) Dogear's
README measures with, `en_US-ljspeech-high` spoke a 14-second passage in 2.1 seconds, and
Picovoice measured it at 0.35 core-hours per hour of audio against Kokoro's 1.28. It has
wheels for Windows, macOS (Intel and Apple silicon), and Linux, and depends only on
onnxruntime. Its voices are single files on Hugging Face, pinned here to one revision of
[rhasspy/piper-voices] by URL and SHA-256. It is actively maintained (commits through
October 2026 in [piper1-gpl]).

Why a separate package. piper-tts is GPL-3.0-or-later, with eSpeak NG built into it, so it
can't be part of Dogear's Apache-2.0 core. The plugin, `plugins/piper/` (`dogear-piper`),
is GPL-3.0-or-later. Dogear never imports it, and the installers and the Docker image
carry Kokoro only.

Which voices. Piper's voices are each trained on their own recordings, under their own
terms. The plugin offers only voices trained from scratch on public-domain or CC BY data
(or, for `en_US-john-medium`, fine-tuned from another of them): five English (American
and British), one French, one German, and one Spanish. Left out:

- Voices from data licensed for research or non-commercial use only, such as
  `en_US-ryan` (CC BY-NC-SA) and `en_US-hfc_female` and `_male` (CC BY-NC-SA).
- `en_US-lessac`, Piper's best-known voice. Its [Blizzard 2013 recordings][lessac] are
  licensed only for research into speech technology. Many other Piper voices, including
  `en_GB-alba`, `fr_FR-siwis`, `de_DE-thorsten`, and `es_ES-davefx`, were fine-tuned from
  it. Their own recordings are open, but whether a fine-tuned voice carries the base
  voice's terms isn't settled, so they are left out until it is.
- Voices whose model card says only "See URL".

`plugins/piper/src/dogear_piper/catalog.toml` lists each voice with its data and license;
the plugin's README credits them.

Open questions:

- The ground rules ask for weights under an OSI license. Piper's voice repository is
  tagged MIT, but the recordings the chosen voices learned from are public domain or
  CC BY 4.0, which are open but not OSI licenses. CC BY asks for credit, which the plugin's
  README gives.
- The French and German voices were trained on many speakers, and Piper speaks with the
  first. Nobody has listened to them closely yet. Dogear reads French, German, and
  Spanish books and passages with them (below).
- Dogear already runs the eSpeak NG program for Kokoro. Running Piper's voices from the
  Apache-2.0 core, with eSpeak NG as a separate program and onnxruntime, would avoid the
  GPL package, if the program's phonemes match the ones piper-tts makes. Not tried yet.

[piper-tts]: https://pypi.org/project/piper-tts/
[piper1-gpl]: https://github.com/OHF-Voice/piper1-gpl
[rhasspy/piper-voices]: https://huggingface.co/rhasspy/piper-voices
[lessac]: https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html
[omnivoice]: https://huggingface.co/k2-fsa/OmniVoice
[qwen]: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice
[qwen-cpu]: https://ocdevel.com/blog/20260711-quantized-cpu-tts
[chatterbox]: https://github.com/resemble-ai/chatterbox
[picovoice]: https://picovoice.ai/blog/on-device-tts/
[pocket]: https://github.com/kyutai-labs/pocket-tts
[supertonic]: https://github.com/supertone-inc/supertonic
[neutts]: https://github.com/neuphonic/neutts
[kani]: https://github.com/nineninesix-ai/kani-tts
[sherpa-pr]: https://github.com/k2-fsa/sherpa-onnx/pull/4055
[sherpa-oov]: https://github.com/k2-fsa/sherpa-onnx/issues/4139

## Decision: an OpenAI-compatible speech engine is the first cloud plugin (2026-10-30)

The roadmap asks for cloud voices as opt-in plugins. Rather than one plugin per company,
the first speaks the request shape most services and local servers share: OpenAI's
`POST /v1/audio/speech`, a JSON body with `model`, `voice`, `input` (at most 4096
characters at OpenAI), `response_format` (`mp3`, `opus`, `aac`, `flac`, `wav`, or `pcm`),
`speed` (0.25 to 4.0), and, for `gpt-4o-mini-tts`, `instructions`. The answer is the audio
itself; `pcm` is 24 kHz, 16-bit, little-endian samples with no header ([reference],
[guide]). The plugin asks for WAV, which says its own sample rate, so Dogear can check it.

Checked on 2026-10-30:

- OpenAI says `tts-1`, `tts-1-hd`, and `gpt-4o-mini-tts` shut down on January 6, 2027, and
  that their replacement works only through its Realtime API, not this endpoint
  ([guide]). The plugin's default is OpenAI all the same, as the shape everyone copies;
  its README says so.
- The same requests work with [OpenRouter] (`mp3` or `pcm` only), Groq's Orpheus voices
  ([groq]: `wav` only, at most 200 characters a request), and servers run at home:
  [Kokoro-FastAPI] (port 8880), [Speaches] (port 8000, Kokoro and Piper models), and
  [LocalAI]. Each has its own voice names, so the voices are a setting.
- Python's own urllib is enough for the requests, so the plugin adds no HTTP library.

[reference]: https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create
[guide]: https://developers.openai.com/api/docs/guides/text-to-speech
[OpenRouter]: https://openrouter.ai/docs/guides/overview/multimodal/tts
[groq]: https://console.groq.com/docs/text-to-speech/orpheus
[Kokoro-FastAPI]: https://github.com/remsky/Kokoro-FastAPI
[Speaches]: https://speaches.ai/usage/text-to-speech/
[LocalAI]: https://localai.io/features/text-to-audio/

## A voice for each language (2026-10-30)

Every utterance knows its language: the one the book marks on its paragraph or chapter,
else the book's, else one language detection guessed. Its voice is, in order: the voice
`[voice.languages]` gives the most specific tag that matches ("fr-CA", then "fr"); the
main voice, if it speaks the language; with `[voice] other_languages = "best"`, the best
installed voice for it (the main voice's engine first, then the others as `dogear voices`
lists them, each engine's voices best first, a voice for the exact tag before one for
the language); otherwise the main voice. `src/dogear/voice/language_voices.py` does the
choosing, with nothing loaded; `voices_for_run` lists every voice a run's settings may
use, before any book is read. Every voice it chooses among comes from
`Engine.voices_in(config)`, so for an engine with `configured_voices`, such as the speech
service plugin, the voices and languages are the ones its settings table gives.

`dogear make` works out which voices the book needs, loads each once, through
`dogear.voice.make_voice` like the main voice, and gives the renderer
`voice.book_voices.BookVoices`. The book's audio is written at the highest sample rate
among them (Kokoro's 24 kHz over Piper's 22.05 kHz), so no voice loses sound, and the
others are resampled as each sentence is written (`render/resample.py`, a Kaiser-windowed
sinc in numpy: about 40 ms for a 5-second sentence). The cache keeps each sentence at its
own voice's rate, under its own engine's and voice's key. Marks are counted in the
section's samples as it is written, so read-along syncs stay exact.

Why the default is `other_languages = "main"`: only Piper has voices in other languages
so far, and it isn't installed with Dogear; another engine's voice sounds different,
mid-chapter; and books are sometimes marked with the wrong language. A stray change of
voice is worse than an English voice reading a French phrase, so a second voice reads
only when the settings ask for one.

What an engine is given. Dogear's own rewrites (abbreviations, numbers, currency, times,
units, roman numerals) are English. Text in another language reaches its voice as
written, tidied only (plain quotes, dashes as pauses, invisible characters removed), so
an engine for other languages must read numbers and symbols itself, as Piper's eSpeak NG
does ("3 500" as "trois mille cinq cents", "1,5" as "un virgule cinq"). Spellings in
pronunciation files apply to English text unless an entry lists its languages.

Open questions: a second voice is often quieter or louder than the first, since Dogear
evens out the volume once per chapter, not per voice (in a test chapter, Piper's French
was 3 to 9 dB quieter than Kokoro). Nobody who speaks French, German, or Spanish has
listened to Dogear's words for those languages yet.

## Decision: py3langid for languages a book doesn't mark (2026-10-30)

Many books mark no languages at all, so `[language_detection]` (off by default) guesses
them. Candidates checked on 2026-10-30, on PyPI:

| Library | License | Size | Short text | Verdict |
|---|---|---|---|---|
| py3langid 0.4.0 (2026-09-02) | BSD-3-Clause | 4.6 MB wheel, pure Python with numpy | Length-calibrated confidences: a short phrase stays unsure | **Chosen** |
| lingua-language-detector 2.2.0 (2026-03-09) | Apache-2.0 | 170 MB wheel for each platform | The most accurate on short text | Too big: it would more than double the installers |
| langdetect 1.0.9 (2021) | MIT on PyPI (Apache-2.0 upstream) | 1 MB | Poor; gives different answers unless seeded | Unmaintained and not deterministic |
| eld 1.0.6 (2023) | Apache-2.0 | 5.4 MB | Good, but its scores aren't probabilities | No confidence to set a threshold on |
| fastText lid.176 (fast-langdetect 1.1.0) | MIT code, CC BY-SA 3.0 weights | 1 MB model, downloaded | Good | Share-alike weights, and the wrapper downloads the model at run time |

Why py3langid ([repository][py3langid], [changelog][py3langid-history]). Its 0.4.0 model
(139 languages, trained on Wikipedia leads, Leipzig news, CC-100, Tatoeba, GlotCC, and
Glot500 sentences, per its [TRAINING.md][py3langid-training]) ships inside the package,
so it runs offline and is in the installers. It gives the same answer every time, takes
about 0.1 ms a sentence, and loads in a third of a second (85 MB of memory), only when
detection is on. Its confidences are normalized by length, so asked about every language
it knows, a short or mixed sentence comes out unsure instead of forced into English or
French. Told to choose among English and French only, it called a German sentence English
(0.82); asked about all of them, it said German. Dogear therefore asks about all of them
and keeps a guess only if it is a language with a voice.

How careful it is: every block (paragraph, list, note, table, heading) with at least
`min_letters` letters (30) is checked whole, and a guess is used at `min_confidence`
(0.9) or more; a paragraph or note of `sentence_check_min_letters` (300) or more is also
checked a sentence at a time. A language the book marks on a passage, or on a chapter
in another language than the book's, is never second-guessed. On Jekyll and Hyde, with
voices for English, French, German, and Spanish, none of its 331 blocks, nor of the 662
sentences of its long paragraphs, with 30 letters or more was guessed to be in another
language (14 came out unsure and stayed English). Five French sentences written for the
test, of 39 to 66 letters, came out French at 0.99 or more; two of 25 letters, at 0.75 and
0.98. The package
states no license for its model apart from the package's own, BSD-3-Clause, the same
footing as spaCy's model.

[py3langid]: https://github.com/adbar/py3langid
[py3langid-history]: https://github.com/adbar/py3langid/blob/master/HISTORY.rst
[py3langid-training]: https://github.com/adbar/py3langid/blob/master/TRAINING.md

## Writing a voice engine plugin

A plugin is a Python package that registers one object under the entry point group
`dogear.engines`. Kokoro registers the same way, in Dogear's own `pyproject.toml`, and
`plugins/piper/` is a complete example.

```toml
[project.entry-points."dogear.engines"]
myengine = "dogear_myengine.plugin:ENGINE"
```

The entry point's name is the engine's name: lowercase letters, digits, and underscores,
starting with a letter, and not the name of one of Dogear's own settings tables (such as
`pdf` or `voice`). It is what `[voice] engine` and `--voice myengine:NAME` take, and it
names the engine's settings table. If two packages register the same name, the first
wins (Dogear's own always does) and the other is reported as unusable.

### The engine object

Defined in `src/dogear/voice/engines.py` as `EnginePlugin`:

| Part | What it is |
|---|---|
| `name` | The entry point's name. |
| `title` | The engine's name for people, such as "Piper". |
| `license` | An SPDX expression for the engine's code and voices, shown by `dogear voices`. |
| `settings` | TOML text: the defaults of the engine's own table, such as `threads = 0`. Dogear adds it to its defaults as `[<name>]`, so a settings file can change it, and an unknown key or a value of the wrong type is rejected as for Dogear's own settings. Use `""` for none. |
| `reads_misaki_phonemes` | True only if the engine reads misaki's English phonemes, as Kokoro does. Then phoneme entries in pronunciation files are given to it, and `dogear words` applies. Otherwise Dogear skips phoneme entries, saying so once and why. Entries that give a spelling apply to every engine: Dogear rewrites the text before any engine sees it. |
| `about_voices` | A sentence `dogear voices` says before the engine's voices: how they are ordered and what any grade means. |
| `voices()` | Every voice, best first, as `Voice` objects. |
| `prepare(config, voice, report)` | Make sure every file the voice needs is on disk and checked, downloading whatever is missing. `dogear download` calls it. |
| `engine(config, voice, pronunciations, report)` | Prepare the voice, then load it: return a `VoiceEngine`. |
| `pace(config, voice)` | The voice's sample rate and about how many words a minute it says at `config["voice"]["speed"]`, as a `VoicePace`, with nothing loaded or downloaded. `dogear make` uses it to work out the disk space a book needs before speaking it, so a generous estimate is better than a low one. Measure it on a real book with your engine's pauses. |
| `sends_text_to(config, voice)` | Where the voice sends the text it speaks, with these settings: `None` if the text stays on this computer, or else the name of the computer it goes to, such as `"api.openai.com"`. A server on this computer counts as staying here. Answer from the settings alone: nothing may be sent to find out. See "Voices that send text off this computer" below. |

Two more methods are optional:

| Part | What it is |
|---|---|
| `configured_voices(config)` | For an engine whose voices are set in its settings table, as a speech service's are: the voices with these settings. `voices()` then gives the voices of its default settings. Dogear lists and finds voices with this whenever it has the settings, and checks them as it checks `voices()`. Nothing may be sent to list them. |
| `pinned_files(config)` | Every file the engine downloads, for any of its voices, as `RemoteFile`s with where each lives on disk. `dogear download --check` hashes each one that is on disk against its pin, as it does Kokoro's, and downloads a damaged one again. Leave it out if the engine downloads nothing. |

A `Voice` has a `name` (unique within the engine, with no colon or spaces), a `language`
(a BCP 47 tag such as `en-GB`, by which Dogear chooses a voice for each language), a
`description` (the language in words, with any accent: "British English"), a `gender`
("female", "male", or "" if not known), and a `quality` (the engine's own grade in words,
such as "grade A" or "high quality", or "").

`config` is Dogear's loaded settings; read your own as `config["<name>"]` and the pace as
`config["voice"]["speed"]` (1.0 is the voice's natural pace). `report` takes one plain
sentence at a time, for progress a screen reader can follow. `pronunciations` is the
user's `PronunciationDictionary`.

Importing the module the entry point names must be quick and must not fail for want of
a model: every Dogear command lists the engines when it loads its settings. Import your
engine's heavy libraries inside `engine()`.

### Speaking: `VoiceEngine`

Defined in `src/dogear/voice/engine.py`. The renderer gives it one sentence at a time.

- `sample_rate`: the audio's rate in Hz, a whole number. It may differ from other voices': a
  book read by two voices is written at the higher rate, and the other is resampled.
- `plan(text)`: quick, no audio. Returns a `SpeechPlan` with the engine's input in
  `payload` (for Kokoro, its phonemes; `dogear say --phonemes` prints it) and a cache
  `key`. Equal keys must mean identical audio, so the key must cover the model files (their
  SHA-256), every setting that changes the sound, and the payload. Dogear adds the
  engine's name and the voice's name to every key itself, so two engines or two voices
  never share a cached sentence.
- `render(plan)`: the audio for the plan's payload, as mono float32 samples from -1 to 1.
  Use only `plan.payload`, not `plan.key`. Leave the level as the model makes it: Dogear
  evens out the volume across each chapter.

Dogear checks what each method gives: a plan that isn't a `SpeechPlan` of two strings, or
audio that isn't one channel of numbers (NaN or infinite samples included), stops the run
with an error that names your engine and says to report it to you.

Book text is untrusted. If your engine reads markup of its own in text (Piper reads
`[[ ]]` as phonemes), give it the text another way.

### Downloads

Use Dogear's pinned downloads, from `src/dogear/voice/downloads.py`: describe each file as
a `RemoteFile` (its URL, SHA-256, size in bytes, and where it goes, inside
`dogear.paths.models_dir(config)` in a folder named after the engine), then call
`ensure_downloaded(files, models_dir(config), config, report)`. It downloads only what's
missing, checks each file against its SHA-256 before using it, says what it's doing in
plain sentences, and lets one Dogear at a time download into the folder. Pin URLs to a
fixed revision, so a file can't change under its pin. List every voice's files in
`pinned_files` as well, so `dogear download --check` checks them.

### Voices that send text off this computer

Dogear is offline by default, so an engine that sends the text it speaks to another
computer (a cloud voice) says so in `sends_text_to`, and Dogear's core does the rest. A
run whose voice sends text off this computer is refused, before anything is sent and
before the book is read, unless the user allows it: `allow_cloud = true` under `[voice]`,
or `--allow-cloud` for one run of `dogear make` or `dogear say`. When it is allowed,
Dogear says once, in plain sentences, which computer the text goes to, that the service's
own terms apply, and how to stop, before the engine is made. `dogear voices` marks these
voices, and the web page lists them only when the setting allows them, marked the same
way, and shows the same warning before a book with one starts.

So an engine needs no opt-in or warning of its own. It must answer honestly from its
settings: a server at `localhost`, `127.0.0.1`, or `[::1]` keeps the text here, and any
other address doesn't, including one on the same network. It must not send anything
before `engine()` is called, and must not follow a redirect to somewhere else. A secret
such as an API key belongs in an environment variable that a setting names, never in a
settings file, a cache key, or a message. `plugins/openai_speech/` is a complete example.

### Errors

Raise Dogear's errors, from `src/dogear/errors.py` (usually `VoiceError`), with a message
and a hint that says what to do next. Dogear shows them as they are. Any other error from
your code becomes a `VoiceError` that names your package and points to its `Issues` (or
`Homepage`) project URL, and `-v` shows the traceback.

An engine that fails to import, or doesn't give what this page describes, is set aside:
`dogear voices` names it and says why in a sentence, and every other engine keeps
working.

### Testing

Test with stand-ins for the model, as Dogear and the Piper plugin do; CI must never need a
GPU or an API key. Dogear's own tests see only Kokoro, whatever is installed where they
run, and install stand-in engines when they need others (`tests/engine_fakes.py`). Keep
one optional test with the real voice that skips when its files aren't there.

### Licenses

Dogear's core is Apache-2.0 and never imports a plugin, so a plugin may use any license.
An engine built on GPL code is GPL itself and ships as its own package, as Piper does.
Never make a non-commercial or custom-licensed voice something Dogear chooses for the
user.
