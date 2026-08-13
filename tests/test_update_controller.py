"""Unit tests for the update controller (update_controller).

No Tk involved: the controller is driven directly and its effects are read
from the shared EventDispatcher and its UpdateState. VerifyWorker/UpdateWorker
are swapped for a scripted fake via monkeypatch.
"""

import queue
import threading
import time

import pytest

import octo_updater.controllers.update as uc
from octo_updater.controllers.update import UpdateController
from octo_updater.state.events import (
    EventDispatcher,
    LogMessage,
    OperationFailed,
    OperationFinished,
    ProgressChanged,
    StatusChanged,
)


class ScriptedWorker:
    """Fake VerifyWorker/UpdateWorker with the real constructor signature.

    run() replays the class-level `script` (log messages) and `prog_script`
    (progress updates) into its queues, then signals `done`.
    """

    instances = []
    script = []
    prog_script = []
    done = threading.Event()

    def __init__(self, out_dir, log_q, prog_q, *args, **kwargs):
        self.out_dir = out_dir
        self.log_q = log_q
        self.prog_q = prog_q
        self.args = args
        self.kwargs = kwargs
        self.overwrite_config = kwargs.get("overwrite_config", False)
        self.cancelled = False
        self.run_args = None
        type(self).instances.append(self)

    def cancel(self):
        self.cancelled = True

    def run(self, *args):
        self.run_args = args
        for msg, tag in type(self).script:
            self.log_q.put((msg, tag))
        for val, lbl in type(self).prog_script:
            self.prog_q.put((val, lbl))
        type(self).done.set()


@pytest.fixture
def worker_cls(monkeypatch):
    ScriptedWorker.instances = []
    ScriptedWorker.script = []
    ScriptedWorker.prog_script = []
    ScriptedWorker.done.clear()
    monkeypatch.setattr(uc, "VerifyWorker", ScriptedWorker)
    monkeypatch.setattr(uc, "UpdateWorker", ScriptedWorker)
    yield ScriptedWorker
    ScriptedWorker.done.clear()


@pytest.fixture
def config(monkeypatch):
    cfg = {"out_dir": "/tmp/octo-game"}
    monkeypatch.setattr(uc, "load_config", lambda: cfg)
    monkeypatch.setattr(uc, "update_config",
                        lambda mutator: (mutator(cfg), cfg)[1])
    monkeypatch.setattr(uc, "can_launch_client", lambda: True)
    return cfg


@pytest.fixture
def controller(config):
    return UpdateController(EventDispatcher())


def _wait_and_poll(controller, worker_cls, timeout=2.0):
    """Wait for the scripted worker's thread, then drain its queues once."""
    deadline = time.monotonic() + timeout
    while not worker_cls.done.is_set():
        if time.monotonic() > deadline:
            raise AssertionError("scripted worker never finished")
        time.sleep(0.005)
    controller.poll()


# ── verify flow ─────────────────────────────────────────────────────────

def test_verify_up_to_date_marks_client_ready(controller, worker_cls, config):
    worker_cls.script = [("__UP_TO_DATE__", "")]
    controller.start_verify()
    initial = controller._dispatcher.drain()
    assert StatusChanged("Verifying…") in initial
    assert ProgressChanged(0.0, "") in initial

    _wait_and_poll(controller, worker_cls)
    events = controller._dispatcher.drain()
    assert ProgressChanged(1.0, "") in events
    assert OperationFinished("verify", True) in events
    assert controller.state.client_ready is True
    assert controller.state.running is False


def test_verify_needs_update_sets_diff_and_not_ready(controller, worker_cls, config):
    diff = [{"type": "file", "name": "a.bin"}]
    worker_cls.script = [("__DIFF_TREE__", diff), ("__UPDATE_NEEDED__", "")]
    controller.start_verify()
    _wait_and_poll(controller, worker_cls)
    events = controller._dispatcher.drain()
    assert OperationFinished("verify", False) in events
    assert ProgressChanged(0.0, "") in events
    assert controller.state.diff_nodes == diff
    assert controller.state.client_ready is False
    assert controller.state.running is False


