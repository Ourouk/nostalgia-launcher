"""QML Update tab tests (Phase 4).

Model unit tests mirror ui/qt/update_panel.py behavior: phase/progress
intake, the 0<v<100 visibility rule, amount/speed/peers formatting, file
tracking, the verify/update finish matrix, and the files model. Engine
tests load main.qml offscreen and assert UpdateView + footer follow the
view-model.
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

from nostalgia_launcher.state.events import ProgressChanged, UpdateFilesList
from nostalgia_launcher.ui.qml.addons import AddonsModel
from nostalgia_launcher.ui.qml.app import qml_dir
from nostalgia_launcher.ui.qml.content import ContentListModel
from nostalgia_launcher.ui.qml.settings import LogModel, SettingsModel
from nostalgia_launcher.ui.qml.viewmodels import (
    LauncherState,
    NewsFeedModel,
    ThemeBridge,
    UpdateFilesModel,
    UpdateState,
)
from nostalgia_launcher.ui.qt.theme import Palette


def _progress(**kw):
    base = {
        "value": 0.0,
        "label": "",
        "phase": "",
        "transport": "",
        "current_file": "",
        "downloaded": 0,
        "total": 0,
        "speed": 0.0,
        "peers": 0,
    }
    base.update(kw)
    return ProgressChanged(**base)


@pytest.fixture(scope="module")
def qapp():
    QQuickStyle.setStyle("Material")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def state(qapp):
    return UpdateState()


@pytest.fixture()
def engine(qapp):
    eng = QQmlApplicationEngine()
    eng.rootContext().setContextProperty("launcherState", LauncherState())
    eng.rootContext().setContextProperty("appTheme", ThemeBridge(Palette()))
    eng.rootContext().setContextProperty("newsModel", NewsFeedModel())
    eng.rootContext().setContextProperty(
        "modsModel", ContentListModel("mods empty")
    )
    eng.rootContext().setContextProperty(
        "assetsModel", ContentListModel("assets empty")
    )
    eng.rootContext().setContextProperty("addonsModel", AddonsModel())
    eng.rootContext().setContextProperty("settingsModel", SettingsModel())
    eng.rootContext().setContextProperty("logModel", LogModel())
    update = UpdateState()
    eng.rootContext().setContextProperty("updateState", update)
    eng.load(QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml")))
    yield eng, update


# ── files model ───────────────────────────────────────────────────────


def test_files_normalize_and_start_pending(qapp):
    files = UpdateFilesModel()
    files.set_files(["a/b.txt", "c\\d.txt"])
    assert files.rowCount() == 2
    assert files.data(files.index(1), UpdateFilesModel.NameRole) == "c/d.txt"
    assert files.data(files.index(0), UpdateFilesModel.DoneRole) is False


def test_files_mark_done(qapp):
    files = UpdateFilesModel()
    files.set_files(["a/b.txt"])
    assert files.mark_done("a/b.txt") is True
    assert files.data(files.index(0), UpdateFilesModel.DoneRole) is True
    assert files.mark_done("missing.txt") is False


def test_files_append_done_dedups(qapp):
    files = UpdateFilesModel()
    files.set_files(["a/b.txt"])
    files.append_done("a\\b.txt")
    assert files.rowCount() == 1
    files.append_done("Data/patch.mpq")
    assert files.rowCount() == 2
    assert files.data(files.index(1), UpdateFilesModel.DoneRole) is True


def test_files_mark_all_done(qapp):
    files = UpdateFilesModel()
    files.set_files(["a", "b"])
    files.mark_all_done()
    assert files.data(files.index(0), UpdateFilesModel.DoneRole) is True
    assert files.data(files.index(1), UpdateFilesModel.DoneRole) is True


# ── progress intake ───────────────────────────────────────────────────


def test_initial_values(state):
    assert state.property("phaseText") == "Idle"
    assert state.property("fileText") == "No update is running."
    assert state.property("transportText") == "-"


def test_progress_sets_phase_and_stats(state):
    state.on_progress(
        _progress(
            value=0.5,
            phase="Downloading",
            transport="torrent",
            current_file="Data/patch.mpq",
            downloaded=100,
            total=200,
            speed=1024.0,
            peers=7,
        )
    )
    assert state.property("phaseText") == "Downloading"
    assert state.property("progressValue") == 0.5
    assert state.property("progressVisible") is True
    assert state.property("fileText") == "Data/patch.mpq"
    assert state.property("transportText") == "torrent"
    assert state.property("peersText") == "7"
    assert "100" in state.property("amountText")
    assert state.property("speedText") != "-"


def test_progress_label_falls_back_to_working(state):
    state.on_progress(_progress(value=0.1, label="Crunching"))
    assert state.property("phaseText") == "Working"
    assert state.property("fileText") == "Crunching"


def test_progress_visibility_rule(state):
    state.on_progress(_progress(value=0.0))
    assert state.property("progressVisible") is False
    state.on_progress(_progress(value=0.5))
    assert state.property("progressVisible") is True
    state.on_progress(_progress(value=1.0))
    assert state.property("progressVisible") is False


def test_progress_percent_without_total(state):
    state.on_progress(_progress(value=0.25))
    assert state.property("amountText") == "25%"


def test_progress_tracks_files(state):
    state.on_files(UpdateFilesList(["Data/a.mpq"]))
    state.on_progress(_progress(value=0.5, current_file="Data/a.mpq"))
    files = state.property("files")
    assert files.data(files.index(0), UpdateFilesModel.DoneRole) is True
    state.on_progress(_progress(value=0.6, current_file="Data/b.mpq"))
    assert files.rowCount() == 2


def test_status_intake(state):
    state.on_status("Verifying…")
    assert state.property("phaseText") == "Verifying…"
    assert state.property("fileText") == "Preparing client update…"
    state.on_status("something else")
    assert state.property("phaseText") == "Verifying…"


# ── finish matrix ─────────────────────────────────────────────────────


def test_verify_ok(state):
    state.on_files(UpdateFilesList(["a"]))
    state.on_finished("verify", True, "")
    assert state.property("phaseText") == "Verified"
    assert state.property("progressVisible") is False
    files = state.property("files")
    assert files.data(files.index(0), UpdateFilesModel.DoneRole) is True


def test_update_ok(state):
    state.on_finished("update", True, "")
    assert state.property("phaseText") == "Complete"


def test_verify_failed_needs_update(state):
    state.on_finished("verify", False, "3 files differ")
    assert state.property("phaseText") == "Update required"
    assert state.property("fileText") == "3 files differ"


def test_update_failed(state):
    state.on_finished("update", False, "")
    assert state.property("phaseText") == "Failed"
    assert state.property("fileText") == "Update failed."


def test_operation_failed(state):
    state.on_failed("update", "boom")
    assert state.property("phaseText") == "Failed"
    assert state.property("fileText") == "boom"
    state.on_failed("mods", "ignored")
    assert state.property("phaseText") == "Failed"


def test_mods_addons_chain(state):
    state.on_finished("mods", True, "")
    assert state.property("phaseText") == "Updating addons and mods"
    state.on_finished("addons", True, "")
    assert state.property("phaseText") == "Complete"


def test_primary_and_recheck_handlers(state):
    calls = []
    state.set_primary_handler(lambda: calls.append("primary"))
    state.set_recheck_handler(lambda: calls.append("recheck"))
    state.primary()
    state.recheck()
    assert calls == ["primary", "recheck"]


def test_handlers_unset_are_noop(state):
    state.primary()
    state.recheck()


def test_update_primary(state):
    state.update_primary("PLAY", True)
    assert state.property("primaryLabel") == "PLAY"
    assert state.property("primaryEnabled") is True
    state.update_primary("Installing…", False)
    assert state.property("primaryEnabled") is False


# ── engine ────────────────────────────────────────────────────────────


def test_update_view_loads(engine):
    eng, _update = engine
    root = eng.rootObjects()[0]
    for name in (
        "qmlUpdateView",
        "qmlUpdateTitle",
        "qmlUpdateRecheck",
        "qmlUpdatePhase",
        "qmlUpdateProgress",
        "qmlUpdateFile",
        "qmlUpdateFileList",
        "qmlUpdateTransport",
        "qmlUpdateAmount",
        "qmlUpdateSpeed",
        "qmlUpdatePeers",
    ):
        assert root.findChild(QObject, name) is not None, name


def test_update_view_follows_model(engine):
    eng, update = engine
    root = eng.rootObjects()[0]
    # UPDATE lives on tab 1; hidden StackLayout pages report
    # visible=False, so select it like a user would.
    root.findChild(QObject, "qmlNavBar").setProperty("currentIndex", 1)
    phase = root.findChild(QObject, "qmlUpdatePhase")
    bar = root.findChild(QObject, "qmlUpdateProgress")
    file_list = root.findChild(QObject, "qmlUpdateFileList")
    update.on_progress(_progress(value=0.5, phase="Downloading"))
    assert phase.property("text") == "Downloading"
    assert bar.property("value") == 50.0
    assert bar.property("visible") is True
    update.on_files(UpdateFilesList(["a", "b"]))
    assert file_list.property("count") == 2


def test_footer_button_follows_primary(engine):
    eng, update = engine
    root = eng.rootObjects()[0]
    button = root.findChild(QObject, "qmlPrimaryButton")
    update.update_primary("PLAY", True)
    assert button.property("text") == "PLAY"
    assert button.property("enabled") is True
    update.update_primary("Installing…", False)
    assert button.property("enabled") is False
