# AGENTS.md

Rules for coding agents (Claude Code, Codex, OpenCode, Cursor, etc.) and humans working on Dogear.

## What this is

Dogear is an open talking-book maker: books in, navigable audio out, built first for blind
and low-vision listeners. Read [`ROADMAP.md`](ROADMAP.md) before starting work. It defines
each phase and what "done" means.

**Current state:** Phase 4 (more voices and languages). `dogear make` reads an EPUB, PDF,
Word (DOCX), HTML, Markdown, or Kindle book into a structured document, narrates it with
spoken structure, speaks it on the CPU with a voice engine (Kokoro, built in, or a plugin
such as Piper in `plugins/piper/`, or a speech service, opt-in, through
`plugins/openai_speech/`), with a voice for each language if the settings give
one (`[voice.languages]`), and packages an M4B, an MP3 folder, an EPUB 3 talking
book, and/or a DAISY 2.02 talking book. `dogear serve` is a local web page that does the
same from a browser, running `dogear` itself as a child process for each book. Standalone
installers (`packaging/`) and a Docker image (`Dockerfile`, `docker/`) carry everything,
the Kokoro voice included.

## Working here

```bash
uv sync             # install, including dev tools
bash test.sh        # ruff check + ruff format check + pytest; must pass before every merge
uv run dogear --help
```

Some tests need ffmpeg, Tesseract, or eSpeak NG and skip without them. CI installs all three
and sets `DOGEAR_REQUIRE_TOOLS=ffmpeg,ffprobe,tesseract,espeak-ng`, which turns those skips
into failures. Unit tests never load the real voice; a separate CI job (`voice`) downloads
it (cached between runs) and makes a short audiobook on each platform.

The Piper plugin has its own tests, run with the plugin installed beside Dogear (the
`piper` CI job runs them, then makes a short audiobook with a real Piper voice):

```bash
uv run --frozen --with-editable ./plugins/piper pytest plugins/piper/tests
```

So does the speech service plugin (the `openai-speech` CI job), against a fake service on
this computer (`plugins/openai_speech/tests/fake_service.py`): no network and no key.

```bash
uv run --frozen --with-editable ./plugins/openai_speech pytest plugins/openai_speech/tests
```

DAISY output is validated outside pytest, with DAISY Pipeline 2 in Docker:

```bash
uv run python tests/daisy_samples.py /tmp/daisy        # sample books, with a fake voice
scripts/check-daisy.sh /tmp/daisy/*/*DAISY             # exits 0 only if every book passes
```

The web page is checked for accessibility outside pytest too, with axe-core in a real
browser. The script makes every page in every state with test data (no voice needed):

```bash
scripts/check-web-a11y.sh /tmp/web-pages               # exits 0 only if every page passes
```

Axe catches only some WCAG failures. When you change a page, also check it by hand: with
the keyboard alone, with a screen reader, at 320 CSS pixels wide, and with text at 200%.

The standalone installers are built on the platform they're for, with
`uv run --no-dev --group installer python packaging/build.py`; `packaging/README.md` lists
what each platform needs. The Installers workflow builds all three, installs each, and runs
`packaging/smoke_test.py` on the installed copy.

## Layout

