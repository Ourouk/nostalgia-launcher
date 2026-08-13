"""Headless Qt tests for the controller bridge.

QT_QPA_PLATFORM=offscreen is set before PySide6 is imported so the module
runs without a display. The QApplication is created once and shared through
the create_qt_app() singleton — a second QApplication in one process would
abort Qt. QTest.qWait drives the event loop so the bridge's QTimer fires.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtTest import QTest

from qt_app import create_qt_app
from qt_bridge import ControllerBridge, ControllerHub
from ui_events import (
    AddonsLoaded,
    EventDispatcher,
    LogMessage,
    MirrorStatusChanged,
    ModsLoaded,
    NewsLoaded,
    OperationFailed,
    OperationFinished,
    ProgressChanged,
    StatusChanged,
)
from ui_state import AddonsState, ModsState


@pytest.fixture(autouse=True)
def _offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return create_qt_app()


class _Spy:
    """Collects every args-tuple a connected signal delivers."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)


def _connect(bridge, signal_name):
    spy = _Spy()
    getattr(bridge, signal_name).connect(spy)
    return spy


def test_bridge_constructs_and_closes_offscreen(qapp):
    dispatcher = EventDispatcher()
    bridge = ControllerBridge(dispatcher)
    assert bridge._dispatcher is dispatcher
    assert bridge._timer.isActive()
    bridge.close()
    assert not bridge._timer.isActive()


def test_every_event_type_reaches_its_signal(qapp):
    dispatcher = EventDispatcher()
    bridge = ControllerBridge(dispatcher)
    spies = {name: _connect(bridge, name) for name in (
        "statusChanged", "logMessage", "progressChanged", "newsLoaded",
        "modsLoaded", "addonsLoaded", "mirrorStatusChanged",
        "operationFinished", "operationFailed")}

    dispatcher.post(StatusChanged("Updating…"))
    dispatcher.post(LogMessage("hello", "ok"))
    dispatcher.post(ProgressChanged(0.5, "5/10"))
    news = NewsLoaded("featured", object())
    dispatcher.post(news)
    mods = ModsLoaded(ModsState())
    dispatcher.post(mods)
    addons = AddonsLoaded(AddonsState())
    dispatcher.post(addons)
    dispatcher.post(MirrorStatusChanged(True, "online"))
    dispatcher.post(OperationFinished("mods", True, "done"))
    dispatcher.post(OperationFailed("update", "boom"))

    QTest.qWait(250)

    assert spies["statusChanged"].calls == [("Updating…",)]
    assert spies["logMessage"].calls == [("hello", "ok")]
    assert spies["progressChanged"].calls == [(0.5, "5/10")]
    assert spies["newsLoaded"].calls == [(news,)]
    assert spies["modsLoaded"].calls == [(mods,)]
    assert spies["addonsLoaded"].calls == [(addons,)]
    assert spies["mirrorStatusChanged"].calls == [(True, "online")]
    assert spies["operationFinished"].calls == [("mods", True, "done")]
    assert spies["operationFailed"].calls == [("update", "boom")]
    bridge.close()


def test_events_posted_before_first_tick_are_not_lost(qapp):
    dispatcher = EventDispatcher()
    dispatcher.post(StatusChanged("queued early"))
    dispatcher.post(LogMessage("still here", "dim"))
    bridge = ControllerBridge(dispatcher)
    status = _connect(bridge, "statusChanged")
    log = _connect(bridge, "logMessage")

    QTest.qWait(200)

    assert status.calls == [("queued early",)]
    assert log.calls == [("still here", "dim")]
    bridge.close()


def test_close_stops_delivery(qapp):
    dispatcher = EventDispatcher()
    bridge = ControllerBridge(dispatcher)
    status = _connect(bridge, "statusChanged")
    bridge.close()

    dispatcher.post(StatusChanged("after close"))
    QTest.qWait(200)

    assert status.calls == []
    assert len(dispatcher.drain()) == 1
    bridge.close()


def test_stop_alias_closes(qapp):
    dispatcher = EventDispatcher()
    bridge = ControllerBridge(dispatcher)
    bridge.stop()
    assert not bridge._timer.isActive()


def test_controller_hub_assembles_shared_dispatcher(qapp):
    hub = ControllerHub()
    try:
        assert hub.bridge._dispatcher is hub.dispatcher
        for ctrl in (hub.updater, hub.news, hub.mods, hub.addons,
                     hub.settings):
            assert ctrl._dispatcher is hub.dispatcher
        status = _connect(hub.bridge, "statusChanged")
        hub.dispatcher.post(StatusChanged("via hub"))
        QTest.qWait(200)
        assert status.calls == [("via hub",)]
    finally:
        hub.close()