def test_verify_failure_records_null_diff(controller, worker_cls, config):
    worker_cls.script = [("__DIFF_TREE__", None), ("__UPDATE_NEEDED__", "")]
    controller.start_verify()
    _wait_and_poll(controller, worker_cls)
    controller._dispatcher.drain()
    assert controller.state.diff_nodes is None
    assert controller.state.client_ready is False
    assert controller.state.running is False


def test_verify_passes_overwrite_and_config_hashes(controller, worker_cls, config):
    config["expected_patched_wow_hash"] = "exp"
    config["original_server_wow_hash"] = "orig"
    controller.start_verify(overwrite_config=True)
    w = worker_cls.instances[0]
    assert w.overwrite_config is True
    assert w.args == ("exp", "orig")
    assert w.out_dir == config["out_dir"]


def test_verify_passes_no_overwrite_by_default(controller, worker_cls, config):
    controller.start_verify()
    assert worker_cls.instances[0].overwrite_config is False


def test_start_verify_cancels_previous_worker(controller, worker_cls, config):
    controller.start_verify()
    first = worker_cls.instances[0]
    controller.start_verify()
    assert first.cancelled is True
    assert len(worker_cls.instances) == 2


def test_start_verify_without_folder_is_noop(worker_cls, config):
    ctrl = UpdateController(EventDispatcher(), get_out_dir=lambda: "")
    ctrl.start_verify()
    assert ctrl._dispatcher.drain() == []
    assert not worker_cls.instances


# ── update flow ─────────────────────────────────────────────────────────

def test_update_done_reports_version(controller, worker_cls, config):
    worker_cls.script = [("__VERSION__1.12.2", ""), ("__DONE__", "")]
    controller.start_update()
    initial = controller._dispatcher.drain()
    assert StatusChanged("Updating…") in initial
    assert ProgressChanged(0.0, "") in initial
    assert LogMessage("\nGame folder: /tmp/octo-game\n", "dim") in initial

    _wait_and_poll(controller, worker_cls)
    events = controller._dispatcher.drain()
    assert OperationFinished("update", True) in events
    assert controller.state.client_version == "1.12.2"
    assert controller.state.client_ready is True
    assert controller.state.running is False


def test_update_error_posts_failure(controller, worker_cls, config):
    worker_cls.script = [("__ERROR__", "")]
    controller.start_update()
    _wait_and_poll(controller, worker_cls)
    events = controller._dispatcher.drain()
    assert OperationFailed("update", "") in events
    assert controller.state.client_ready is False
    assert controller.state.running is False


def test_update_receives_diff_from_verify(controller, worker_cls, config):
    diff = [{"type": "file", "name": "a.bin"}]
    worker_cls.script = [("__DIFF_TREE__", diff), ("__UPDATE_NEEDED__", "")]
    controller.start_verify()
    _wait_and_poll(controller, worker_cls)
    controller._dispatcher.drain()

    worker_cls.script = [("__DONE__", "")]
    worker_cls.prog_script = []
    worker_cls.done.clear()
    controller.start_update()
    _wait_and_poll(controller, worker_cls)
    w = worker_cls.instances[1]
    assert w.run_args == (diff,)
    assert controller.state.diff_nodes is None


def test_start_update_without_folder_logs_error(worker_cls, config):
    ctrl = UpdateController(EventDispatcher(), get_out_dir=lambda: "  ")
    ctrl.start_update()
    events = ctrl._dispatcher.drain()
    assert LogMessage("✗  Please set the game folder first.\n", "err") in events
    assert ctrl.state.running is False
    assert not worker_cls.instances


# ── queue draining / progress / hashes ──────────────────────────────────

