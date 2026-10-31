"""`dogear serve` and its `[web]` settings: each setting changes what the server does."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from web_samples import BASE, sample_app, sample_config, visitor

from dogear import cli, paths
from dogear.config import load_config
from dogear.errors import ConfigError, DogearError
from dogear.web import child, server
from dogear.web.server import ServeOptions, bind, lock_web_folder, serve
from dogear.web.settings import web_settings

FAKE = Path(__file__).parent / "fake_dogear.py"
# `dogear serve`, with the stand-in `dogear` (tests/fake_dogear.py) for its children.
SERVER = """
import sys
from dogear.web import child
child.dogear_command = lambda: [sys.executable, sys.argv[1]]
from dogear.config import default_config
from dogear.web.server import ServeOptions, serve
config = default_config()
config["web"]["dir"] = sys.argv[2]
sys.exit(serve(ServeOptions(port=int(sys.argv[3]), open_browser=False), config))
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def eventually(check, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


@pytest.mark.skipif(os.name == "nt", reason="Ctrl+C reaches a process group on Linux and macOS")
def test_ctrl_c_in_the_terminal_leaves_a_book_being_read_to_be_read_again(tmp_path, monkeypatch):
    web, port, pids = tmp_path / "web", free_port(), tmp_path / "pids"
    # In a session of its own, like a terminal's foreground job: Ctrl+C there goes to
    # every process in the job's process group.
    server_process = subprocess.Popen(
        [sys.executable, "-c", SERVER, str(FAKE), str(web), str(port)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "DOGEAR_FAKE_PIDS": str(pids)},
        start_new_session=True,
    )
    try:
        link = server_process.stdout.readline().split("link in a web browser: ")[1].strip()
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, headers={"Origin": base}) as client:
            client.get(link)
            response = client.post("/books", files={"book": ("b.epub", b"slowread", "")})
            book_id = response.headers["location"].rsplit("/", 1)[1]
        eventually(lambda: pids.exists() and pids.read_text().strip())
        reader = int(pids.read_text())
        os.killpg(server_process.pid, signal.SIGINT)  # Ctrl+C in the server's terminal
        assert server_process.wait(timeout=60) == 0
    finally:
        if server_process.poll() is None:
            server_process.kill()
            server_process.wait()
        server_process.stdout.close()
    with pytest.raises(ProcessLookupError):
        os.kill(reader, 0)  # the server stopped it before it stopped itself
    record = json.loads((web / "books" / book_id / "book.json").read_text())
    assert (record["state"], record["error"]) == ("reading", "")

    monkeypatch.setattr(child, "dogear_command", lambda: [sys.executable, str(FAKE)])
    (web / "books" / book_id / "book.epub").write_text("ok")
    app = sample_app(web)
    with visitor(app):
        eventually(lambda: app.state.library.get(book_id).state == "ready")


@pytest.fixture
def no_server(monkeypatch):
    """serve() without serving: uvicorn's run is replaced, and Ctrl+C pressed at once."""
    runs = []

    def run(self, sockets):
        runs.append([listener.getsockname() for listener in sockets])
        raise KeyboardInterrupt

    monkeypatch.setattr(server.uvicorn.Server, "run", run)
    opened = []
    monkeypatch.setattr(server.webbrowser, "open", opened.append)
    return SimpleNamespace(runs=runs, opened=opened)


def test_serve_says_where_to_go_and_stops_cleanly(tmp_path, no_server):
    config = sample_config(tmp_path)
    port = free_port()
    config["web"]["port"] = port
    said = []
    assert serve(ServeOptions(), config, said.append) == 0
    key = (tmp_path / "access-key.txt").read_text().strip()
    link = f"http://127.0.0.1:{port}/start?key={key}"
    assert said[0] == f"Dogear is running. Open this link in a web browser: {link}"
    assert said[-2] == "Keep this window open while you use Dogear. Press Ctrl+C to stop."
    assert said[-1] == "Stopped."
    [opened] = no_server.opened
    assert opened.startswith(f"http://127.0.0.1:{port}/start?token=")
    assert key not in opened  # the command opening a browser is seen by other users
    assert no_server.runs == [[("127.0.0.1", port)]]


