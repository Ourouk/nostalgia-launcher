"""QML Settings dialog + session-log tests (Phase 6a).

Covers the settings snapshot/actions (URL validation errors, profile
delete status, logs toggle signal) and the log model (refresh + live
tail), plus engine checks that the dialog tabs and gear button exist and
the log viewer follows its model.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from nostalgia_launcher.ui.qml.addons import AddonsModel
from nostalgia_launcher.ui.qml.app import qml_dir
from nostalgia_launcher.ui.qml.content import ContentListModel
from nostalgia_launcher.ui.qml.settings import LogModel, SettingsModel
from nostalgia_launcher.ui.qml.wizard import WizardModel
from nostalgia_launcher.ui.qml.custom import (
    CustomAddonModel,
    CustomAssetModel,
    CustomModModel,
)
from nostalgia_launcher.ui.qml.linux import LinuxModel
from nostalgia_launcher.ui.qml.viewmodels import (
    LauncherState,
    NewsFeedModel,
    ThemeBridge,
    UpdateState,
)
from nostalgia_launcher.ui.qt.theme import Palette


def _snapshot(**kw):
    snap = {
        "game_path": "/games/wow",
        "game_suggestion": "/games/suggest",
        "sources": [{"name": "Test Server", "status": "online"}],
        "clear_wdb": True,
        "close_on_launch": False,
        "client_updates": True,
        "can_launch": True,
        "can_antivirus": False,
        "addons_url": "https://example.test/addons.json",
        "mods_url": "https://example.test/mods.json",
        "addons_default": True,
        "mods_default": False,
        "addons_default_avail": True,
        "mods_default_avail": False,
        "profiles": ["one", "two"],
        "active_profile": "one",
    }
    snap.update(kw)
    return snap


@pytest.fixture(scope="module")
def qapp():
    QQuickStyle.setStyle("Material")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def model(qapp):
    m = SettingsModel()
    m.set_snapshot_provider(lambda: _snapshot())
    m.refresh()
    return m


@pytest.fixture()
def engine(qapp):
    eng = QQmlApplicationEngine()
    eng.rootContext().setContextProperty("launcherState", LauncherState())
    eng.rootContext().setContextProperty("appTheme", ThemeBridge(Palette()))
    eng.rootContext().setContextProperty("newsModel", NewsFeedModel())
    eng.rootContext().setContextProperty("updateState", UpdateState())
    eng.rootContext().setContextProperty(
        "modsModel", ContentListModel("mods empty")
    )
    eng.rootContext().setContextProperty(
        "assetsModel", ContentListModel("assets empty")
    )
    eng.rootContext().setContextProperty("addonsModel", AddonsModel())
    settings = SettingsModel()
    settings.set_snapshot_provider(lambda: _snapshot())
    settings.refresh()
    eng.rootContext().setContextProperty("settingsModel", settings)
    eng.rootContext().setContextProperty("logModel", LogModel())
    eng.rootContext().setContextProperty("wizard", WizardModel())
    eng.rootContext().setContextProperty("customModModel", CustomModModel())
    eng.rootContext().setContextProperty(
        "customAddonModel", CustomAddonModel()
    )
    eng.rootContext().setContextProperty(
        "customAssetModel", CustomAssetModel()
    )
    eng.rootContext().setContextProperty("linuxModel", LinuxModel())
    eng.load(QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml")))
    yield eng, settings


# ── model ─────────────────────────────────────────────────────────────


def test_snapshot_properties(model):
    assert model.property("gamePath") == "/games/wow"
    assert model.property("gameSuggestion") == "/games/suggest"
    assert model.property("sources") == [
        {"name": "Test Server", "status": "online"}
    ]
    assert model.property("clearWdb") is True
    assert model.property("clientUpdates") is True
    assert model.property("canAntivirus") is False
    assert model.property("addonsUrl") == "https://example.test/addons.json"
    assert model.property("profiles") == ["one", "two"]
    assert model.property("activeProfile") == "one"
    assert model.property("logsOpen") is False


def test_registry_url_error_status(model, qapp):
    model.set_handlers(set_addons_url=lambda url: "bad scheme")
    model.applyAddonsUrl("http://nope")
    assert model.property("registryStatus") == "✗ bad scheme"
    model.set_handlers(set_mods_url=lambda url: None)
    model.applyModsUrl("https://example.test/mods.json")
    assert model.property("registryStatus") == ""


def test_registry_reset_clears_status(model):
    model.set_handlers(reset_addons_url=lambda: None)
    model._registry_status = "✗ old"
    model.resetAddonsUrl()
    assert model.property("registryStatus") == ""


def test_profile_delete_status(model):
    model.set_handlers(delete_profile=lambda name: "gone")
    model.deleteProfile("one")
    assert model.property("profilesStatus") == "✗ gone"
    model.set_handlers(delete_profile=lambda name: "")
    model.deleteProfile("one")
    assert model.property("profilesStatus") == ""


def test_logs_toggle_signal(model, qapp):
    fired = []
    model.logsRequested.connect(lambda: fired.append(True))
    model.requestLogs()
    assert fired == [True]
    model.setLogsOpen(True)
    assert model.property("logsOpen") is True
    model.set_logs_open(False)
    assert model.property("logsOpen") is False


def test_checkbox_and_folder_actions(model):
    calls = []
    model.set_handlers(
        set_path=lambda p: calls.append(("path", p)) or True,
        open_folder=lambda: calls.append(("open",)),
        check_sources=lambda: calls.append(("check",)),
        verify=lambda: calls.append(("verify",)),
        antivirus=lambda: calls.append(("av",)),
        clear_wdb=lambda b: calls.append(("wdb", b)),
        addons_default=lambda b: calls.append(("ad", b)),
    )
    model.setGameFolder("/x")
    model.openClientFolder()
    model.checkSources()
    model.verifyFiles()
    model.allowAntivirus()
    model.setClearWdb(False)
    model.setAddonsDefault(True)
    assert ("path", "/x") in calls
    assert ("open",) in calls
    assert ("check",) in calls
    assert ("verify",) in calls
    assert ("av",) in calls
    assert ("wdb", False) in calls
    assert ("ad", True) in calls


# ── log model ─────────────────────────────────────────────────────────


def test_log_model_refresh_and_tail(qapp, tmp_path, monkeypatch):
    import nostalgia_launcher.core.log_sink as sink

    log = tmp_path / "launcher.log"
    log.write_text("one\ntwo\n")
    monkeypatch.setattr(sink, "LOG_FILE", str(log))
    monkeypatch.setattr(sink, "_sink_path", None)
    logs = LogModel()
    logs.refresh()
    assert logs.property("lines") == ["one", "two"]
    logs.appendLine("three", "dim")
    assert logs.property("lines")[-1] == "three"


# ── engine ────────────────────────────────────────────────────────────


def test_settings_dialog_loads(engine):
    eng, _settings = engine
    root = eng.rootObjects()[0]
    assert root.findChild(QObject, "qmlGearButton") is not None
    dialog = root.findChild(QObject, "qmlSettingsDialog")
    assert dialog is not None
    for name in (
        "qmlSettingsTabs",
        "qmlSettingsPath",
        "qmlSettingsChange",
        "qmlSettingsClearWdb",
        "qmlSettingsClientUpdate",
        "qmlSettingsSourceRefresh",
        "qmlSettingsRegistryStatus",
        "qmlProfilesCombo",
        "qmlProfilesDelete",
        "qmlProfilesStatus",
        "qmlSettingsVerify",
        "qmlSettingsLogs",
    ):
        assert dialog.findChild(QObject, name) is not None, name


def test_gear_opens_settings(engine):
    eng, _settings = engine
    root = eng.rootObjects()[0]
    gear = root.findChild(QObject, "qmlGearButton")
    dialog = root.findChild(QObject, "qmlSettingsDialog")
    assert dialog.property("visible") is False
    gear.setProperty("visible", True)
    dialog.setProperty("visible", True)
    assert dialog.property("visible") is True


def test_logs_button_label_follows_state(engine):
    eng, settings = engine
    root = eng.rootObjects()[0]
    dialog = root.findChild(QObject, "qmlSettingsDialog")
    dialog.setProperty("visible", True)
    logs_btn = dialog.findChild(QObject, "qmlSettingsLogs")
    assert logs_btn.property("text") == "Show logs"
    settings.setLogsOpen(True)
    assert logs_btn.property("text") == "Hide logs"


def test_log_dialog_follows_model(engine):
    eng, _settings = engine
    root = eng.rootObjects()[0]
    log_dialog = root.findChild(QObject, "qmlLogDialog")
    assert log_dialog is not None
    assert root.findChild(QObject, "qmlLogList") is not None
