"""QML manage-list tests (shared ADDONS/MODS/ASSETS uninstall list).

Covers the row builder (sorting, uninstall staging, server-managed
locking), the model (text filter, chrome, apply/reload seams, echo
guard), the addon tab adapter and the engine views (three tabs load,
badges, search, empty state, footer).
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

from nostalgia_launcher.ui.qml.app import qml_dir
from nostalgia_launcher.ui.qml.content import ContentListModel, build_rows
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
    },
    {
        "id": "a-mod",
        "name": "A Mod",
        "description": "First shiny",
        "repo_url": "https://example.test/a",
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
    _addons_fixture = ContentListModel("addons empty")
    eng.rootContext().setContextProperty("addonsModel", _addons_fixture)
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
    )
    rows = build_rows(ENTRIES, state)
    assert [r["eid"] for r in rows] == ["a-mod", "b-mod"]
    assert rows[0]["installed"] is True
    assert rows[0]["repoUrl"] == "https://example.test/a"
    assert rows[1]["repoUrl"] == ""
    assert set(rows[0]) == {
        "eid",
        "name",
        "description",
        "repoUrl",
        "installed",
        "enabled",
        "serverManaged",
        "staged",
    }


def test_build_rows_pending_overrides_enabled():
    state = SimpleNamespace(
        records={"a-mod": _rec(enabled=False)},
        pending={"a-mod": SimpleNamespace(enabled=False)},
    )
    rows = build_rows([ENTRIES[1]], state)
    # A tick stages removal: pending.enabled False → checked on.
    assert rows[0]["enabled"] is True
    assert rows[0]["staged"] is True


def test_build_rows_unticked_by_default():
    state = SimpleNamespace(
        records={"a-mod": _rec(enabled=True, present=True)},
        pending={},
    )
    rows = build_rows([ENTRIES[1]], state)
    # Installed but untouched: checkbox off, not staged.
    assert rows[0]["enabled"] is False
    assert rows[0]["staged"] is False


def test_build_rows_marks_server_managed():
    state = SimpleNamespace(records={}, pending={})
    rows = build_rows(
        [{"id": "manifest-x", "name": "X", "description": "d"}], state
    )
    assert rows[0]["serverManaged"] is True
    assert "server" in rows[0]["description"]


# ── model ─────────────────────────────────────────────────────────────


def _snapshot(model, **kw):
    rows = [
        {
            "eid": "a",
            "name": "Alpha",
            "description": "shiny alpha",
            "repoUrl": "",
            "installed": True,
            "enabled": True,
            "serverManaged": False,
            "staged": True,
        },
        {
            "eid": "b",
            "name": "Beta",
            "description": "beta things",
            "repoUrl": "",
            "installed": False,
            "enabled": False,
            "serverManaged": False,
            "staged": False,
        },
    ]
    base = {"updates_count": 1, "pending_count": 1}
    base.update(kw)
    model.set_snapshot(
        rows,
        updates_count=base["updates_count"],
        pending_count=base["pending_count"],
    )


def test_roles(model):
    _snapshot(model)
    idx = model.index(0)
    assert model.data(idx, ContentListModel.EIDRole) == "a"
    assert model.data(idx, ContentListModel.NameRole) == "Alpha"
    assert model.data(idx, ContentListModel.EnabledRole) is True
    assert model.data(idx, Qt.ItemDataRole.DisplayRole) is None


def test_filter_text(model):
    _snapshot(model)
    assert model.rowCount() == 2
    model.setFilterText("shiny")
    assert model.rowCount() == 1
    model.setFilterText("BETA")
    assert model.rowCount() == 1
    model.setFilterText("")
    assert model.rowCount() == 2


def test_chrome(model):
    _snapshot(model)
    assert model.property("updatesCount") == 1
    assert model.property("pendingCount") == 1
    model.set_running(True)
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
    # Row "a" renders checked=True (staged for uninstall): echoing the
    # tick reaches no handler…
    model.toggleEntry("a", True)
    assert calls == []
    # …while unticking flips through to the handler.
    model.toggleEntry("a", False)
    assert calls == [("a", False)]
    model.toggleEntry("missing", True)
    assert calls == [("a", False)]


def test_apply_reload(model):
    calls = []
    model.set_handlers(
        apply=lambda: calls.append(("apply",)) or True,
        reload=lambda: calls.append(("reload",)),
    )
    _snapshot(model)
    model.applyAll()
    assert ("apply",) in calls
    assert model.property("busy") is True
    model.set_running(False)
    model.reloadCatalog()
    assert ("reload",) in calls


def test_refresh_uses_provider(model):
    _snapshot(model, updates_count=0)
    model.set_snapshot_provider(lambda: ([], 0, 0))
    model.refresh()
    assert model.rowCount() == 0
    assert model.property("updatesCount") == 0


# ── engine ────────────────────────────────────────────────────────────


def test_content_tabs_load(engine):
    eng, _mods, _assets = engine
    root = eng.rootObjects()[0]
    assert len(root.findChildren(QObject, "qmlContentView")) == 3
    assert root.findChild(QObject, "qmlNavBar") is not None


def test_mods_tab_follows_model(engine):
    eng, mods, _assets = engine
    root = eng.rootObjects()[0]
    root.findChild(QObject, "qmlNavBar").setProperty("currentIndex", 3)
    _snapshot(mods)
    lists = root.findChildren(QObject, "qmlContentList")
    assert any(lst.property("count") == 2 for lst in lists)


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


# ── addon tab adapter ─────────────────────────────────────────────────


def _addon_state(**kw):
    base = {"addons": {}, "pending": {}, "updates_count": 0}
    base.update(kw)
    return SimpleNamespace(**base)


def _addon_rec(folder, **kw):
    base = {
        "folder": folder,
        "git": None,
        "toc": {},
        "description": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_addon_tab_entries_strips_wow_colors():
    from nostalgia_launcher.ui.qml.browse import addon_tab_entries

    state = _addon_state(
        addons={
            "Foo": _addon_rec(
                "Foo",
                git="https://example.test/foo.git",
                toc={"Title": "|cff00ff00Foo|r", "Notes": "|cFFFF0000shiny|r"},
            )
        }
    )
    (entry,) = addon_tab_entries(state)
    assert entry == {
        "id": "Foo",
        "name": "Foo",
        "description": "shiny",
        "repo_url": "https://example.test/foo",
    }


def test_addon_tab_entries_falls_back_to_folder():
    from nostalgia_launcher.ui.qml.browse import addon_tab_entries

    state = _addon_state(addons={"Bar": _addon_rec("Bar")})
    (entry,) = addon_tab_entries(state)
    assert entry["id"] == "Bar"
    assert entry["name"] == "Bar"
    assert entry["description"] == ""


def test_addon_tab_rows_installed_plus_uninstall_staged():
    from nostalgia_launcher.ui.qml.browse import addon_tab_rows

    state = _addon_state(
        addons={"Bar": _addon_rec("Bar")},
        pending={
            "Gone": SimpleNamespace(enabled=False),
            "Kept": SimpleNamespace(enabled=True),
        },
    )
    assert [e["id"] for e in addon_tab_rows(state)] == ["Bar"]


def test_addon_tab_build_rows_tick():
    from nostalgia_launcher.ui.qml.browse import addon_tab_rows

    state = _addon_state(
        addons={"Bar": _addon_rec("Bar")},
        pending={"Bar": SimpleNamespace(enabled=False)},
    )
    (row,) = build_rows(addon_tab_rows(state), state)
    assert row["eid"] == "Bar"
    assert row["enabled"] is True
    assert row["staged"] is True


def test_loading_until_data_or_event(model):
    assert model.property("loading") is True
    model.set_snapshot([])
    assert model.property("loading") is True
    model.markLoaded()
    assert model.property("loading") is False
    model2 = ContentListModel("x")
    model2.set_snapshot(
        [
            {
                "eid": "a",
                "name": "A",
                "description": "",
                "repoUrl": "",
                "installed": False,
                "enabled": False,
                "serverManaged": False,
                "staged": False,
            }
        ]
    )
    assert model2.property("loading") is False


def test_waiting_screen_visibility(engine, qapp):
    eng, mods, _assets = engine
    root = eng.rootObjects()[0]
    root.findChild(QObject, "qmlNavBar").setProperty("currentIndex", 3)
    qapp.processEvents()
    waitings = root.findChildren(QObject, "qmlContentWaiting")
    assert waitings
    assert any(w.property("visible") is True for w in waitings)


def test_tab_rows_excludes_not_installed():
    from nostalgia_launcher.ui.qml.browse import tab_rows

    entries = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    state = SimpleNamespace(
        records={"a": SimpleNamespace(present=True)}, pending={}
    )
    assert [r["id"] for r in tab_rows(entries, state)] == ["a"]


def test_tab_rows_uninstall_staged_only():
    """The manage tab is installed-only: present records plus rows
    staged for uninstall — never error-only or install-staged rows."""
    from nostalgia_launcher.ui.qml.browse import tab_rows

    entries = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    state = SimpleNamespace(
        records={
            "a": SimpleNamespace(present=False),
            "b": SimpleNamespace(present=True),
        },
        pending={
            "a": SimpleNamespace(enabled=False),
            "b": SimpleNamespace(enabled=True),
        },
    )
    # b is present; a stays only because it is staged for uninstall.
    # The enabled=True staging entry is not an uninstall stage.
    assert [r["id"] for r in tab_rows(entries, state)] == ["a", "b"]
    state2 = SimpleNamespace(
        records={"a": SimpleNamespace(present=False)}, pending={}
    )
    assert tab_rows(entries, state2) == []
