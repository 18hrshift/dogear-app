"""Try a built Dogear offline, with nothing to lean on but what it carries.

    uv run python packaging/smoke_test.py PATH/TO/dogear

The program runs with an empty home folder, so no voice or speech cache from earlier
can be used; a PATH with nothing on it, so only the bundled ffmpeg and espeak-ng can be
found; and web proxies pointed at a closed port, so any download fails at once. Then it
runs dogear --version, voices, say, and make (the first section of the test book, as an
M4B), and starts the web page (dogear serve) and uploads the test book to it. It passes
only if each step works and no voice file was downloaded.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOK = next((ROOT / "tests" / "fixtures").glob("*.epub"))
CLOSED_PORT = "http://127.0.0.1:9"
# What Windows programs need from the environment to start at all.
WINDOWS_KEEP = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS")
MODEL_SUFFIXES = (".onnx", ".bin", ".whl", ".cfg")
SERVE_PORT = 18765  # not the default, so a Dogear already running here doesn't clash
READ_SECONDS = 120  # for the web page to read the test book


class Failed(Exception):
    pass


def check(passed: bool, problem: str) -> None:
    if not passed:
        raise Failed(problem)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: smoke_test.py PATH/TO/dogear", file=sys.stderr)
        return 2
    program = Path(args[0]).resolve()
    with tempfile.TemporaryDirectory(prefix="dogear-smoke-") as scratch:
        try:
            smoke_test(program, Path(scratch))
        except Failed as e:
            print(f"Failed: {e}", file=sys.stderr)
            return 1
    print("Every step passed.")
    return 0


def smoke_test(program: Path, scratch: Path) -> None:
    home, out = scratch / "home", scratch / "out"
    env = offline_environment(home, scratch / "empty-path", scratch / "tmp")
    for name in ("ffmpeg", "ffprobe", "espeak-ng"):
        check(not shutil.which(name, path=env["PATH"]), f"{name} is on the test's PATH")

    version = run(program, ["--version"], env)
    check(version.startswith("dogear "), version)
    voices = run(program, ["voices"], env)
    check("This is the one Dogear uses now." in voices, voices)
    # Kokoro, found through Dogear's own entry points, and no other engine: a plugin
    # installed where the build ran must not have come along.
    engines = re.findall(r"^(\w+), licensed ", voices, flags=re.MULTILINE)
    check(engines == ["Kokoro"], f"the build has the voice engines {engines}:\n{voices}")
    hello = out / "hello.wav"
    run(program, ["say", "Hello there.", "-o", str(hello)], env)
    check(hello.is_file() and hello.stat().st_size > 10_000, "say made no audio")
    made = run(
        program, ["make", str(BOOK), "--sections", "1", "--format", "m4b", "-o", str(out)], env
    )
    books = list(out.glob("*.m4b"))
    check(len(books) == 1, f"make saved no M4B:\n{made}")
    with books[0].open("rb") as f:
        check(f.read(8)[4:] == b"ftyp", f"{books[0].name} isn't an MP4 file")
    check_web_page(program, env)
    downloaded = [p for p in home.rglob("*") if p.suffix in MODEL_SUFFIXES]
    check(not downloaded, f"voice files were written outside the bundle: {downloaded}")


def check_web_page(program: Path, env: dict[str, str]) -> None:
    """Start `dogear serve`, open its access link, and upload the test book. The book's
    sections are listed only once a child `dogear outline` has run, so this also checks
    that the build can run itself as a child process, as every book on the page needs."""
    print(f"Running: dogear serve --no-browser --port {SERVE_PORT}", flush=True)
    server = subprocess.Popen(
        [str(program), "serve", "--no-browser", "--port", str(SERVE_PORT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        link = _access_link(server)
        browser = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),  # this computer, never through a proxy
            urllib.request.HTTPCookieProcessor(),
        )
        home_page = _read(browser, urllib.request.Request(link))
        check("Make a talking book" in home_page, "the web page didn't open")
        book = _upload(browser, f"http://127.0.0.1:{SERVE_PORT}")
        deadline = time.monotonic() + READ_SECONDS
        while 'name="section"' not in (page := _read(browser, urllib.request.Request(book))):
            check("Error:" not in page, f"the web page couldn't read the book:\n{page}")
            check(time.monotonic() < deadline, "the web page didn't list the book's sections")
            time.sleep(1)
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()


def _access_link(server: subprocess.Popen) -> str:
    seen = []
    for line in server.stdout:
        seen.append(line)
        found = re.search(r"http://127\.0\.0\.1:\d+/start\?key=\S+", line)
        if found:
            return found.group(0)
    check(False, "dogear serve printed no access link:\n" + "".join(seen))
    raise AssertionError  # unreachable: check raised


def _upload(browser: urllib.request.OpenerDirector, site: str) -> str:
    """POST the test book as the page's form does. Returns the book page's address."""
    boundary = "dogear-smoke-test-boundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="book"; filename="{BOOK.name}"\r\n'.encode(),
            b"Content-Type: application/epub+zip\r\n\r\n",
            BOOK.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        f"{site}/books",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Origin": site,
        },
    )
    with browser.open(request, timeout=60) as response:
        return response.geturl()  # where the upload redirected to


def _read(browser: urllib.request.OpenerDirector, request: urllib.request.Request) -> str:
    with browser.open(request, timeout=60) as response:
        return response.read().decode("utf-8")


def offline_environment(home: Path, empty: Path, tmp: Path) -> dict[str, str]:
    for folder in (home, empty, tmp):
        folder.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": str(empty),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "TMPDIR": str(tmp),
        "TEMP": str(tmp),
        "TMP": str(tmp),
        "NO_PROXY": "",
    }
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env[name] = CLOSED_PORT
    if os.name == "nt":
        env.update({k: os.environ[k] for k in WINDOWS_KEEP if k in os.environ})
    return env


def run(program: Path, args: list[str], env: dict[str, str]) -> str:
    print(f"Running: dogear {' '.join(args)}", flush=True)
    result = subprocess.run(
        [str(program), *args],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    output = result.stdout + result.stderr
    check(result.returncode == 0, f"dogear {args[0]} failed:\n{output}")
    return output


if __name__ == "__main__":
    raise SystemExit(main())
