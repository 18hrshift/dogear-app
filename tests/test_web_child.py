"""How the server runs `dogear` as a child process."""

import ctypes
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dogear.web import child
from dogear.web.child import (
    MAX_LINE_CHARS,
    UTF8_OUTPUT_ENV,
    Child,
    child_command,
    dogear_command,
    interrupted,
    popen_options,
    read_lines,
    run_captured,
    start_lines,
)
from dogear.web.server_watch import STOP_WITH_SERVER_ENV, watch
from dogear.web.windows_job import ExtendedLimits

FAKE = Path(__file__).parent / "fake_dogear.py"

# A child that starts a grandchild (as `dogear` starts ffmpeg), which adds a byte to a
# file every 20 ms for as long as it runs. The child then waits, or ends at once.
BEAT = """
import sys, time
while True:
    with open(sys.argv[1], "ab") as f:
        f.write(b".")
    time.sleep(0.02)
"""
TREE = """
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", sys.argv[3], sys.argv[1]], stdin=subprocess.DEVNULL)
print("started", flush=True)
if sys.argv[2] == "wait":
    time.sleep(60)
"""


def beating(path: Path) -> bool:
    """Whether the grandchild is still running: its file grows."""
    before = path.stat().st_size if path.exists() else 0
    time.sleep(0.4)
    return (path.stat().st_size if path.exists() else 0) > before


def start_tree(tmp_path, then: str) -> tuple[Child, Path]:
    beat = tmp_path / "beat"
    tree = start_lines([sys.executable, "-c", TREE, str(beat), then, BEAT])
    assert tree.process.stdout.readline().strip() == b"started"
    deadline = time.monotonic() + 10
    while not beat.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    return tree, beat


def test_stopping_a_child_stops_what_it_started(tmp_path):
    tree, beat = start_tree(tmp_path, "wait")
    try:
        assert beating(beat)
        tree.stop()
        tree.process.wait(timeout=20)
        assert not beating(beat)
    finally:
        tree.close()
        tree.process.stdout.close()


def test_closing_a_child_ends_what_it_left_running(tmp_path):
    tree, beat = start_tree(tmp_path, "end")
    try:
        assert tree.process.wait(timeout=20) == 0
        assert beating(beat)  # the grandchild outlived the child
    finally:
        tree.close()
        tree.process.stdout.close()
    assert not beating(beat)


