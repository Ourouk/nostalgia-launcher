"""QML content-list tests (Phase 5: shared MODS/ASSETS core).

Covers the row builder (sorting, enabled precedence, required/version
mapping), the model (filter matrix, chrome, action seams, echo guard) and
the engine views (both tabs load, badges, search, empty state, footer).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest

pytest.importorskip("PySide6")

from types import SimpleNamespace

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from nostalgia_launcher.ui.qml.addons import AddonsModel
from nostalgia_launcher.ui.qml.app import qml_dir
from nostalgia_launcher.ui.qml.content import (
    ContentListModel,
    build_rows,
    essential_pending,
)
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

ENTRIES = [
    {
        "id": "b-mod",
        "name": "B Mod",
        "description": "Second",
        "repo_url": "",
        "installation": "user_opt_in",
    },
    {
        "id": "a-mod",
        "name": "A Mod",
        "description": "First shiny",
        "repo_url": "https://example.test/a",
        "installation": "required",
    },
]


def _rec(**kw):
    base = {
        "present": False,
        "enabled": False,
        "installed_version": None,
        "error": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(scope="module")
def qapp():
    QQuickStyle.setStyle("Material")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def model(qapp):
    return ContentListModel("nothing here")


@pytest.fixture()
def engine(qapp):
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
    eng.load(QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml")))
    yield eng, mods, assets


# ── builder ───────────────────────────────────────────────────────────


def test_build_rows_sorts_and_maps():
    state = SimpleNamespace(
        records={
            "a-mod": _rec(present=True, enabled=True, installed_version="1.0")
        },
        pending={},
        latest_versions={"b-mod": "2.0"},
    )
    rows = build_rows(
        ENTRIES,
        state,
        required_of=lambda e: e.get("installation") == "required",
        version_of=lambda e, r, s: (
            (r.installed_version if r else None)
            or s.latest_versions.get(e["id"])
            or "unknown"
        ),
        action_for=lambda eid: "update" if eid == "b-mod" else None,
    )
    assert [r["eid"] for r in rows] == ["a-mod", "b-mod"]
    assert rows[0]["required"] is True
    assert rows[0]["version"] == "1.0"
    assert rows[0]["installed"] is True
    assert rows[1]["version"] == "2.0"
    assert rows[1]["action"] == "update"
    assert rows[1]["repoUrl"] == ""
    assert rows[0]["repoUrl"] == "https://example.test/a"


def test_build_rows_pending_overrides_enabled():
    state = SimpleNamespace(
        records={"a-mod": _rec(enabled=False)},
        pending={"a-mod": SimpleNamespace(enabled=True)},
        latest_versions={},
    )
    rows = build_rows(
        [ENTRIES[1]],
        state,
        required_of=lambda e: False,
        version_of=lambda e, r, s: "unknown",
        action_for=lambda eid: None,
    )
    assert rows[0]["enabled"] is True


def test_essential_pending():
    state = SimpleNamespace(records={}, pending={}, latest_versions={})

    def req(entry):
        return entry.get("installation") == "required"

    assert essential_pending(ENTRIES, state, required_of=req) is True
    state.records["a-mod"] = _rec(present=True)
    assert essential_pending(ENTRIES, state, required_of=req) is False
    assert essential_pending([], state, required_of=req) is False


# ── model ─────────────────────────────────────────────────────────────


def _snapshot(model, **kw):
    rows = [
        {
            "eid": "a",
            "name": "Alpha",
            "version": "1.0",
            "description": "shiny alpha",
            "repoUrl": "",
            "installed": True,
            "enabled": True,
            "required": True,
            "action": "",
            "error": "",
        },
        {
            "eid": "b",
            "name": "Beta",
            "version": "2.0",
            "description": "beta things",
            "repoUrl": "",
            "installed": False,
            "enabled": False,
            "required": False,
            "action": "update",
            "error": "",
        },
        {
            "eid": "c",
            "name": "Gamma",
            "version": "?",
            "description": "broken",
            "repoUrl": "",
            "installed": False,
            "enabled": False,
            "required": False,
            "action": "retry",
            "error": "boom",
        },
    ]
    base = {
        "updates_count": 1,
        "has_pending": False,
        "has_errors": True,
        "essential_pending": True,
    }
    base.update(kw)
    model.set_snapshot(
        rows,
        updates_count=base["updates_count"],
        has_pending=base["has_pending"],
        has_errors=base["has_errors"],
        essential_pending=base["essential_pending"],
    )


def test_roles(model):
    _snapshot(model)
    idx = model.index(0)
    assert model.data(idx, ContentListModel.EIDRole) == "a"
    assert model.data(idx, ContentListModel.NameRole) == "Alpha"
    assert model.data(idx, ContentListModel.EnabledRole) is True
    assert model.data(idx, Qt.ItemDataRole.DisplayRole) is None


def test_filter_modes(model):
    _snapshot(model)
    assert model.rowCount() == 3
    model.setFilterMode("installed")
    assert model.rowCount() == 1
    model.setFilterMode("updates")
    assert model.rowCount() == 1
    model.setFilterMode("all")
    assert model.rowCount() == 3
    model.setFilterMode("bogus")
    assert model.property("filterMode") == "all"


def test_filter_text(model):
    _snapshot(model)
    model.setFilterText("shiny")
    assert model.rowCount() == 1
    model.setFilterText("BETA")
    assert model.rowCount() == 1
    model.setFilterText("")
    assert model.rowCount() == 3


def test_chrome(model):
    _snapshot(model)
    assert model.property("updatesCount") == 1
    assert model.property("applyVisible") is True
    assert model.property("essentialEnabled") is True
    model.set_running(True)
    assert model.property("essentialEnabled") is False
    assert model.property("busy") is True
    model.set_running(False)


def test_empty_state(model):
    assert model.property("emptyVisible") is True
    assert model.property("emptyText") == "nothing here"
    _snapshot(model)
    assert model.property("emptyVisible") is False


def test_toggle_echo_guard(model):
    calls = []
    model.set_handlers(toggle=lambda eid, c: calls.append((eid, c)))
    _snapshot(model)
    model.toggleEntry("a", True)
    assert calls == []
    model.toggleEntry("a", False)
    assert calls == [("a", False)]
    model.toggleEntry("missing", True)
    assert calls == [("a", False)]


def test_action_apply_essential_reload(model):
    calls = []
    model.set_handlers(
        action=lambda eid: calls.append(("act", eid)) or True,
        apply=lambda: calls.append(("apply",)) or True,
        essential=lambda: calls.append(("essential",)) or False,
        reload=lambda: calls.append(("reload",)),
    )
    _snapshot(model)
    model.actOn("b")
    assert ("act", "b") in calls
    assert model.property("busy") is True
    model.set_running(False)
    model.installEssential()
    assert ("essential",) in calls
    assert model.property("busy") is False
    model.applyAll()
    assert ("apply",) in calls
    assert model.property("busy") is True
    model.set_running(False)
    model.reloadCatalog()
    assert ("reload",) in calls


def test_refresh_uses_provider(model):
    _snapshot(model, updates_count=0)
    model.set_snapshot_provider(
        lambda: ([], 0, False, False, False, [], "", [])
    )
    model.refresh()
    assert model.rowCount() == 0
    assert model.property("updatesCount") == 0


# ── engine ────────────────────────────────────────────────────────────


def test_content_tabs_load(engine):
    eng, _mods, _assets = engine
    root = eng.rootObjects()[0]
    assert len(root.findChildren(QObject, "qmlContentView")) == 2
    assert root.findChild(QObject, "qmlNavBar") is not None


def test_mods_tab_follows_model(engine):
    eng, mods, _assets = engine
    root = eng.rootObjects()[0]
    root.findChild(QObject, "qmlNavBar").setProperty("currentIndex", 3)
    _snapshot(mods)
    lists = root.findChildren(QObject, "qmlContentList")
    assert any(lst.property("count") == 3 for lst in lists)


def test_search_filters_engine_list(engine):
    eng, mods, _assets = engine
    root = eng.rootObjects()[0]
    root.findChild(QObject, "qmlNavBar").setProperty("currentIndex", 3)
    _snapshot(mods)
    mods.setFilterText("shiny")
    lists = root.findChildren(QObject, "qmlContentList")
    assert any(lst.property("count") == 1 for lst in lists)
    mods.setFilterText("")


def test_nav_badge_follows_updates(engine):
    eng, mods, _assets = engine
    root = eng.rootObjects()[0]
    nav = root.findChild(QObject, "qmlNavBar")
    _snapshot(mods)
    assert mods.property("updatesCount") == 1
    texts = [
        btn.property("text")
        for btn in nav.findChildren(QObject)
        if btn.property("text")
    ]
    assert "MODS (1)" in texts


# ── extras ────────────────────────────────────────────────────────────


def test_unknown_sections():
    from nostalgia_launcher.ui.qml.content import unknown_sections

    assert unknown_sections([]) == []
    assert unknown_sections(None) == []
    sections = unknown_sections(["stray.dll"])
    assert sections[0]["title"] == "Detected (not in catalog)"
    assert sections[0]["rows"][0] == {
        "name": "stray.dll",
        "meta": "",
        "action": "Remove",
        "confirm": "",
    }


def test_managed_names():
    from nostalgia_launcher.ui.qml.content import managed_names

    registry = [
        {"id": "a", "name": "Patch A", "dest": "Data\\patch-a.mpq"},
        {"id": "b", "name": "No dest"},
    ]
    assert managed_names(registry) == {
        "patch-a.mpq": "Patch A (launcher asset)"
    }


def test_scan_extras_no_client():
    from nostalgia_launcher.ui.qml.content import scan_extras

    headline, sections = scan_extras({}, {}, "")
    assert "game folder" in headline
    assert sections == []


def test_scan_extras_shapes_sections():
    from nostalgia_launcher.ui.qml.content import scan_extras

    scan = {
        "version": "1.12.1",
        "stock": [{}, {}],
        "custom_managed": [{"path": "Data/patch-a.mpq", "size": 8}],
        "custom_foreign": [{"path": "Data/stray.mpq", "size": 4}],
    }
    headline, sections = scan_extras(
        scan, {"patch-a.mpq": "Patch A (launcher asset)"}, "/games/wow"
    )
    assert "2 stock" in headline
    assert sections[0]["title"] == "Foreign / untracked"
    assert sections[0]["color"] == "err"
    assert "Delete Data/stray.mpq" in sections[0]["rows"][0]["confirm"]
    assert sections[1]["title"] == "Launcher-managed custom"
    assert "Patch A" in sections[1]["rows"][0]["meta"]


def test_extras_snapshot_and_confirm(model):
    extras = [
        {
            "title": "Foreign / untracked",
            "color": "err",
            "rows": [
                {
                    "name": "Data/x.mpq",
                    "meta": "",
                    "action": "Remove",
                    "confirm": "Delete it?",
                }
            ],
        }
    ]
    model.set_snapshot([], headline="scan!", extras=extras)
    assert model.property("extraHeadline") == "scan!"
    assert model.property("extraSections") == extras
    calls = []
    model.set_handlers(extra=lambda s, n: calls.append((s, n)))
    model.requestExtra("Foreign / untracked", "Data/x.mpq")
    assert model.property("extraConfirmText") == "Delete it?"
    assert calls == []
    model.confirmExtra()
    assert calls == [("Foreign / untracked", "Data/x.mpq")]
    assert model.property("extraConfirmText") == ""


def test_extra_direct_action_and_cancel(model):
    model.set_snapshot(
        [],
        extras=[
            {
                "title": "Detected (not in catalog)",
                "color": "dim",
                "rows": [
                    {
                        "name": "s.dll",
                        "meta": "",
                        "action": "Remove",
                        "confirm": "",
                    }
                ],
            }
        ],
    )
    calls = []
    model.set_handlers(extra=lambda s, n: calls.append((s, n)))
    model.requestExtra("Detected (not in catalog)", "s.dll")
    assert calls == [("Detected (not in catalog)", "s.dll")]
    model.requestExtra("Nope", "s.dll")
    assert len(calls) == 1
    model.cancelExtra()
    assert model.property("extraConfirmText") == ""


def test_scan_version_slot(model):
    model.set_snapshot([], scan_versions=["1.12.1", "2.4.3"])
    assert model.property("scanVersions") == ["1.12.1", "2.4.3"]
    model.set_snapshot_provider(
        lambda: ([], 0, False, False, False, [], "", [])
    )
    model.setScanVersion("2.4.3")
    assert model.current_scan_version() == "2.4.3"


def test_extras_render_in_engine(engine):
    eng, mods, _assets = engine
    root = eng.rootObjects()[0]
    root.findChild(QObject, "qmlNavBar").setProperty("currentIndex", 3)
    _snapshot(mods)
    mods.set_snapshot(
        [],
        extras=[
            {
                "title": "Detected (not in catalog)",
                "color": "dim",
                "rows": [
                    {
                        "name": "s.dll",
                        "meta": "",
                        "action": "Remove",
                        "confirm": "",
                    }
                ],
            }
        ],
    )
    assert root.findChild(QObject, "qmlExtrasBottom") is not None
