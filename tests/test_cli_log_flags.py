"""Tests for the debugging-oriented CLI flags: --print-log and --show-log.

--print-log must dump the retained session log (rotated .old first, then
current) and exit without importing Qt; --show-log must reach the Qt shell
as `open_log=True`. The conftest `_log_sink_env` fixture keeps both away
from the real per-user launcher.log.
"""

import json

import pytest

import nostalgia_launcher.core.log_sink as log_sink
from nostalgia_launcher import cli


@pytest.fixture
def launcher_file(tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    path.write_text(
        json.dumps({"server": {"base_url": "https://launcher.test"}}),
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    """An enabled file sink on a private path; resets the global afterwards."""
    path = str(tmp_path / "launcher.log")
    log_sink.configure_file(path)
    yield path
    log_sink._sink_path = None


def _parse(argv):
    return cli._parse_args(argv)


def test_print_log_absent_by_default():
    assert _parse([]).print_log is False
    assert _parse(["--show-log"]).print_log is False


def test_print_log_bare_means_whole_log():
    assert _parse(["--print-log"]).print_log is None


def test_print_log_n_gives_tail_size():
    assert _parse(["--print-log", "5"]).print_log == 5


def test_show_log_flag_defaults_false():
    assert _parse([]).show_log is False
    assert _parse(["--show-log"]).show_log is True


def test_print_log_without_any_log_says_so(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        log_sink, "LOG_FILE", str(tmp_path / "missing" / "launcher.log")
    )
    assert cli.main(["--print-log"]) == 0
    assert "No launcher log yet" in capsys.readouterr().err


def test_print_log_dumps_old_then_current(tmp_path, monkeypatch, capsys):
    old = tmp_path / "launcher.log.old"
    cur = tmp_path / "launcher.log"
    old.write_text("first session\n", encoding="utf-8")
    cur.write_text("line A\nline B\n", encoding="utf-8")
    monkeypatch.setattr(log_sink, "LOG_FILE", str(cur))
    assert cli.main(["--print-log"]) == 0
    assert capsys.readouterr().out == "first session\nline A\nline B\n"


def test_print_log_tail_spans_rotation(tmp_path, monkeypatch, capsys):
    old = tmp_path / "launcher.log.old"
    cur = tmp_path / "launcher.log"
    old.write_text("1\n2\n", encoding="utf-8")
    cur.write_text("3\n4\n", encoding="utf-8")
    monkeypatch.setattr(log_sink, "LOG_FILE", str(cur))
    assert cli.main(["--print-log", "3"]) == 0
    assert capsys.readouterr().out == "2\n3\n4\n"


def test_main_constructs_backend_with_open_log_true(
    monkeypatch, launcher_file
):
    seen = {}

    class FakeQtApp:
        def __init__(self, open_log=False):
            seen["open_log"] = open_log

        def show(self):
            pass

        def run(self):
            return 0

    monkeypatch.setenv("NOSTALGIA_UI_BACKEND", "qt")
    monkeypatch.setattr(cli, "resolve_backend", lambda name: FakeQtApp)
    assert cli.main(["--launcher-config", launcher_file, "--show-log"]) == 0
    assert seen["open_log"] is True


def test_main_constructs_backend_with_open_log_false(
    monkeypatch, launcher_file
):
    seen = {}

    class FakeQtApp:
        def __init__(self, open_log=False):
            seen["open_log"] = open_log

        def show(self):
            pass

        def run(self):
            return 0

    monkeypatch.setenv("NOSTALGIA_UI_BACKEND", "qt")
    monkeypatch.setattr(cli, "resolve_backend", lambda name: FakeQtApp)
    assert cli.main(["--launcher-config", launcher_file]) == 0
    assert seen["open_log"] is False
