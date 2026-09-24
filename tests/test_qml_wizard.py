"""QML import wizard tests (Phase 6b).

Covers stage flow (input → folder → trust → accept), file validation
errors, the required-folder gate, back navigation, URL fetch plumbing
(threaded, invalid URL rejected synchronously), and the engine dialog
(first-launch window + embedded import wizard + switch prompt).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from nostalgia_launcher.core import launcher
from nostalgia_launcher.ui.qml.addons import AddonsModel
from nostalgia_launcher.ui.qml.app import qml_dir
from nostalgia_launcher.ui.qml.content import ContentListModel
from nostalgia_launcher.ui.qml.settings import LogModel, SettingsModel
from nostalgia_launcher.ui.qml.viewmodels import (
    LauncherState,
    NewsFeedModel,
    ThemeBridge,
    UpdateState,
)
from nostalgia_launcher.ui.qml.wizard import WizardModel
from nostalgia_launcher.ui.qml.custom import (
    CustomAddonModel,
    CustomAssetModel,
    CustomModModel,
)
from nostalgia_launcher.ui.qml.linux import LinuxModel
from nostalgia_launcher.ui.qt.theme import Palette

VALID_CONFIG = {
    "server": {
        "name": "Wizard Server",
        "url": "https://wizard.test",
        "download": {
            "http": {"fallback": "https://wizard.test/client.zip"},
            "content": {"type": "zip"},
        },
    },
}


@pytest.fixture(scope="module")
def qapp():
    QQuickStyle.setStyle("Material")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def model(qapp):
    return WizardModel()


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
    eng.rootContext().setContextProperty("settingsModel", settings)
    eng.rootContext().setContextProperty("logModel", LogModel())
    wizard = WizardModel()
    eng.rootContext().setContextProperty("wizard", wizard)
    eng.load(QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml")))
    yield eng, wizard


@pytest.fixture()
def config_file(tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    path.write_text(json.dumps(VALID_CONFIG))
    return str(path)


# ── input stage ───────────────────────────────────────────────────────


def test_initial_state(model):
    assert model.property("stage") == "input"
    assert model.property("okEnabled") is False
    assert model.property("okText") == "Continue"
    assert model.property("backVisible") is False


def test_source_selection(model):
    model.setSource("url")
    assert model.property("source") == "url"
    model.setUrl("https://example.test/c.json")
    assert model.property("okEnabled") is True
    model.setSource("file")
    assert model.property("okEnabled") is False


def test_empty_submit_shows_error(model):
    model.submit()
    assert model.property("errorText") == "Choose a local configuration file."


def test_invalid_file_shows_error(model, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    model.setPath(str(bad))
    model.submit()
    assert model.property("stage") == "input"
    assert model.property("errorText") != ""


# ── folder stage ──────────────────────────────────────────────────────


def test_file_flow_reaches_folder(model, config_file):
    model.setPath(config_file)
    model.submit()
    assert model.property("stage") == "folder"
    # Suggestion prefilled (widget parity) → Continue enabled.
    assert model.property("folder") != ""
    assert model.property("okEnabled") is True
    model.setFolder("")
    assert model.property("okEnabled") is False


def test_folder_required(model, config_file):
    model.setPath(config_file)
    model.submit()
    model.setFolder("")
    model.submit()
    assert model.property("stage") == "folder"
    assert "game client" in model.property("errorText").lower()


def test_folder_continue_reaches_trust(model, config_file):
    model.setPath(config_file)
    model.submit()
    model.setFolder("/games/wizard")
    assert model.property("okEnabled") is True
    model.submit()
    assert model.property("stage") == "trust"
    assert model.property("okText") == "Trust configuration"
    assert "Wizard Server" in model.property("trustName")
    assert model.property("trustHosts") != []
    assert model.property("trustCaps") != []


def test_back_navigation(model, config_file):
    model.setPath(config_file)
    model.submit()
    model.setFolder("/games/wizard")
    model.submit()
    model.goBack()
    assert model.property("stage") == "folder"
    model.goBack()
    assert model.property("stage") == "input"


def test_trust_accept_selection(model, config_file):
    model.setPath(config_file)
    model.submit()
    model.setFolder("/games/wizard")
    model.submit()
    assert model.submit() is True
    sel = model.takeSelection()
    assert sel["kind"] == "file"
    assert sel["path"] == config_file
    assert sel["install_dir"] == "/games/wizard"
    assert sel["server_name"] == "Wizard Server"


def test_reset_clears_state(model, config_file):
    model.setPath(config_file)
    model.submit()
    model.reset()
    assert model.property("stage") == "input"
    assert model.property("folder") == ""
    assert model.takeSelection() is None


# ── url flow ──────────────────────────────────────────────────────────


def test_bad_url_rejected_synchronously(model):
    model.setSource("url")
    model.setUrl("not a url")
    model.submit()
    assert model.property("stage") == "input"
    assert model.property("errorText") != ""
    assert model.property("fetching") is False


def test_fetch_result_advances(model, monkeypatch, qapp):
    import nostalgia_launcher.services.config_import as ci

    def fake_fetch(url):
        return VALID_CONFIG, json.dumps(VALID_CONFIG), ""

    monkeypatch.setattr(ci, "fetch_config_url", fake_fetch)
    model.setSource("url")
    model.setUrl("https://example.test/c.json")
    model.submit()
    assert model.property("fetching") is True
    # The worker posts back over a queued connection: pump the loop.
    for _ in range(200):
        qapp.processEvents()
        if model.property("stage") == "folder":
            break
        qapp.processEvents()
        import time

        time.sleep(0.01)
    assert model.property("stage") == "folder"
    assert model.property("fetching") is False


# ── engine ────────────────────────────────────────────────────────────


def test_wizard_dialog_loads(engine):
    eng, _wizard = engine
    root = eng.rootObjects()[0]
    assert root.findChild(QObject, "qmlImportWizard") is not None
    assert root.findChild(QObject, "qmlProfilesImport") is not None


def test_first_launch_window_loads(qapp):
    eng = QQmlApplicationEngine()
    eng.rootContext().setContextProperty("wizard", WizardModel())
    eng.rootContext().setContextProperty("customModModel", CustomModModel())
    eng.rootContext().setContextProperty(
        "customAddonModel", CustomAddonModel()
    )
    eng.rootContext().setContextProperty(
        "customAssetModel", CustomAssetModel()
    )
    eng.rootContext().setContextProperty("linuxModel", LinuxModel())
    eng.rootContext().setContextProperty("appTheme", ThemeBridge(Palette()))
    eng.load(QUrl.fromLocalFile(os.path.join(qml_dir(), "WizardWindow.qml")))
    assert eng.rootObjects()
    root = eng.rootObjects()[0]
    assert root.objectName() == "qmlWizardWindow"
    assert root.findChild(QObject, "qmlWizardDialog") is not None
    for name in (
        "qmlWizardTitle",
        "qmlWizardStep",
        "qmlWizardSourceUrl",
        "qmlWizardSourceFile",
        "qmlWizardBack",
        "qmlWizardCancel",
        "qmlWizardOk",
    ):
        assert root.findChild(QObject, name) is not None, name