def test_the_browser_is_opened_with_a_link_that_works_once(tmp_path, no_server, monkeypatch):
    apps = []

    def keep(*args):
        apps.append(server_create_app(*args))
        return apps[-1]

    server_create_app = server.create_app
    monkeypatch.setattr(server, "create_app", keep)
    config = sample_config(tmp_path)
    serve(ServeOptions(), config, lambda line: None)
    [opened] = no_server.opened
    start = opened.removeprefix(BASE)
    browser = visitor(apps[0], signed_in=False)
    response = browser.get(start, follow_redirects=False)
    assert response.status_code == 303
    assert browser.get("/").status_code == 200
    assert browser.get(start, follow_redirects=False).headers["location"] == "/"  # let in
    stranger = visitor(apps[0], signed_in=False)
    response = stranger.get(start)
    assert response.status_code == 403
    assert "That link worked only once" in response.text
    assert stranger.get("/").status_code == 403


def test_no_browser_opens_none(tmp_path, no_server):
    config = sample_config(tmp_path)
    config["web"]["port"] = free_port()
    serve(ServeOptions(open_browser=False), config, lambda line: None)
    assert no_server.opened == []


def test_port_option_beats_the_config(tmp_path, no_server):
    config = sample_config(tmp_path)
    port = free_port()
    said = []
    serve(ServeOptions(port=port, open_browser=False), config, said.append)
    assert f"http://127.0.0.1:{port}/start?key=" in said[0]


def test_on_the_network_it_listens_everywhere_and_warns(tmp_path, no_server, monkeypatch):
    monkeypatch.setattr(server, "lan_address", lambda: "192.168.1.20")
    config = sample_config(tmp_path)
    port = free_port()
    said = []
    serve(ServeOptions(port=port, lan=True, open_browser=False), config, said.append)
    assert said[1].startswith(
        f"On a phone or another computer on the same network, open: http://192.168.1.20:{port}/start?key="
    )
    assert said[2] == "Anyone on this network who has the link can use Dogear."
    assert no_server.runs == [[("0.0.0.0", port)]]


def test_with_the_network_address_off_it_says_to_use_this_computers(
    tmp_path, no_server, monkeypatch
):
    def not_asked():
        raise AssertionError("the address was looked for")

    monkeypatch.setattr(server, "lan_address", not_asked)
    config = sample_config(tmp_path)
    config["web"]["show_network_address"] = False
    port = free_port()
    said = []
    serve(ServeOptions(port=port, lan=True, open_browser=False), config, said.append)
    assert said[0].startswith(
        f"Dogear is running. Open this link in a web browser: http://127.0.0.1:{port}/"
    )
    assert said[1] == (
        "On a phone or another computer on the same network, open the link above with this "
        "computer's network address in place of 127.0.0.1."
    )
    assert said[2] == "Anyone on this network who has the link can use Dogear."
    assert not any("http://" in line for line in said[1:])
    assert no_server.runs == [[("0.0.0.0", port)]]


def test_with_no_address_found_it_says_what_to_use_instead(tmp_path, no_server, monkeypatch):
    monkeypatch.setattr(server, "lan_address", lambda: None)
    config = sample_config(tmp_path)
    said = []
    serve(ServeOptions(port=free_port(), lan=True, open_browser=False), config, said.append)
    assert said[1] == (
        "Dogear couldn't find this computer's address on the network. On a phone or another "
        "computer on the same network, open the link above with this computer's network "
        "address in place of 127.0.0.1."
    )


def test_the_docker_image_turns_the_network_address_off():
    docker = Path(__file__).parents[1] / "docker" / "config.toml"
    assert load_config(docker)["web"]["show_network_address"] is False


def test_a_port_in_use_is_a_plain_error(tmp_path):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        settings = web_settings(sample_config(tmp_path), port=port)
        with pytest.raises(DogearError, match=f"Port {port} is in use already") as raised:
            bind(settings)
    assert raised.value.hint.startswith("If it is, use the link that window shows.")


