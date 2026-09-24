"""QML custom dialogs + Linux settings + realm prompt tests (Phase 6b).

Covers the three form models (entry assembly, extract-map parsing,
validation errors, accept shape), the Linux model (snapshot, renderer
mapping, action seams) and the realm prompt state, plus engine checks
that every dialog loads and its key objects exist.
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
from nostalgia_launcher.ui.qml.custom import (
    CustomAddonModel,
    CustomAssetModel,
    CustomModModel,
)
from nostalgia_launcher.ui.qml.linux import (
    LinuxModel,
    renderer_label,
    renderer_labels,
    renderer_value,
)
from nostalgia_launcher.ui.qml.settings import LogModel, SettingsModel
from nostalgia_launcher.ui.qml.viewmodels import (
    LauncherState,
    NewsFeedModel,
    ThemeBridge,
    UpdateState,
)
from nostalgia_launcher.ui.qml.wizard import WizardModel
from nostalgia_launcher.ui.qt.theme import Palette


@pytest.fixture(scope="module")
def qapp():
    QQuickStyle.setStyle("Material")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


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
    eng.load(QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml")))
    yield eng


# ── custom mod ────────────────────────────────────────────────────────


def test_mod_requires_id(qapp):
    model = CustomModModel()
    assert model.submit() is False
    assert model.property("errorText") == "A mod id is required."


def test_mod_rejects_bad_extract_map(qapp):
    model = CustomModModel()
    model.setField("modId", "m")
    model.setField("kind", "direct_file")
    model.setField("fileUrl", "https://example.test/m.zip")
    model.setField("extractMap", "no-equals-here")
    assert model.submit() is False
    assert "pattern = dest" in model.property("errorText")


def test_mod_accepts_release_entry(qapp):
    entries = []
    model = CustomModModel()
    model.entryReady.connect(entries.append)
    model.setField("modId", "m")
    model.setField("kind", "github_release")
    model.setField("owner", "o")
    model.setField("repo", "r")
    model.setField("pattern", "*.zip")
    assert model.submit() is True
    assert entries[0]["id"] == "m"
    assert entries[0]["source"]["owner"] == "o"


def test_mod_rejects_unusable_entry(qapp):
    model = CustomModModel()
    model.setField("modId", "m")
    model.setField("kind", "direct_file")
    model.setField("fileUrl", "http://insecure.test/m.zip")
    assert model.submit() is False
    assert model.property("errorText") != ""


def test_mod_lists_come_from_catalog(qapp):
    model = CustomModModel()
    assert "github_release" in model.property("sourceKinds")
    assert model.property("modTypes") != []
    assert model.property("installations") != []


# ── custom addon ──────────────────────────────────────────────────────


def test_addon_rejects_bad_host(qapp):
    model = CustomAddonModel()
    model.setUrl("https://evil.test/a")
    assert model.submit() is False
    assert "allowed host" in model.property("errorText")


def test_addon_accepts_github(qapp):
    recs = []
    model = CustomAddonModel()
    model.entryReady.connect(recs.append)
    model.setUrl("https://github.com/org/CoolAddon.git")
    assert model.submit() is True
    assert recs[0]["folder"] == "CoolAddon"
    assert recs[0]["git"] == "https://github.com/org/CoolAddon"


def test_addon_hint_lists_hosts(qapp):
    model = CustomAddonModel()
    assert "github.com" in model.property("hostsHint")


# ── custom asset ──────────────────────────────────────────────────────


def test_asset_requires_id(qapp):
    model = CustomAssetModel()
    assert model.submit() is False
    assert "asset id" in model.property("errorText").lower()


def test_asset_rejects_bad_size(qapp):
    model = CustomAssetModel()
    model.setField("assetId", "a")
    model.setField("size", "lots")
    assert model.submit() is False
    assert "whole number" in model.property("errorText")


def test_asset_accepts_entry(qapp):
    entries = []
    model = CustomAssetModel()
    model.entryReady.connect(entries.append)
    model.setField("assetId", "a")
    model.setField("url", "https://example.test/patch.mpq")
    model.setField("dest", "Data/patch.mpq")
    model.setFlag("essential", True)
    assert model.submit() is True
    assert entries[0]["dest"] == "Data/patch.mpq"
    assert entries[0]["essential"] is True


# ── linux ─────────────────────────────────────────────────────────────


def test_renderer_mapping():
    labels = renderer_labels()
    assert labels != []
    assert renderer_value(labels[0]) != ""
    assert renderer_label("bogus-value") == "Auto (Proton default)"
    assert renderer_value("bogus-label") == "auto"


def test_linux_snapshot_and_actions(qapp):
    calls = []
    model = LinuxModel()
    model.set_snapshot_provider(
        lambda: {
            "umu_hint": "umu-run detected at: /usr/bin/umu-run",
            "proton_options": ["UMU-Proton"],
            "proton": "UMU-Proton",
            "renderer_options": renderer_labels(),
            "renderer": renderer_label("auto"),
            "dxvk": False,
            "gamemode": True,
            "gamemode_avail": True,
            "wayland": False,
            "wayland_avail": False,
            "game_id": "umu-test",
            "umu_path": "",
        }
    )
    model.set_handlers(
        proton=lambda v: calls.append(("proton", v)),
        renderer=lambda v: calls.append(("renderer", v)),
        dxvk=lambda b: calls.append(("dxvk", b)),
        gamemode=lambda b: calls.append(("gamemode", b)),
        wayland=lambda b: calls.append(("wayland", b)),
        game_id=lambda v: calls.append(("game_id", v)),
        umu_path=lambda v: calls.append(("umu_path", v)),
    )
    model.refresh()
    assert model.property("proton") == "UMU-Proton"
    assert model.property("gamemodeAvail") is True
    assert model.property("waylandAvail") is False
    model.applyProton("GE-Proton")
    model.applyRenderer(model.property("rendererOptions")[0])
    model.setDxvk(True)
    model.applyGameId("umu-x")
    model.applyUmuPath("/usr/bin/umu-run")
    assert ("proton", "GE-Proton") in calls
    assert ("dxvk", True) in calls
    assert ("game_id", "umu-x") in calls
    assert ("umu_path", "/usr/bin/umu-run") in calls


# ── realm prompt ──────────────────────────────────────────────────────


def test_realm_prompt_flow(qapp):
    answers = []
    state = UpdateState()
    state.set_realm_handler(answers.append)
    assert state.property("realmPrompt") == ""
    state.prompt_realm("OldRealm", "NewRealm")
    assert "OldRealm" in state.property("realmPrompt")
    state.resolveRealm(True)
    assert state.property("realmPrompt") == ""
    assert answers == [True]


# ── engine ────────────────────────────────────────────────────────────


def test_dialogs_load(engine):
    root = engine.rootObjects()[0]
    for name in (
        "qmlCustomModDialog",
        "qmlCustomAddonDialog",
        "qmlCustomAssetDialog",
        "qmlLinuxSettingsDialog",
    ):
        assert root.findChild(QObject, name) is not None, name


def test_dialog_key_objects(engine):
    root = engine.rootObjects()[0]
    mod = root.findChild(QObject, "qmlCustomModDialog")
    for name in (
        "qmlCustomModId",
        "qmlCustomModType",
        "qmlCustomModKind",
        "qmlCustomModExtractMap",
        "qmlCustomModError",
        "qmlCustomModSubmit",
    ):
        assert mod.findChild(QObject, name) is not None, name
    addon = root.findChild(QObject, "qmlCustomAddonDialog")
    assert addon.findChild(QObject, "qmlCustomAddonUrl") is not None
    assert addon.findChild(QObject, "qmlCustomAddonInstall") is not None
    asset = root.findChild(QObject, "qmlCustomAssetDialog")
    assert asset.findChild(QObject, "qmlCustomAssetId") is not None
    assert asset.findChild(QObject, "qmlCustomAssetSubmit") is not None
    linux = root.findChild(QObject, "qmlLinuxSettingsDialog")
    for name in ("qmlLinuxProton", "qmlLinuxRenderer", "qmlLinuxUmuHint"):
        assert linux.findChild(QObject, name) is not None, name
