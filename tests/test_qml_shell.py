"""Headless QML shell tests (Phase 0 spikes + Phase 1 wiring).

QT_QPA_PLATFORM=offscreen + QT_QUICK_BACKEND=software are set before
PySide6 is imported so the QQmlApplicationEngine loads without a display.
Covers: engine loads main.qml error-free, the chrome objects exist, the
view-models expose sane defaults, and the CLI backend selector routes
``qml`` (unknown names still return None).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from nostalgia_launcher import cli
from nostalgia_launcher.state.events import ProgressChanged, StatusChanged
from nostalgia_launcher.ui.qml.addons import AddonsModel
from nostalgia_launcher.ui.qml.app import QmlNostalgiaLauncherApp, qml_dir
from nostalgia_launcher.ui.qml.content import ContentListModel
from nostalgia_launcher.ui.qml.settings import LogModel, SettingsModel
from nostalgia_launcher.ui.qml.viewmodels import (
    LauncherState,
    NewsFeedModel,
    ThemeBridge,
    UpdateState,
)
from nostalgia_launcher.ui.qt.theme import HEX, Palette


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def engine(qapp):
    state = LauncherState()
    theme = ThemeBridge(Palette())
    news = NewsFeedModel()
    update = UpdateState()
    eng = QQmlApplicationEngine()
    eng.rootContext().setContextProperty("launcherState", state)
    eng.rootContext().setContextProperty("appTheme", theme)
    eng.rootContext().setContextProperty("newsModel", news)
    eng.rootContext().setContextProperty(
        "modsModel", ContentListModel("mods empty")
    )
    eng.rootContext().setContextProperty(
        "assetsModel", ContentListModel("assets empty")
    )
    eng.rootContext().setContextProperty("addonsModel", AddonsModel())
    eng.rootContext().setContextProperty("settingsModel", SettingsModel())
    eng.rootContext().setContextProperty("logModel", LogModel())
    eng.rootContext().setContextProperty("updateState", update)
    eng.load(QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml")))
    yield eng, state, theme


def test_main_qml_loads_without_errors(engine):
    eng, _state, _theme = engine
    assert eng.rootObjects(), "main.qml failed to load (see warnings)"


def test_shell_chrome_objects_exist(engine):
    eng, _state, _theme = engine
    root = eng.rootObjects()[0]
    assert root.objectName() == "qmlMainWindow"
    assert root.property("color").name() == HEX["C_BG"]
    for name in (
        "qmlHeader",
        "qmlWordmark",
        "qmlNavBar",
        "qmlFooter",
        "qmlStatusLabel",
        "qmlProgressBar",
        "qmlPrimaryButton",
    ):
        assert root.findChild(QObject, name) is not None, name


def test_status_binding_follows_view_model(engine):
    eng, state, _theme = engine
    root = eng.rootObjects()[0]
    label = root.findChild(QObject, "qmlStatusLabel")
    state.setStatusText("Fetching torrent…")
    assert label.property("text") == "Fetching torrent…"


def test_view_model_ingests_dispatcher_events(qapp):
    state = LauncherState()
    state.on_event(StatusChanged("Verifying…"))
    state.on_event(ProgressChanged(0.5, "halfway"))
    assert state.property("statusText") == "Verifying…"
    assert state.property("progressValue") == 0.5
    assert state.property("progressLabel") == "halfway"


def test_theme_bridge_exposes_palette_slots(qapp):
    theme = ThemeBridge(Palette())
    colors = theme.property("colors")
    assert colors["C_GOLD"] == HEX["C_GOLD"]
    assert theme.color("C_BG") == HEX["C_BG"]
    assert theme.color("NOPE") == ""
    assert theme.property("themed") is True


def test_backend_selector_routes_qml():
    assert cli.resolve_backend("qml") is QmlNostalgiaLauncherApp
    assert cli.resolve_backend("bogus") is None
    assert "PySide6" in cli.backend_error_message("qml", ImportError("x"))