def test_log_lines_become_log_events(controller, worker_cls, config):
    worker_cls.script = [("hello world", "acct")]
    controller.start_verify()
    _wait_and_poll(controller, worker_cls)
    assert LogMessage("hello world", "acct") in controller._dispatcher.drain()


def test_progress_posts_latest(controller, worker_cls, config):
    worker_cls.prog_script = [(0.3, "a.bin"), (0.9, "b.mpq")]
    controller.start_verify()
    _wait_and_poll(controller, worker_cls)
    events = controller._dispatcher.drain()
    assert ProgressChanged(0.9, "b.mpq") in events
    assert controller.state.progress == 0.9
    assert controller.state.progress_label == "b.mpq"


def test_patch_hashes_written_to_config(controller, worker_cls, config):
    worker_cls.script = [
        ("__ORIGINAL_HASH__abc", ""),
        ("__PATCHED_HASH__def", ""),
    ]
    controller.start_verify()
    _wait_and_poll(controller, worker_cls)
    controller._dispatcher.drain()
    assert config["original_server_wow_hash"] == "abc"
    assert config["expected_patched_wow_hash"] == "def"


def test_cancel_stops_live_workers(controller, worker_cls, config):
    controller.start_verify()
    w = worker_cls.instances[0]
    controller.cancel()
    assert w.cancelled is True


def test_invalidate_resets_readiness(controller, worker_cls, config):
    controller.state.client_ready = True
    controller.state.diff_nodes = [{"type": "file", "name": "a.bin"}]
    controller.invalidate()
    assert controller.state.client_ready is False
    assert controller.state.diff_nodes is None


def test_events_delivered_to_subscribers(controller, worker_cls, config):
    got = []
    controller._dispatcher.subscribe(got.append)
    worker_cls.script = [("__UP_TO_DATE__", "")]
    controller.start_verify()
    _wait_and_poll(controller, worker_cls)
    controller._dispatcher.dispatch_all()
    kinds = {type(e) for e in got}
    assert StatusChanged in kinds
    assert ProgressChanged in kinds
    assert OperationFinished in kinds


# ── compute_readiness ────────────────────────────────────────────────────

def test_readiness_update_available_when_not_ready(controller, worker_cls, config):
    r = controller.compute_readiness()
    assert r.mode == "update"
    assert r.label == "UPDATE"
    assert r.status == "Update available!"


def test_readiness_play_when_ready_and_launchable(controller, worker_cls, config):
    controller.state.client_ready = True
    r = controller.compute_readiness()
    assert r.mode == "play"
    assert r.status == "Everything up to date!"


def test_readiness_ready_when_not_launchable(controller, worker_cls, config, monkeypatch):
    monkeypatch.setattr(uc, "can_launch_client", lambda: False)
    controller.state.client_ready = True
    r = controller.compute_readiness()
    assert r.mode == "busy"
    assert r.label == "READY"
    assert r.status == "Everything up to date!"


def test_readiness_play_blocked_by_mod_errors(controller, worker_cls, config):
    config["mods"] = {"SomeMod": {"error": "download blocked"}}
    controller.state.client_ready = True
    r = controller.compute_readiness()
    assert r.mode == "busy"
    assert r.label == "PLAY"
    assert r.status == "Mod errors — check MODS tab"


def test_readiness_blocked_while_addons_install(controller, worker_cls, config):
    controller.state.client_ready = True
    r = controller.compute_readiness(addons_installing=True)
    assert r.mode == "busy"
    assert r.label == "Installing…"
    assert r.status == "Downloading addons…"


def test_readiness_busy_while_verifying(controller, worker_cls, config):
    controller.start_verify()
    r = controller.compute_readiness()
    assert r.mode == "busy"
    assert r.label == "Checking…"
    assert r.status == "Verifying…"


def test_readiness_busy_while_updating(controller, worker_cls, config):
    controller.start_update()
    r = controller.compute_readiness()
    assert r.mode == "busy"
    assert r.label == "Updating…"
    assert r.status == "Updating…"
