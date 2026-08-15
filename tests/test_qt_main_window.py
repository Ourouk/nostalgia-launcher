"""Headless Qt tests for the main window shell.

QT_QPA_PLATFORM=offscreen is set before PySide6 is imported so the module
runs without a display. The QApplication is created once and shared through
the create_qt_app() singleton; each test builds a fresh ControllerHub +
MainWindow and shows it so child-widget visibility (e.g. the progress bar)
can be asserted. QTest.qWait drives the event loop so the bridge's QTimer
fires.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtTest import QTest

from vanilla_wow_launcher.ui.qt.app import create_qt_app
from vanilla_wow_launcher.ui.qt.bridge import ControllerHub
from vanilla_wow_launcher.ui.qt.main_window import MainWindow
from vanilla_wow_launcher.core import launcher
from vanilla_wow_launcher.state.events import OperationFailed, OperationFinished, ProgressChanged, StatusChanged


@pytest.fixture(autouse=True)
def _offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return create_qt_app()


@pytest.fixture()
def window(qapp):
    hub = ControllerHub()
    win = MainWindow(hub)
    win.show()
    yield win
    win.close()


def test_construction_sets_title_and_default_tab(qapp, window):
    assert window.windowTitle() == "Vanilla WoW Launcher"
    assert window._stack.count() == 4
    assert window._pages == {"NEWS": 0, "TWEAKS": 1, "ADDONS": 2, "MODS": 3}
    assert window._stack.currentIndex() == 0
    assert window._navButtons["NEWS"].isChecked()
    assert window._discordButton is None


def test_discord_button_opens_configured_url(qapp, monkeypatch):
    launcher.configure_from_dict({
        "server": {"base_url": "https://launcher.test"},
        "discord_url": "https://discord.gg/example",
    })
    hub = ControllerHub()
    win = MainWindow(hub)
    win.show()
    try:
        opened = []
        monkeypatch.setattr(
            "vanilla_wow_launcher.ui.qt.main_window.webbrowser.open",
            opened.append)
        assert win._discordButton is not None
        assert win._discordButton.text() == "DISCORD"
        win._discordButton.click()
        assert opened == ["https://discord.gg/example"]
    finally:
        win.close()


def test_switch_tab_changes_stack_and_checked_state(qapp, window):
    window.switch_tab("MODS")
    assert window._stack.currentIndex() == window._pages["MODS"]
    assert window._navButtons["MODS"].isChecked()
    assert not window._navButtons["NEWS"].isChecked()


def test_switch_tab_unknown_name_is_noop(qapp, window):
    before = window._stack.currentIndex()
    window.switch_tab("UNKNOWN")
    assert window._stack.currentIndex() == before


def test_status_and_progress_events_reach_footer(qapp, window):
    hub = window._hub
    hub.dispatcher.post(StatusChanged("Ready to update"))
    hub.dispatcher.post(ProgressChanged(0.5, "Downloading…"))
    QTest.qWait(200)

    assert window._statusLabel.text() == "Ready to update"
    assert window._progressBar.value() == 50
    assert window._progressLabel.text() == "Downloading…"
    assert window._progressBar.isVisible()


def test_progress_bar_hides_when_idle(qapp, window):
    hub = window._hub
    hub.dispatcher.post(ProgressChanged(0.5, "Downloading…"))
    QTest.qWait(200)
    assert window._progressBar.isVisible()

    hub.dispatcher.post(ProgressChanged(0.0, ""))
    QTest.qWait(200)
    assert not window._progressBar.isVisible()
    assert window._progressBar.value() == 0

    hub.dispatcher.post(ProgressChanged(1.0, ""))
    QTest.qWait(200)
    assert not window._progressBar.isVisible()
    assert window._progressBar.value() == 100


def test_operation_events_flip_button_state(qapp, window, monkeypatch):
    hub = window._hub
    # The client can only be PLAY-launched where the Windows client runs.
    import vanilla_wow_launcher.controllers.update as update_controller
    monkeypatch.setattr(update_controller, "can_launch_client", lambda: True)
    assert window._updateButton.text() == "UPDATE"

    # A finished update marks the client ready on the controller before the
    # event is posted — the footer mirrors that real state.
    hub.updater.state.client_ready = True
    hub.updater.state.manifest_available = True
    hub.dispatcher.post(StatusChanged("all up to date"))
    hub.dispatcher.post(OperationFinished("update", True, "done"))
    QTest.qWait(200)
    assert window._updateButton.text() == "PLAY"

    # A failed update drops readiness back down.
    hub.updater.state.client_ready = False
    hub.dispatcher.post(OperationFailed("update", "boom"))
    QTest.qWait(200)
    assert window._updateButton.text() == "UPDATE"


def test_close_stops_bridge(qapp, window):
    window.close()
    assert not window._hub.bridge._timer.isActive()

    window._hub.dispatcher.post(StatusChanged("after close"))
    QTest.qWait(200)
    assert window._statusLabel.text() != "after close"