def test_a_second_server_on_the_same_folder_is_refused(tmp_path, no_server):
    config = sample_config(tmp_path)
    config["web"]["port"] = free_port()
    with lock_web_folder(tmp_path):
        with pytest.raises(DogearError) as raised:
            serve(ServeOptions(open_browser=False), config, lambda line: None)
        assert raised.value.message == f"Dogear is already running with the web folder {tmp_path}."
        assert raised.value.hint.startswith("Use the link shown in the window where it's running.")
        assert no_server.runs == []  # refused before listening or touching the books
    serve(ServeOptions(open_browser=False), config, lambda line: None)
    assert len(no_server.runs) == 1
    # The lock is let go when the server stops, so it can start again.
    serve(ServeOptions(open_browser=False), config, lambda line: None)
    assert len(no_server.runs) == 2


def test_the_port_setting_changes_the_port_and_the_host_check(tmp_path):
    config = sample_config(tmp_path)
    config["web"]["port"] = 9123
    app = sample_app(tmp_path, config, port=None)
    assert app.state.settings.port == 9123
    client = visitor(app)
    assert client.get("/", headers={"Host": "127.0.0.1:9123"}).status_code == 200
    assert client.get("/", headers={"Host": "127.0.0.1:8765"}).status_code == 400
    listener = bind(web_settings(config, port=free_port()))
    listener.close()


def test_the_upload_limit_setting_changes_the_limit(tmp_path):
    config = sample_config(tmp_path)
    config["web"]["max_upload_mb"] = 2
    client = visitor(sample_app(tmp_path, config))
    two_and_a_bit = b"x" * (2 * 1024 * 1024 + 10)
    assert client.post("/books", files={"book": ("a.pdf", two_and_a_bit, "")}).status_code == 413
    one_and_a_bit = b"x" * (1024 * 1024 + 10)
    response = client.post("/books", files={"book": ("a.pdf", one_and_a_bit, "")})
    assert response.status_code == 200
    assert "up to 2 MB" in client.get("/").text


def test_the_dir_setting_changes_where_books_are_kept(tmp_path):
    config = sample_config(tmp_path / "elsewhere")
    client = visitor(sample_app(tmp_path / "elsewhere", config))
    client.post("/books", files={"book": ("a.epub", b"ok", "")})
    [folder] = (tmp_path / "elsewhere" / "books").iterdir()
    assert (folder / "book.epub").read_bytes() == b"ok"


def test_the_usual_web_folder_is_in_the_data_folder(config, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "_platform_dir", lambda kind: tmp_path / kind)
    assert paths.web_dir(config) == tmp_path / "data" / "web"
    config["web"]["dir"] = str(tmp_path / "mine")
    assert paths.web_dir(config) == tmp_path / "mine"


def test_the_read_timeout_setting_is_used(tmp_path):
    config = sample_config(tmp_path)
    config["web"]["read_timeout_seconds"] = 42
    assert web_settings(config).read_timeout_seconds == 42


@pytest.mark.parametrize(
    ("key", "value"),
    [("port", 0), ("port", 70000), ("max_upload_mb", 0), ("read_timeout_seconds", 0)],
)
def test_bad_web_settings_are_errors(tmp_path, key, value):
    config = sample_config(tmp_path)
    config["web"][key] = value
    with pytest.raises(ConfigError):
        web_settings(config)


def test_the_serve_command_passes_its_options(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(server, "serve", lambda options, config: seen.append(options) or 0)
    settings_file = tmp_path / "d.toml"
    settings_file.write_text("")
    argv = ["serve", "--port", "9000", "--lan", "--no-browser", "--config", str(settings_file)]
    assert cli.main(argv) == 0
    assert seen == [
        ServeOptions(port=9000, lan=True, open_browser=False, config_file=settings_file)
    ]


def test_python_dash_m_dogear_runs_the_command():
    result = subprocess.run(
        [sys.executable, "-m", "dogear", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stdout.startswith("dogear ")


def test_the_samples_use_the_default_port(config):
    assert BASE.endswith(f":{config['web']['port']}")