@pytest.mark.skipif(os.name == "nt", reason="process groups are for Linux and macOS")
def test_ctrl_c_in_the_servers_terminal_does_not_reach_a_child():
    """The terminal sends Ctrl+C to its foreground process group; a child leads its own,
    in a session of its own, so only the server decides when it stops."""
    tree = start_lines([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        pid = tree.process.pid
        assert os.getpgid(pid) == pid != os.getpgid(0)
        assert os.getsid(pid) == pid != os.getsid(0)
    finally:
        tree.end()
        tree.close()
        tree.process.stdout.close()


def test_a_child_stops_by_itself_when_the_server_has_gone(tmp_path):
    """The server holds the child's stdin; its end (the server ended, however it ended)
    stops the child as Ctrl+C would, so it tidies up."""
    book = tmp_path / "book.epub"
    book.write_text("slow")
    make = start_lines(
        [sys.executable, str(FAKE), "make", str(book), "-o", str(tmp_path), "--format", "m4b"]
    )
    try:
        assert make.process.stdout.readline().startswith(b"Making an audiobook")
        make.process.stdin.close()  # what the server's end does
        assert make.process.wait(timeout=20) == 130
        assert b"Stopped." in make.process.stdout.read()
    finally:
        make.close()
        make.process.stdout.close()


def test_the_server_watch_reads_to_the_end_then_stops_the_child():
    calls = []
    reading, writing = os.pipe()
    try:
        thread = watch(
            reading,
            interrupt=lambda: calls.append("interrupt"),
            end=lambda code: calls.append(code),
            grace=0,
        )
        os.write(writing, b"ignored")
        time.sleep(0.2)
        assert calls == []  # the server is still there
        os.close(writing)
        thread.join(5)
        assert calls == ["interrupt", 130]
    finally:
        os.close(reading)


def test_children_are_told_to_watch_for_the_servers_end():
    options = popen_options()
    assert options["env"][STOP_WITH_SERVER_ENV] == "1"
    assert options["stdin"] == subprocess.PIPE
    assert bool(options.get("start_new_session")) == (os.name != "nt")


def test_what_counts_as_stopped():
    assert interrupted(130)
    assert interrupted(-2)  # SIGINT, on Linux and macOS
    assert interrupted(-9)  # SIGKILL
    assert not interrupted(0)
    assert not interrupted(1)


def test_the_windows_job_limits_have_the_windows_layout():
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION is 144 bytes on 64-bit Windows and 112 on
    32-bit; the fixed-size fields give the same layout here."""
    expected = 144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112
    assert ctypes.sizeof(ExtendedLimits) == expected


def test_normal_python_runs_the_dogear_module(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert dogear_command() == [sys.executable, "-m", "dogear"]


def test_a_frozen_build_runs_itself(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/Dogear/dogear")
    assert dogear_command() == ["/opt/Dogear/dogear"]
    assert child_command(["outline", "b.epub", "--json"], None) == [
        "/opt/Dogear/dogear",
        "outline",
        "b.epub",
        "--json",
    ]


def test_the_servers_config_file_is_passed_on(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    command = child_command(["make", "b.epub"], Path("/home/me/dogear.toml"))
    assert command[-2:] == ["--config", str(Path("/home/me/dogear.toml"))]


def test_typed_text_comes_last_after_two_hyphens_so_it_is_never_an_option(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    config = Path("/home/me/dogear.toml")
    command = child_command(["say", "-o", "a.wav"], config, text="--config=/tmp/evil.toml")
    assert command[2:] == [
        "dogear",
        "say",
        "-o",
        "a.wav",
        "--config",
        str(config),
        "--",
        "--config=/tmp/evil.toml",
    ]
    assert child_command(["say", "-o", "a.wav"], None, text="-x")[-2:] == ["--", "-x"]


def test_children_write_utf8_and_open_no_window_on_windows(monkeypatch):
    options = popen_options()
    assert options["env"][UTF8_OUTPUT_ENV] == "1"
    assert options["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "creationflags" not in options or sys.platform == "win32"
    monkeypatch.setattr(child.os, "name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert popen_options()["creationflags"] == 0x08000000


def test_the_real_dogear_writes_utf8_when_asked(tmp_path):
    """The child's side: `dogear` honours the variable even where the console isn't UTF-8."""
    script = "from dogear.cli import main; import sys; sys.exit(main(['outline', 'é.xyz']))"
    env = {**popen_options()["env"], "PYTHONIOENCODING": "ascii"}
    del env[STOP_WITH_SERVER_ENV]  # run without the server's stdin
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, env=env, cwd=tmp_path
    )
    assert "Error: No such file: é.xyz".encode() in result.stderr


def test_lines_are_decoded_and_cut_to_a_bound():
    long = b"x" * (MAX_LINE_CHARS * 5)
    stream = io.BytesIO(b"first \xc3\xa9\r\n" + long + b"\nlast\n\xff\n")
    lines = list(read_lines(stream))
    assert lines[0] == "first é"
    assert lines[1] == "x" * MAX_LINE_CHARS
    assert lines[2:] == ["last", "�"]


def test_captured_output_is_bounded():
    command = [sys.executable, "-c", "print('y' * 100000)"]
    result = run_captured(command, timeout=30, max_stdout=1000)
    assert result.returncode == 0
    assert result.too_long
    assert result.stdout == b"y" * 1000


def test_a_child_that_takes_too_long_is_stopped():
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    result = run_captured(command, timeout=0.5, max_stdout=1000)
    assert result.timed_out
    assert result.returncode != 0


# Reports a line on stderr every 0.3 seconds, five times, then goes quiet for `argv[1]`
# seconds before it ends.
REPORTING = """
import sys, time
for n in range(1, 6):
    print(f"Read {n} of 5 pages with OCR.", file=sys.stderr, flush=True)
    time.sleep(0.3)
time.sleep(float(sys.argv[1]))
print("done")
"""


def test_a_child_that_keeps_reporting_runs_past_the_timeout():
    """The timeout counts from the child's latest line, so a long job that says how it's
    going (reading a scanned PDF with OCR) isn't cut short."""
    heard = []
    command = [sys.executable, "-c", REPORTING, "0"]
    result = run_captured(command, timeout=1.0, max_stdout=1000, on_line=heard.append)
    assert not result.timed_out
    assert result.returncode == 0
    assert result.stdout.strip() == b"done"
    assert heard == [f"Read {n} of 5 pages with OCR." for n in range(1, 6)]
    assert result.stderr.splitlines() == heard


def test_a_child_that_goes_quiet_for_the_timeout_is_stopped():
    command = [sys.executable, "-c", REPORTING, "30"]
    started = time.monotonic()
    result = run_captured(command, timeout=1.0, max_stdout=1000)
    assert result.timed_out
    assert time.monotonic() - started < 15


def test_only_the_last_stderr_lines_are_kept():
    script = "import sys\nfor n in range(5000): print(f'line {n}', file=sys.stderr)"
    result = run_captured([sys.executable, "-c", script], timeout=30, max_stdout=1000)
    kept = result.stderr.splitlines()
    assert len(kept) == child.MAX_STDERR_LINES
    assert kept[-1] == "line 4999"


def test_a_bug_in_the_line_handler_is_raised_once_the_child_has_ended():
    def broken(line):
        raise RuntimeError("a bug")

    command = [sys.executable, "-c", REPORTING, "0"]
    with pytest.raises(RuntimeError, match="a bug"):
        run_captured(command, timeout=30, max_stdout=1000, on_line=broken)
