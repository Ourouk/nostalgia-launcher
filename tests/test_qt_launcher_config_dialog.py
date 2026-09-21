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
    QRadioButton,
    QScrollArea,
    QWidget,
)

from nostalgia_launcher.core.platform_support import default_game_folder
from nostalgia_launcher.ui.qt.app import create_qt_app
from nostalgia_launcher.ui.qt.launcher_config_dialog import (
    LauncherConfigDialog,
    trust_capabilities,
    trust_hosts,
)


@pytest.fixture(autouse=True)
def _offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return create_qt_app()


def _write_config(path, payload=None):
    path.write_text(
        json.dumps(payload or {"server": {"url": "https://launcher.test"}}),
        encoding="utf-8",
    )
    return str(path)


def _trust_text(dlg, name):
    label = dlg.findChild(QLabel, name)
    assert isinstance(label, QLabel)
    return label.text()


def test_dialog_widgets_present(qapp, tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    dlg = LauncherConfigDialog(initial_path=_write_config(path))
    dlg.show()
    try:
        title = dlg.findChild(QLabel, "launcherConfigTitle")
        assert isinstance(title, QLabel)
        assert title.text() == "Welcome to Nostalgia Launcher"
        assert isinstance(dlg.findChild(QLabel, "launcherConfigStep"), QLabel)
        assert isinstance(dlg.findChild(QLabel, "launcherConfigIntro"), QLabel)
        assert isinstance(
            dlg.findChild(QRadioButton, "launcherConfigSourceFileRadio"),
            QRadioButton,
        )
        assert isinstance(
            dlg.findChild(QRadioButton, "launcherConfigSourceUrlRadio"),
            QRadioButton,
        )
        assert isinstance(
            dlg.findChild(QLineEdit, "launcherConfigUrl"), QLineEdit
        )
        path_edit = dlg.findChild(QLineEdit, "launcherConfigPath")
        assert path_edit.isReadOnly()
        assert path_edit.text() == str(path)
        assert isinstance(
            dlg.findChild(QPushButton, "launcherConfigBrowse"), QPushButton
        )
        # Install-folder stage widgets exist but stay hidden until a
        # configuration validated (input stage).
        assert isinstance(
            dlg.findChild(QLabel, "launcherConfigFolderTitle"), QLabel
        )
        folder_edit = dlg.findChild(QLineEdit, "launcherConfigFolder")
        assert isinstance(folder_edit, QLineEdit)
        assert not folder_edit.isReadOnly()
        assert isinstance(
            dlg.findChild(QPushButton, "launcherConfigFolderBrowse"),
            QPushButton,
        )
        assert not dlg.findChild(
            QWidget, "launcherConfigFolderGroup"
        ).isVisible()
        assert isinstance(
            dlg.findChild(QPushButton, "launcherConfigOk"), QPushButton
        )
        assert isinstance(
            dlg.findChild(QPushButton, "launcherConfigCancel"), QPushButton
        )
        assert not dlg.findChild(QLabel, "launcherConfigError").isVisible()
        # Starts in the input stage: trust screen hidden, no Back button.
        assert not dlg.findChild(
            QScrollArea, "launcherConfigSummaryScroll"
        ).isVisible()
        assert not dlg.findChild(QPushButton, "launcherConfigBack").isVisible()
    finally:
        dlg.close()


def test_source_radios_toggle_visible_row(qapp):
    dlg = LauncherConfigDialog()
    dlg.show()
    try:
        file_radio = dlg.findChild(
            QRadioButton, "launcherConfigSourceFileRadio"
        )
        url_radio = dlg.findChild(QRadioButton, "launcherConfigSourceUrlRadio")
        assert file_radio.isChecked()
        assert dlg.findChild(QWidget, "launcherConfigPathRow").isVisible()
        assert not dlg.findChild(QWidget, "launcherConfigUrlRow").isVisible()
        url_radio.setChecked(True)
        assert dlg.findChild(QWidget, "launcherConfigUrlRow").isVisible()
        assert not dlg.findChild(QWidget, "launcherConfigPathRow").isVisible()
        assert not dlg.findChild(QPushButton, "launcherConfigOk").isEnabled()
        dlg.findChild(QLineEdit, "launcherConfigUrl").setText(
            "https://example.invalid/community.json"
        )
        assert dlg.findChild(QPushButton, "launcherConfigOk").isEnabled()
    finally:
        dlg.close()


def test_typing_url_selects_url_source(qapp):
    dlg = LauncherConfigDialog()
    try:
        dlg.findChild(QLineEdit, "launcherConfigUrl").setText(
            "https://example.invalid/community.json"
        )
        assert dlg.findChild(
            QRadioButton, "launcherConfigSourceUrlRadio"
        ).isChecked()
        assert dlg._active_source() == "url"
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


def test_ok_with_valid_file_accepts_after_trust(qapp, tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    dlg = LauncherConfigDialog()
    dlg.show()
    try:
        dlg.findChild(QLineEdit, "launcherConfigPath").setText(
            _write_config(path)
        )
        # First submit validates and shows the install-folder stage.
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert dlg._stage == "folder"
        assert dlg.findChild(QWidget, "launcherConfigFolderGroup").isVisible()
        folder = dlg.findChild(QLineEdit, "launcherConfigFolder")
        assert folder.text() == default_game_folder("launcher.test")
        # Second submit confirms the folder and shows the trust stage.
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert dlg._stage == "summary"
        assert dlg.findChild(QLabel, "launcherConfigTitle").text() == (
            "You're about to trust:"
        )
        assert "launcher.test" in _trust_text(dlg, "launcherConfigTrustName")
        assert "launcher.test" in _trust_text(dlg, "launcherConfigTrustHosts")
        assert folder.text() in _trust_text(dlg, "launcherConfigTrustFolder")
        assert (
            dlg.findChild(QPushButton, "launcherConfigOk").text()
            == "Trust configuration"
        )
        # Third submit (Trust) closes the dialog with the selection.
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.selection()["kind"] == "file"
        assert dlg.selection()["path"] == str(path)
        assert dlg.selection()["install_dir"] == folder.text()
    finally:
        dlg.close()


def test_folder_stage_requires_a_choice(qapp, tmp_path):
    """The install folder is REQUIRED: an empty field disables Continue
    and a manual empty submit keeps the dialog on the folder stage."""
    path = tmp_path / "nostalgia_launcher.json"
    dlg = LauncherConfigDialog()
    dlg.show()
    try:
        dlg.findChild(QLineEdit, "launcherConfigPath").setText(
            _write_config(path)
        )
        ok = dlg.findChild(QPushButton, "launcherConfigOk")
        ok.click()
        assert dlg._stage == "folder"
        folder = dlg.findChild(QLineEdit, "launcherConfigFolder")
        folder.clear()
        assert not ok.isEnabled()  # live gating on the empty field
        ok.setEnabled(True)  # bypass the gate to exercise the guard
        ok.click()
        error = dlg.findChild(QLabel, "launcherConfigError")
        assert error.isVisible()
        assert "install" in error.text().lower()
        assert dlg._stage == "folder"
        assert dlg.selection() is None
    finally:
        dlg.close()


def test_folder_stage_browse_updates_field(qapp, tmp_path, monkeypatch):
    import nostalgia_launcher.ui.qt.launcher_config_dialog as dialog_module

    game_dir = tmp_path / "Games" / "WoW"
    game_dir.mkdir(parents=True)
    monkeypatch.setattr(
        dialog_module.QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *a, **k: str(game_dir)),
    )
    path = tmp_path / "nostalgia_launcher.json"
    dlg = LauncherConfigDialog()
    dlg.show()
    try:
        dlg._submit_file(_write_config(path))
        assert dlg._stage == "folder"
        dlg.findChild(QPushButton, "launcherConfigFolderBrowse").click()
        folder = dlg.findChild(QLineEdit, "launcherConfigFolder")
        assert folder.text() == str(game_dir)
    finally:
        dlg.close()


def test_folder_stage_back_returns_to_input(qapp, tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    dlg = LauncherConfigDialog()
    try:
        dlg._submit_file(_write_config(path))
        assert dlg._stage == "folder"
        dlg.findChild(QPushButton, "launcherConfigBack").click()
        assert dlg._stage == "input"
        # The validated config was dropped: continuing re-validates.
        assert dlg._validated is None
        assert not dlg.findChild(
            QWidget, "launcherConfigFolderGroup"
        ).isVisible()
    finally:
        dlg.close()


def test_summary_back_returns_to_folder_stage(qapp, tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    dlg = LauncherConfigDialog()
    try:
        dlg._submit_file(_write_config(path))
        dlg._confirm_folder()
        assert dlg._stage == "summary"
        dlg.findChild(QPushButton, "launcherConfigBack").click()
        assert dlg._stage == "folder"
        assert not dlg.findChild(
            QScrollArea, "launcherConfigSummaryScroll"
        ).isVisible()
    finally:
        dlg.close()


def test_install_dir_is_expanded_and_normalized(qapp, tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    dlg = LauncherConfigDialog(initial_path=_write_config(path))
    try:
        dlg._submit_file(str(path))
        folder = dlg.findChild(QLineEdit, "launcherConfigFolder")
        folder.setText(str(tmp_path / "sub" / ".." / "game"))
        dlg._confirm_folder()
        dlg._accept_pending()
        expected = os.path.normpath(str(tmp_path / "sub" / ".." / "game"))
        assert dlg.selection()["install_dir"] == expected
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


def test_url_submission_reaches_trust(qapp, monkeypatch):
    import nostalgia_launcher.services.config_import as config_import_module

    payload = json.dumps({"server": {"url": "https://x.example"}})
    monkeypatch.setattr(
        config_import_module,
        "fetch_config_url",
        lambda url: (
            {"server": {"url": "https://x.example"}},
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
        # Fetch done → install-folder stage (pre-filled suggestion).
        assert _wait_until(qapp, lambda: dlg._stage == "folder")
        folder = dlg.findChild(QLineEdit, "launcherConfigFolder")
        assert folder.text() == default_game_folder("x.example")
        assert dlg.selection() is None  # not yet accepted
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert dlg._stage == "summary"
        assert "x.example" in _trust_text(dlg, "launcherConfigTrustName")
        dlg.findChild(QPushButton, "launcherConfigOk").click()
        assert dlg.result() == QDialog.DialogCode.Accepted
        sel = dlg.selection()
        assert sel["kind"] == "url"
        assert sel["config_url"] == "https://example.invalid/community.json"
        assert sel["raw"] == payload
        assert sel["install_dir"] == default_game_folder("x.example")
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


# ── trust screen ─────────────────────────────────────────────────────────


def test_trust_lists_hosts_and_capabilities(qapp, tmp_path):
    path = tmp_path / "cfg.json"
    _write_config(
        path,
        {
            "server": {
                "name": "Example Community",
                "url": "https://launcher.test",
                "news_url": "https://launcher.test/news.json",
                "mods_registry_url": "https://launcher.test/mods.json",
                "addons_registry_urls": ["https://launcher.test/addons.json"],
                "download": {
                    "http": {
                        "fallback": "https://cdn.test/client.zip",
                    },
                    "content": {"type": "zip"},
                },
            },
            "mods": [{"id": "m"}],
            "addons": [{"name": "a", "git": "https://github.com/e/a"}],
        },
    )
    dlg = LauncherConfigDialog(initial_path=str(path))
    dlg._submit_file(str(path))
    dlg._confirm_folder()  # pre-filled suggestion stands in as the choice
    assert dlg._stage == "summary"
    hosts = _trust_text(dlg, "launcherConfigTrustHosts")
    for host in ("launcher.test", "cdn.test", "github.com"):
        assert host in hosts
    assert _trust_text(dlg, "launcherConfigTrustClient").startswith("✓")
    assert _trust_text(dlg, "launcherConfigTrustMods").startswith("✓")
    assert _trust_text(dlg, "launcherConfigTrustAddons").startswith("✓")
    assert _trust_text(dlg, "launcherConfigTrustNews").startswith("✓")
    assert "1 embedded" in _trust_text(dlg, "launcherConfigTrustMods")
    dlg.close()


def test_trust_shows_missing_capabilities_as_unavailable(qapp, tmp_path):
    path = tmp_path / "cfg.json"
    _write_config(path)
    dlg = LauncherConfigDialog(initial_path=str(path))
    dlg._submit_file(str(path))
    dlg._confirm_folder()  # pre-filled suggestion stands in as the choice
    assert _trust_text(dlg, "launcherConfigTrustClient").startswith("—")
    assert _trust_text(dlg, "launcherConfigTrustMods").startswith("—")
    assert _trust_text(dlg, "launcherConfigTrustAddons").startswith("—")
    assert _trust_text(dlg, "launcherConfigTrustNews").startswith("—")
    dlg.close()


def test_trust_helpers_cover_full_example():
    from nostalgia_launcher.core import launcher

    with open("examples/community.example.json", encoding="utf-8") as f:
        config, err = launcher.validate_dict(json.load(f))
    assert err == ""
    assert config is not None
    hosts = trust_hosts(config)
    assert "launcher.example.com" in hosts
    assert "github.com" in hosts
    caps = dict(
        (k, (ok, label)) for k, ok, label in trust_capabilities(config)
    )
    assert caps["client"][0] is True
    assert caps["mods"][0] is True
    assert caps["addons"][0] is True
    assert caps["news"][0] is True
