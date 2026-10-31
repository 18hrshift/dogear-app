# Dogear

**An open talking-book maker.** Turn the books you own into audiobooks that blind and
low-vision listeners can actually navigate: by chapter, by heading, by print page.

> **Status: early.** Dogear makes M4B audiobooks, MP3 folders, and talking books (EPUB 3
> and DAISY 2.02, with the text and the narration in step) with a natural-sounding voice
> that runs on an ordinary computer, offline. [`ROADMAP.md`](ROADMAP.md) has the plan.

**Making books for someone, with no programming?** Start with the
[guide for helpers](docs/guide-for-helpers.md). The rest of this page covers everything,
including the `dogear` command.

## Install

There are three ways: an installer, which needs nothing else; Python; or, for a shared
computer or server, Docker.

### With an installer

The installers hold everything Dogear needs: its own Python, ffmpeg, eSpeak NG, and the
voice, so it can make a book without an internet connection. They don't include
Tesseract, the program Dogear reads scanned PDFs with: to make books from scanned PDFs,
install it as well (see "Scanned PDFs" below). On Windows and Linux they
install Dogear for you alone, with no administrator password. They aren't offered as
downloads yet; until they are, someone comfortable with a terminal can build one (see
below).

- **Windows** (64-bit): run `Dogear-<version>-windows-x86_64-setup.exe`. The installer
  isn't signed yet, so Windows may say "Windows protected your PC". Choose "More info",
  then "Run anyway". The installer can also put the `dogear` command on your PATH. To
  remove Dogear, use Settings, Apps, Installed apps.
- **Mac** (Apple silicon, macOS 14 or newer): open `Dogear-<version>-macos-arm64.dmg` and
  drag Dogear into Applications. Dogear isn't signed by Apple yet, so macOS stops it the
  first time; "Read me first.txt" on the disk image says how to open it.
- **Linux** (64-bit PC): unpack `Dogear-<version>-linux-x86_64.tar.gz`, open a terminal in
  its folder, and run `sh install.sh`. To remove Dogear, run
  `sh ~/.local/opt/dogear/uninstall.sh`.

Choosing Dogear in the Start menu, in Applications, or in your applications menu opens a
window running `dogear serve`, and your web browser opens at Dogear's page (see "Use it
in a web browser" below). On Windows and Linux, "Dogear for phones and tablets" next to it
runs `dogear serve --lan` instead. Keep that window open while you use Dogear, and close it to stop
Dogear. The `dogear` command works in a terminal too (on a Mac, it's
`/Applications/Dogear.app/Contents/Resources/dogear/dogear`).

To build an installer, on the system it's for, from a copy of this repository:

```bash
uv run --no-dev --group installer python packaging/build.py
```

It saves the installer in `dist/`. Each system needs a few build tools as well;
[`packaging/README.md`](packaging/README.md) lists them.

### With Python

Dogear runs on Windows, macOS, and Linux with Python 3.11 or newer. It needs two free
programs first:

| | ffmpeg (audio) | eSpeak NG (pronouncing unusual names) |
|---|---|---|
| Windows | `winget install Gyan.FFmpeg` | `winget install eSpeak-NG.eSpeak-NG` |
| macOS | `brew install ffmpeg` | `brew install espeak-ng` |
| Debian, Ubuntu | `sudo apt install ffmpeg` | `sudo apt install espeak-ng` |

Then install Dogear itself with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/18hrshift/dogear-app
```

(`pipx install git+https://github.com/18hrshift/dogear-app` works too.) Dogear isn't on PyPI
yet. Don't `pip install dogear`: that name on PyPI belongs to a different project. For Kindle books, also install [Calibre](https://calibre-ebook.com). For scanned PDFs,
install Tesseract OCR (see "Scanned PDFs" below).

### With Docker

For a shared computer or a server, such as at a library or school, there is a Docker image
with everything inside, the voice and Tesseract (with English) included, running the web
page: see [`docker/README.md`](docker/README.md).

## Use it in a web browser

If you'd rather not type commands, Dogear has a web page. Start it with:

```bash
dogear serve
```

Dogear opens your web browser at its page, with a link that works only once. If it
doesn't, open the link Dogear shows in its window. That link looks like
`http://127.0.0.1:8765/start?key=abcde-fghjk-mnpqr-stuvw-xyz23`.

On the page:

