"""Regression tests for typed EventDispatcher migration.

Covers the 11 required scenarios after queue/marker removal.
"""

import pytest

from nostalgia_launcher.controllers.update import UpdateController
from nostalgia_launcher.services.update_backend.worker_base import WorkerBase
from nostalgia_launcher.state.events import (
    EventDispatcher,
    LogMessage,
    ProgressChanged,
    TorrentDiffReady,
    TorrentReachable,
    TorrentRecoveryDone,
    TorrentUnavailable,
    UpdateCompleted,
    UpdateFailed,
    VerificationUpToDate,
)


@pytest.fixture
def dispatcher():
    return EventDispatcher()


@pytest.fixture
def controller(dispatcher, monkeypatch):
    from nostalgia_launcher.controllers import update as uc
    from nostalgia_launcher.core import launcher

    cfg = {"out_dir": "/tmp/game"}
    monkeypatch.setattr(uc, "load_config", lambda: cfg)
    monkeypatch.setattr(
        launcher, "effective_client_updates_enabled", lambda: True
    )
    # Avoid real launcher config
    monkeypatch.setattr(launcher, "download_update_enabled", lambda: True)
    return UpdateController(dispatcher)


def test_worker_log_emitted_exactly_once():
    disp = EventDispatcher()
    w = WorkerBase("/tmp", disp)
    w.log("hello", "ok")
    events = disp.drain()
    logs = [e for e in events if isinstance(e, LogMessage)]
    assert len(logs) == 1
    assert logs[0].text == "hello"
    assert logs[0].tag == "ok"
    # No second emission
    assert disp.drain() == []


def test_worker_progress_emitted_exactly_once():
    disp = EventDispatcher()
    w = WorkerBase("/tmp", disp)
    w.progress(0.42, "Downloading", phase="Downloading", transport="HTTP")
    events = disp.drain()
    progs = [e for e in events if isinstance(e, ProgressChanged)]
    assert len(progs) == 1
    assert progs[0].value == 0.42
    assert progs[0].label == "Downloading"
    assert progs[0].phase == "Downloading"
    assert disp.drain() == []


def test_verify_completion_up_to_date(controller, dispatcher):
    dispatcher.post(VerificationUpToDate())
    dispatcher.dispatch_all()
    assert controller.state.client_ready is True
    assert controller.state.running is False
    # OperationFinished posted for UI; check via fresh controller
    disp2 = EventDispatcher()
    ctrl2 = UpdateController(disp2)
    # Re-run via controller's dispatcher
    ctrl2._dispatcher.post(VerificationUpToDate())
    ctrl2._dispatcher.dispatch_all()
    # After dispatch, OperationFinished should be in queue for bridge (but ctrl consumed it and posted new)
    # So we check ctrl2's dispatcher has OperationFinished
    # After first dispatch, OperationFinished is queued for next drain
    queued = disp2.drain()
    from nostalgia_launcher.state.events import OperationFinished

    assert any(isinstance(e, OperationFinished) for e in queued)
    assert ctrl2.state.client_ready is True


def test_update_completion(controller, dispatcher):
    controller.state.running = True
    controller._op = "update"
    dispatcher.post(UpdateCompleted(version="1.12.1"))
    dispatcher.dispatch_all()
    assert controller.state.running is False
    assert controller.state.client_ready is True
    assert controller.state.client_version == "1.12.1"


def test_verify_failure_manifest_unavailable(controller, dispatcher):
    pytest.skip("manifest removed — torrent-only")


def test_manifest_available_sets_flag(controller, dispatcher):
    pytest.skip("manifest removed — torrent-only")


def test_torrent_reachable(controller, dispatcher):
    dispatcher.post(TorrentReachable())
    dispatcher.dispatch_all()
    assert controller.state.torrent_reachable is True


def test_torrent_unreachable(controller, dispatcher):
    dispatcher.post(TorrentUnavailable(message="timeout"))
    dispatcher.dispatch_all()
    assert controller.state.torrent_reachable is False
    assert controller.state.torrent_error == "timeout"


def test_torrent_stale_file_result(controller, dispatcher):
    dispatcher.post(TorrentReachable())
    dispatcher.dispatch_all()
    dispatcher.post(TorrentDiffReady(stale=["Data/a.mpq", "WoW.exe"]))
    dispatcher.dispatch_all()
    assert controller.state.torrent_stale == ["Data/a.mpq", "WoW.exe"]
    assert controller.state.client_ready is False
    # Verify UpdateFilesList and OperationFinished were posted
    disp2 = EventDispatcher()
    ctrl2 = UpdateController(disp2)
    disp2.post(TorrentDiffReady(stale=["a", "b"]))
    disp2.dispatch_all()
    queued = disp2.drain()
    from nostalgia_launcher.state.events import (
        OperationFinished,
        UpdateFilesList,
    )

    assert ctrl2.state.torrent_stale == ["a", "b"]
    assert any(isinstance(e, UpdateFilesList) for e in queued)
    assert any(isinstance(e, OperationFinished) for e in queued)


def test_torrent_recovery_completion(controller, dispatcher):
    controller.state.running = True
    controller._op = "update"
    dispatcher.post(TorrentRecoveryDone())
    dispatcher.dispatch_all()
    assert controller.state.running is False
    assert controller.state.client_ready is True


def test_cancellation_via_update_failed(controller, dispatcher):
    controller.state.running = True
    controller._op = "verify"
    dispatcher.post(UpdateFailed(message="", op="verify"))
    dispatcher.dispatch_all()
    assert controller.state.running is False
    assert controller.state.client_ready is False


def test_diff_tree_ready_sets_diff_nodes(controller, dispatcher):
    pytest.skip("manifest diff removed — torrent-only")


def test_dispatcher_handler_failure_no_recursive_log(capsys):
    disp = EventDispatcher()

    def bad_handler(event):
        raise RuntimeError("boom")

    disp.subscribe(bad_handler)
    disp.subscribe(lambda e: None)
    disp.post(LogMessage("hello", "ok"))
    # Should not raise, should print to stderr, not post LogMessage
    result = disp.dispatch_all()
    assert len(result) == 1
    # Queue should be empty now, no recursive LogMessage
    assert disp.drain() == []
    # No LogMessage generated for handler failure
    # Ensure stderr contains failure (capsys captures)
    captured = capsys.readouterr()
    assert "Event handler failed" in captured.err
    assert "boom" in captured.err


def test_progress_mirrored_to_state(controller, dispatcher):
    dispatcher.post(
        ProgressChanged(
            0.75,
            "Downloading",
            phase="Downloading",
            transport="HTTP",
            current_file="a.mpq",
            downloaded=75,
            total=100,
        )
    )
    dispatcher.dispatch_all()
    assert controller.state.progress == 0.75
    assert controller.state.progress_label == "Downloading"
    assert controller.state.progress_file == "a.mpq"
