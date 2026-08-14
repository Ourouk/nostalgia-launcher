"""Unit tests for the global log sink and its stdout debug mirror."""

import pytest

import vanilla_wow_launcher.core.log_sink as log_sink
from vanilla_wow_launcher.core.log_sink import debug_emit, debug_enabled, log


def _drain():
    q = log_sink._LOG_Q
    while not q.empty():
        q.get()


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.delenv("VANILLA_WOW_DEBUG", raising=False)
    _drain()
    yield
    _drain()


def test_debug_disabled_by_default():
    assert debug_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_debug_enabled_values(monkeypatch, value):
    monkeypatch.setenv("VANILLA_WOW_DEBUG", value)
    assert debug_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_debug_falsey_values(monkeypatch, value):
    if value:
        monkeypatch.setenv("VANILLA_WOW_DEBUG", value)
    else:
        monkeypatch.delenv("VANILLA_WOW_DEBUG", raising=False)
    assert debug_enabled() is False


def test_log_silent_on_stdout_by_default(capsys):
    log("hello")
    assert capsys.readouterr().out == ""


def test_log_mirrors_to_stdout_in_debug(monkeypatch, capsys):
    monkeypatch.setenv("VANILLA_WOW_DEBUG", "1")
    log("hello")
    assert capsys.readouterr().out == "hello\n"


def test_log_mirror_preserves_trailing_newline(monkeypatch, capsys):
    monkeypatch.setenv("VANILLA_WOW_DEBUG", "1")
    log("hello\n")
    assert capsys.readouterr().out == "hello\n"


def test_debug_emit_never_raises_on_bad_stdout(monkeypatch):
    monkeypatch.setenv("VANILLA_WOW_DEBUG", "1")

    def _boom(*a, **k):
        raise OSError("broken pipe")

    monkeypatch.setattr(log_sink.sys.stdout, "write", _boom)
    debug_emit("boom")  # must not raise
