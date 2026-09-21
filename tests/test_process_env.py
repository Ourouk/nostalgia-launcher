"""Unit tests for the scrubbed system-executable environment."""

import os

from nostalgia_launcher.core.process_env import clean_system_env


def test_clean_system_env_scrubs_bundled_vars(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/appimage/_internal")
    monkeypatch.setenv("LD_PRELOAD", "/appimage/preload.so")
    monkeypatch.setenv("PYTHONPATH", "/app/venv")
    monkeypatch.setenv("PYTHONHOME", "/app/venv")
    monkeypatch.setenv("VIRTUAL_ENV", "/app/venv")
    monkeypatch.setenv("CONDA_PREFIX", "/opt/conda")
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "/usr/bin/python3")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/appimage/plugins")
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    monkeypatch.setenv("HOME", "/home/tester")
    env = clean_system_env()
    for key in (
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "__PYVENV_LAUNCHER__",
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM",
    ):
        assert key not in env
    assert env.get("HOME") == "/home/tester"


def test_clean_system_env_layers_extra(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/appimage/_internal")
    env = clean_system_env({"GIT_TERMINAL_PROMPT": "0"})
    assert "LD_LIBRARY_PATH" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_clean_system_env_extra_none_deletes(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    env = clean_system_env({"FOO": None})
    assert "FOO" not in env
    # The caller's os.environ is untouched.
    assert os.environ.get("FOO") == "bar"