1. Choose a book file (EPUB, PDF, Word, HTML, Markdown, MOBI, or AZW3) and upload it.
   Dogear reads it to find its chapters. A scanned PDF is read with OCR first (see
   "Scanned PDFs" below), which takes a few seconds a page: the book's page says how far
   it has got and about how long is left, as it does while making. "Stop reading this
   book" stops it, and Read again carries on with the pages not read yet.
2. Choose what to make, the voice, the speed, and which sections to read. To hear the
   voice before making a whole book, choose one or two sections.
3. To fix names the voice says wrong, follow "check how words are said" on that page
   (or on a finished book's page). Dogear lists the 50 words the voice doesn't know that
   come up most, each with a Listen button and a "Say it like" field, and a link to show
   the rest. Write "utter son" for Utterson, say, choose Save, and Listen again. The list
   is kept with the book, so it is still there after Dogear restarts. Save changes only the words you
   changed, so a page left open in another tab never undoes a change made since; if one
   of your words was also changed there, Dogear saves nothing and says so beside it. The
   Pronunciations page lists every word you have fixed, and can say any short text. The
   page keeps these words in `words.toml` in its web folder and uses them for every book
   it makes, after any pronunciation files in your settings, so the page's spellings win.
4. Choose Start making. The book's page says how far it has got, with its latest
   progress lines. A warning repeated many times in a row is one line that says how many
   times. Dogear makes one book at a time, and others wait their turn. You can close the
   page and come back later.
5. When the book is ready, download what Dogear made. Folders (MP3 and DAISY) download
   as a zip file. The page also shows the folder on the computer where everything is
   saved.

If Dogear can't read a book, its page says why and what to do. Once that's done (for a
Kindle book, say, installing Calibre), choose Read again on the book's page.

The page works without a mouse and without JavaScript, and it is built to work with
screen readers. If something on it is hard to use, please tell us.

**Keep Dogear running.** The page only works while Dogear runs. Keep the window where
you started it open. To stop Dogear, press Ctrl+C in that window. A book being made
stops too. Its page then has a Start again button, which carries on where it stopped:
every sentence already spoken is kept. A book that was still being read is read again
when Dogear next starts. Closing the window stops Dogear and its books as well.

Dogear runs once for each web folder. Starting it again while it runs says so: use the
link in the window where it's running.

On Linux and macOS, only the user who runs Dogear can open its web folder's books and
what's made from them.

**The access link.** The part after `key=` is a password, made the first time Dogear's
page starts and kept for later. Opening the link lets this browser in, so you need it
once per browser. Anyone without it sees a page asking for it. To make a new key, for
example after sharing the link by mistake, delete `access-key.txt` from Dogear's web
folder (Dogear names the folder when it starts) and start Dogear again.

**From a phone or tablet.** To use Dogear from a phone on the same Wi-Fi network as the
computer, start it with:

```bash
dogear serve --lan
```

Dogear shows a second link, with the computer's address on the network. Open it on the
phone. Anyone on the network who has the link can use Dogear, and the connection isn't
encrypted, so use `--lan` only on a network you trust, such as your home Wi-Fi. Where the
address Dogear finds would be wrong, as in a container, `show_network_address = false`
under `[web]` leaves the second link out, and Dogear says to use the first one with this
computer's network address in place of `127.0.0.1`.

Other options: `--port 8766` uses another port (if another program has 8765), and
`--no-browser` doesn't open a browser. In a settings file, `[web]` sets the port, the largest book file
the page accepts (200 MB at first), how long reading a book (or checking its words, or
saying something for Listen) may go without any progress before Dogear takes it to be
stuck and stops it (15 minutes at first), the folder where books are kept, how many
words the page's pronunciation file holds and how long each word and spelling may be, and
how many of a book's words "check how words are said" keeps and lists (500 at first).

## Make an audiobook

```bash
dogear make mybook.epub
```

That's it. Dogear saves `<book title>.m4b` next to the book, with a chapter marker for every
chapter, the cover, and the title and author filled in. The first time, it downloads the
voice (about 340 MB, once).

It takes a while. On a 6-core desktop processor from 2017 (Intel i7-8700K), Dogear spoke
about three times faster than real time, so an hour of audio took about 20 minutes. It
prints a plain progress line now and then, with an estimate of the time left. **Stopping
is safe**: press Ctrl+C, run the same command later, and it carries on where it stopped.
Every spoken sentence is kept, so fixing one word in a book re-speaks only the sentences
that contain it.

It needs disk space, too. The spoken sentences take about 170 MB for each hour of audio,
in Dogear's speech cache (in your usual cache folder; `cache_dir` under `[render]` in a
settings file moves it). While it works, it needs about twice that again, and then room for
what it saves. Before speaking anything, Dogear estimates what the book needs on each disk.
If a disk doesn't have it, Dogear stops at once and says how much is needed, how much is
free, and what to do. The estimate counts every sentence as new, so carrying on after a stop
may ask for more than it uses. `space_margin_percent` under `[output]` (20 at first) adds
room to it.

