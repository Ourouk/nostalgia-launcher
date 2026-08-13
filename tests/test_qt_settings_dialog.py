"""Headless Qt tests for the settings dialog (qt_settings_dialog).

QT_QPA_PLATFORM=offscreen is set before PySide6 is imported so the module
runs without a display. The dialog is opened from the main window's gear
button and driven through the same controller methods the Tk overlay drove;
platform gates (Defender exclusions, launch-on-close checkboxes) are flipped
via monkeypatch, and every controller side-effect is mocked so no worker or
network request ever runs.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QWidget,
)

import platform_support
from qt_app import create_qt_app
from qt_bridge import ControllerHub
from qt_main_window import MainWindow
from qt_theme import Palette
from qt_settings_dialog import SettingsDialog
from ui_events import MirrorStatusChanged


@pytest.fixture(autouse=True)
def _offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return create_qt_app()


@pytest.fixture()
def hub(qapp):
    hub = ControllerHub()
    yield hub
    hub.close()


@pytest.fixture()
def window(hub):
    win = MainWindow(hub)
    win.show()
    yield win
    win.close()


def _open(window) -> SettingsDialog:
    QTest.mouseClick(window._gearButton, Qt.LeftButton)
    dialog = window._settingsDialog
    assert isinstance(dialog, SettingsDialog)
    QTest.qWait(20)
    return dialog


# ── gear → dialog ───────────────────────────────────────────────────────

def test_gear_opens_settings_dialog(qapp, window):
    assert window._settingsDialog is None
    dialog = _open(window)
    assert dialog.objectName() == "settingsDialog"
    assert dialog.isVisible()
    assert dialog.windowTitle() == "Settings"
    for name in ("settingsPath", "settingsChange", "settingsOpenFolder",
                 "settingsMirrorStatus", "settingsMirrorRefresh",
                 "settingsVerify", "settingsLogs", "settingsKoFi",
                 "settingsBmc", "settingsClose", "settingsAutoMods",
                 "settingsAutoAddons"):
        assert dialog.findChild(QWidget, name) is not None


def test_gear_reuses_open_dialog(qapp, window):
    first = _open(window)
    QTest.mouseClick(window._gearButton, Qt.LeftButton)
    assert window._settingsDialog is first
    assert first.isVisible()


# ── game folder ─────────────────────────────────────────────────────────

def test_path_field_shows_state_path(qapp, window):
    dialog = _open(window)
    path = dialog.findChild(QLineEdit, "settingsPath")
    assert path.text() == window._hub.settings.state.path
    assert path.isReadOnly()


def test_open_folder_calls_open_client_folder(qapp, window, monkeypatch):
    hub = window._hub
    open_client = Mock()
    monkeypatch.setattr(hub.settings, "open_client_folder", open_client)
    dialog = _open(window)
    QTest.mouseClick(dialog.findChild(QWidget, "settingsOpenFolder"),
                     Qt.LeftButton)
    open_client.assert_called_once()


def test_change_updates_path(qapp, window, monkeypatch, tmp_path):
    hub = window._hub
    chosen = str(tmp_path / "game folder")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: chosen)
    set_path = Mock(return_value=True)
    monkeypatch.setattr(hub.settings, "set_path", set_path)
    dialog = _open(window)

    QTest.mouseClick(dialog.findChild(QPushButton, "settingsChange"),
                     Qt.LeftButton)
    set_path.assert_called_once_with(os.path.normpath(chosen))
    assert (dialog.findChild(QLineEdit, "settingsPath").text()
            == os.path.normpath(chosen))


def test_change_cancelled_leaves_path(qapp, window, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: "")
    dialog = _open(window)
    before = window._hub.settings.state.path
    QTest.mouseClick(dialog.findChild(QPushButton, "settingsChange"),
                     Qt.LeftButton)
    assert (dialog.findChild(QLineEdit, "settingsPath").text() == before)


# ── download mirror ─────────────────────────────────────────────────────

def test_mirror_status_renders_initial_state(qapp, window):
    hub = window._hub
    hub.settings.mirror_status = "online"
    dialog = _open(window)
    status = dialog.findChild(QLabel, "settingsMirrorStatus")
    assert status.text() == "online"
    p = Palette()
    assert p.ok.name() in status.styleSheet()


def test_mirror_status_updates_on_event(qapp, window):
    dialog = _open(window)
    status = dialog.findChild(QLabel, "settingsMirrorStatus")
    hub = window._hub
    p = Palette()

    hub.dispatcher.post(MirrorStatusChanged(True, "online"))
    QTest.qWait(200)
    assert status.text() == "online"
    assert p.ok.name() in status.styleSheet()

    hub.dispatcher.post(MirrorStatusChanged(False, "offline"))
    QTest.qWait(200)
    assert status.text() == "offline"
    assert p.err.name() in status.styleSheet()


def test_mirror_refresh_calls_check_mirror(qapp, window, monkeypatch):
    hub = window._hub
    check = Mock()
    monkeypatch.setattr(hub.settings, "check_mirror", check)
    dialog = _open(window)
    dialog.findChild(QToolButton, "settingsMirrorRefresh").click()
    check.assert_called_once()
    assert (dialog.findChild(QLabel, "settingsMirrorStatus").text()
            == "checking…")


# ── troubleshooting rows ────────────────────────────────────────────────

def test_verify_row_calls_verify_files(qapp, window, monkeypatch):
    hub = window._hub
    verify = Mock()
    monkeypatch.setattr(hub.settings, "verify_files", verify)
    dialog = _open(window)
    QTest.mouseClick(dialog.findChild(QWidget, "settingsVerify"),
                     Qt.LeftButton)
    verify.assert_called_once()


def test_logs_row_emits_show_logs_requested(qapp, window):
    dialog = _open(window)
    spy = Mock()
    dialog.showLogsRequested.connect(spy)
    QTest.mouseClick(dialog.findChild(QWidget, "settingsLogs"), Qt.LeftButton)
    spy.assert_called_once()


def test_av_row_absent_when_cannot_manage_antivirus(qapp, window):
    dialog = _open(window)
    assert dialog.findChild(QWidget, "settingsAv") is None


def test_av_row_calls_allow_through_antivirus(qapp, window, monkeypatch):
    monkeypatch.setattr(platform_support, "can_manage_antivirus",
                        lambda: True)
    hub = window._hub
    allow = Mock()
    monkeypatch.setattr(hub.settings, "allow_through_antivirus", allow)
    dialog = _open(window)
    row = dialog.findChild(QWidget, "settingsAv")
    assert row is not None
    QTest.mouseClick(row, Qt.LeftButton)
    allow.assert_called_once()


# ── support links ───────────────────────────────────────────────────────

def test_support_links_call_open_url(qapp, window, monkeypatch):
    hub = window._hub
    open_url = Mock()
    monkeypatch.setattr(hub.settings, "open_url", open_url)
    dialog = _open(window)

    QTest.mouseClick(dialog.findChild(QWidget, "settingsKoFi"), Qt.LeftButton)
    open_url.assert_called_once_with("https://ko-fi.com/rebased")
    QTest.mouseClick(dialog.findChild(QWidget, "settingsBmc"), Qt.LeftButton)
    open_url.assert_called_with("https://buymeacoffee.com/rebased")


# ── general checkboxes ─────────────────────────────────────────────────

def test_checkboxes_reflect_config(qapp, window, monkeypatch):
    hub = window._hub
    hub.settings.state.config = {
        "auto_install_mods": True,
        "auto_install_addons": False,
        "clear_wdb_on_launch": True,
        "close_on_launch": False,
    }
    monkeypatch.setattr(platform_support, "can_launch_client", lambda: True)
    dialog = _open(window)

    assert (dialog.findChild(QCheckBox, "settingsAutoMods").isChecked()
            is True)
    assert (dialog.findChild(QCheckBox, "settingsAutoAddons").isChecked()
            is False)
    assert (dialog.findChild(QCheckBox, "settingsClearWdb").isChecked()
            is True)
    assert (dialog.findChild(QCheckBox, "settingsCloseOnLaunch").isChecked()
            is False)


def test_launch_checkboxes_absent_when_cannot_launch_client(qapp, window):
    dialog = _open(window)
    assert dialog.findChild(QCheckBox, "settingsClearWdb") is None
    assert dialog.findChild(QCheckBox, "settingsCloseOnLaunch") is None
    assert dialog.findChild(QCheckBox, "settingsAutoMods") is not None


def test_toggle_auto_mods_calls_set_auto_mods(qapp, window, monkeypatch):
    hub = window._hub
    hub.settings.state.config = {"auto_install_mods": True}
    set_mods = Mock()
    monkeypatch.setattr(hub.settings, "set_auto_mods", set_mods)
    dialog = _open(window)
    check = dialog.findChild(QCheckBox, "settingsAutoMods")
    assert check.isChecked() is True
    check.setChecked(False)
    set_mods.assert_called_once_with(False)


def test_toggle_auto_addons_calls_set_auto_addons(qapp, window, monkeypatch):
    hub = window._hub
    hub.settings.state.config = {"auto_install_addons": True}
    set_addons = Mock()
    monkeypatch.setattr(hub.settings, "set_auto_addons", set_addons)
    dialog = _open(window)
    check = dialog.findChild(QCheckBox, "settingsAutoAddons")
    assert check.isChecked() is True
    check.setChecked(False)
    set_addons.assert_called_once_with(False)


def test_toggle_clear_wdb_calls_set_clear_wdb(qapp, window, monkeypatch):
    monkeypatch.setattr(platform_support, "can_launch_client", lambda: True)
    hub = window._hub
    set_wdb = Mock()
    monkeypatch.setattr(hub.settings, "set_clear_wdb", set_wdb)
    dialog = _open(window)
    check = dialog.findChild(QCheckBox, "settingsClearWdb")
    check.setChecked(True)
    set_wdb.assert_called_once_with(True)


# ── close ──────────────────────────────────────────────────────────────

def test_close_works_headlessly(qapp, window):
    dialog = _open(window)
    assert dialog.isVisible()
    QTest.mouseClick(dialog.findChild(QWidget, "settingsClose"),
                     Qt.LeftButton)
    QTest.qWait(20)
    assert not dialog.isVisible()
    # Reopening via the gear reuses the same (hidden) dialog instance.
    QTest.mouseClick(window._gearButton, Qt.LeftButton)
    assert window._settingsDialog is dialog
    assert dialog.isVisible()
    dialog.close()
    QTest.qWait(20)
    assert not dialog.isVisible()


def test_close_triggers_pending_auto_install(qapp, window, monkeypatch):
    hub = window._hub
    dialog = _open(window)
    mods_install = Mock()
    addons_install = Mock()
    monkeypatch.setattr(hub.settings, "install_missing_essential_mods",
                        mods_install)
    monkeypatch.setattr(hub.settings, "install_missing_recommended_addons",
                        addons_install)
    hub.settings._pending_auto_mods = True
    hub.settings._pending_auto_addons = True

    dialog.close()
    QTest.qWait(20)
    mods_install.assert_called_once()
    addons_install.assert_called_once()
    assert not dialog.isVisible()
