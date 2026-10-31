"""A local web server that counts requests, to prove a book file fetched nothing.

Pointing a book's images, styles, scripts, DTD, and external entities at it catches any
fetch, including one made inside libxml2, which Python's own socket checks can't see.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Counter(BaseHTTPRequestHandler):
    requests: list[str]

    def do_GET(self) -> None:  # the name http.server calls
        self.requests.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"FETCHED")

    def log_message(self, format: str, *args) -> None:  # quiet: no lines on stderr
        pass


@contextmanager
def watched_server() -> Iterator[tuple[str, list[str]]]:
    """(base URL, the paths requested so far) for a server running while in the block."""
    requests: list[str] = []
    handler = type("Handler", (_Counter,), {"requests": requests})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", requests
    finally:
        server.shutdown()
        server.server_close()


def remote_references(base: str) -> str:
    """HTML that would make a browser fetch from `base` six times."""
    return (
        f'<link rel="stylesheet" href="{base}/style.css">'
        f'<script src="{base}/script.js"></script>'
        f'<img src="{base}/photo.jpg" alt="A photo">'
        f'<img srcset="{base}/big.jpg 2x" src="{base}/small.jpg" alt="Small">'
        f'<iframe src="{base}/frame.html"></iframe>'
        f'<object data="{base}/object.svg"></object>'
    )