Useful options:

```bash
dogear make mybook.epub --format mp3      # a folder of numbered MP3s plus a playlist
dogear make mybook.epub --format daisy    # a DAISY talking book (see below)
dogear make mybook.epub --format m4b,mp3  # the M4B and the MP3 folder
dogear make mybook.epub --sections 3      # just section 3, to try the voice first
dogear make mybook.epub --voice bf_emma   # another voice; see `dogear voices`
dogear make mybook.epub --speed 1.0       # faster (the default is 0.9)
dogear make mybook.epub -o ~/Audiobooks   # save somewhere else
dogear make mybook.epub --words names.toml  # your own pronunciations (see below)
```

### EPUB talking books

```bash
dogear make mybook.epub --format epub
```

This saves `<book title> talking book.epub`, an EPUB 3 book with the text and the
narration in step (Media Overlays). A reading app that plays them shows the text and
highlights each sentence as it is read, in black on yellow and underlined. In it:

- **A title page**: the book's own, or else the title track (unless
  `announce_title = false`). Then one part per section of the book.
- **A table of contents with every heading**, nested by level. Reading apps move through
  a talking book by its table of contents, so every heading is there.
- **Print page numbers**, when the source book has them: shown where each page begins
  and listed, so an app can go to a page. With `announce_pages = true` they are read
  aloud too, marked so an app can skip them. The book names where the numbers came from:
  the source book's identifier, or its title and authors if it has none.
- **Notes** shown where they are read, **tables** with their column headings, and
  **image descriptions** as text. Dogear keeps no pictures, only their descriptions.
- **Accessibility information** that says what the book has, that its voice is
  synthetic, and that no person has checked it.

[Thorium Reader](https://www.edrlab.org/software/thorium-reader/) (free for Windows,
macOS, and Linux; 3.5.1 is current) plays EPUB talking books. Add the book to its library
and open it to hear it with the text highlighted. Thorium's table of contents takes you
to any heading, and its Go to page command to a print page.

### DAISY talking books

`dogear make mybook.epub --format daisy` saves a folder named `<book title> DAISY` that
holds a DAISY 2.02 talking book, with the full text and the full audio:

- `ncc.html`, the book's map: every heading, at its level, and every print page.
- For each section, an MP3 file, its text as an `.html` file, and a `.smil` file that
  ties each sentence of the text to the stretch of audio that reads it.

A listener can move by heading level and by print page, and an app that shows the text
highlights each sentence as it's read. Page numbers read aloud (`announce_pages = true`)
are marked so that players that let you turn page numbers off can skip them.

To listen:

- **Thorium Reader**, a free reading app for Windows, macOS, and Linux: open `ncc.html`
  from the folder.
- **Dolphin EasyReader**: zip the folder and import the zip.
- **A DAISY player**, such as a Victor Reader Stream or a Plextalk Pocket: copy the whole
  folder to the player's memory card, where its manual says talking books go.

These all list DAISY 2.02 among the formats they read. Dogear's DAISY books pass the
DAISY Pipeline 2 validator, but they haven't been tried on these players yet. If you try
one, please tell us how it went.

### Check a book before making it

```bash
dogear outline mybook.epub                # title, authors, and every section
dogear text mybook.epub --section 3       # the text Dogear found in section 3
dogear text mybook.epub --spoken          # every line exactly as the voice will say it
dogear text mybook.epub --spoken --voice fr_FR-mls-medium  # ...with another main voice
dogear download                           # fetch the voice now, before going offline
dogear download --check                   # check the voice files, and fix damaged ones
```

Everything Dogear prints is plain text, written to read well with a screen reader. A
scanned PDF is read with OCR first, by any of these (see "Scanned PDFs" below).

