# Standalone installers

For maintainers. These scripts build Dogear's installers for Windows, macOS, and Linux:
Dogear frozen with its own Python, plus ffmpeg, ffprobe, eSpeak NG, and the default voice,
so a helper with no Python can install it and make a book offline.

## What gets built

Each build makes the installer for the platform it runs on, in `dist/`:

- **Linux x86_64:** `Dogear-<version>-linux-x86_64.tar.gz`. Unpacked, it holds the bundle,
  `install.sh`, `uninstall.sh`, and `README.txt`. `install.sh` installs for one user: the
  program in `~/.local/opt/dogear`, a `dogear` link in `~/.local/bin`, and a `.desktop`
  entry with `Terminal=true` that runs `dogear serve`, so the desktop opens its own
  terminal window, and a second, "Dogear for phones and tablets", that runs
  `dogear serve --lan`. A tarball was chosen over an AppImage because an AppImage usually
  needs FUSE 2 (libfuse2), which Ubuntu stopped installing by default in 22.04, and can't
  open a terminal window by itself.
- **macOS arm64:** `Dogear-<version>-macos-arm64.dmg`, holding `Dogear.app`, a link to
  Applications, and `Read me first.txt`. The app's own program is `macos/launcher.sh`: it
  writes a small `.command` file to the temporary folder and opens it with Terminal, which
  runs `dogear serve` in a new window. A Terminal window was chosen because Dogear needs a
  visible "keep this open" window that a screen reader can read and that stops Dogear
  when closed, and Terminal opens `.command` files without the permission prompt that
  scripting Terminal with AppleScript would raise. The bundle is in
  `Dogear.app/Contents/Resources/dogear`.
- **Windows x86_64:** `Dogear-<version>-windows-x86_64-setup.exe`, made with Inno Setup
  from `windows/dogear.iss`. It installs for the current user without administrator
  rights, into `%LOCALAPPDATA%\Programs\Dogear`, adds "Dogear" to the Start menu, and
  offers to put `dogear` on the user's PATH. A second Start menu entry, "Dogear for phones
  and tablets", runs `dogear.exe serve --lan`. The shortcut runs `dogear.exe serve`, a
  console program, so Windows opens a console window: closing it stops Dogear. The
  uninstaller takes the folder back off PATH.

In every case the bundle is the `dogear` command itself: `dogear make`, `dogear outline`,
and the rest work as in a Python install. The shortcuts run `dogear serve`, Dogear's local
web page. When frozen, `dogear serve` runs each book as a child process with
`[sys.executable, "make", ...]`, which is this program.

## How it works

`build.py` runs these steps; each is a module in `dogear_build/`.

1. **pins, fetch:** download the source archives in `pins.toml` into
   `build/packaging/downloads`, each checked against its SHA-256 (with a mirror for LAME).
2. **programs:** run `build-programs.sh`, which builds LAME, then FFmpeg with LAME (LGPL:
   no `--enable-gpl`, autodetection off so nothing from the build machine is linked by
   accident), then eSpeak NG with CMake. On Windows the same script runs in MSYS2's UCRT64
   shell with MinGW-w64 GCC, linking statically. The result is kept in
   `build/packaging/programs` and reused until the script or a pin changes.
3. **freeze:** PyInstaller freezes `entry.py` (which calls `dogear.cli.main`) into a folder,
   `dist/dogear`, with its contents in `dist/dogear/_internal`. A folder, not one file, so
   the 800 MB bundle isn't unpacked again on every start. Dogear's own package metadata
   goes in too, because Dogear finds Kokoro through its entry points
   (`docs/voice-engines.md`). Only Dogear's: a voice engine plugin installed where the
   build runs is never frozen, and the smoke test fails if `dogear voices` lists one.
4. **voice, assemble:** copy the programs into `_internal/bundled/tools`, and the default
   voice into `_internal/bundled/models`: the Kokoro build `model` from
   `src/dogear/voice/kokoro.toml` and the voice `[voice] voice` from `defaults.toml`
   (whose engine must be Kokoro), the tokenizer, and spaCy's English model, all
   checked against `src/dogear/voice/catalog.toml`. `models/manifest.json` records each
   file's SHA-256. Then `LICENSE.txt` and `THIRD-PARTY-NOTICES.txt` go beside the program.
