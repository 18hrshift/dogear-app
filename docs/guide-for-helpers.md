# Making talking books with Dogear: a guide for helpers

This guide is for anyone making books for a blind or low-vision listener: a family member,
a friend, a volunteer, or the listener themselves. You don't need to know anything about
programming. If you can install a program and use a web browser, you can use Dogear.

Dogear reads a book file aloud with a natural-sounding computer voice and saves it as an
audiobook the listener can move around in: by chapter, by heading, and by the page numbers
of the printed book. It runs on your own computer. The book never leaves it, and once
Dogear is installed it works without an internet connection.

## Before you start

- **Check the listener's library first.** Libraries for print-disabled readers (such as
  Bookshare, or your country's library service for the blind) may already have the book,
  read by a person, at no cost. Dogear is for books they don't have.
- **A book you have the right to convert,** as an EPUB, PDF, Word (DOCX), HTML, Markdown,
  MOBI, or AZW3 file. Books bought from some stores are copy-protected (DRM), and Dogear
  can't read those. Free public-domain books from Standard Ebooks or Project Gutenberg
  work well.
- **A computer:** Windows (64-bit), a Mac with Apple silicon and macOS 14 or newer, or a
  64-bit Linux PC, with about 1 GB of free space for Dogear and more for the books you
  make (see "Disk space" below).
- **Time.** A long book takes hours. On a desktop computer from 2017, an hour of audio
  took about 20 minutes to make. The computer must stay on and awake while it works; you
  can do other things on it.

## Install Dogear

Dogear's installers hold everything it needs, the voice included. They aren't offered as
downloads yet. Until they are, someone comfortable with a terminal can make one, as the
"Install" section of the [README](../README.md) explains, or install Dogear with Python
instead.

Once it's installed, you start Dogear from the Start menu (Windows), from Applications
(Mac), or from your applications menu (Linux). A window opens with a few lines of text,
and your web browser opens at Dogear's page.

**Keep that window open while you use Dogear.** Closing it, or pressing Ctrl+C in it,
stops Dogear. You can close the browser whenever you like; the window is what matters.

## Choose what to make

Dogear makes four kinds of book. Pick by what the listener will play it on. You can make
more than one at once.

1. **M4B audiobook:** one file with chapters. For an audiobook app on a phone, tablet, or
   computer, such as Apple Books or BookPlayer on an iPhone, or an audiobook player app on
   Android. The listener can skip from chapter to chapter.
2. **DAISY talking book:** for DAISY players, such as a Victor Reader Stream, and DAISY
   apps. The listener can move by chapter, by heading, and by printed page number, and
   the full text is included.
3. **EPUB talking book:** the text and the audio together, with each sentence highlighted
   as it's read. For reading apps such as Thorium Reader (free, for Windows, Mac, and
   Linux). Good for a listener with some sight who likes to follow along.
4. **MP3 folder:** one MP3 file for each chapter, with a playlist. Plays almost anywhere,
   including older MP3 players.

If you're not sure, make an M4B for a phone, or a DAISY book for a DAISY player.

Dogear checks every DAISY and EPUB talking book with the standard checking tools. They
haven't been tried on every player yet, so if one doesn't play well on the listener's
device, please tell us (see "Getting help" below).

## Make a book

1. **Add the book.** On Dogear's page, under "Add a book", choose the book file and
   choose "Upload the book". Dogear reads it to find its chapters. This usually takes
   less than a minute. A scanned PDF takes longer, a few seconds a page, because Dogear
   first reads the words in the pictures of its pages (this is called OCR). The book's
   page says how far it has got and about how long is left. "Stop reading this book"
   stops it, and "Read again" carries on with the pages it hasn't read yet.
