"""Headless Qt tests for the first-launch configuration import dialog.

QT_QPA_PLATFORM=offscreen is set before PySide6 is imported so the module
runs without a display. The QApplication is shared through create_qt_app().
"""

import os
import threading
import time as time_mod

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
)

from nostalgia_launcher.ui.qt.app import create_qt_app
from nostalgia_launcher.ui.qt.launcher_config_dialog import (
    LauncherConfigDialog,
)


@pytest.fixture(autouse=True)
def _offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return create_qt_app()


def _write_config(path):
    path.write_text(
        json.dumps({"server": {"base_url": "https://launcher.test"}}),
        encoding="utf-8",
    )
    return str(path)


def test_dialog_widgets_present(qapp, tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    dlg = LauncherConfigDialog(initial_path=_write_config(path))
    dlg.show()
    try:
        assert isinstance(dlg.findChild(QLabel, "launcherConfigTitle"), QLabel)
        assert isinstance(dlg.findChild(QLabel, "launcherConfigIntro"), QLabel)
        assert isinstance(
            dlg.findChild(QLineEdit, "launcherConfigUrl"), QLineEdit
        )
        path_edit = dlg.findChild(QLineEdit, "launcherConfigPath")
        assert path_edit.isReadOnly()
        assert path_edit.text() == str(path)
        assert isinstance(
            dlg.findChild(QPushButton, "launcherConfigBrowse"), QPushButton
        )
        assert isinstance(
            dlg.findChild(QPushButton, "launcherConfigOk"), QPushButton
        )
        assert isinstance(
            dlg.findChild(QPushButton, "launcherConfigCancel"), QPushButton
        )
        assert not dlg.findChild(QLabel, "launcherConfigError").isVisible()
        # Starts in the input stage: no summary, no Back button.
        assert not dlg.findChild(QLabel, "launcherConfigSummary").isVisible()
        assert not dlg.findChild(QPushButton, "launcherConfigBack").isVisible()
    finally:
        dlg.close()


def test_ok_without_input_shows_error(qapp):
    dlg = LauncherConfigDialog()
    dlg.show()
    try:
        dlg._submit()
        assert dlg.result() != QDialog.DialogCode.Accepted
        error = dlg.findChild(QLabel, "launcherConfigError")
        assert error.isVisible()
        assert error.text()
    finally:
        dlg.close()


def test_ok_with_valid_file_accepts_after_summary(qapp, tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    dlg = LauncherConfigDialog()
    dlg.show()
    try:
        dlg.findChild(QLineEdit, "launcherConfigPath").setText(
            _write_config(path)
        )
        # First submit validates and shows the summary stage.
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert dlg._stage == "summary"
        assert dlg.findChild(QLabel, "launcherConfigSummary").isVisible()
        summary = dlg.findChild(QLabel, "launcherConfigSummary").text()
        assert "launcher.test" in summary
        # Second submit (Accept) closes the dialog with the selection.
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.selection()["kind"] == "file"
        assert dlg.selection()["path"] == str(path)
        assert dlg.selection()["kind"] == "file"
    finally:
        dlg.close()


def test_summary_back_returns_to_input(qapp, tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    dlg = LauncherConfigDialog()
    try:
        dlg.findChild(QLineEdit, "launcherConfigPath").setText(
            _write_config(path)
        )
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert dlg._stage == "summary"
        dlg.findChild(QPushButton, "launcherConfigBack").click()
        assert dlg._stage == "input"
        assert not dlg.findChild(QLabel, "launcherConfigSummary").isVisible()
    finally:
        dlg.close()


def test_ok_with_invalid_file_shows_error(qapp, tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(b"not json")
    dlg = LauncherConfigDialog()
    dlg.show()
    try:
        dlg.findChild(QLineEdit, "launcherConfigPath").setText(str(path))
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert dlg.result() != QDialog.DialogCode.Accepted
        error = dlg.findChild(QLabel, "launcherConfigError")
        assert error.isVisible()
        assert error.text()
    finally:
        dlg.close()


def test_browse_updates_path_and_validates(qapp, tmp_path, monkeypatch):
    import nostalgia_launcher.ui.qt.launcher_config_dialog as dialog_module

    path = tmp_path / "nostalgia_launcher.json"
    valid = _write_config(path)
    monkeypatch.setattr(
        dialog_module.QFileDialog,
        "getOpenFileName",
        staticmethod(
            lambda *a, **k: (valid, "Launcher configuration (*.json)")
        ),
    )
    dlg = LauncherConfigDialog()
    try:
        dlg.findChild(QPushButton, "launcherConfigBrowse").click()
        assert dlg.findChild(QLineEdit, "launcherConfigPath").text() == valid
    finally:
        dlg.close()


def test_cancel_rejects(qapp):
    dlg = LauncherConfigDialog()
    try:
        dlg.findChild(QPushButton, "launcherConfigCancel").click()
        assert dlg.result() == QDialog.DialogCode.Rejected
    finally:
        dlg.close()


def _wait_until(qapp, cond, timeout_ms=4000):
    """Pump the event loop until `cond()` is true (async fetch polling)."""
    deadline = time_mod.monotonic() + timeout_ms / 1000
    while not cond():
        if time_mod.monotonic() > deadline:
            return False
        qapp.processEvents()
        time_mod.sleep(0.01)
    return True


def test_url_submission_reaches_summary(qapp, monkeypatch):
    import nostalgia_launcher.services.config_import as config_import_module

    payload = json.dumps({"server": {"base_url": "https://x.example"}})
    monkeypatch.setattr(
        config_import_module,
        "fetch_config_url",
        lambda url: (
            {"server": {"base_url": "https://x.example"}},
            payload,
            "",
        ),
    )
    dlg = LauncherConfigDialog()
    dlg.show()
    try:
        dlg.findChild(QLineEdit, "launcherConfigUrl").setText(
            "https://example.invalid/community.json"
        )
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert _wait_until(qapp, lambda: dlg._stage == "summary")
        summary = dlg.findChild(QLabel, "launcherConfigSummary").text()
        assert "x.example" in summary
        assert dlg.selection() is None  # not yet accepted
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert dlg.result() == QDialog.DialogCode.Accepted
        sel = dlg.selection()
        assert sel["kind"] == "url"
        assert sel["config_url"] == "https://example.invalid/community.json"
        assert sel["raw"] == payload
    finally:
        dlg.close()


def test_url_non_https_rejected_immediately(qapp):
    dlg = LauncherConfigDialog()
    dlg.show()
    try:
        dlg.findChild(QLineEdit, "launcherConfigUrl").setText(
            "http://insecure.example/community.json"
        )
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        error = dlg.findChild(QLabel, "launcherConfigError")
        assert error.isVisible()
        assert error.text()
        assert dlg._stage == "input"
    finally:
        dlg.close()


def test_url_fetch_failure_shows_error(qapp, monkeypatch):
    import nostalgia_launcher.services.config_import as config_import_module

    monkeypatch.setattr(
        config_import_module,
        "fetch_config_url",
        lambda url: (None, None, "Could not fetch the configuration: boom"),
    )
    dlg = LauncherConfigDialog()
    dlg.show()
    error = dlg.findChild(QLabel, "launcherConfigError")
    try:
        dlg.findChild(QLineEdit, "launcherConfigUrl").setText(
            "https://example.invalid/community.json"
        )
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert _wait_until(qapp, lambda: error.isVisible() and error.text())
        assert dlg.result() != QDialog.DialogCode.Accepted
        assert dlg._stage == "input"
    finally:
        dlg.close()


def test_cancel_during_fetch_never_accepts(qapp, monkeypatch):
    """A fetch result arriving after Cancel must not re-accept the dialog."""
    import nostalgia_launcher.services.config_import as config_import_module

    release = threading.Event()

    def slow_fetch(url):
        release.wait(2)
        return ({"server": {}}, "{}", "")

    monkeypatch.setattr(config_import_module, "fetch_config_url", slow_fetch)
    dlg = LauncherConfigDialog()
    dlg.show()
    try:
        dlg.findChild(QLineEdit, "launcherConfigUrl").setText(
            "https://example.invalid/community.json"
        )
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        deadline = time_mod.monotonic() + 0.2
        while time_mod.monotonic() < deadline:
            qapp.processEvents()
            time_mod.sleep(0.01)
        dlg.reject()
        release.set()
        deadline = time_mod.monotonic() + 1.0
        while time_mod.monotonic() < deadline:
            qapp.processEvents()
            time_mod.sleep(0.01)
        assert dlg.result() != QDialog.DialogCode.Accepted
        assert dlg.selection() is None
    finally:
        dlg.close()


# ── three-category summary ───────────────────────────────────────────────────


def test_summary_lists_embedded_and_local_store(qapp, tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps(
            {
                "server": {"base_url": "https://launcher.test"},
                "mods": [{"id": "m"}],
                "addons": [{"name": "a", "git": "https://github.com/e/a"}],
                "assets": [
                    {
                        "id": "p",
                        "url": "https://launcher.test/p.mpq",
                        "dest": "Data/p.mpq",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dlg = LauncherConfigDialog(initial_path=str(path))
    dlg._submit_file(str(path))
    text = dlg.findChild(QLabel, "launcherConfigSummary").text()
    assert "Will store locally: 1 mod, 1 addon, 1 asset" in text
    assert "+1 embedded" in text
    assert "Asset catalog:" in text


def test_summary_omits_asset_line_when_unconfigured(qapp, tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps({"server": {"base_url": "https://launcher.test"}}),
        encoding="utf-8",
    )
    dlg = LauncherConfigDialog(initial_path=str(path))
    dlg._submit_file(str(path))
    text = dlg.findChild(QLabel, "launcherConfigSummary").text()
    assert "Asset catalog:" not in text
    assert "Will store locally" not in text
