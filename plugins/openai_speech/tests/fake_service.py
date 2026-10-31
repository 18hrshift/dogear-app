"""A speech service on this computer that speaks the OpenAI API, for the tests and for
trying the plugin by hand. Nothing goes to the network.

It takes `POST .../audio/speech` with a JSON body and answers with audio: a short tone
for each request (longer for a longer sentence), or the same recording every time. It
keeps every request it was sent, and can be told to answer with errors first. It also
answers requests sent to it as a web proxy, so a test can give the plugin an address on
"another computer" (such as http://speech.invalid/v1) whose requests come here.

By hand, with real audio (a WAV file made with `dogear say`, for example):

    python plugins/openai_speech/tests/fake_service.py --audio speech.wav --port 8899
"""

from __future__ import annotations

import argparse
import io
import json
import threading
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

RATE = 24000


def tone(seconds: float, rate: int = RATE, channels: int = 1, width: int = 2) -> bytes:
    """A WAV file of a quiet 220 Hz tone."""
    count = max(1, round(seconds * rate))
    samples = 0.3 * np.sin(2 * np.pi * 220 * np.arange(count) / rate)
    ints = (samples * 32767).astype("<i2")
    frames = np.repeat(ints, channels) if channels > 1 else ints
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(frames.tobytes())
    return out.getvalue()


@dataclass
class Reply:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Sent:
    """One request the service was sent."""

    target: str  # the path, or the whole address when sent to it as a proxy
    host: str  # the computer it was meant for
    headers: dict[str, str]
    body: dict


class FakeService:
    def __init__(self, audio: bytes | None = None, key: str = "", log=None) -> None:
        self.audio = audio  # the same recording for every request, or a tone if None
        self.key = key  # the key to insist on, if any
        self.log = log  # called with a line for each request, if given
        self.sent: list[Sent] = []
        self.replies: list[Reply] = []  # answered first, in order, before audio
        self._lock = threading.Lock()

    def answer(self, target: str, host: str, headers: dict[str, str], raw: bytes) -> Reply:
        body = json.loads(raw or b"{}")
        with self._lock:
            self.sent.append(Sent(target, host, headers, body))
            if self.log is not None:
                key = "with a key" if headers.get("authorization") else "with no key"
                self.log(f"{host}, {key}: {body.get('voice')}: {body.get('input')}")
            if self.replies:
                return self.replies.pop(0)
        if not urlsplit(target).path.endswith("/audio/speech"):
            return Reply(404, b'{"error": {"message": "Not found."}}', _json())
        if self.key and headers.get("authorization") != f"Bearer {self.key}":
            return Reply(401, b'{"error": {"message": "Incorrect API key provided."}}', _json())
        if self.audio is not None:
            return Reply(200, self.audio, {"Content-Type": "audio/wav"})
        words = len(str(body.get("input", "")).split())
        return Reply(200, tone(0.2 + 0.05 * words), {"Content-Type": "audio/wav"})


def _json() -> dict[str, str]:
    return {"Content-Type": "application/json"}


class _Handler(BaseHTTPRequestHandler):
    service: FakeService

    def do_POST(self) -> None:  # the name http.server calls
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        # Sent to it as a proxy, the path is the whole address; else the Host header has it.
        host = urlsplit(self.path).hostname or urlsplit(f"//{self.headers['Host']}").hostname
        headers = {k.lower(): v for k, v in self.headers.items()}
        reply = self.service.answer(self.path, host, headers, raw)
        self.send_response(reply.status)
        for name, value in reply.headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(reply.body)))
        self.end_headers()
        self.wfile.write(reply.body)

    def log_message(self, format: str, *args) -> None:  # quiet
        pass


def start(service: FakeService, port: int = 0) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"service": service})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    return server


@contextmanager
def running(service: FakeService) -> Iterator[str]:
    """The service's address, http://127.0.0.1:PORT, while in the block."""
    server = start(service)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--audio", help="a WAV file to answer every request with")
    parser.add_argument("--key", default="", help="a key to insist on")
    args = parser.parse_args()
    audio = Path(args.audio).read_bytes() if args.audio else None
    service = FakeService(audio, args.key, log=lambda line: print(line, flush=True))
    server = start(service, args.port)
    print(f"Speaking the OpenAI speech API at http://127.0.0.1:{args.port}/v1", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
        print(f"{len(service.sent)} requests.")


if __name__ == "__main__":
    main()
