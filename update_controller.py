"""Update/verify orchestration controller.

Phase 1b of the PySide6 migration: owns the verify/update lifecycle that
OctoUpdaterApp used to drive directly — starting VerifyWorker/UpdateWorker,
polling their queues, computing the footer READY/PLAY/UPDATE button state and
publishing everything as events on the shared EventDispatcher. No tkinter, no
Qt: the controller never touches widgets, it only posts events and mutates its
own UpdateState.
"""

import queue
import threading
from dataclasses import dataclass

from client_update import UpdateWorker, VerifyWorker
from config_store import load_config, update_config
from constants import DEFAULT_OUT_DIR
from platform_support import can_launch_client
from ui_events import (
    EventDispatcher,
    LogMessage,
    OperationFailed,
    OperationFinished,
    ProgressChanged,
    StatusChanged,
)
from ui_state import UpdateState


@dataclass
class Readiness:
    """Footer button decision produced by UpdateController.compute_readiness.

    mode is "play", "update" or "busy"; label is the button text used in busy
    mode; status is the footer status line the UI should show.
    """
    mode: str
    label: str
    status: str


class UpdateController:
    """Owns the verify/update flow; speaks to the UI only through events.

    `get_out_dir` is an optional zero-arg callable returning the current game
    folder (a Qt app would supply its path field's getter). When omitted the
    controller reads ``out_dir`` from the on-disk config, mirroring the Tk
    app's default.
    """

    def __init__(self, dispatcher: EventDispatcher, get_out_dir=None):
        self._dispatcher = dispatcher
        self.state = UpdateState()
        self._log_q: queue.Queue = queue.Queue()
        self._prog_q: queue.Queue = queue.Queue()
        self._worker: UpdateWorker | None = None
        self._verify_worker: VerifyWorker | None = None
        self._op: str | None = None
        if get_out_dir is None:
            get_out_dir = lambda: load_config().get("out_dir", DEFAULT_OUT_DIR)
        self._get_out_dir = get_out_dir

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self.state.running

    @property
    def client_ready(self) -> bool:
        return self.state.client_ready

    @property
    def diff_nodes(self):
        return self.state.diff_nodes

    def start_verify(self, overwrite_config: bool = False):
        out = (self._get_out_dir() or "").strip()
        if not out:
            return
        # Cancel any verify already in flight before swapping the queues, so
        # a stale worker can't keep writing to a queue we no longer poll.
        if self._verify_worker is not None:
            self._verify_worker.cancel()
        self.state.running = True
        self._op = "verify"
        self.state.status = "Verifying…"
        self._log_q = queue.Queue()
        self._prog_q = queue.Queue()
        cfg = load_config()
        worker = VerifyWorker(
            out, self._log_q, self._prog_q,
            cfg.get("expected_patched_wow_hash", ""),
            cfg.get("original_server_wow_hash", ""),
            overwrite_config=overwrite_config)
        self._verify_worker = worker
        threading.Thread(target=worker.run, daemon=True).start()
        self._dispatcher.post(StatusChanged("Verifying…"))
        self._dispatcher.post(ProgressChanged(0.0, ""))

    def start_update(self):
        if self.state.running:
            return
        out = (self._get_out_dir() or "").strip()
        if not out:
            self._dispatcher.post(
                LogMessage("✗  Please set the game folder first.\n", "err"))
            return
        update_config(lambda c: c.__setitem__("out_dir", out))
        self._dispatcher.post(LogMessage(f"\nGame folder: {out}\n", "dim"))
        self.state.running = True
        self._op = "update"
        self.state.status = "Updating…"
        self._log_q = queue.Queue()
        self._prog_q = queue.Queue()
        cfg = load_config()
        worker = UpdateWorker(out, self._log_q, self._prog_q,
                              cfg.get("expected_patched_wow_hash", ""))
        worker.original_server_wow_hash = cfg.get("original_server_wow_hash", "")
        self._worker = worker
        diff = self.state.diff_nodes
        self.state.diff_nodes = None
        threading.Thread(target=worker.run, args=(diff,), daemon=True).start()
        self._dispatcher.post(StatusChanged("Updating…"))
        self._dispatcher.post(ProgressChanged(0.0, ""))

    def cancel(self):
        """Ask every live worker to stop; the queues are drained as normal."""
        for worker in (self._verify_worker, self._worker):
            if worker is not None:
                worker.cancel()

    def invalidate(self):
        """Drop readiness and the cached diff tree (game folder changed or a
        verify-game-files recheck)."""
        self.state.client_ready = False
        self.state.diff_nodes = None

    def poll(self):
        """Drain the worker queues once and post the resulting events."""
        try:
            while True:
                msg, tag = self._log_q.get_nowait()
                self._handle_log(msg, tag)
        except queue.Empty:
            pass

        latest = None
        try:
            while True:
                latest = self._prog_q.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            val, lbl = latest
            self.state.progress = max(0.0, min(1.0, val))
            self.state.progress_label = lbl
            self._dispatcher.post(ProgressChanged(val, lbl))

    def compute_readiness(self, addons_installing: bool = False) -> Readiness:
        """Footer button/status decision for the current state.

        `addons_installing` is owned by the (not-yet-extracted) addons
        controller; the UI passes its own flag so the button stays disabled
        while addons download, exactly like the mods flow.
        """
        if addons_installing:
            return Readiness("busy", "Installing…", "Downloading addons…")
        if self.state.running:
            label = "Updating…" if self._op == "update" else "Checking…"
            return Readiness("busy", label, self.state.status)
        if not self.state.client_ready:
            return Readiness("update", "UPDATE", "Update available!")
        if self._mods_have_errors():
            return Readiness("busy", "PLAY", "Mod errors — check MODS tab")
        if not can_launch_client():
            return Readiness("busy", "READY", "Everything up to date!")
        return Readiness("play", "PLAY", "Everything up to date!")

    # ── internals ───────────────────────────────────────────────────────────

    def _handle_log(self, msg: str, tag: str):
        if msg == "__DONE__":
            self.state.running = False
            self.state.client_ready = True
            self._op = None
            self._dispatcher.post(ProgressChanged(1.0, ""))
            self._dispatcher.post(OperationFinished("update", True))
        elif msg == "__ERROR__":
            self.state.running = False
            self.state.client_ready = False
            self._op = None
            self._dispatcher.post(ProgressChanged(0.0, ""))
            self._dispatcher.post(OperationFailed("update", ""))
        elif msg == "__UP_TO_DATE__":
            self.state.running = False
            self.state.client_ready = True
            self._op = None
            self._dispatcher.post(ProgressChanged(1.0, ""))
            self._dispatcher.post(OperationFinished("verify", True))
        elif msg == "__UPDATE_NEEDED__":
            self.state.running = False
            self.state.client_ready = False
            self._op = None
            self._dispatcher.post(ProgressChanged(0.0, ""))
            self._dispatcher.post(OperationFinished("verify", False))
        elif msg == "__DIFF_TREE__":
            self.state.diff_nodes = tag
        elif msg.startswith("__ORIGINAL_HASH__"):
            h = msg[len("__ORIGINAL_HASH__"):]
            update_config(lambda c: c.__setitem__("original_server_wow_hash", h))
        elif msg.startswith("__PATCHED_HASH__"):
            h = msg[len("__PATCHED_HASH__"):]
            update_config(lambda c: c.__setitem__("expected_patched_wow_hash", h))
        elif msg.startswith("__VERSION__"):
            self.state.client_version = msg[len("__VERSION__"):]
        else:
            self._dispatcher.post(LogMessage(msg, tag))

    def _mods_have_errors(self) -> bool:
        return any(bool(s.get("error"))
                   for s in load_config().get("mods", {}).values())
