"""QML ADDONS tab tests (Phase 5b).

Covers the section/row builder (sections order, status matrix, search,
shadow suppression), the model (snapshot chrome, action seams) and the
engine view (loads, search narrows, badge, footer).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest

pytest.importorskip("PySide6")

from types import SimpleNamespace

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from nostalgia_launcher.ui.qml.addons import (
    AddonsModel,
    build_items,
    expected_interface,
    matches,
    row_status,
    row_warning,
)
from nostalgia_launcher.ui.qml.app import qml_dir
from nostalgia_launcher.ui.qml.content import ContentListModel
from nostalgia_launcher.ui.qml.viewmodels import (
    LauncherState,
    NewsFeedModel,
    ThemeBridge,
    UpdateState,
)
from nostalgia_launcher.ui.theme import Palette


def _rec(folder, **kw):
    base = {
        "folder": folder,
        "status": "upToDate",
        "git": None,
        "error": None,
        "toc": {},
        "description": "",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _state(**kw):
    base = {
        "addons": {},
        "available": [],
        "pending": {},
        "busy": False,
        "installing": False,
        "state": "done",
        "sections_open": {},
        "updates_count": 0,
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
    return AddonsModel()


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
    addons = AddonsModel()
    eng.rootContext().setContextProperty("addonsModel", addons)
    eng.load(QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml")))
    yield eng, addons


# ── helpers ───────────────────────────────────────────────────────────


def test_expected_interface():
    assert expected_interface("1.12.1") == "11200"
    assert expected_interface("3.3.5a") == "30300"
    assert expected_interface("bogus") == "11200"


def test_matches_space_insensitive():
    assert matches("SellValue", "", "sell value") is True
    assert matches("Sell Value", "", "sellvalue") is True
    assert matches("Other", "", "sell") is False
    assert matches("A", "", "") is True


def test_row_status_matrix():
    assert (
        row_status(_rec("a", status="downloading"), True, "")[0]
        == "downloading"
    )
    assert (
        row_status(_rec("a", status="unknown", git="u"), True, "")[0]
        == "retry"
    )
    assert row_status(_rec("a", status="unknown"), True, "") == (
        "note",
        "Not tracked",
    )
    assert row_status(_rec("a", error="x"), True, "")[0] == "error"
    assert row_status(_rec("a", status="outOfDate"), True, "") == (
        "update",
        "Update",
    )
    assert row_status(_rec("a"), True, "dep missing")[0] == "warning"
    assert row_status(_rec("a"), True, "") == ("none", "")
    assert row_status(_rec("a", status="odd"), True, "")[0] == "note"


def test_row_warning():
    rec = _rec("a", toc={"Interface": "99999"})
    assert "99999" in row_warning(rec, True, {"a"}, "11200")
    rec = _rec("a", toc={"Dependencies": "LibX, LibY"}, status="upToDate")
    assert "LibX" in row_warning(rec, True, {"a"}, "11200")
    assert row_warning(_rec("pfUI"), True, set(), "11200") == ""
    assert row_warning(_rec("a"), False, set(), "11200") == ""


# ── builder ───────────────────────────────────────────────────────────


def _sample_state():
    installed = {
        "Old": _rec("Old", status="outOfDate"),
        "Good": _rec("Good", status="upToDate"),
    }
    available = [
        _rec("New", git="https://example.test/new.git"),
        _rec("Old", git="https://example.test/old.git"),
    ]
    return _state(addons=installed, available=available, updates_count=1)


def test_build_sections_order():
    items = build_items(_sample_state(), recommended={"New"}, expected="11200")
    kinds = [(i["kind"], i.get("title", i.get("folder"))) for i in items]
    assert kinds[0] == ("section", "NEED UPDATE")
    assert ("section", "INSTALLED") in kinds
    assert ("section", "AVAILABLE") in kinds
    # Same-spelling same-repo AVAILABLE row suppressed (shadow of Old).
    folders = [i.get("folder") for i in items if i["kind"] == "row"]
    assert folders.count("Old") == 1
    assert "New" in folders


def test_build_row_fields():
    state = _state(
        addons={
            "A": _rec(
                "A",
                git="https://example.test/a.git",
                toc={"Title": "|cffff0000Red|r"},
            )
        },
    )
    items = build_items(state, recommended={"A"}, expected="11200")
    row = next(i for i in items if i["kind"] == "row")
    assert row["title"] == "Red"
    assert row["checked"] is True
    assert row["recommended"] is True
    assert row["repoUrl"] == "https://example.test/a"


def test_build_pending_overrides_checked():
    """Staged checkbox changes render instead of snapping back (mods
    build_rows parity): a checked AVAILABLE row stays checked, an
    unchecked INSTALLED row stays unchecked."""
    state = _state(
        addons={"A": _rec("A")},
        available=[_rec("B", git="https://example.test/b.git")],
    )
    items = build_items(
        state, recommended=set(), expected="11200", pending={"A": False}
    )
    by_folder = {i.get("folder"): i for i in items if i["kind"] == "row"}
    assert by_folder["A"]["checked"] is False
    assert by_folder["B"]["checked"] is False
    items = build_items(
        state,
        recommended=set(),
        expected="11200",
        pending={"A": False, "B": True},
    )
    by_folder = {i.get("folder"): i for i in items if i["kind"] == "row"}
    assert by_folder["A"]["checked"] is False
    assert by_folder["B"]["checked"] is True
    # Without pending the on-disk truth renders as before.
    items = build_items(state, recommended=set(), expected="11200")
    by_folder = {i.get("folder"): i for i in items if i["kind"] == "row"}
    assert by_folder["A"]["checked"] is True
    assert by_folder["B"]["checked"] is False


def test_build_search_and_collapse():
    state = _sample_state()
    items = build_items(
        state, recommended=set(), expected="11200", needle="good"
    )
    folders = [i.get("folder") for i in items if i["kind"] == "row"]
    assert folders == ["Good"]
    state.sections_open = {"INSTALLED": False}
    items = build_items(state, recommended=set(), expected="11200")
    titles = [i["title"] for i in items if i["kind"] == "section"]
    assert "INSTALLED" in titles
    assert "Good" not in [i.get("folder") for i in items if i["kind"] == "row"]


def test_build_empty_section_message():
    state = _state(state="verifying")
    items = build_items(state, recommended=set(), expected="11200")
    empties = [i["empty"] for i in items if i["kind"] == "section"]
    assert all(e == "Verifying…" for e in empties)


# ── model ─────────────────────────────────────────────────────────────


def _snapshot(**kw):
    snap = {
        "items": [
            {
                "kind": "section",
                "title": "INSTALLED",
                "count": 1,
                "open": True,
                "empty": "",
            },
            {
                "kind": "row",
                "folder": "A",
                "title": "A",
                "checked": True,
                "recommended": False,
                "statusKind": "none",
                "statusText": "",
                "repoUrl": "",
                "description": "d",
                "error": "",
            },
        ],
        "updates_count": 2,
        "apply_visible": True,
        "recommended_enabled": True,
        "footer_text": "Update all",
        "footer_color": "#00ff00",
        "footer_clickable": True,
        "age_text": "Catalog updated recently",
    }
    snap.update(kw)
    return snap


def test_snapshot_chrome(model):
    model.set_snapshot(_snapshot())
    assert model.rowCount() == 2
    assert model.property("updatesCount") == 2
    assert model.property("applyVisible") is True
    assert model.property("recommendedEnabled") is True
    assert model.property("footerText") == "Update all"
    assert model.property("footerClickable") is True
    assert model.property("ageText") == "Catalog updated recently"
    model.set_running(True)
    assert model.property("recommendedEnabled") is False
    assert model.property("busy") is True


def test_action_seams(model):
    calls = []
    model.set_handlers(
        toggle=lambda f, c: calls.append(("toggle", f, c)),
        section=lambda t: calls.append(("section", t)),
        update_one=lambda f: calls.append(("update_one", f)) or True,
        update_all=lambda: calls.append(("update_all",)) or True,
        apply=lambda: calls.append(("apply",)) or True,
        recommended=lambda: calls.append(("rec",)) or False,
        check=lambda: calls.append(("check",)),
    )
    model.set_snapshot(_snapshot())
    model.toggleEntry("A", False)
    model.toggleSection("INSTALLED")
    model.updateOne("A")
    assert model.property("busy") is True
    model.set_running(False)
    model.updateAll()
    model.applyAll()
    model.installRecommended()
    assert model.property("busy") is True
    model.set_running(False)
    model.checkForUpdates()
    assert ("toggle", "A", False) in calls
    assert ("section", "INSTALLED") in calls
    assert ("check",) in calls


def test_filter_triggers_refresh(model):
    seen = []
    model.set_snapshot_provider(lambda: seen.append(1) or _snapshot())
    model.refresh()
    model.setFilterText("x")
    assert len(seen) == 2
    assert model.current_filter() == "x"


# ── engine ────────────────────────────────────────────────────────────


def test_addons_view_loads(engine):
    eng, _addons = engine
    root = eng.rootObjects()[0]
    for name in (
        "qmlAddonsView",
        "qmlAddonsAge",
        "qmlAddonsCheck",
        "qmlAddonsSearch",
        "qmlAddonsList",
        "qmlAddonsRecommended",
        "qmlAddonsApply",
        "qmlAddonsFooter",
    ):
        assert root.findChild(QObject, name) is not None, name


def test_addons_list_follows_model(engine):
    eng, addons = engine
    root = eng.rootObjects()[0]
    root.findChild(QObject, "qmlNavBar").setProperty("currentIndex", 2)
    addons.set_snapshot(_snapshot())
    lst = root.findChild(QObject, "qmlAddonsList")
    assert lst.property("count") == 2
    footer = root.findChild(QObject, "qmlAddonsFooter")
    assert footer.property("text") == "Update all"
    assert footer.property("enabled") is True


def test_addons_badge(engine):
    eng, addons = engine
    root = eng.rootObjects()[0]
    nav = root.findChild(QObject, "qmlNavBar")
    addons.set_snapshot(_snapshot())
    texts = [
        btn.property("text")
        for btn in nav.findChildren(QObject)
        if btn.property("text")
    ]
    assert "ADDONS (2)" in texts


def _delegate_texts(obj, _depth=0):
    """Collect label texts under a QML item (delegate tree walk)."""
    found = []
    try:
        text = obj.property("text")
    except Exception:
        text = None
    if isinstance(text, str) and text:
        found.append(text)
    if _depth > 12:
        return found
    try:
        if hasattr(obj, "childItems"):
            children = obj.childItems()
        else:
            children = obj.children()
    except Exception:
        children = []
    for child in children:
        found += _delegate_texts(child, _depth + 1)
    return found


def test_toggleEntry_echo_dropped():
    """Delegate creation sets CheckBox.checked programmatically — those
    onCheckedChanged echoes must not reach the toggle handler (each one
    triggered model.refresh() mid-build, destroying the sibling
    delegates so only the last installed row survived)."""
    from nostalgia_launcher.ui.qml.addons import AddonsModel as M

    model = M()
    calls = []
    model.set_handlers(toggle=lambda f, c: calls.append((f, c)))
    model.set_snapshot(
        {
            "items": [
                {
                    "kind": "section",
                    "title": "INSTALLED",
                    "count": 2,
                    "open": True,
                    "empty": "",
                },
                {
                    "kind": "row",
                    "folder": "A",
                    "title": "A",
                    "checked": True,
                    "recommended": False,
                    "statusKind": "none",
                    "statusText": "",
                    "repoUrl": "",
                    "description": "",
                    "error": "",
                },
                {
                    "kind": "row",
                    "folder": "B",
                    "title": "B",
                    "checked": False,
                    "recommended": False,
                    "statusKind": "none",
                    "statusText": "",
                    "repoUrl": "",
                    "description": "",
                    "error": "",
                },
            ]
        }
    )
    # Echoes of the current state are dropped…
    model.toggleEntry("A", True)
    model.toggleEntry("B", False)
    assert calls == []
    # …while real user flips still reach the handler.
    model.toggleEntry("A", False)
    model.toggleEntry("B", True)
    assert calls == [("A", False), ("B", True)]


def test_all_installed_rows_render(engine, qapp):
    """End-to-end: with the real refresh-on-toggle handler wired, all
    installed rows must render (no reentrant reset during delegate
    build). Role-derived objectNames are not findChild-visible (QTBUG),
    so titles are collected via a delegate tree walk."""
    eng, addons = engine
    root = eng.rootObjects()[0]
    root.findChild(QObject, "qmlNavBar").setProperty("currentIndex", 2)
    qapp.processEvents()

    toggles = []

    def on_toggle(folder, checked):
        toggles.append((folder, checked))
        addons.refresh()

    def snapshot():
        items = [
            {
                "kind": "section",
                "title": "INSTALLED",
                "count": 3,
                "open": True,
                "empty": "",
            }
        ]
        for folder in ("Alpha", "Beta", "Gamma"):
            items.append(
                {
                    "kind": "row",
                    "folder": folder,
                    "title": folder,
                    "checked": True,
                    "recommended": False,
                    "statusKind": "none",
                    "statusText": "",
                    "repoUrl": "",
                    "description": "desc " + folder,
                    "error": "",
                }
            )
        return {"items": items}

    addons.set_snapshot_provider(snapshot)
    addons.set_handlers(toggle=on_toggle, section=lambda t: None)
    addons.refresh()
    for _ in range(10):
        qapp.processEvents()

    assert toggles == []
    lst = root.findChild(QObject, "qmlAddonsList")
    assert lst.property("count") == 4
    titles = sorted(set(_delegate_texts(lst)) & {"Alpha", "Beta", "Gamma"})
    assert titles == ["Alpha", "Beta", "Gamma"]


def _pending_snapshot(pending):
    """Snapshot provider shaped like app.py: pending overrides checked."""

    def provider():
        items = [
            {
                "kind": "section",
                "title": "INSTALLED",
                "count": 1,
                "open": True,
                "empty": "",
            },
            {
                "kind": "row",
                "folder": "Alpha",
                "title": "Alpha",
                "checked": True,
                "recommended": False,
                "statusKind": "none",
                "statusText": "",
                "repoUrl": "",
                "description": "d",
                "error": "",
            },
            {
                "kind": "section",
                "title": "AVAILABLE",
                "count": 1,
                "open": True,
                "empty": "",
            },
            {
                "kind": "row",
                "folder": "Beta",
                "title": "Beta",
                "checked": False,
                "recommended": False,
                "statusKind": "note",
                "statusText": "Not versioned",
                "repoUrl": "",
                "description": "d",
                "error": "",
            },
        ]
        for item in items:
            staged = pending.get(item.get("folder", ""))
            if item.get("kind") == "row" and staged is not None:
                item["checked"] = bool(staged)
        return {"items": items, "apply_visible": bool(pending)}

    return provider


def _model_checked(model, folder):
    """The rendered `checked` value for `folder`'s row in the model.

    The delegate's CheckBox binds `checked: model.checked` directly,
    so the model item IS the rendered state. (ListView only
    instantiates visible delegates, so a childItems walk can't reach
    off-screen rows — the model is the reliable assertion point.)
    """
    for item in model._items:
        if item.get("kind") == "row" and item.get("folder") == folder:
            return ("found", bool(item.get("checked")))
    return ("miss", None)


def test_staged_toggle_stays_visible(engine, qapp):
    """Staging a checkbox survives the refresh: the row keeps the staged
    state and Apply appears (previously the tick snapped back)."""
    eng, addons = engine
    root = eng.rootObjects()[0]
    root.findChild(QObject, "qmlNavBar").setProperty("currentIndex", 2)
    qapp.processEvents()

    pending = {}
    toggles = []

    def on_toggle(folder, checked):
        toggles.append((folder, checked))
        installed = folder == "Alpha"  # on-disk truth in this fixture
        if checked == installed:
            pending.pop(folder, None)
        else:
            pending[folder] = checked
        addons.refresh()

    addons.set_snapshot_provider(_pending_snapshot(pending))
    addons.set_handlers(toggle=on_toggle, section=lambda t: None)
    addons.refresh()
    for _ in range(6):
        qapp.processEvents()

    # Stage an install and a removal through the model's slot (user flip).
    addons.toggleEntry("Beta", True)
    addons.toggleEntry("Alpha", False)
    for _ in range(6):
        qapp.processEvents()

    assert toggles == [("Beta", True), ("Alpha", False)]
    assert pending == {"Beta": True, "Alpha": False}
    assert addons.property("applyVisible") is True
    assert _model_checked(addons, "Beta") == ("found", True)
    assert _model_checked(addons, "Alpha") == ("found", False)


def test_staged_toggle_revert_restores(engine, qapp):
    """Flipping a staged row back to its on-disk state clears pending and
    restores the row (no stuck checkbox, no phantom Apply)."""
    eng, addons = engine
    root = eng.rootObjects()[0]
    root.findChild(QObject, "qmlNavBar").setProperty("currentIndex", 2)
    qapp.processEvents()

    pending = {"Beta": True}

    def on_toggle(folder, checked):
        installed = folder == "Alpha"
        if checked == installed:
            pending.pop(folder, None)
        else:
            pending[folder] = checked
        addons.refresh()

    addons.set_snapshot_provider(_pending_snapshot(pending))
    addons.set_handlers(toggle=on_toggle, section=lambda t: None)
    addons.refresh()
    for _ in range(6):
        qapp.processEvents()

    assert _model_checked(addons, "Beta") == ("found", True)
    addons.toggleEntry("Beta", False)
    for _ in range(6):
        qapp.processEvents()

    assert pending == {}
    assert addons.property("applyVisible") is False
    assert _model_checked(addons, "Beta") == ("found", False)


def test_loading_until_data_or_event(qapp):
    model = AddonsModel()
    assert model.property("loading") is True
    model.set_snapshot({"items": []})
    assert model.property("loading") is True
    model.markLoaded()
    assert model.property("loading") is False
    model2 = AddonsModel()
    model2.set_snapshot(
        {
            "items": [
                {
                    "kind": "row",
                    "folder": "A",
                    "title": "A",
                    "checked": False,
                    "recommended": False,
                    "statusKind": "none",
                    "statusText": "",
                    "repoUrl": "",
                    "description": "",
                    "error": "",
                }
            ]
        }
    )
    assert model2.property("loading") is False


def test_waiting_screen_visibility(engine):
    eng, _addons = engine
    root = eng.rootObjects()[0]
    root.findChild(QObject, "qmlNavBar").setProperty("currentIndex", 2)
    waiting = root.findChild(QObject, "qmlAddonsWaiting")
    assert waiting is not None
    assert waiting.property("visible") is True