For other programs, `dogear outline mybook.epub --json` prints the same facts as JSON: the
`title`, `authors`, `language`, `source_format`, and `words`, and `sections`, each with its
`number`, `title`, `kind` (`front`, `body`, or `back`), `words`, `first_page` and
`last_page` (print page labels, or null), and counts of `images`, `notes`, and `tables`.
Errors are still the plain `Error:` and `What to do:` lines, and the exit status is 1.

### Scanned PDFs

A scanned PDF is pictures of pages, with no text in it for Dogear to read. When Dogear
finds one, it reads the words in the pictures first, with OCR (optical character
recognition) by the free program [Tesseract](https://github.com/tesseract-ocr/tesseract),
on your computer, then goes on as with any book. Every command that reads a book does
this (`outline`, `text`, `make`, and `words`), and so does the web page.

It takes a while: on the 2017 desktop processor above, a full page of a novel took about
2.5 seconds at the usual 300 dpi, so a 300-page book would take about 12 minutes. Dogear
says so when it starts, then says how far it has got after the first page and every 10
pages, with the time left.

**Stopping is safe**: each page is kept in Dogear's cache folder as it is read, so after
Ctrl+C, the same command carries on from the page it stopped at. Once every page is read,
the text is kept, so reading the same file again (`dogear make` after `dogear outline`,
say) needs no OCR at all. Dogear checks that it's the same file by its contents, so a
renamed copy counts too.

Tesseract has to be installed for this:

- Windows: `winget install UB-Mannheim.TesseractOCR`
- macOS: `brew install tesseract`
- Debian, Ubuntu: `sudo apt install tesseract-ocr`

Dogear's installers don't include it, so install it as well on a computer with an
installer build. The Docker image has it, with English.

Settings, under `[ocr]`: `language` is the book's language as Tesseract names it, such as
`"eng"` (the default) or `"eng+fra"`. Each one needs its language pack (Debian, Ubuntu:
`sudo apt install tesseract-ocr-fra`; macOS: `brew install tesseract-lang`), and Dogear
says which is missing. `dpi` is how sharply pages are drawn for Tesseract (300). Changing
either, or updating Tesseract or a language pack, reads the book again. A page whose picture would be over
`max_page_megapixels` (100) or `max_page_side_pixels` (32,767 pixels wide or tall) is
drawn at a lower resolution; one that would need less than 70 dpi to fit, a page many feet
across, isn't read at all, and Dogear says which page it is. To have Dogear stop at a scanned PDF
and say so instead, set `auto_ocr = false` under `[pdf]`.

`dogear ocr scanned.pdf` saves a copy of a scanned PDF with its words added as a text
layer over the pictures, as `scanned_ocr.pdf`, for other programs to read as well. `--language` and
`--dpi` change those settings for one run. Unlike reading a scanned PDF for a book, it
starts again from the first page if it is stopped.

### Fix how a word is said

The voice reads most English well, but names and made-up words can come out wrong. You
can fix them in a pronunciation file: a plain text file you write yourself, such as
`names.toml`:

```toml
[words]
Utterson = "utter son"             # the voice reads this spelling instead
"St. John" = "sin jun"             # several words, or a word with a dot, go in quotes
Lanyon = { phonemes = "lˈænjən" }  # exact phonemes, for experts (see below)
```

- Each line under `[words]` is one entry: the word as the book writes it, then how to
  say it. Most entries are a spelling that sounds right: the voice reads "utter son"
  wherever the book says "Utterson".
- A word matches whole and in any capitals: "Utterson" also matches "UTTERSON" and the
  possessive "Utterson's", but not "Uttersons". When the book's word starts with a
  capital, so does the spelling.
- Put the word in quotes if it has spaces, dots, apostrophes, or letters other than A
  to Z (such as "Zoë").
- When entries overlap, the longest wins: with entries for "John" and "St. John",
  "St. John" uses its own.
- Entries apply to English text. In a book with passages in another language read by a
  voice of their own (see "Books with more than one language"), an entry for that
  language lists it: `Mme = { sounds_like = "madame", languages = ["fr"] }`, or
  `languages = ["en", "fr"]` for both.

Use the file for one run with `--words` (`make`, `text`, `words`, and `say` all take
it), or list it in your settings so every book uses it:

```toml
[pronunciation]
files = ["~/names.toml"]
```

When two files list the same word, the later one wins; a `--words` file comes after the
ones in your settings.

To fix a book's words:

1. List the words the voice doesn't know. It guesses these, and they're usually names.
   The first time, this downloads a small language model (about 13 MB), not the whole
   voice.

   ```bash
   dogear words mybook.epub                # all of them, most frequent first
   dogear words mybook.epub --sections 3   # just section 3
   ```

   Each is a numbered line, such as "1. Utterson, 131 times." Words your pronunciation
   files already cover aren't listed.
2. Listen to one in a sentence. `dogear say` speaks the way `dogear make` would and
   saves a WAV file:

   ```bash
   dogear say "Mr. Utterson the lawyer." -o test.wav
   ```

3. If it sounds wrong, add an entry, then listen again with the file:

   ```bash
   dogear say "Mr. Utterson the lawyer." -o test.wav --words names.toml
   ```

4. Make the book with the file. If you made it before, only the sentences whose words
   changed are spoken again; the rest are reused.

`dogear text mybook.epub --spoken --words names.toml` shows every line with your
spellings in place.

For other programs, `dogear words mybook.epub --json` prints the same list as JSON, in
ASCII: an object whose `words` is a list, most frequent first, of objects with the
`word` and its `count`. Errors are still the plain `Error:` and `What to do:` lines, and
the exit status is 1.

To say a text that starts with a hyphen, put `--` before it, so it isn't read as an
option: `dogear say -o test.wav -- "-ing words"`.

**Exact phonemes, for experts.** An entry can give the phonemes the voice should use,
written in the symbols of misaki, the voice's pronunciation dictionary
([the list of symbols](https://github.com/hexgrad/misaki/blob/main/EN_PHONES.md)).
`dogear say "Lanyon" -o test.wav --phonemes` shows the phonemes the voice uses now, a
good place to start. Phoneme entries work with Kokoro's voices only (with another engine's,
Dogear skips them and says so), and only for plain
words (letters, spaces, hyphens, and apostrophes): Dogear spells out abbreviations and
numbers before the voice reads them, so write "Saint John", not "St. John". A symbol the
voice doesn't have stops Dogear with an error that names it.

### What it reads, and how

- **EPUB, PDF, and Kindle** (MOBI and AZW3, through Calibre). EPUB chapters come from the
  book's table of contents. Scanned PDFs are read with OCR.
- **Word documents (DOCX), HTML, and Markdown.** These have no table of contents, so each
  chapter starts at a heading. Dogear uses the highest heading level that appears more
  than once, so a title above the chapters gets a short section of its own. Word's
  heading styles count even when renamed or in another language, and its lists, tables,
  footnotes, endnotes, and image descriptions (alt text) are read. Word's headers,
  footers, and comments aren't, nor is its table of contents. An HTML file is read on its
  own: nothing it links to, such as pictures or style sheets, is downloaded. A Markdown
  file can start with YAML front matter giving its `title`, `author` (or `authors`), and
  `lang`.
- **A title track** opens the book with its title and who wrote it.
- **Headings** are read with a pause after them. Longer pauses mark bigger breaks: a
  sentence, a paragraph, a scene break, a chapter.
- **Image descriptions** are read as "Image: ...". Decorative images are skipped.
- **Footnotes** are read right after the paragraph that refers to them.
- **Tables** are read row by row, with each column's heading.
- **Numbers, dates, money, times, and units** are spoken the way a person would say them:
  "$5.99" is "five dollars and ninety-nine cents", "Chapter IV" is "Chapter four".
- **Print page numbers** are kept for navigation and not read aloud, unless you ask.

Every one of these is a setting. Put the ones you want to change in a TOML file and pass
it with `--config`:

```toml
[voice]
voice = "bf_emma"
speed = 1.0

[narration]
footnotes = "end_of_section"   # or "after_paragraph", "where_printed", "skip"
announce_pages = true          # say "Page 42" where each print page begins
```

The full list, with what each one does, is in
[`src/dogear/defaults.toml`](src/dogear/defaults.toml). Each voice engine has a table of
its own: Kokoro's `[kokoro]` (which build of the model, and how many CPU threads) is in
[`src/dogear/voice/kokoro.toml`](src/dogear/voice/kokoro.toml).

To use the same settings file for every command without typing `--config` each time, set
the `DOGEAR_CONFIG` environment variable to its full path. `--config` still wins when given.

## Why another audiobook tool?

There are good ebook-to-audiobook converters already. None of them are built for people who
can't see the screen. Dogear is:

- **Made for listening without sight.** It makes DAISY talking books, not just MP3s, and
  EPUB 3 with synchronized audio is next. Listeners can jump by heading or by print page.
- **Structure-aware.** Headings, image descriptions, footnotes, and tables are read on purpose,
  not dumped as a wall of text. Page numbers become navigation points instead of being spoken.
- **Offline and CPU-first.** A normal computer is enough. No GPU, no account, and your books
  never leave your machine, unless you add a voice from a speech service and allow it to
  send them (see "Voices from speech services" below).
- **Accessible to run.** The command line and the web page are designed to work with
  screen readers (NVDA, JAWS, VoiceOver, TalkBack, Orca). The web page aims at WCAG 2.2
  AA, and CI checks every page with axe-core; testing with screen-reader users is still
  to come.

## Outputs

| Format | Good for | Status |
|---|---|---|
| M4B with chapters | Phone audiobook apps | Works |
| MP3 folder + playlist | Anything that plays audio, including older players | Works |
| EPUB 3 with Media Overlays | Read along with highlighted text (low vision, dyslexia) | Works |
| DAISY 2.02 | Talking-book players and apps, with heading and page navigation | Works |

## The voice

Dogear speaks with [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0),
an open model small enough to run on a CPU. It has 28 English voices, American and British;
`dogear voices` lists them with Kokoro's own quality grades. The default, `af_heart`, is the
best rated. Pronunciation comes from the dictionaries Kokoro was trained with
([misaki](https://github.com/hexgrad/misaki)), with eSpeak NG for words they don't have.
Your own pronunciation files come first.

Every voice file Dogear downloads is checked against a checksum pinned in Dogear before it
is used, and the files that come with an installer were checked when it was built. If a
file is damaged later, by a failing disk, say, Dogear may stop with an error that says
so. `dogear download --check` checks every voice file Dogear has against its checksum,
including the ones that came with an installer and those of voice engine plugins such as
Piper, lists each as good or damaged, and downloads the damaged ones again. Files that came with an installer can't be downloaded
again in place: reinstall Dogear to fix one. The command exits with status 1 if a file is
still damaged at the end. The model alone is 325 MB, so the check takes a few seconds,
with a progress line now and then.

### More voices: Piper

Other voice engines can be added to Dogear as plugins. The first is
[Piper](plugins/piper/README.md): eight more voices, quick on an ordinary computer, five of
them English. It's a separate package because Piper's license is the GPL. The installers
and the Docker image can't take plugins yet, so this is for Dogear installed with Python.
To install Dogear with Piper, run this one command (it works whether or not Dogear is
installed already):

```bash
uv tool install git+https://github.com/18hrshift/dogear-app \
  --with "dogear-piper @ git+https://github.com/18hrshift/dogear-app#subdirectory=plugins/piper"
```

Then `dogear voices` lists Piper's voices after Kokoro's, and you choose one by its name,
such as `dogear make mybook.epub --voice en_US-ljspeech-high`. The web page lists them
too, in a group of their own. Kokoro stays the voice Dogear uses unless you choose another.
With a Piper voice, spellings in your pronunciation files work, but phoneme entries are
skipped (they're written in Kokoro's symbols), and `dogear words` doesn't apply.
[`plugins/piper/README.md`](plugins/piper/README.md) has the details, and
[`docs/voice-engines.md`](docs/voice-engines.md) explains why Piper, and how to write a
plugin for another engine.

### Voices from speech services

The [speech service plugin](plugins/openai_speech/README.md) adds the voices of any service
that takes requests the way OpenAI's speech API does: OpenAI itself, services that copy
its API, or a speech server running on your own computer. Install it the same way as
Piper, with `dogear-openai-speech` and `subdirectory=plugins/openai_speech`.

A service on another computer is sent the book's text, sentence by sentence, as it is
spoken. That service then has the text, its own terms and privacy policy apply, and it
usually charges: with OpenAI's `gpt-4o-mini-tts`, a 100,000-word novel was about $10 at
the prices of October 2026 (the plugin's README has the sums). So Dogear never uses such
a "cloud voice" unless you allow it:

```bash
dogear make mybook.epub --voice marin --allow-cloud   # this run only
```

or with `allow_cloud = true` under `[voice]` in your settings file, which also lets the
web page offer cloud voices. Without it, Dogear stops before reading the book, says which
computer the voice would send the text to, and sends nothing. With it, Dogear says once,
in plain sentences, where the text is going and that the service's terms apply, before
sending any; the web page shows the same words and asks before it starts, naming every
cloud voice the book may use, including one `[voice.languages]` gives another language.
`dogear voices`
marks every such voice with the computer it sends text to, such as "(sends the book's
text to api.openai.com)". The service's key goes in an environment variable
(`OPENAI_API_KEY` at first), never in a settings file.

To keep the text on your computer and still use the plugin, run a speech server there,
such as Kokoro-FastAPI, and point the plugin at it (`base_url = "http://localhost:8880/v1"`
under `[openai_speech]`). A server at `localhost`, `127.0.0.1`, or `[::1]` counts as this
computer, so Dogear doesn't ask or warn. OpenAI says its speech models shut down on
January 6, 2027.

### Books with more than one language

Kokoro speaks English only. Piper adds voices in French, German, and Spanish. With Piper
installed, Dogear can read:

- **A whole book in French, German, or Spanish.** Choose a voice in its language:
  `dogear make roman.epub --voice fr_FR-mls-medium`. On the web page, "Choose what to
  make" lists the voices for the book's language first and picks the best of them. A book
  that doesn't say its language is taken to be in the language of the voice you chose,
  and one marked with the wrong language is read as another with `--language`, such as
  `--language en`. A book in a language no voice speaks stops before anything is spoken,
  and says which voices could read it.
- **A French quotation in an English book.** Say in your settings which voice reads each
  language. Kokoro then reads the English, and Piper the French:

  ```toml
  [voice.languages]
  fr = "piper:fr_FR-mls-medium"
  ```

  A language is written as a short code: `fr` is every kind of French, `fr-CA` only
  Canadian French, and the most exact one that fits wins. Each voice must speak its
  language, which Dogear checks when it starts. To let Dogear pick the best installed
  voice for any language you haven't listed, set `other_languages = "best"` under
  `[voice]`.

Without these settings, one voice reads the whole book, as it always has: an English
voice reads a French paragraph as best it can, and `dogear make` says how many sentences
that was and which voices could read them. That is the default on purpose: a voice that
changes in the middle of a chapter is jarring, and books are sometimes marked with the
wrong language.

Which passages are in another language? Dogear goes by what the book marks: EPUB, HTML,
Markdown, and Word books can mark a paragraph or a chapter as French. Many books mark
nothing. To have Dogear guess, turn on language detection, which runs on your computer:

```toml
[language_detection]
enabled = true
```

It only looks for languages it has a voice for, never changes a language the book marks,
and guesses carefully: a paragraph needs at least 30 letters ("Oui, monsieur." is too
short to tell) and a sure guess, and a long paragraph is also checked a sentence at a
time. `defaults.toml` has the settings. To see what it found, and which voice reads each
part, before making anything:

```bash
dogear text mybook.epub --spoken
```

Lines such as `[In fr, read by piper:fr_FR-mls-medium.]` say where each language starts.

What changes with the language:

- **Words Dogear adds**, such as "Image", "Note", and "Page", are said in French, German,
  or Spanish for passages in those languages, and in English for any other, which
  `dogear make` says once. Change them, or add another language's, under
  `[narration.words.fr]` and so on in your settings.
- **Numbers and abbreviations** in English text are spelled out by Dogear. In another
  language they go to the voice as written: Piper reads "3 500" in French as "trois mille
  cinq cents".
- **Pronunciation files** apply to English text. For an entry in another language, list
  its languages: `Mme = { sounds_like = "madame", languages = ["fr"] }`.
- **Talking books** mark each passage and sentence with its language, so a reading app
  or screen reader reading the text switches too, and their details name every voice.
- **A second voice may sound quieter or louder** than the first: Dogear evens out the
  volume once per chapter.

## Get involved

We especially want **blind and low-vision testers** from the start. See
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## A note on books and copyright

Dogear is for converting books you have the right to convert. It has no hosting or sharing
features. If you are print-disabled, check accessible libraries first (for example Bookshare,
or your national library service for the blind). Many books already exist in accessible
formats there.

## Origins

Dogear grew out of [`18hrshift/audiobook-maker`](https://github.com/18hrshift/audiobook-maker),
a personal PDF-to-audiobook tool. Its chapter detection and text cleanup are the starting point.

## License

[Apache-2.0](LICENSE).
