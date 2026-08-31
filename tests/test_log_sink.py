"""Unit tests for the global log sink: stdout debug mirror and the
optional on-disk session-log file (configure_file / read_lines)."""

import os

import pytest

import nostalgia_launcher.core.log_sink as log_sink
from nostalgia_launcher.core.log_sink import debug_emit, debug_enabled, log


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.delenv("NOSTALGIA_DEBUG", raising=False)
    log_sink._dispatcher = None
    yield
    log_sink._dispatcher = None


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    """An enabled file sink on a private path; resets the global afterwards."""
    path = str(tmp_path / "launcher.log")
    log_sink.configure_file(path)
    yield path
    log_sink._sink_path = None


def test_debug_disabled_by_default():
    assert debug_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_debug_enabled_values(monkeypatch, value):
    monkeypatch.setenv("NOSTALGIA_DEBUG", value)
    assert debug_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_debug_falsey_values(monkeypatch, value):
    if value:
        monkeypatch.setenv("NOSTALGIA_DEBUG", value)
    else:
        monkeypatch.delenv("NOSTALGIA_DEBUG", raising=False)
    assert debug_enabled() is False


def test_log_silent_on_stdout_by_default(capsys):
    log("hello")
    assert capsys.readouterr().out == ""


def test_log_mirrors_to_stdout_in_debug(monkeypatch, capsys):
    monkeypatch.setenv("NOSTALGIA_DEBUG", "1")
    log("hello")
    assert capsys.readouterr().out == "hello\n"


def test_log_mirror_preserves_trailing_newline(monkeypatch, capsys):
    monkeypatch.setenv("NOSTALGIA_DEBUG", "1")
    log("hello\n")
    assert capsys.readouterr().out == "hello\n"


def test_debug_emit_never_raises_on_bad_stdout(monkeypatch):
    monkeypatch.setenv("NOSTALGIA_DEBUG", "1")

    def _boom(*a, **k):
        raise OSError("broken pipe")

    monkeypatch.setattr(log_sink.sys.stdout, "write", _boom)
    debug_emit("boom")  # must not raise


# ── file sink ─────────────────────────────────────────────────────


def test_unconfigured_sink_writes_nothing():
    assert log_sink._sink_path is None
    log("quiet line")
    assert not os.path.exists(log_sink.current_log_path())


def test_configure_file_appends_every_line(log_file):
    log("one\n", "ok")
    log("two")
    with open(log_file, encoding="utf-8") as fh:
        assert fh.read() == "one\ntwo\n"


def test_read_lines_defaults_to_log_file(tmp_path, monkeypatch):
    path = tmp_path / "launcher.log"
    path.write_text("kept\n", encoding="utf-8")
    monkeypatch.setattr(log_sink, "LOG_FILE", str(path))
    assert log_sink.read_lines() == ["kept"]


def test_read_lines_missing_files_yield_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(
        log_sink, "LOG_FILE", str(tmp_path / "nope" / "launcher.log")
    )
    assert log_sink.read_lines() == []


def test_read_lines_old_first_then_current(log_file):
    with open(log_file + ".old", "w", encoding="utf-8") as fh:
        fh.write("1\n2\n")
    with open(log_file, "w", encoding="utf-8") as fh:
        fh.write("3\n")
    assert log_sink.read_lines() == ["1", "2", "3"]


def test_read_lines_tail_n_across_rotation(log_file):
    with open(log_file + ".old", "w", encoding="utf-8") as fh:
        fh.write("1\n2\n")
    with open(log_file, "w", encoding="utf-8") as fh:
        fh.write("3\n4\n")
    assert log_sink.read_lines(3) == ["2", "3", "4"]
    assert log_sink.read_lines(0) == []
    assert log_sink.read_lines(99) == ["1", "2", "3", "4"]


def test_file_sink_rotates_past_cap(log_file, monkeypatch):
    monkeypatch.setattr(log_sink, "_MAX_BYTES", 16)
    for i in range(5):
        log(f"rotate-me-{i}xxxxxxxxxxx")
    assert os.path.exists(log_file + ".old")
    lines = log_sink.read_lines()
    assert lines[-1] == "rotate-me-4xxxxxxxxxxx"
    assert len(lines) < 5


def test_file_sink_failure_disables_quietly(tmp_path, monkeypatch):
    blocker = tmp_path / "a-directory"
    blocker.mkdir()
    log_sink.configure_file(str(blocker))  # makedirs on existing dir ok,
    # but the target itself is a directory → open() fails every time.
    log("into a directory")  # must not raise
    assert log_sink._sink_path == str(blocker)