2. **Choose what to make.** Check one or more kinds of book. Choose a voice: the list
   starts with the best rated. If someone has added more voices (such as Piper's: see
   Dogear's README), each set is grouped under its engine's name, such as "Kokoro
   voices". Choose a speed: "a little slower" suits most listeners,
   and players can change the speed later. Then choose which sections to read; all of
   them are checked to start with.
3. **Try a short piece first.** For your first book, check only one or two short
   sections, so you can hear the voice and speed in a few minutes before making the whole
   thing.
4. **Check the names (optional).** Before a long book, follow "check how words are said"
   on the same page. It lists the names and unusual words the voice has to guess, so you
   can listen to them and fix any it gets wrong (see "A name or word is said wrong"
   below). It takes a minute and saves making the book twice.
5. **Start.** Choose "Start making". The book's page says how far Dogear has got and
   about how long is left. You can close the page and come back to it from "Your books".
   Dogear makes one book at a time; others wait their turn.
6. **Download.** When the book is ready, its page has a download link for each thing you
   made, with its size. A DAISY book or an MP3 folder downloads as a zip file. The page
   also shows the folder on the computer where everything was saved.

Stopping is safe. "Stop making this book" stops it, and "Start again" carries on where it
stopped: every sentence already spoken is kept. The same goes if the computer was turned
off part way.

## Give the book to the listener

- **M4B, for a phone:** copy the file to the phone (AirDrop, a cable, or a cloud drive
  such as iCloud Drive or Google Drive), then open it in the audiobook app.
- **DAISY, for a player:** unzip the download, and copy the whole folder onto the
  player's SD card or USB stick. The player's manual says which folder on the card it
  looks in for talking books.
- **EPUB talking book:** open the file in Thorium Reader, or copy it to the device where
  the listener reads.
- **MP3 folder:** unzip the download and copy the whole folder. Keep the files together:
  their names and the playlist keep the chapters in order.

## Disk space

Besides the books themselves, Dogear keeps every sentence it has spoken, so that it can
carry on after a stop and remake a book quickly with other choices. That takes about
170 MB for each hour of audio, so a long book can leave a few gigabytes behind. The words
Dogear read from scanned PDFs are kept there too, which takes little space. Once the
listener has the book, it is safe to delete. It is in a folder named `dogear`:

- Windows: `%LOCALAPPDATA%\dogear\Cache` (paste that into File Explorer's address bar).
- Mac: `Library/Caches/dogear` in your home folder.
- Linux: `.cache/dogear` in your home folder.

To delete a book Dogear made, along with the file you uploaded, use "Delete this book" on
its page.

Before it starts on a book, Dogear checks that the computer has room for it. If it
doesn't, the book's page says how much space the book needs and how much is free. Free up
some space (the folder above is safe to empty), or choose fewer sections or kinds of book,
then start it again.

## Using Dogear from a phone or tablet

Dogear can also be used from a phone or tablet on the same Wi-Fi network as the
computer, for example to upload a book from the phone. Start it with "Dogear for phones
and tablets" instead of "Dogear":

- Windows: in the Start menu, next to Dogear.
- Linux: in your applications menu, next to Dogear.
- Mac: there is no second app. Open Terminal (in Applications, Utilities) and run
  `/Applications/Dogear.app/Contents/Resources/dogear/dogear serve --lan`.

The window then shows a second link, with the computer's address on the network. Open
that one on the phone; sending it to yourself in a message saves typing it. Windows may
ask whether to let Dogear use the network: allow it on private networks. Anyone on the
network who has the link can use Dogear, so do this only on a network you trust, such as
your home Wi-Fi.

## When something goes wrong

Dogear says what went wrong and what to do next. The most common problems:

- **"protected by DRM":** the book is copy-protected. Look for a DRM-free copy, or ask the
  listener's library for the book.
- **"This PDF is scanned, and reading it needs Tesseract OCR":** the PDF is photos of
  pages. Dogear can read the words in them (OCR) with a free program, Tesseract, which
  Dogear's installers don't include. Install it, then choose "Read again" on the book's
  page. On Windows, get the installer from
  <https://github.com/UB-Mannheim/tesseract/wiki> and keep the folder it suggests, so
  Dogear finds it. On a Mac with Homebrew, run `brew install tesseract`; on Ubuntu or
  Debian, `sudo apt install tesseract-ocr`. (The Docker image has it already.)
- **"probably scanned page images":** the PDF is photos of pages, and reading them with
  OCR is turned off in Dogear's settings (`auto_ocr = false`). Set it back to `true`, or
  remove that line, then choose "Read again".
- **"found almost no text":** Dogear read a scanned PDF with OCR but found next to no
  words. Check the scan in a PDF reader: the pages should be sharp, printed text the right
  way up.
- **A Kindle book (MOBI or AZW3) won't open:** Dogear needs the free program Calibre to
  read Kindle books. Install it from calibre-ebook.com, then try again.
- **A Word document comes out as one long section:** Dogear starts a section at each
  heading, and Word marks headings with its Heading styles. In Word, give each chapter's
  title the Heading 1 style (on the Home tab, under Styles), save, and upload it again.
  The same goes for HTML and Markdown files: each chapter needs a heading.
- **"None of Dogear's voices speaks this book's language":** the book is marked as
  being in another language, and the voices that come with Dogear speak English only.
  If it really is in English (books are sometimes marked wrongly), check "Read it as
  English". Voices in French, German, and Spanish come with the Piper plugin, for Dogear
  installed with Python (the README says how). With one installed, "Choose what to make"
  lists the voices for the book's language first and picks the best of them.
- **"The voice you chose doesn't speak" the book's language:** choose one of the voices
  listed first, under "Voices for this book's language", or check "Read it as English"
  if the book is really in English.
- **A name or word is said wrong:** on the book's page (or on "Choose what to make",
  before making it), follow "check how words are said". Dogear lists the words the voice
  doesn't know, most frequent first; they are usually names. It shows the 50 that come up
  most, and "Show all" lists the rest. The list stays with the book, even after Dogear is
  closed and started again. Choose Listen beside a word
  to hear it. If it sounds wrong, type how it sounds in "Say it like", using ordinary
  words or parts of words (Utterson as "utter son"), choose Save, and Listen again. Then
  make the book again: only the sentences with those words are spoken again. Your
  spellings are used for every book, and the Pronunciations page, in the list at the top
  of every page, lets you change or remove them, add a word, and listen to any short
  text.
- **"The file may be damaged":** one of the voice's files on the computer is damaged.
  Reinstalling Dogear fixes it. Or, in a terminal, `dogear download --check` checks every
  voice file and downloads the damaged ones again (it needs the internet).
- **"This page needs Dogear's access link":** open the link shown in Dogear's window,
  which logs this browser in. It looks like `http://127.0.0.1:8765/start?key=...`.
- **"Port 8765 is in use already":** Dogear is probably running in another window. Use
  the link that window shows.
- **The page doesn't load at all:** Dogear isn't running. Start it again from the menu.

## Getting help

If something doesn't work, or is hard to use with a screen reader or magnifier, please
tell us at <https://github.com/18hrshift/dogear-app/issues>. Choose "Accessibility
barrier" for anything that got in your way. You don't need to know what caused it: say
what you tried to do and what happened.
