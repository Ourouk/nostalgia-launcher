"""Tests for GUI backend selection in nostalgia_launcher.cli.

Only the resolver and startup wiring are exercised here. The QML backend
is faked (it never opens a display in these tests). `main()` with no
launcher config opens the first-launch wizard (`_pick_launcher_config`),
which is monkeypatched here.
"""

import json

import pytest

import nostalgia_launcher.core.launcher as launcher
from nostalgia_launcher import cli

QT_UNAVAILABLE = "Nostalgia Launcher needs PySide6 (Qt) to run"


@pytest.fixture
def launcher_file(tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    path.write_text(
        json.dumps({"server": {"url": "https://launcher.test"}}),
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture
def no_persisted_config(monkeypatch, tmp_path):
    """Keep auto-discovery from picking up a real per-user config."""
    monkeypatch.setattr(
        launcher, "user_config_path", lambda: str(tmp_path / "none.json")
    )


def test_resolve_backend_default_is_qml(monkeypatch):
    import nostalgia_launcher.ui.qml.app as qml_app

    monkeypatch.delenv("NOSTALGIA_UI_BACKEND", raising=False)
    assert cli.resolve_backend() is qml_app.QmlNostalgiaLauncherApp


def test_resolve_backend_legacy_names_are_unknown():
    assert cli.resolve_backend("qt") is None
    assert cli.resolve_backend("pyside6") is None


def test_qml_backend_error_message_is_friendly():
    msg = cli.backend_error_message("qml", ImportError("broken"))
    assert QT_UNAVAILABLE in msg


def test_main_returns_1_when_qml_import_fails(
    monkeypatch, capsys, launcher_file, hermetic_cli
):
    import sys

    monkeypatch.setenv("NOSTALGIA_UI_BACKEND", "qml")
    monkeypatch.setitem(sys.modules, "nostalgia_launcher.ui.qml.app", None)
    assert cli.main(["--launcher-config", launcher_file]) == 1
    assert QT_UNAVAILABLE in capsys.readouterr().err


def test_unknown_backend_returns_none():
    assert cli.resolve_backend("bogus") is None


def test_main_returns_1_for_unknown_backend(
    monkeypatch, capsys, launcher_file, hermetic_cli
):
    monkeypatch.setenv("NOSTALGIA_UI_BACKEND", "bogus")
    assert cli.main(["--launcher-config", launcher_file]) == 1
    assert "Unknown NOSTALGIA_UI_BACKEND: bogus" in capsys.readouterr().err


def test_main_returns_1_without_launcher_config(
    monkeypatch, capsys, tmp_path, no_persisted_config, hermetic_cli
):
    monkeypatch.setenv("NOSTALGIA_UI_BACKEND", "qml")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_pick_launcher_config", lambda: None)
    assert cli.main([]) == 1
    assert "No launcher configuration selected" in capsys.readouterr().err


def test_main_wizard_selection_runs_backend(
    monkeypatch,
    capsys,
    launcher_file,
    tmp_path,
    no_persisted_config,
    hermetic_cli,
):
    calls = []

    class FakeApp:
        def __init__(self, open_log=False):
            calls.append("constructed")

        def show(self):
            calls.append("shown")

        def run(self):
            calls.append("run")
            return 0

    monkeypatch.setenv("NOSTALGIA_UI_BACKEND", "qml")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(
        cli,
        "_pick_launcher_config",
        lambda: {"kind": "file", "path": launcher_file},
    )
    monkeypatch.setattr(cli, "resolve_backend", lambda name: FakeApp)
    assert cli.main([]) == 0
    assert calls == ["constructed", "shown", "run"]


def test_main_wizard_selection_persists_config(
    monkeypatch, launcher_file, tmp_path, hermetic_cli
):
    calls = []

    class FakeApp:
        def __init__(self, open_log=False):
            calls.append("constructed")

        def show(self):
            calls.append("shown")

        def run(self):
            calls.append("run")
            return 0

    dest = tmp_path / "persisted" / "nostalgia_launcher.json"
    monkeypatch.setattr(launcher, "user_config_path", lambda: str(dest))
    monkeypatch.setenv("NOSTALGIA_UI_BACKEND", "qml")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(
        cli,
        "_pick_launcher_config",
        lambda: {"kind": "file", "path": launcher_file},
    )
    monkeypatch.setattr(cli, "resolve_backend", lambda name: FakeApp)
    assert cli.main([]) == 0
    assert dest.exists()
    assert json.loads(dest.read_text()) == {
        "server": {"url": "https://launcher.test"}
    }
    assert launcher.config().server_url == "https://launcher.test"


def test_main_wizard_persistence_failure_aborts(
    monkeypatch, capsys, launcher_file, tmp_path, hermetic_cli
):
    """If saving the imported config fails, startup aborts and the backend is
    never constructed."""
    calls = []

    class FakeApp:
        def __init__(self, open_log=False):
            calls.append("constructed")

        def show(self):
            calls.append("shown")

        def run(self):
            calls.append("run")
            return 0

    monkeypatch.setattr(
        launcher, "user_config_path", lambda: str(tmp_path / "none.json")
    )
    monkeypatch.setattr(
        launcher,
        "persist",
        lambda path: ("", "Could not save the launcher configuration: boom"),
    )
    monkeypatch.setenv("NOSTALGIA_UI_BACKEND", "qml")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(
        cli,
        "_pick_launcher_config",
        lambda: {"kind": "file", "path": launcher_file},
    )
    monkeypatch.setattr(cli, "resolve_backend", lambda name: FakeApp)
    assert cli.main([]) == 1
    assert (
        "Could not save the launcher configuration" in capsys.readouterr().err
    )
    assert calls == []


def test_main_explicit_bad_config_never_opens_wizard(
    monkeypatch, capsys, tmp_path, hermetic_cli
):
    recorder = []
    monkeypatch.setattr(
        cli, "_pick_launcher_config", lambda: recorder.append("called")
    )
    assert cli.main(["--launcher-config", str(tmp_path / "missing.json")]) == 1
    assert "Invalid launcher configuration" in capsys.readouterr().err
    assert recorder == []


def test_main_wizard_qml_import_failure(
    monkeypatch, capsys, tmp_path, no_persisted_config, hermetic_cli
):
    monkeypatch.setenv("NOSTALGIA_UI_BACKEND", "qml")
    monkeypatch.chdir(tmp_path)

    def _fail():
        raise ImportError("no qml")

    monkeypatch.setattr(cli, "_pick_launcher_config", _fail)
    assert cli.main([]) == 1
    assert "PySide6" in capsys.readouterr().err


def test_main_constructs_shows_and_runs_qml_backend(
    monkeypatch, launcher_file, hermetic_cli
):
    calls = []

    class FakeApp:
        def __init__(self, open_log=False):
            calls.append("constructed")

        def show(self):
            calls.append("shown")

        def run(self):
            calls.append("run")
            return 0

    monkeypatch.setenv("NOSTALGIA_UI_BACKEND", "qml")
    monkeypatch.setattr(cli, "resolve_backend", lambda name: FakeApp)
    assert cli.main(["--launcher-config", launcher_file]) == 0
    assert calls == ["constructed", "shown", "run"]


def test_main_unknown_backend_releases_guard_server(
    hermetic_cli, monkeypatch, capsys, launcher_file
):
    """Early exits after the single-instance handshake must still release
    this instance's guard server — a leaked QLocalServer would make the
    next launch see a stale 'already running' for the profile."""
    from nostalgia_launcher.ui import app_lock_qt

    monkeypatch.setenv("NOSTALGIA_UI_BACKEND", "bogus")
    assert cli.main(["--launcher-config", launcher_file]) == 1
    assert app_lock_qt._SERVERS == {}
