"""QML News pilot tests (Phase 3).

Model unit tests run without QML; the engine tests load main.qml offscreen
(QT_QPA_PLATFORM=offscreen + QT_QUICK_BACKEND=software) and assert the
NEWS ListView follows the model. Mirrors the widget panel's render matrix:
loading / error / unconfigured / empty / items + the featured strip.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from nostalgia_launcher.state.events import NewsLoaded
from nostalgia_launcher.state.models import NewsResult
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

ITEMS = [
    {
        "id": "1",
        "title": "First post",
        "date": "2026-01-01",
        "body": "Hello",
        "author": "Admin",
        "url": "https://example.test/1",
    },
    {
        "id": "2",
        "title": "Second post",
        "date": "2026-01-02",
        "body": "",
        "author": "",
        "url": "",
    },
]


@pytest.fixture(scope="module")
def qapp():
    QQuickStyle.setStyle("Material")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def model(qapp):
    return NewsFeedModel()


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
    eng.rootContext().setContextProperty("wizard", WizardModel())
    eng.rootContext().setContextProperty("customModModel", CustomModModel())
    eng.rootContext().setContextProperty(
        "customAddonModel", CustomAddonModel()
    )
    eng.rootContext().setContextProperty(
        "customAssetModel", CustomAssetModel()
    )
    eng.rootContext().setContextProperty("linuxModel", LinuxModel())
    eng.rootContext().setContextProperty("updateState", update)
    eng.load(QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml")))
    yield eng, news


# ── model ─────────────────────────────────────────────────────────────


def test_initial_state_is_loading(model):
    assert model.rowCount() == 0
    assert model.property("statusText") == "Loading…"


def test_roles_expose_item_fields(model):
    model.set_snapshot(ITEMS)
    assert model.rowCount() == 2
    idx = model.index(0)
    assert model.data(idx, NewsFeedModel.TitleRole) == "First post"
    assert model.data(idx, NewsFeedModel.DateRole) == "2026-01-01"
    assert model.data(idx, NewsFeedModel.BodyRole) == "Hello"
    assert model.data(idx, NewsFeedModel.AuthorRole) == "Admin"
    assert model.data(idx, NewsFeedModel.UrlRole) == "https://example.test/1"
    assert model.data(idx, Qt.ItemDataRole.DisplayRole) is None
    assert model.property("statusText") == ""


def test_status_matrix(model):
    model.set_snapshot(None, loading=True)
    assert model.property("statusText") == "Loading…"
    model.set_snapshot(None, error="Couldn't reach the news feed.")
    assert model.property("statusText") == "Couldn't reach the news feed."
    model.set_snapshot(None, configured=False)
    assert model.property("statusText") == "News feed not configured."
    model.set_snapshot([])
    assert model.property("statusText") == "No announcements."
    model.set_snapshot(ITEMS)
    assert model.property("statusText") == ""


def test_on_event_items(model):
    model.on_event(
        NewsLoaded("items", NewsResult(data=ITEMS, configured=True))
    )
    assert model.rowCount() == 2
    assert model.property("statusText") == ""


def test_on_event_featured(model):
    model.on_event(
        NewsLoaded(
            "featured",
            NewsResult(
                data={"title": "Big news", "url": "https://example.test/f"}
            ),
        )
    )
    assert model.rowCount() == 0
    assert model.property("featuredTitle") == "Big news"
    assert model.property("featuredUrl") == "https://example.test/f"


def test_on_event_ignores_junk(model):
    model.on_event(NewsLoaded("items", None))
    model.on_event(NewsLoaded("other", NewsResult(data=ITEMS)))
    model.on_event(object())
    assert model.rowCount() == 0


def test_refresh_calls_handler(model):
    calls = []
    model.set_refresh_handler(lambda: calls.append("x"))
    model.refresh()
    assert calls == ["x"]


def test_refresh_without_handler_is_noop(model, qapp):
    model.refresh()


# ── engine ────────────────────────────────────────────────────────────


def test_material_style_pinned(qapp):
    assert QQuickStyle.name() == "Material"


def test_news_view_loads_with_model(engine):
    eng, _news = engine
    root = eng.rootObjects()[0]
    assert root.findChild(QObject, "qmlNewsView") is not None
    assert root.findChild(QObject, "qmlNewsList") is not None
    assert root.findChild(QObject, "qmlNewsStatus") is not None
    assert root.findChild(QObject, "qmlNewsRefresh") is not None


def test_news_list_follows_model(engine):
    eng, news = engine
    root = eng.rootObjects()[0]
    news_list = root.findChild(QObject, "qmlNewsList")
    status = root.findChild(QObject, "qmlNewsStatus")
    assert news_list.property("count") == 0
    news.set_snapshot(ITEMS)
    assert news_list.property("count") == 2
    assert status.property("text") == ""
    news.set_snapshot(None, error="Couldn't reach the news feed.")
    assert news_list.property("count") == 0
    assert status.property("text") == "Couldn't reach the news feed."
