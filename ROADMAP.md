# Roadmap

Dogear is an open talking-book maker: books in, navigable audio out, built first for blind
and low-vision listeners and the people who help them.

Each phase lists what it must deliver. A phase is done when every item is built, tested,
and documented.

## Who it's for

- **The listener.** Often someone losing their sight later in life, not a screen-reader power
  user. Listens on a phone app, a talking-book player, or a smart speaker.
- **The producer.** A family member, a volunteer, or a blind user themselves, running Dogear
  on an ordinary computer. The tool itself must be usable with a screen reader.

## Decisions so far

| Decision | Choice | Why |
|---|---|---|
| Name | Dogear | A dog-eared page, plus an ear for listening. |
| Home | `github.com/18hrshift/dogear-app` | |
| License | Apache-2.0 | Permissive, with a patent grant. Matches the default voice engine (Kokoro). |
| Default voice engine | Kokoro-82M | Apache-2.0, runs on a laptop CPU, the most-downloaded open TTS model on Hugging Face. |
| Hardware baseline | CPU only | A GPU makes it faster, never required. |
| Network | Offline by default | Cloud voices are opt-in and warn that the book text leaves the machine. |
| Pronunciation | misaki dictionaries, then the eSpeak NG program | misaki (Apache-2.0) is what Kokoro was trained with. Its packaged English setup loads espeak-ng as a GPL library, so Dogear runs the espeak-ng program instead, only for words misaki doesn't know. |
| Pace and level | Speed 0.9, -20 LUFS | Kokoro at 1.0 read about 190 words a minute; narrators average about 155. Its peaks are high for its loudness, so -20 LUFS (inside ACX's range) needs about 5 dB of limiting where -18 needed 7. |
| DAISY | DAISY 2.02, written by Dogear itself; DAISY Pipeline 2 (1.15.5) only validates it | Every player surveyed in October 2026 that reads DAISY 3 also reads 2.02, so DAISY 3 isn't needed now (it would matter for a library that requires it). Dogear already has exact timings, and writing the files itself keeps installs Python-only. Pipeline 2 is a Java server, and in a test its own EPUB to DAISY conversion left out metadata and failed its own validator. |
| Talking-book audio | Constant-bitrate mono MP3 at 44.1 kHz, 64 kbps | The one sample rate DAISY 2.02 players, DAISY 3 players, and EPUB's core MP3 type all accept. The voice makes 24 kHz; resampling doesn't move the sync times. |
| Title track | On by default, unless the book opens with its own title page | DAISY 2.02 requires the book's title first. Every format gets the same opening, and a book whose title page already says it doesn't say it twice. |
| Accessibility claims | Say what the book has; claim no WCAG conformance | A conformance claim means a person evaluated the book. Dogear can't do that for any book it is given, so its EPUBs describe their features and say no one has checked them. |
| Web page | `dogear serve`: server-rendered HTML (Starlette, Jinja2) that works without JavaScript | Plain pages work best with every screen reader and on old phones. Each book runs as a `dogear make` child process, so the server never parses an untrusted book, and stopping a book is just stopping a process. An access key in the link guards it, even on the computer itself. |
| Installers | PyInstaller, with ffmpeg (LGPL build) and eSpeak NG compiled from pinned sources, and the default voice inside | A helper installs one thing and makes a book offline. No LGPL ffmpeg build existed for every platform, so Dogear builds its own. Inno Setup on Windows, a `.dmg` on macOS, a tarball with menu entries on Linux. Unsigned until the project has certificates. |
| Docker image | Debian's ffmpeg, eSpeak NG, and Tesseract, the default voice inside, everything kept on a `/data` volume | For a shared computer at a library or school: one command runs the web page, offline once built. |

**Open question:** who is the first real listener, and what device do they use? That decides
which output Phase 2 polishes first.

## Phase 0: Foundation

- [x] Python package skeleton (`src/dogear/`), `pyproject.toml`, `uv` lockfile.
- [x] `bash test.sh` runs lint plus tests; CI runs it on Linux, macOS, and Windows.
- [x] Port what works from `18hrshift/audiobook-maker`, with its tests: PDF chapter detection
      cascade (bookmarks, chapter dividers, font size, patterns, page split), text cleanup,
      number and abbreviation expansion, the Tesseract OCR helper, ffmpeg M4B assembly
      (fixing its Windows `which` bug).
- [x] **Document model.** Extractors output a structured book (sections, headings with levels,
      paragraphs, lists, tables, footnotes, images with alt text, print-page markers, language
      tags), not flat text. Everything downstream works from this.
- [x] Test fixtures from public-domain books only (Standard Ebooks or Project Gutenberg), with
      the source recorded next to each fixture.
- [x] Issue templates (including an "accessibility barrier" template).
- [ ] `CODE_OF_CONDUCT.md` (Contributor Covenant), once there's a contact address for reports.

## Phase 1: First listen

Goal: one command turns an EPUB or PDF into a good-sounding M4B on a laptop with no GPU.

- [x] EPUB and PDF ingest into the document model (done in Phase 0).
- [x] PDF reading gaps found in Phase 0: multi-column pages, footnotes at the foot of a page
      (read as paragraphs today), mid-chapter subheadings, images, two-line running headers,
      and compounds hyphenated at a line end ("well-" + "known" becomes "wellknown").
      Known limits: footnotes need a raised marker to be found, vector drawings aren't
      found as images, and a wide table on a two-column page is read column by column.
- [x] EPUB sections built from the table of contents, not one per file, so books that split a
      chapter across files (common in Calibre and Project Gutenberg EPUBs) navigate cleanly.
      Also reads the cover image from EPUB (and Kindle books, through Calibre).
- [x] More speech normalization: currency ("$5"), times, units, and roman numerals in titles.
- [x] Kokoro engine (ONNX, CPU) behind a voice-engine interface.
- [x] Sentence-level rendering with a cache keyed on text, voice, engine, and settings. A crash
      resumes where it stopped. Fixing one word re-renders only the sentences that contain it.
- [x] Spoken structure: headings announced, pauses scaled by structure (longer before a new
      chapter), image alt text read as "Image: ...". Footnote and table handling set in config.
- [x] Loudness normalized across the whole book.
- [x] Outputs: M4B with chapter markers, cover, and metadata; MP3 folder with an M3U playlist.
- [x] Screen-reader-friendly CLI: no animations at all, occasional plain-text progress lines
      ("Section 3 of 12 done: The Storm. About 40 minutes left."), errors that say what to
      do next.
- [x] Install with one command: `uv tool install git+https://github.com/18hrshift/dogear-app`
      (or `pipx install`).
- [ ] Publish to PyPI. The name `dogear` was taken there on 2026-10-25 by an unrelated
      tool, so the package needs another name (the command can stay `dogear`). The project
      owner picks it.
- [ ] README sample audio from a public-domain book. Needs a decision on where it lives:
      generated audio stays out of git.

## Phase 2: Talking book

Goal: output a listener can navigate without sight.

- [x] EPUB 3 with Media Overlays. Sentence-level rendering already gives exact timings for
      every sentence, which is what the SMIL sync files need.
- [x] DAISY 2.02 output with full text and full audio: headings at every level, print
      pages, and each sentence tied to its audio. Dogear writes it itself; DAISY Pipeline
      2 only validates it (`scripts/check-daisy.sh`). DAISY 3 isn't needed now (see the
      decisions above).
