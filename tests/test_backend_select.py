"""Tests for GUI backend selection in octo_updater.py.

Only the resolver and startup wiring are exercised here. The Qt backend
runs headless (it never opens a display in these tests).
"""

import pytest

import octo_updater

QT_UNAVAILABLE = "Octo Updater needs PySide6 (Qt) to run"


def test_resolve_backend_default_is_qt(monkeypatch):
    import qt_app
    monkeypatch.delenv("OCTO_UI_BACKEND", raising=False)
    assert octo_updater.resolve_backend() is qt_app.QtOctoUpdaterApp


def test_resolve_backend_qt_returns_app_class():
    import qt_app
    assert octo_updater.resolve_backend("qt") is qt_app.QtOctoUpdaterApp


def test_resolve_backend_pyside6_returns_app_class():
    import qt_app
    assert octo_updater.resolve_backend("pyside6") is qt_app.QtOctoUpdaterApp


def test_qt_backend_error_message_is_friendly():
    msg = octo_updater.backend_error_message("qt", ImportError("broken"))
    assert QT_UNAVAILABLE in msg


def test_main_exits_1_when_qt_import_fails(monkeypatch, capsys):
    import sys
    monkeypatch.setenv("OCTO_UI_BACKEND", "qt")
    monkeypatch.setitem(sys.modules, "qt_app", None)
    with pytest.raises(SystemExit) as excinfo:
        octo_updater.main()
    assert excinfo.value.code == 1
    assert QT_UNAVAILABLE in capsys.readouterr().err


def test_unknown_backend_returns_none():
    assert octo_updater.resolve_backend("bogus") is None


def test_main_exits_1_for_unknown_backend(monkeypatch, capsys):
    monkeypatch.setenv("OCTO_UI_BACKEND", "bogus")
    with pytest.raises(SystemExit) as excinfo:
        octo_updater.main()
    assert excinfo.value.code == 1
    assert "Unknown OCTO_UI_BACKEND: bogus" in capsys.readouterr().err


@pytest.mark.parametrize("backend", ["qt", "pyside6"])
def test_main_constructs_shows_and_runs_qt_backend(monkeypatch, backend):
    calls = []

    class FakeQtApp:
        def __init__(self):
            calls.append("constructed")

        def show(self):
            calls.append("shown")

        def mainloop(self):
            calls.append("mainloop")

    monkeypatch.setenv("OCTO_UI_BACKEND", backend)
    monkeypatch.setattr(octo_updater, "resolve_backend", lambda name: FakeQtApp)
    octo_updater.main()
    assert calls == ["constructed", "shown", "mainloop"]
