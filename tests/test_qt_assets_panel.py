"""Headless Qt tests for the assets panel (qt assets panel).

QT_QPA_PLATFORM=offscreen is set before PySide6 is imported so the module
runs without a display. The AssetsController registry is swapped for a tiny
fake and AssetsLoaded snapshots are posted straight onto the shared
EventDispatcher (bypassing network fetchers); the bridge QTimer delivers
them to the panel via QTest.qWait.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from unittest.mock import Mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QWidget

from nostalgia_launcher.state.events import AssetsLoaded
from nostalgia_launcher.state.models import AssetsState, AssetState
from nostalgia_launcher.ui.qt.app import create_qt_app
from nostalgia_launcher.ui.qt.assets_panel import AssetsPanel
from nostalgia_launcher.ui.qt.bridge import ControllerHub
from nostalgia_launcher.ui.qt.main_window import MainWindow

FAKE_REGISTRY = [
    {
        "id": "Patch3",
        "name": "Patch 3",
        "essential": True,
        "repo_url": "https://example.invalid/patch",
        "description": "Essential content patch.",
        "url": "https://server.test/patch-3.MPQ",
        "dest": "Data/patch-3.MPQ",
        "version": "v1",
        "sha1": None,
        "size": None,
        "probe": False,
    },
    {
        "id": "Optional",
        "name": "Optional HD textures",
        "essential": False,
        "description": "Optional asset.",
        "url": "https://server.test/hd.MPQ",
        "dest": "Data/hd.MPQ",
        "version": None,
        "sha1": None,
        "size": None,
        "probe": False,
    },
]


@pytest.fixture(autouse=True)
def _offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return create_qt_app()


@pytest.fixture()
def hub(qapp, monkeypatch):
    import nostalgia_launcher.services.assets as assets_module

    monkeypatch.setattr(
        assets_module, "assets_registry", lambda *a, **k: FAKE_REGISTRY
    )
    h = ControllerHub()
    yield h
    h.close()


@pytest.fixture()
def window(hub):
    win = MainWindow(hub)
    win.show()
    yield win
    win.close()


def _panel(window) -> AssetsPanel:
    panel = window._stack.widget(window._pages["ASSETS"])
    assert isinstance(panel, AssetsPanel)
    return panel


def _post(hub, state):
    hub.assets.state = state
    hub.dispatcher.post(AssetsLoaded(state))
    QTest.qWait(200)


# ── build ────────────────────────────────────────────────────────────────────


def test_panel_replaces_the_assets_placeholder(qapp, window):
    assert window._pages["ASSETS"] == MainWindow.TABS.index("ASSETS")
    panel = _panel(window)
    assert panel.objectName() == "assetsPanel"
    assert panel.scroll.objectName() == "assetsScroll"
    for asset in FAKE_REGISTRY:
        assert panel.findChild(QWidget, f"assetsRow_{asset['id']}") is not None
    assert panel.findChild(QPushButton, "assetsApply") is not None


def test_rerender_rebuilds_rows(qapp, window, hub):
    panel = _panel(window)
    _post(hub, AssetsState())
    for asset in FAKE_REGISTRY:
        assert panel.findChild(QWidget, f"assetsRow_{asset['id']}") is not None
    assert panel.findChild(QLabel, "assetsName_Patch3").text() == "Patch 3"


# ── rendering state ──────────────────────────────────────────────────────────


def test_installed_asset_is_highlighted_and_essential_shows_star(
    qapp, window, hub
):
    state = AssetsState(
        records={
            "Patch3": AssetState(
                enabled=True, installed_version="v1", present=True
            )
        }
    )
    _post(hub, state)

    panel = _panel(window)
    name = panel.findChild(QLabel, "assetsName_Patch3")
    assert panel._palette.mod_hl.name() in name.styleSheet()
    assert panel.findChild(QLabel, "assetsVer_Patch3").text() == "  v1"
    assert panel.findChild(QLabel, "assetsStar_Patch3").text() == "★"
    assert panel.findChild(QLabel, "assetsStar_Optional").text() == ""


def test_error_asset_shows_red_name_and_error_line(qapp, window, hub):
    window.switch_tab("ASSETS")
    state = AssetsState(
        records={
            "Patch3": AssetState(
                enabled=True, installed_version="v1", error="boom"
            )
        }
    )
    _post(hub, state)

    panel = _panel(window)
    name = panel.findChild(QLabel, "assetsName_Patch3")
    assert panel._palette.err.name() in name.styleSheet()
    error = panel.findChild(QLabel, "assetsError_Patch3")
    assert error.isVisible()
    assert error.text() == "  \u26a0  boom"


def test_empty_registry_shows_placeholder(qapp, window, hub, monkeypatch):
    import nostalgia_launcher.services.assets as assets_module

    monkeypatch.setattr(assets_module, "assets_registry", lambda *a, **k: [])
    _post(hub, AssetsState())
    panel = _panel(window)
    assert panel.findChild(QLabel, "assetsEmptyState") is not None


# ── actions ──────────────────────────────────────────────────────────────────


def test_toggle_and_apply_forward_to_controller(qapp, window, hub):
    panel = _panel(window)
    check = panel.findChild(QCheckBox, "assetsCheck_Patch3")
    assert check is not None
    check.setChecked(True)
    assert "Patch3" in hub.assets.state.pending

    apply_mock = Mock(return_value=True)
    hub.assets.apply = apply_mock
    panel._apply()
    apply_mock.assert_called_once()


def test_action_button_calls_single_asset_apply(qapp, window, hub):
    state = AssetsState(
        records={
            "Patch3": AssetState(
                enabled=True, installed_version="v1", present=True
            )
        }
    )
    hub.assets.action_for = lambda aid: "update" if aid == "Patch3" else None
    _post(hub, state)
    panel = _panel(window)

    button = panel.findChild(QPushButton, "assetsAction_Patch3")
    assert button is not None
    assert button.text() == "Update"

    apply_mock = Mock(return_value=True)
    hub.assets.apply = apply_mock
    button.click()
    apply_mock.assert_called_once_with(only_asset_id="Patch3")


def test_badge_receives_updates_count(qapp, window, hub):
    seen = []
    panel = _panel(window)
    panel._on_badge = seen.append
    _post(hub, AssetsState(updates_count=1))
    assert seen and seen[-1] == 1