- [x] Navigation by heading level and by print page: every heading in the EPUB contents
      and the DAISY NCC, at levels settled across the book so none is skipped, and print
      pages in the EPUB page list and the NCC.
- [x] User-editable pronunciation dictionary (names, places, jargon).
- [x] Every output validated in CI: EPUBCheck and Ace by DAISY for EPUB 3, DAISY Pipeline
      2's validator for DAISY (the Talking books job, on the fixture with the real voice and
      on sample books that use every kind of block).
- [ ] Tested by at least one blind or low-vision listener on Thorium Reader and one hardware
      or app player.

## Phase 3: Anyone can run it

Goal: a helper with no Python experience can make a book.

- [x] Standalone installers for Windows, macOS, and Linux with the default voice bundled.
      CI builds each one, installs it, and makes a book with it offline.
- [ ] Publish the installers as downloads, signed. Needs the project owner: a release
      process, a Windows code-signing certificate, and an Apple Developer account. Until
      then they are unsigned and only built from source or taken from CI.
- [x] Local web UI, usable from a phone on the same network. Meets WCAG 2.2 AA as far as
      automated checks (axe-core, in CI) and a review by hand can tell; the test matrix
      below is the real check.
- [ ] Screen-reader test matrix for the UI: NVDA and JAWS (Windows), VoiceOver (macOS, iOS),
      TalkBack (Android), Orca (Linux). The plan and the tasks are in
      [`docs/screen-reader-testing.md`](docs/screen-reader-testing.md); it needs testers.
- [x] Docker image for libraries, schools, and volunteer groups.
- [x] Plain-language guide written for helpers, not developers:
      [`docs/guide-for-helpers.md`](docs/guide-for-helpers.md).

## Phase 4: More voices and languages

- [x] Voice engines as plugins, through the `dogear.engines` entry points, Kokoro included.
      The first plugin is Piper (`plugins/piper/`, GPL-3.0, so a separate package), with
      voices trained only on recordings anyone may use. Candidates re-checked 2026-10-30
      (see [`docs/voice-engines.md`](docs/voice-engines.md)): OmniVoice's weights turned
      out to be non-commercial, and Qwen3-TTS and Chatterbox are too slow on a CPU. Pocket
      TTS (MIT code, CC BY weights) is the best candidate for a second plugin.
- [x] Cloud voices as opt-in plugins with a clear privacy warning. Dogear itself refuses a
      voice that sends text off the computer unless `[voice] allow_cloud` or `--allow-cloud`
      says yes, and names the host first. The first plugin speaks the OpenAI-compatible
      speech API (`plugins/openai_speech/`), which local servers speak too. Its default is
      OpenAI, whose speech models shut down on 2027-01-06: change the default before then.
- [x] Language detection per section, so a French quote in an English book gets a French voice.
      Built per passage: `[voice.languages]` gives each language a voice, the book's own
      language marks come first, and `[language_detection]` (py3langid, off by default)
      guesses the rest. Narration words in French, German, and Spanish. Known limits: the
      volume is evened per chapter, not per voice, and no native speaker has checked the
      word tables yet.
- [x] More inputs: DOCX, HTML, Markdown, MOBI/AZW3 via Calibre, automatic OCR for scanned PDFs.
      Known limits: a PDF with only some pages scanned isn't OCR'd, and the installers
      don't include Tesseract (the Docker image does).

## Ground rules for every phase

- **Accessibility is a requirement, not a feature.** A change that makes Dogear harder to use
  with a screen reader is a bug.
- **No non-commercial or custom-licensed voice as a default.** As of 2026-10-24 that rules out
  Voxtral TTS, F5-TTS, and Fish Speech (non-commercial) and Kitten TTS 2 (custom license).
- **Voice cloning is off by default** and requires the speaker's consent when turned on.
- **No book hosting or sharing.** Docs point print-disabled readers to accessible libraries
  first and explain the legal basics without giving legal advice.
- **Nothing about us without us.** Recruit blind and low-vision testers early, through
  communities like AppleVis, the NVDA forums, and r/Blind.