| Path | Job |
|---|---|
| `src/dogear/document.py` | The structured book every stage reads: sections and blocks (headings, paragraphs, lists, tables, images, notes, page markers). Display text only. |
| `src/dogear/defaults.toml`, `config.py` | Every default setting, and loading and validating a user's TOML on top. Each installed voice engine adds its own table's defaults, checked the same way. |
| `src/dogear/errors.py` | Errors a user can fix, each with a hint. Anything else is a bug. |
| `src/dogear/tools.py` | Finding and running ffmpeg, Tesseract, and Calibre with install hints: the program set in config, else a standalone build's own copy, else PATH, else known install folders. |
| `src/dogear/bundle.py` | What a standalone (PyInstaller) build carries: where its programs are, the folder to run espeak-ng in, with `--path=.`, so it finds its data, and its voice files, used when the build-time manifest's SHA-256 matches the catalog's. Every answer is None in a normal install. |
| `src/dogear/file_lock.py` | An exclusive lock on a file (flock, or msvcrt.locking on Windows) that the system lets go of when the process ends, however it ends. Keeps a second `dogear serve` off a web folder in use, and `dogear make` off another run's work folder. |
| `src/dogear/extract/` | One module per format: `epub.py` (+ `epub_package`, `epub_nav`, `epub_sections` for sections from the table of contents, `epub_kind`, `epub_cover`, `xhtml`, `xhtml_blocks`, `xhtml_split`, `safe_xml`), `pdf.py` (+ `pdf_page`, `pdf_text`, `pdf_furniture` for running headers, `pdf_columns`, `pdf_notes` for page-foot footnotes, `pdf_headings`, `pdf_figures`, `pdf_hyphens`, `pdf_chapters`, `pdf_sections`), `docx.py` for Word (+ `docx_package` for the archive and its parts, `docx_styles` for heading levels through the style chain, `docx_numbering` for lists, `docx_runs` for a paragraph's text, images, and note references, `docx_blocks`, `docx_notes` for footnotes, endnotes, and their marks), `html_book.py` for HTML (+ `html_parse` for decoding and lenient parsing with the network off, `html_encodings` for the character set labels browsers know), `markdown_book.py` for Markdown (rendered to HTML, + `front_matter` for YAML metadata, `markdown_limits` for the caps on lines and tokens that keep a small file from filling the memory), `calibre.py` for Kindle. Shared: `__init__` holds `FORMATS`, the one list of what Dogear reads (the extensions and the names every help text and the web page use); `zip_archive` reads EPUB and Word archives in memory with a size limit; `limited_read` reads HTML and Markdown files with theirs; `stretches` cuts a book with no table of contents into sections at its top heading level; `note_links` labels notes once a book is read. `extract_book` reads a PDF that `pdf.py` finds scanned with OCR first (`pdf.extract_scanned`), when `pdf.auto_ocr` is on. |
| `src/dogear/speech/` | `sentences.py` splits display text into sentences; `normalize.py` rewrites a sentence of English into what the voice should say, with `money`, `times`, `units`, and `roman` handling one kind of text each. Text in another language is only tidied and given that language's pronunciation entries: its voice reads numbers and symbols by its own rules. |
| `src/dogear/language_match.py`, `language_detection.py` | Matching BCP 47 tags (first parts, most specific key first), shared by voices, words, and pronunciations; and `[language_detection]`, guessing the language of text a book doesn't mark with py3langid, only for languages with a voice, at a minimum length and confidence. |
| `src/dogear/narration.py` | Spoken structure: a section becomes utterances with pauses (headings, image descriptions, notes, tables, scene breaks), per `[narration]`, plus passages that say which utterances read which block. Also the title track. Each utterance carries its language (marked, the book's, or guessed with a `LanguageGuesser`; a guessed block carries its language in its passage), and the words it adds come from that language's table in `[narration.words]`, else English. |
| `src/dogear/voice/` | Voice engines. `engine.py` is what a voice gives the renderer (`plan` is cheap and gives the cache key; `render` makes audio). `engines.py` is the plugin interface: what an engine gives Dogear, finding the installed ones through the `dogear.engines` entry points (setting aside any that fail, with a reason), choosing a voice (`engine:voice` or a name only one engine has; an engine may take its voices from its settings, `configured_voices`), and `EngineVoice`, which adds the engine and voice to every cache key and turns an engine's unexpected errors into `VoiceError`s. `__init__.py` makes a voice (`make_voice`, which `make_engine` uses for the configured one), the one way every voice is made. `cloud.py` is the opt-in for voices that send text off this computer (each engine says where in `sends_text_to`): refused unless `[voice] allow_cloud` or `--allow-cloud`, and said plainly before any text is sent; `make_voice` checks each voice, and `make` checks every voice a run may use before reading the book. `language_voices.py` is `[voice.languages]` and `[voice] other_languages`: which voice reads which language, checked as the settings load, and `voices_for_run`, every voice a run's settings may use. `book_voices.py` works out the voices one book needs (`make` loads each once) and holds them for the renderer, at the book's sample rate, the highest of theirs. `kokoro_plugin.py` is Kokoro as a plugin, registered in `pyproject.toml` like any other, with its settings in `kokoro.toml`. `kokoro.py` runs Kokoro with onnxruntime; `g2p.py` turns English into phonemes (misaki, plus the espeak-ng program for unknown words); `downloads.py` and `catalog.toml` fetch model files pinned by SHA-256, one Dogear at a time per models folder (`spacy_model.py` unpacks spaCy's model). `__init__.py` also gives the voice's pace (`voice_pace`, from each engine's `pace`) for the disk space check. `check.py` is `dogear download --check`: it hashes every Kokoro and spaCy file on disk, downloaded or bundled, against the catalog (spaCy's unpacked model as one folder hash), and every file another installed engine pins (its optional `pinned_files`), and downloads the damaged ones again. |
| `src/dogear/render/` | Speaking a narrated book into one WAV per section, through the sentence cache (`cache.py`), with plain-text progress (`progress.py`). Each utterance is said by its language's voice (`voices.py`), and a voice at another sample rate than the book's is resampled (`resample.py`, windowed sinc in numpy). Records where each utterance starts in the audio. `work.py` is each run's own folder under `<cache>/work`, locked while the run lasts, and clears away the folders of runs that died. |
| `src/dogear/readalong/` | Talking-book text: `build.py` turns passages and audio timings into text items with ids and syncs (`model.py`), shared by the EPUB 3 and DAISY packagers; `outline.py` settles heading levels across the book and sorts page numbers. |
| `src/dogear/output/` | Packaging, one module per job: `loudness` (one gain per chapter to a target, with a capped limiter), `m4b`, `mp3_folder` with `playlist` and safe `filenames`, `tags`, `cover`, and `folder_record` (so a re-run replaces only its own files). `talking_audio` encodes talking-book MP3s; `book_identity` gives their identifier and narrator. The EPUB 3 talking book is `epub3` (putting it together, written next to the output and moved into place), with `epub3_text` (a section's content document), `epub3_smil` (its Media Overlay), `epub3_nav` (contents, page list, landmarks), `epub3_package` (the package document), `epub3_a11y` (accessibility claims decided from the content), `epub3_container` (the zip), `epub3_xhtml` (what the XHTML documents share), and `epub3.css`; `language_tag` checks a book's language tags before they go into markup. DAISY 2.02: `daisy202` puts the book in its folder; `daisy202_plan` names the files and settles the metadata and times; `daisy202_clock` rounds clip times to milliseconds and writes them; `daisy202_xhtml` is the XHTML 1.0 skeleton; `daisy202_text`, `daisy202_smil`, and `daisy202_ncc` write the text documents, SMIL files, and NCC. |
| `src/dogear/make.py` | `dogear make` from start to finish: extract, settle the book's language and check a voice speaks it, narrate (guessing languages if asked), say which voice reads what, load each voice the book needs, speak, package. |
| `src/dogear/disk_space.py` | Before `make` speaks anything: what the run needs on the cache folder's disk (the speech cache and the work folder) and the output folder's (each format), from the words at the voice's pace (`voice_pace`, from the catalog's `words_per_minute`) and the `[audio]` rates, checked against each disk's free space with `output.space_margin_percent`. `sizes.py` puts sizes in words. |
| `src/dogear/pronunciations.py` | The user's pronunciation files (`[pronunciation]`, `--words`): reading and checking them. `speech/word_match.py` finds their words in text. Sounds-like entries are applied first in `speech/normalize.py`, to English text unless an entry lists its `languages`; phoneme entries are pinned in `voice/g2p.py` and checked against Kokoro's symbols in `voice/kokoro.py`. `pronunciation_writer.py` writes such a file, every word and value an escaped TOML basic string. |
| `src/dogear/unknown_words.py` | `dogear words`: the words the voice would hand to espeak-ng, most frequent first, also as JSON (`words --json`) with a reader that checks it. Needs spaCy's model, not the Kokoro voice. |
| `src/dogear/say.py` | `dogear say`: a short text spoken into a WAV file through the same normalization, voice, and renderer as `dogear make`. |
| `src/dogear/paths.py` | Per-user folders for downloaded models, the speech cache, and the web page's books. |
| `src/dogear/ocr/` | Reading scanned PDFs with Tesseract. `page.py` reads one page (drawn no bigger than `ocr.max_page_megapixels` and `ocr.max_page_side_pixels`, and refused if that needs under 70 dpi) into a one-page PDF; `tesseract.py` finds Tesseract, checks its language packs, and runs it; `cache.py` reads a scanned PDF for every command that reads a book, a page at a time into `<cache>/ocr/<key>/` (keyed by the file's SHA-256, `[ocr]`, Tesseract's version, and the SHA-256 of each language's traineddata file), resuming after a stop, locked against a second run, and making a kept text layer that was damaged again; `progress.py` says how it goes; `settings.py` checks `[ocr]`. `__init__.py` is `dogear ocr`, which saves a copy with the pictures kept. |
| `src/dogear/book_summary.py` | The facts `dogear outline` gives (title, authors, and each section's kind, words, print pages, and counts of images, notes, and tables), in words and as JSON (`outline --json`), and reading that JSON back with every field checked. |
| `src/dogear/cli.py`, `__main__.py` | The `dogear` command, also run as `python -m dogear`. |
| `src/dogear/web/` | `dogear serve`, the local web page, one module per job. `server` starts it, prints the links, and stops on Ctrl+C; `settings` is `[web]` plus the command's options, and the server locks the web folder while it runs; `app` wires the routes, checks, and queues. Checks: `guard` (Host on this computer only, same-origin forms, the access cookie), `headers` (security headers), `access_key` (the key file, comparing keys, slowing wrong ones, and the one-time start token the browser is opened with), `private` (owner-only folders for the books). Pages: `access_pages`, `book_pages`, `site_pages`, `word_pages` (a book's "Check how words are said": the 50 most frequent words, and `/words/all` with every word the check kept), `pronunciation_pages` (the Pronunciations page), and `listen_pages` (Listen and the recording it makes), rendered by `templating` from `templates/` (Jinja2, autoescaped), with `static/` (one stylesheet; `progress.js`, which only adds live updates; and `listen.js`, which only plays a Listen in place). `forms` reads small forms with a bound; `uploads` streams a book to disk with the size limit; `choices` is the choose form (cloud voices only when `[voice] allow_cloud` is true, and a book that may use one, as its voice or for another language, starts only after `send_text.html` says where its text goes); `spelling_forms` reads and checks the forms that give words their spellings, and saves only the words changed since the page was shown, never over a change made elsewhere since; `status` puts a book's state in words; `downloads` serves outputs and cached zips. `page_words` is the page's own pronunciation file (`words.toml` in the web folder, passed to every `make`, `words`, and `say` child with `--words`): checking words and spellings against the `[web]` limits, and saving it whole, read back before it replaces the old one, keeping hand-written phoneme entries. Work: `books` (each book's record and folder, saved with `atomic`, which writes whole files and, on Windows, waits a moment for a file another program has open), `library` (the books and three queues: reading, making, and checking words), `worker` (a queue that runs one job at a time), `word_check` (`words --json` for a book, checked and capped at `web.max_words_listed`, on its own queue, the latest check saved in the book's folder and read back on start), `listening` (`say -- TEXT` on its own queue, apart from making, with only the latest recordings kept), `child` (running `dogear` as a child process, never a book in the server), `process_tree` (a child and everything it starts, which only the server stops: its own session on Linux and macOS, a Job object from `windows_job` on Windows), `server_watch` (the child's side: it stops by itself when the server ends), `reading` (`outline --json`, checked) and `making` (`make`, its progress, and what it saved), with `progress` keeping a book's progress lines apart from its record (repeats in a row collapsed into one line with a count, only the latest kept, saved every few seconds). |
| `scripts/check-epub.sh` | Checks an EPUB with EPUBCheck 5.4.0 (`--failonwarnings`) and Ace 1.4.6 (`-E`), downloading both pinned into `$DOGEAR_VALIDATORS` (default `~/.cache/dogear-validators`). Reports go next to the book. |
| `scripts/make-sample-epub.py` | Makes two sample EPUB talking books with a fake voice. Between them they use every kind of block, with pages announced and not, tables read and skipped, and the title track on and off. Check them with `check-epub.sh`. |
| `scripts/check-daisy.sh` | Validates DAISY 2.02 books with DAISY Pipeline 2's `daisy202-validator` (pinned Docker image), saving its report next to each book. Used locally and in CI. |
| `packaging/build.py` | Builds the standalone bundle and installer for the platform it runs on (`uv run --no-dev --group installer python packaging/build.py`); `packaging/README.md` explains it. The steps are modules in `packaging/dogear_build/`: `pins` (reads `pins.toml`, every source archive pinned by URL and SHA-256), `fetch`, `programs` (runs `build-programs.sh`, which builds LAME, FFmpeg, and eSpeak NG from source, in MSYS2 on Windows), `freeze` (PyInstaller, with `entry.py` as the program), `voice` (the default voice and its manifest), `notices` (`THIRD-PARTY-NOTICES.txt`), `assemble`, and one installer module per platform: `linux` (a .tar.gz with `linux/install.sh`), `macos` (an app in a .dmg, with `macos/launcher.sh`), `windows` (Inno Setup, `windows/dogear.iss`). |
| `packaging/smoke_test.py` | Runs a built Dogear offline with an empty home folder and nothing on PATH: `--version`, `voices` (Kokoro, and no other engine), `say`, and `make`. CI runs it on each installed copy (`.github/workflows/installers.yml`). |
| `tests/daisy_samples.py`, `tests/dtd/` | A book with every kind of block, made into DAISY books through `dogear make` with a fake voice; and the W3C XHTML 1.0 and SMIL 1.0 DTDs the unit tests validate against, offline. |
| `scripts/check-web-a11y.sh`, `scripts/web-a11y.cjs` | Checks every web page in every state with axe-core 4.14.0 in Playwright 1.64.1's headless Chromium (pinned, downloaded into `$DOGEAR_VALIDATORS`): desktop and 320 pixels wide, light and dark, plus no sideways scrolling with text at 200% and a focus ring on everything Tab reaches. Used locally and in CI. |
| `tests/engine_fakes.py` | Stand-in voice engine plugins and entry points. `conftest.py` shows the tests only Dogear's own engines unless a test installs stand-ins with `installed_engines`. |
| `tests/web_samples.py`, `tests/fake_dogear.py` | The web app with books in every state, each page saved as HTML (for the unit tests and `check-web-a11y.sh`); and a stand-in `dogear` command the web tests run as the child process. |
| `plugins/openai_speech/` | The speech service plugin, `dogear-openai-speech` (Apache-2.0): voices from any service or server that takes OpenAI's `POST /audio/speech`. `plugin.py` is what its entry point gives Dogear (voices from its settings; `sends_text_to` from `base_url`), `address.py` checks `base_url` and decides whether it is this computer, `settings.py` checks the `[openai_speech]` table and reads the key from the environment variable it names, `key_text.py` checks the key can go in a header and takes it out of every message, `client.py` sends one sentence per request with urllib (retries with backoff, a requests-a-minute limit, no redirects, no proxy for this computer, errors as `VoiceError`s with hints, never with the key), `audio.py` reads and checks the WAV or PCM it gets back, and `engine.py` is the `VoiceEngine`. Its tests use `tests/fake_service.py`. |
| `plugins/piper/` | The Piper voice engine plugin, `dogear-piper`: a separate package under GPL-3.0-or-later, which Dogear never imports. `plugin.py` is what its entry point gives Dogear, `engine.py` speaks with piper-tts (eSpeak NG phonemes, onnxruntime), `catalog.py` and `catalog.toml` pin its voices by URL and SHA-256. Its own tests are in `plugins/piper/tests/`. |
| `docs/` | Guides for people, not code: `guide-for-helpers.md` (making a book with the web page, and fixing how words are said, in plain language), `screen-reader-testing.md` (the screen-reader test plan for the web page), and `voice-engines.md` (why Piper is the first plugin, and the interface for writing one). |
| `Dockerfile`, `docker/` | The Docker image: Dogear, ffmpeg, eSpeak NG, Tesseract, and the default voice, running `dogear serve --lan` with everything it keeps on a `/data` volume. `docker/config.toml` is its settings file (via `DOGEAR_CONFIG`); `docker/README.md` is the guide. The `Docker` workflow builds it and makes a book offline with it. |

## Project rules

1. **Accessibility is a requirement.** Every user-facing surface (CLI output, error messages,
   web UI, docs) must work with a screen reader. In the CLI: plain sentences and numbered
   lists, no tables, colors, or animations, progress as occasional plain lines, and errors
   that say what to do next. Web UI targets WCAG 2.2 AA.
2. **Structure, not text dumps.** Extractors produce the structured document model. Never
   flatten to plain text before the speech stage. The model holds display text; spoken forms
   are made per sentence in `speech/` and never stored.
3. **Licenses matter.** Core code is Apache-2.0. No GPL or AGPL dependency in the core
   package (PyMuPDF and EbookLib were rejected for being AGPL); GPL engines such as Piper
   ship as separate optional packages, which the core never imports and the installers and
   Docker image never carry. LGPL is allowed only when used unmodified, and must be listed
   below. Never make a non-commercial or custom-licensed voice the default.
4. **Book files are untrusted input.** Never unpack them to disk, never let them trigger
   network access or external entities, and bound anything a file can make large.
5. **Offline by default.** Anything that sends book text off the machine is opt-in and warns first.
6. **Config is the single source of truth.** Defaults live only in `defaults.toml`, and each
   voice engine's in its own settings file (Kokoro's `voice/kokoro.toml`), and are read from
   the loaded config. A test should prove changing a setting changes behavior.
7. **Small modules.** One job per module: an extractor per format, an engine per voice
   backend (registered as a plugin, `docs/voice-engines.md`), a packager per output format.
8. **Fail loudly.** Raise with a clear message. Never swallow errors or silently drop content:
   front and back matter are classified, not removed.
9. **Test what you build.** Voice engines are tested with mocks; CI never needs a GPU or an
   API key.
10. **Docs are claims.** Don't document a command, flag, or format that isn't built. When you
    finish a roadmap item, tick it in `ROADMAP.md` in the same commit.
11. **No secrets, no books.** API keys go in `.env` (gitignored). Only public-domain books go
    in the repo, listed in `tests/fixtures/README.md`. Never commit generated audio.

## Runtime dependency licenses

Checked with `uv tree --no-dev` when added. Recheck when adding or upgrading.

| Package | License | Used for |
|---|---|---|
| lxml | BSD-3-Clause | EPUB, XHTML, HTML, and Word (DOCX) parsing |
| pdfplumber, pdfminer.six | MIT | PDF text, positions, font sizes |
| pypdf | BSD-3-Clause | PDF outline, page labels, merging |
| pypdfium2 | BSD-3-Clause / Apache-2.0 | Rendering PDF pages for OCR |
| Pillow | MIT-CMU | Page images for OCR |
| num2words (+ docopt, MIT) | **LGPL-2.1**, used unmodified | Numbers to words |
| cryptography, charset-normalizer, cffi, pycparser | Apache-2.0 or BSD / MIT | pdfminer.six dependencies |
| onnxruntime (+ flatbuffers, protobuf, packaging) | MIT (Apache-2.0, BSD-3-Clause) | Running the Kokoro model |
| numpy | BSD-3-Clause | Audio samples |
| misaki (+ addict, regex) | Apache-2.0 (MIT, Apache-2.0) | English phonemes for Kokoro. Installed without its `en` extra, which pulls in GPL phonemizer and espeak-ng libraries. |
| spaCy and its dependencies (thinc, pydantic, typer, requests, and others) | MIT, BSD, Apache-2.0, ISC, PSF; **tqdm and certifi are MPL-2.0**, used unmodified | Part-of-speech tags, so heteronyms are pronounced right |
| Starlette (+ anyio, idna, typing-extensions) | BSD-3-Clause (MIT, BSD-3-Clause, PSF) | The web page's routes, requests, and responses |
| uvicorn (+ click, h11) | BSD-3-Clause (BSD-3-Clause, MIT) | Serving the web page |
| Jinja2 (+ MarkupSafe) | BSD-3-Clause | The web page's HTML templates, autoescaped |
| python-multipart | Apache-2.0 | Reading a book upload as it streams in |
| markdown-it-py (+ mdurl) | MIT (MIT) | Markdown to HTML (CommonMark and tables) |
| mdit-py-plugins | MIT | Markdown footnotes and front matter |
| PyYAML | MIT | Markdown front matter, read with its BaseLoader (text, lists, and mappings only) |
| py3langid (+ numpy) | BSD-3-Clause; its model, inside the package, has no license of its own stated | Guessing the language of passages a book doesn't mark (`[language_detection]`, off by default). Chosen over lingua (170 MB wheels), langdetect (unmaintained, not deterministic), eld (no probabilities), and fastText's model (CC BY-SA, downloaded): `docs/voice-engines.md`. |

Downloaded on first use: the Kokoro-82M model and voices (Apache-2.0) and spaCy's
`en_core_web_sm` model (MIT), each pinned by SHA-256 in `src/dogear/voice/catalog.toml`. The
standalone installers bundle the default ones.

External programs (run as separate processes, never linked): ffmpeg, Tesseract, Calibre, and
eSpeak NG (GPL-3.0, used only for words misaki's dictionaries don't have, the same fallback
Kokoro's own pipeline uses). The standalone installers bundle ffmpeg and ffprobe (FFmpeg,
LGPL-2.1-or-later, built with LAME, LGPL-2.0-or-later) and eSpeak NG, built from the source
archives pinned in `packaging/pins.toml`, with every license and a link to the exact source in
the bundle's `THIRD-PARTY-NOTICES.txt`.

## Build-only tool licenses

Used to build the standalone installers, never needed to run Dogear. The installer
dependency group in `pyproject.toml` holds the Python ones. Only PyInstaller's bootloader
ends up in the bundle, under its exception; the bundle's notices include it.

| Tool | License | Used for |
|---|---|---|
| PyInstaller | GPL-2.0-or-later, with an exception that lets the programs it builds use any license | Freezing Dogear into one folder with its Python |
| pyinstaller-hooks-contrib | GPL-2.0-or-later or Apache-2.0 | PyInstaller's hooks for spaCy, onnxruntime, pypdfium2, and others |
| altgraph, macholib (macOS), pefile (Windows) | MIT | PyInstaller dependencies |
| pywin32-ctypes (Windows) | BSD-3-Clause | PyInstaller dependency |
| setuptools | MIT | PyInstaller dependency |
| Inno Setup 6 | Inno Setup License (free to use; commercial users are asked to buy a license) | The Windows installer |
| MSYS2 with MinGW-w64 GCC, CMake, nasm | Various free licenses. The Windows programs are linked statically, so the MinGW-w64 runtime parts they use are inside them; GCC's runtime library exception covers GCC's | Building FFmpeg and eSpeak NG on Windows |

CI only, never needed to run Dogear: DAISY Pipeline 2 (LGPL-3.0), run from its Docker image
by `scripts/check-daisy.sh` to validate DAISY output; Playwright (Apache-2.0), its Chromium,
and axe-core (MPL-2.0), downloaded by `scripts/check-web-a11y.sh` to check the web page.
The web tests use httpx2 (BSD-3-Clause), a dev dependency, for Starlette's test client.

## Plugin dependency licenses

Not part of the core package: installed only with a plugin, never by Dogear itself, and
never in the installers or the Docker image.

| Plugin | Package | License | Used for |
|---|---|---|---|
| `dogear-piper` (GPL-3.0-or-later) | piper-tts 1.8 | **GPL-3.0-or-later**, with eSpeak NG (GPL-3.0-or-later) built in | Piper's phonemes (eSpeak NG) and voices |
| `dogear-piper` | pathvalidate | MIT | piper-tts dependency |
| `dogear-piper` | onnxruntime, numpy | MIT, BSD-3-Clause (as in the core) | Running the voice model |

Downloaded on first use of a Piper voice: its model and config from
`rhasspy/piper-voices` (tagged MIT), each pinned by SHA-256 in
`plugins/piper/src/dogear_piper/catalog.toml`, which also names the recordings each voice
was trained on and their license (public domain or CC BY 4.0).

## Prior art

Dogear grew out of `github.com/18hrshift/audiobook-maker`. Phase 0 ported its chapter
detection, text cleanup, number and abbreviation expansion, OCR, and M4B assembly, and
fixed bugs found on the way. Its TTS backends were not ported (all three were broken).
