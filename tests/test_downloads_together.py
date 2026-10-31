"""Two Dogear processes downloading the same voice files at once, as `dogear make` and
the web page's Listen can on a first run. A server on this computer stands in for the
model host. It holds each download after its first piece until the test lets it go on,
so the downloads overlap every time. Nothing here uses the network."""

import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from dogear.voice.catalog import catalog
from dogear.voice.downloads import TEMPORARY_SUFFIX, WAITING

PIECE = 4096
MODEL_BYTES = bytes(range(256)) * 400


def model_wheel() -> bytes:
    """A wheel holding a model, big enough to send in many pieces."""
    model_dir = catalog()["spacy"]["model_dir"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(f"{model_dir}/config.cfg", "[nlp]")
        archive.writestr(f"{model_dir}/tagger/model", MODEL_BYTES)
    return buffer.getvalue()


class HeldServer:
    """Serves one file: its first piece at once, the rest once `go` is set. Counts the
    requests for it."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.requests = 0
        self.go = threading.Event()
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                server.requests += 1
                self.send_response(200)
                self.send_header("Content-Length", str(len(server.data)))
                self.end_headers()
                for start in range(0, len(server.data), PIECE):
                    self.wfile.write(server.data[start : start + PIECE])
                    self.wfile.flush()
                    server.go.wait(30)
                    time.sleep(0.002)  # so two downloads' pieces take turns

            def log_message(self, *args):
                pass

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.http.server_port}/en_core_web_sm-3.8.0.whl"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def wait_for_requests(self, count: int, *running: subprocess.Popen) -> None:
        deadline = time.monotonic() + 30
        while self.requests < count:
            for process in running:
                assert process.poll() is None, process.communicate()
            assert time.monotonic() < deadline, f"{self.requests} requests"
            time.sleep(0.01)

    def close(self) -> None:
        self.go.set()
        self.http.shutdown()
        self.http.server_close()


@pytest.fixture
def server():
    served = HeldServer(model_wheel())
    yield served
    served.close()


def start(script: str, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", script, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def finish(process: subprocess.Popen) -> str:
    out, err = process.communicate(timeout=60)
    assert process.returncode == 0, err
    return out


# `dogear words` getting spaCy's model, as the words page and a terminal both can: the
# catalog's wheel comes from the test's server. Each report line is printed as it comes.
PREPARE_SPACY = """
import json, sys
from dogear.config import default_config
from dogear.voice.kokoro_plugin import prepare_spacy

catalog_module = sys.modules["dogear.voice.catalog"]
pinned = catalog_module.catalog()
pinned["spacy"].update(url=sys.argv[2], sha256=sys.argv[3], size=int(sys.argv[4]))
catalog_module.catalog = lambda: pinned
config = default_config()
config["models"]["dir"] = sys.argv[1]
folder = prepare_spacy(config, lambda line: print(json.dumps(line), flush=True))
print(json.dumps({"folder": str(folder)}), flush=True)
"""


def test_two_dogears_on_a_first_run_get_one_good_model(tmp_path, server):
    models = tmp_path / "models"
    sha256 = hashlib.sha256(server.data).hexdigest()
    args = (str(models), server.url, sha256, str(len(server.data)))
    first = start(PREPARE_SPACY, *args)
    server.wait_for_requests(1, first)  # the first is downloading, and held
    second = start(PREPARE_SPACY, *args)
    assert json.loads(second.stdout.readline()) == WAITING
    server.go.set()
    first_lines = [json.loads(line) for line in finish(first).splitlines()]
    second_lines = [json.loads(line) for line in finish(second).splitlines()]

    assert first_lines[0].startswith("Downloading voice files")
    assert second_lines == [{"folder": first_lines[-1]["folder"]}]  # and nothing else to do
    assert server.requests == 1
    folder = Path(first_lines[-1]["folder"])
    assert (folder / "config.cfg").read_text(encoding="utf-8") == "[nlp]"
    assert (folder / "tagger" / "model").read_bytes() == MODEL_BYTES
    left = sorted(p.relative_to(models).as_posix() for p in models.rglob("*") if p.is_file())
    assert left == [
        "download.lock",
        "spacy/en_core_web_sm-3.8.0/config.cfg",
        "spacy/en_core_web_sm-3.8.0/tagger/model",
    ]  # no wheel, no temporary files, no staging folders


# A single download, without the lock: what each process does once it has the lock, or
# what an older Dogear without the lock does alongside this one. With a number of reads,
# the process stops dead after that many, as a crash would.
DOWNLOAD = """
import os, sys
from pathlib import Path
from dogear.voice import downloads

target, url, sha256, size, stop_after = sys.argv[1:6]
downloads._CHUNK = 8192
real_urlopen = downloads.urllib.request.urlopen

def urlopen(url, timeout):
    response = real_urlopen(url, timeout=timeout)
    real_read, reads = response.read, []

    def read(size):
        reads.append(size)
        if int(stop_after) and len(reads) > int(stop_after):
            os._exit(9)
        return real_read(size)

    response.read = read
    return response

downloads.urllib.request.urlopen = urlopen
downloads.download(downloads.RemoteFile(url, sha256, int(size), Path(target)), 30, print)
print("done", flush=True)
"""


@pytest.mark.parametrize("second_stops", [False, True])
def test_overlapping_downloads_never_spoil_the_file(tmp_path, server, second_stops):
    target = tmp_path / "models" / "downloads" / "model.whl"
    sha256 = hashlib.sha256(server.data).hexdigest()
    args = (str(target), server.url, sha256, str(len(server.data)))
    first = start(DOWNLOAD, *args, "0")
    server.wait_for_requests(1, first)
    second = start(DOWNLOAD, *args, "4" if second_stops else "0")
    server.wait_for_requests(2, first, second)  # both have begun
    server.go.set()
    assert finish(first) == "done\n"
    if second_stops:
        second.communicate(timeout=60)
        assert second.returncode == 9
    else:
        assert finish(second) == "done\n"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == sha256
    # A stopped download leaves only its own temporary file, which a later download
    # removes once it's old (test_downloads.py).
    temporaries = sorted(p.name for p in target.parent.glob(f".*{TEMPORARY_SUFFIX}"))
    assert len(temporaries) == (1 if second_stops else 0)
    assert sorted(os.listdir(target.parent)) == sorted(["model.whl", *temporaries])
