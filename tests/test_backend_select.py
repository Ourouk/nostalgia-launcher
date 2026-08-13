"""Tests for GUI backend selection in octo_updater.py.

Only the resolver and startup error paths are exercised here — no GUI is
instantiated, so these run headless.
"""

import octo_updater
import pytest

QT_UNAVAILABLE = "The PySide6 (Qt) interface is not available in this build yet"


def test_resolve_backend_tk_returns_app_class():
    import app
    assert octo_updater.resolve_backend("tk") is app.OctoUpdaterApp


def test_resolve_backend_default_is_tk(monkeypatch):
    import app
    monkeypatch.delenv("OCTO_UI_BACKEND", raising=False)
    assert octo_updater.resolve_backend() is app.OctoUpdaterApp


def test_resolve_backend_qt_unavailable_raises_import_error():
    with pytest.raises(ImportError):
        octo_updater.resolve_backend("qt")


def test_qt_backend_message_is_friendly():
    with pytest.raises(ImportError) as excinfo:
        octo_updater.resolve_backend("qt")
    msg = octo_updater.backend_error_message("qt", excinfo.value)
    assert QT_UNAVAILABLE in msg


def test_unknown_backend_returns_none():
    assert octo_updater.resolve_backend("bogus") is None


def test_main_exits_1_for_unknown_backend(monkeypatch, capsys):
    monkeypatch.setenv("OCTO_UI_BACKEND", "bogus")
    with pytest.raises(SystemExit) as excinfo:
        octo_updater.main()
    assert excinfo.value.code == 1
    assert "Unknown OCTO_UI_BACKEND: bogus" in capsys.readouterr().err


def test_main_exits_1_for_qt_backend(monkeypatch, capsys):
    monkeypatch.setenv("OCTO_UI_BACKEND", "qt")
    with pytest.raises(SystemExit) as excinfo:
        octo_updater.main()
    assert excinfo.value.code == 1
    assert QT_UNAVAILABLE in capsys.readouterr().err


def test_main_exits_1_for_pyside6_backend(monkeypatch, capsys):
    monkeypatch.setenv("OCTO_UI_BACKEND", "pyside6")
    with pytest.raises(SystemExit) as excinfo:
        octo_updater.main()
    assert excinfo.value.code == 1
    assert QT_UNAVAILABLE in capsys.readouterr().err