5. **notices:** `THIRD-PARTY-NOTICES.txt` lists every bundled part with its license text:
   the programs (texts from their source archives, with the exact source link and
   SHA-256), the voice, Python, every Python package Dogear requires (found from package
   metadata), and PyInstaller's bootloader.
6. **linux, macos, windows:** make the installer.

At run time, `src/dogear/bundle.py` answers "is this a standalone build, and where are its
programs and voice files". `tools.find_tool` uses the program set in config first, then the
bundled one, then PATH, then known install folders. The catalog uses a bundled voice file
when the manifest's SHA-256 is the one it expects, so nothing is hashed at startup; other
voices download to the per-user folder as usual. The bundled espeak-ng runs in its own
folder and is told `--path=.`, so the install folder's name never goes on its command line,
which Windows hands it in the system code page (a user name in another script wouldn't
survive). It's also built with a larger path buffer (`N_PATH_HOME=1024`): by default it
can't use a data folder whose path is longer than 160 characters, and its own build reads
the data it compiles from the build folder, which can be that deep.

A frozen Dogear on Linux runs with `LD_LIBRARY_PATH` pointing into its own folder (set by
PyInstaller's bootloader). `tools.run_tool` gives every program it runs the value from
before, via `bundle.program_environment`, so a system program such as Tesseract loads its
own libraries, not Dogear's older copies. On Windows the bootloader sets a DLL folder
instead, which programs started from Dogear inherit; Dogear's own programs are linked
statically and Tesseract and Calibre ship their own DLLs, so no clash is expected, but
that hasn't been tried.

## Building

On every platform you need [uv](https://docs.astral.sh/uv/), and a checkout of this
repository. Then, from the repository folder:

```bash
uv run --no-dev --group installer python packaging/build.py
uv run --no-project python packaging/smoke_test.py dist/dogear/dogear   # dogear.exe on Windows
```

`--bundle-only` stops before the installer; `--work` and `--dist` move the work folder
(default `build/packaging`) and the output folder (default `dist`). The first build
downloads about 370 MB (the voice and the source archives) and compiles for several
minutes; later builds reuse both.
The smoke test runs the bundle with an empty home folder, nothing on PATH, and downloads
blocked, so it passes only on what the bundle carries. Besides `say` and `make`, it starts
`dogear serve` and uploads the test book, which only works if the bundle can run itself as
a child process.

What each platform needs as well:

- **Linux:** gcc, make, CMake, nasm, and zlib's headers. The bundle needs the glibc of the
  machine it was built on or newer (PyInstaller also copies that machine's C++ runtime
  in), so release builds are made in the manylinux_2_28 container (glibc 2.28), as CI
  does. With Docker, from the repository folder:

  ```bash
  docker run --rm -v "$PWD":/src -w /src -e HOST_IDS="$(id -u):$(id -g)" \
    quay.io/pypa/manylinux_2_28_x86_64 bash -c '
      dnf install -y -q nasm &&
      curl -LsSf https://astral.sh/uv/install.sh | sh &&
      UV_PROJECT_ENVIRONMENT=/tmp/venv UV_PYTHON_PREFERENCE=only-managed \
        ~/.local/bin/uv run --python 3.13 --no-dev --group installer \
        python packaging/build.py;
      status=$?; chown -R "$HOST_IDS" build dist; exit $status'
  ```

  `UV_PROJECT_ENVIRONMENT` keeps the container's Python out of your own `.venv`;
  `UV_PYTHON_PREFERENCE=only-managed` makes uv use its own Python, as the image's Pythons
  lack the shared library PyInstaller needs; `chown` gives you back the files it made.

- **macOS (Apple silicon):** Xcode's command line tools (`xcode-select --install`) and
  CMake (`brew install cmake`). The programs target macOS 14, the oldest that
  onnxruntime's macOS wheels support (`minimum_os` in `pins.toml`). Intel Macs aren't
  built for: onnxruntime no longer publishes Intel macOS wheels.
- **Windows:** [MSYS2](https://www.msys2.org) (in `C:\msys64`, or set `DOGEAR_MSYS2` to its
  folder) with these packages, installed from an MSYS2 shell:
  `pacman -S make diffutils tar xz mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-cmake
  mingw-w64-ucrt-x86_64-nasm mingw-w64-ucrt-x86_64-pkgconf mingw-w64-ucrt-x86_64-zlib`.
  And [Inno Setup 6](https://jrsoftware.org/isdl.php); its compiler, `ISCC.exe`, is found on
  PATH or in its usual install folder.

CI (`.github/workflows/installers.yml`) builds all three on pull requests that touch
`packaging/` or `src/`, and when run by hand. Each job installs its installer the way a
user would (silently, on Windows), runs the smoke test on the installed copy, and
uninstalls it. The installers are kept as workflow artifacts for 14 days; nothing is
published.

## Updating a pinned download

1. Download the new archive and note its SHA-256 and size, for example
   `curl -LO URL && sha256sum FILE && stat -c %s FILE` (on macOS, `shasum -a 256` and
   `stat -f %z`).
2. Change `version`, `url`, `sha256`, and `size` in `pins.toml`. Check `license` and
   `license_files` against the new release.
3. Build. The build rejects a download whose SHA-256 doesn't match, and rebuilds the
   programs because a pin changed. CI's cache key includes `pins.toml`, so it does too.

eSpeak NG's build fetches Sonic with git unless it's handed the source; `pins.toml` pins
the commit eSpeak NG asks for (`sonic`), so the build fetches nothing. When eSpeak NG is
updated, check `cmake/deps.cmake` in its source for the commit it wants.

The voice isn't pinned here: the build bundles what `defaults.toml` selects, pinned in
`src/dogear/voice/catalog.toml`.

## Sizes

Measured on the Linux x86_64 build of Dogear 0.0.1, made in the manylinux_2_28 container,
in megabytes (millions of bytes):

- The installer, `Dogear-0.0.1-linux-x86_64.tar.gz`: 488 MB.
- Installed: 805 MB. The voice is 341 MB, ffmpeg and ffprobe 57 MB, eSpeak NG and its
  data 19 MB, and Python with Dogear's packages 388 MB.

The Python part is large because the binaries keep their debug symbols. PyInstaller's
`--strip` cut it to about 240 MB in a build on Ubuntu 24.04, but in the manylinux
container it broke numpy's bundled OpenBLAS, so it's off; stripping selectively is a
possible saving. macOS and Windows haven't been measured yet; CI's "Report the size" step
prints them.

## Licenses

Dogear is Apache-2.0. The bundle adds:

- **FFmpeg** (ffmpeg and ffprobe): LGPL-2.1-or-later, built with **LAME**
  (LGPL-2.0-or-later) linked in. Both are run as separate programs.
- **eSpeak NG:** GPL-3.0-or-later, run as a separate program for words misaki doesn't know.
  Shipping a GPL program beside Dogear is aggregation; Dogear's own code stays Apache-2.0.
- **The voice:** Kokoro-82M (Apache-2.0) and spaCy's `en_core_web_sm` (MIT).
- **Python** (PSF-2.0) and the Python packages Dogear requires, under their own licenses
  (see the table in `AGENTS.md`), and PyInstaller's bootloader (GPL-2.0-or-later with an
  exception that lets frozen programs use any license).

`THIRD-PARTY-NOTICES.txt` in the bundle has every license text and, for FFmpeg, LAME, and
eSpeak NG, a link to the exact source archive and its SHA-256. The GPL and LGPL require
that source to stay available to anyone who has the binaries: when releases are published,
attach the source archives from `build/packaging/downloads` to each release too, rather
than relying on the upstream links alone.

## Not done yet: signing

The installers are unsigned, and the read-me files say how to get past each system's
warning:

- **Windows:** SmartScreen says "Windows protected your PC" for an unsigned installer
  from the internet. Choose "More info", then "Run anyway". A code-signing certificate
  would remove this; that's a cost and a decision for the project owner.
- **macOS:** Gatekeeper blocks an app that isn't signed and notarized. On macOS 15 and
  newer, the user opens System Settings, Privacy & Security, and chooses "Open Anyway";
  on macOS 14, Control-click and Open. Signing and notarizing need an Apple Developer
  account (paid, yearly), which the project owner would have to decide on. The bundle
  identifier in `dogear_build/macos.py` should be settled before then.
