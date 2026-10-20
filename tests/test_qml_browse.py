"""QML browse-dialog tests: catalog search + one-click install."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest

pytest.importorskip("PySide6")

from nostalgia_launcher.ui.qml.browse import BrowseModel


@pytest.fixture()
def qapp():
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    QQuickStyle.setStyle("Material")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def engine(qapp):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from nostalgia_launcher.ui.qml.addons import AddonsModel
    from nostalgia_launcher.ui.qml.app import qml_dir
    from nostalgia_launcher.ui.qml.content import ContentListModel
    from nostalgia_launcher.ui.qml.custom import (
        CustomAddonModel,
        CustomAssetModel,
        CustomModModel,
    )
    from nostalgia_launcher.ui.qml.linux import LinuxModel
    from nostalgia_launcher.ui.qml.settings import LogModel, SettingsModel
    from nostalgia_launcher.ui.qml.viewmodels import (
        LauncherState,
        NewsFeedModel,
        ThemeBridge,
        UpdateState,
    )
    from nostalgia_launcher.ui.qml.wizard import WizardModel
    from nostalgia_launcher.ui.theme import Palette

    eng = QQmlApplicationEngine()
    eng.rootContext().setContextProperty("launcherState", LauncherState())
    eng.rootContext().setContextProperty("appTheme", ThemeBridge(Palette()))
    eng.rootContext().setContextProperty("newsModel", NewsFeedModel())
    eng.rootContext().setContextProperty("updateState", UpdateState())
    mods = ContentListModel("mods empty")
    assets = ContentListModel("assets empty")
    eng.rootContext().setContextProperty("modsModel", mods)
    eng.rootContext().setContextProperty("assetsModel", assets)
    eng.rootContext().setContextProperty("addonsModel", AddonsModel())
    eng.rootContext().setContextProperty("settingsModel", SettingsModel())
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
    eng.rootContext().setContextProperty("browseModsModel", BrowseModel())
    eng.load(QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml")))
    yield eng, mods, assets


@pytest.fixture()
def model():
    return BrowseModel()


def _entries():
    return [
        {
            "id": "a",
            "name": "Alpha",
            "version": "1.0",
            "description": "shiny",
            "repo_url": "",
        },
        {
            "id": "b",
            "name": "Beta",
            "version": "2.0",
            "description": "beta",
            "repo_url": "",
        },
    ]


def test_search_filters(model):
    model.set_entries(_entries())
    assert model.rowCount() == 2
    model.setFilterText("alp")
    assert model.rowCount() == 1
    model.setFilterText("")
    assert model.rowCount() == 2


def test_install_calls_handler(model):
    calls = []
    model.set_entries(_entries())
    model.set_install_handler(lambda eid: calls.append(eid) or True)
    model.install("b")
    assert calls == ["b"]
    assert model.property("busy") is True
    model.set_running(False)
    assert model.property("busy") is False


def test_content_browse_entries_excludes_installed():
    from types import SimpleNamespace

    from nostalgia_launcher.ui.qml.browse import content_browse_entries

    entries = [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}]
    rec_a = SimpleNamespace(present=True)
    state = SimpleNamespace(records={"a": rec_a}, pending={})
    out = content_browse_entries(entries, state)
    assert [e["id"] for e in out] == ["b"]


def test_content_browse_entries_keeps_failed_records():
    from types import SimpleNamespace

    from nostalgia_launcher.ui.qml.browse import content_browse_entries

    entries = [{"id": "a", "name": "Alpha"}]
    state = SimpleNamespace(
        records={"a": SimpleNamespace(present=False)}, pending={}
    )
    assert [e["id"] for e in content_browse_entries(entries, state)] == ["a"]


def test_addon_browse_entries_passthrough():
    from types import SimpleNamespace

    from nostalgia_launcher.ui.qml.browse import addon_browse_entries

    available = [{"folder": "New"}]
    state = SimpleNamespace(available=available)
    assert addon_browse_entries(state) == available
    assert addon_browse_entries(state) == available


def test_browse_view_loads(qapp):
    """BrowseView compiles and instantiates against a BrowseModel."""
    import os

    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

    from nostalgia_launcher.ui.qml.app import qml_dir
    from nostalgia_launcher.ui.qml.viewmodels import ThemeBridge
    from nostalgia_launcher.ui.theme import Palette

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("appTheme", ThemeBridge(Palette()))
    model = BrowseModel()
    model.set_entries(
        [
            {
                "id": "a",
                "name": "Alpha",
                "version": "1.0",
                "description": "shiny",
                "repo_url": "",
            }
        ]
    )
    comp = QQmlComponent(
        engine, QUrl.fromLocalFile(os.path.join(qml_dir(), "BrowseView.qml"))
    )
    assert comp.isReady(), comp.errorString()
    dlg = comp.create()
    assert dlg is not None
    dlg.setProperty("browseModel", model)
    assert dlg.property("objectName") == "qmlBrowseDialog"
    assert dlg.property("objectName") == "qmlBrowseDialog"


def test_browse_mods_dialog_loads(engine):
    from PySide6.QtCore import QObject

    eng, _mods, _assets = engine
    root = eng.rootObjects()[0]
    assert root.findChild(QObject, "qmlBrowseModsDialog") is not None
    assert root.findChild(QObject, "qmlBrowseModsDialog") is not None


def test_browse_assets_dialog_loads(engine):
    from PySide6.QtCore import QObject

    eng, _mods, _assets = engine
    root = eng.rootObjects()[0]
    assert root.findChild(QObject, "qmlBrowseAssetsDialog") is not None
