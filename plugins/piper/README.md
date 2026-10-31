# Piper voices for Dogear

This adds [Piper](https://github.com/OHF-Voice/piper1-gpl) to
[Dogear](https://github.com/18hrshift/dogear-app): eight more voices, quick on an ordinary
computer. Dogear's own voice, Kokoro, stays the one it uses unless you choose another.

It is a separate package because Piper is licensed under the GPL, and Dogear isn't (see
"License" below). Dogear finds it by itself once it is installed.

## Install it

The Dogear installers and the Docker image can't take plugins yet. This is for Dogear
installed with Python and [uv](https://docs.astral.sh/uv/), as Dogear's README describes.
Install Dogear and Piper together with one command:

```bash
uv tool install git+https://github.com/18hrshift/dogear-app \
  --with "dogear-piper @ git+https://github.com/18hrshift/dogear-app#subdirectory=plugins/piper"
```

If Dogear is installed already, the same command installs it again with Piper. Then
check that Piper's voices are there:

```bash
dogear voices
```

The list now has a part for Piper after Kokoro's. If it says instead that Piper is
installed but can't be used, it says why.

From a copy of Dogear's repository, for working on it, run `uv run --with-editable
./plugins/piper dogear voices` instead, or install it into Dogear's environment with
`uv pip install -e plugins/piper`.

Don't use `pip install dogear`: that name on PyPI belongs to a different project.

## Use it

Choose a Piper voice the way you choose any voice: by its name.

```bash
dogear make mybook.epub --voice en_US-ljspeech-high
dogear say "Utterson met Lanyon." -o test.wav --voice en_GB-cori-high
```

If another engine ever has a voice of the same name, put `piper:` first, as in
`--voice piper:en_US-ljspeech-high`. The first time a voice is used, Dogear downloads it
(28 to 115 MB) and checks it. To download it ahead of time, before going offline:

```bash
dogear download --voice en_US-ljspeech-high
```

`dogear download --check` checks Piper's files on disk along with Kokoro's, and downloads
any damaged one again.

To use a Piper voice every time, put this in your settings file:

```toml
[voice]
engine = "piper"
voice = "en_US-ljspeech-high"

[piper]
threads = 0  # CPU threads to use while speaking; 0 means all of them
```

On the web page (`dogear serve`), the voice list has Piper's voices in a group of their
own, after Kokoro's.

What's different from Kokoro:

- **Books and passages in French, German, and Spanish.** Choose one of those voices to
  read a whole book in its language, or give the language a voice in your settings so
  it reads the passages in that language in a book Kokoro reads (Dogear's README, "Books
  with more than one language"). Dogear leaves numbers and abbreviations in those
  languages to Piper, whose eSpeak NG reads them by the language's own rules.
- **Spellings in your pronunciation files work; phoneme entries don't.** Phoneme entries
  are written in Kokoro's symbols. With a Piper voice, Dogear skips them and says so.
- **`dogear words` doesn't apply.** It lists the words missing from Kokoro's
  dictionaries. Listen to a book's names with `dogear say` instead.

## The voices

Best first: Piper's own quality levels, high before medium. Each was trained on
recordings anyone may use, and from scratch, or (John) from another voice here.

1. `en_US-ljspeech-high`: American English, female, high quality. From LJ Speech, public
   domain.
2. `en_GB-cori-high`: British English, female, high quality. From LibriVox recordings,
   public domain.
3. `en_US-kristin-medium`: American English, female. From LibriVox recordings, public
   domain.
4. `en_US-norman-medium`: American English, male. From LibriVox recordings, public domain.
5. `en_US-john-medium`: American English, male. From LibriVox recordings, public domain,
   fine-tuned from Kristin.
6. `fr_FR-mls-medium`: French. From Multilingual LibriSpeech (French), CC BY 4.0.
7. `de_DE-mls-medium`: German. From Multilingual LibriSpeech (German), CC BY 4.0.
8. `es_ES-carlfm-x_low`: Spanish (Spain), lowest quality. From carlfm01's recordings,
   public domain.

The voices were trained by the Piper project and its contributors (the English ones by
Bryce Beattie) and are published in
[rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices). Multilingual
LibriSpeech is by Vineel Pratap and others (Facebook AI), CC BY 4.0
([openslr.org/94](https://openslr.org/94/)).

Many well-known Piper voices are left out on purpose, including `en_US-lessac` and the
voices fine-tuned from it: its recordings are licensed for research only. Dogear's
[`docs/voice-engines.md`](../../docs/voice-engines.md) explains the choice, and
`src/dogear_piper/catalog.toml` pins every file by its SHA-256.

## License

GPL-3.0-or-later ([LICENSE](LICENSE)). It uses
[piper-tts](https://pypi.org/project/piper-tts/) (GPL-3.0-or-later), which has eSpeak NG
(GPL-3.0-or-later) built in. Dogear itself is Apache-2.0 and never imports this package.
The voices have their own licenses, listed above.

## Working on it

```bash
uv run --frozen --with-editable ./plugins/piper pytest plugins/piper/tests
```

Run that from Dogear's repository folder. The tests replace onnxruntime with a stand-in,
so they need no voice. One test speaks with the real voice: download it first with
`dogear download --voice en_US-ljspeech-high`, and set `DOGEAR_PIPER_TEST_MODELS` to the
models folder it went to (`models.dir` in your settings, or the usual one: on Linux,
`~/.local/share/dogear/models`). Without it, that test is skipped.
