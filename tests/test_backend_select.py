"""Tests for GUI backend selection in vanilla_wow_launcher.cli.

Only the resolver and startup wiring are exercised here. The Qt backend
runs headless (it never opens a display in these tests). `main()` requires a
launcher configuration, so every call passes --launcher-config.
"""

import json

import pytest

from vanilla_wow_launcher import cli

QT_UNAVAILABLE = "Vanilla WoW Launcher needs PySide6 (Qt) to run"


@pytest.fixture
def launcher_file(tmp_path):
    path = tmp_path / "vanilla_wow_launcher.json"
    path.write_text(json.dumps(
        {"server": {"base_url": "https://launcher.test"}}), encoding="utf-8")
    return str(path)


def test_resolve_backend_default_is_qt(monkeypatch):
    import vanilla_wow_launcher.ui.qt.app as qt_app
    monkeypatch.delenv("VANILLA_WOW_UI_BACKEND", raising=False)
    assert cli.resolve_backend() is qt_app.QtVanillaWoWLauncherApp


def test_resolve_backend_qt_returns_app_class():
    import vanilla_wow_launcher.ui.qt.app as qt_app
    assert cli.resolve_backend("qt") is qt_app.QtVanillaWoWLauncherApp


def test_resolve_backend_pyside6_returns_app_class():
    import vanilla_wow_launcher.ui.qt.app as qt_app
    assert cli.resolve_backend("pyside6") is qt_app.QtVanillaWoWLauncherApp


def test_qt_backend_error_message_is_friendly():
    msg = cli.backend_error_message("qt", ImportError("broken"))
    assert QT_UNAVAILABLE in msg


def test_main_returns_1_when_qt_import_fails(monkeypatch, capsys,
                                             launcher_file):
    import sys
    monkeypatch.setenv("VANILLA_WOW_UI_BACKEND", "qt")
    monkeypatch.setitem(sys.modules, "vanilla_wow_launcher.ui.qt.app", None)
    assert cli.main(["--launcher-config", launcher_file]) == 1
    assert QT_UNAVAILABLE in capsys.readouterr().err


def test_unknown_backend_returns_none():
    assert cli.resolve_backend("bogus") is None


def test_main_returns_1_for_unknown_backend(monkeypatch, capsys,
                                            launcher_file):
    monkeypatch.setenv("VANILLA_WOW_UI_BACKEND", "bogus")
    assert cli.main(["--launcher-config", launcher_file]) == 1
    assert "Unknown VANILLA_WOW_UI_BACKEND: bogus" in capsys.readouterr().err


def test_main_returns_1_without_launcher_config(monkeypatch, capsys,
                                                tmp_path):
    monkeypatch.setenv("VANILLA_WOW_UI_BACKEND", "qt")
    monkeypatch.chdir(tmp_path)
    assert cli.main([]) == 1
    assert "launcher configuration is required" in capsys.readouterr().err


@pytest.mark.parametrize("backend", ["qt", "pyside6"])
def test_main_constructs_shows_and_runs_qt_backend(monkeypatch, backend,
                                                   launcher_file):
    calls = []

    class FakeQtApp:
        def __init__(self):
            calls.append("constructed")

        def show(self):
            calls.append("shown")

        def run(self):
            calls.append("run")
            return 0

    monkeypatch.setenv("VANILLA_WOW_UI_BACKEND", backend)
    monkeypatch.setattr(cli, "resolve_backend", lambda name: FakeQtApp)
    assert cli.main(["--launcher-config", launcher_file]) == 0
    assert calls == ["constructed", "shown", "run"]
