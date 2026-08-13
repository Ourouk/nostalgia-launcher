"""Update/verify orchestration controller.

Owns the verify/update lifecycle: starting VerifyWorker/UpdateWorker,
polling their queues, computing the footer READY/PLAY/UPDATE button state
and publishing everything as events on the shared EventDispatcher. No GUI
toolkit: the controller never touches widgets, it only posts events and
mutates its own UpdateState.
"""

import os
import queue
import threading
from dataclasses import dataclass

from ..services.client_update import UpdateWorker, VerifyWorker
from ..core.config_store import load_config, update_config
from ..core.constants import DEFAULT_OUT_DIR
from ..core.filesystem import get_client_version, remove_wdb
from ..core.platform_support import can_launch_client
from ..services.self_update import fetch_updater_latest_tag, updater_update_available
from ..state.events import (
    EventDispatcher,
    LogMessage,
    OperationFailed,
    OperationFinished,
    ProgressChanged,
    StatusChanged,
)
from ..state.models import UpdateState


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
    folder (the Qt UI supplies its path field's getter). When omitted the
    controller reads ``out_dir`` from the on-disk config, mirroring the
    UI's default.
    """

    def __init__(self, dispatcher: EventDispatcher, get_out_dir=None):
        self._dispatcher = dispatcher
        self.state = UpdateState()
        self._log_q: queue.Queue = queue.Queue()
        self._prog_q: queue.Queue = queue.Queue()
        self._worker: UpdateWorker | None = None
        self._verify_worker: VerifyWorker | None = None
        self._op: str | None = None
        # Set by check_updater_update(): a newer updater release exists and
        # the header "Update available!" label should be shown.
        self.updater_update_available = False
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

    def read_client_version(self) -> str:
        """The client version straight from disk, cached on state (footer
        label at startup, before any worker has run)."""
        self.state.client_version = get_client_version(
            (self._get_out_dir() or "").strip())
        return self.state.client_version

    def check_updater_update(self):
        """Background daily check of the updater's own GitHub releases; sets
        `updater_update_available` when a newer version exists. The UI polls
        that flag from its event loop and draws the header label."""

        def worker():
            try:
                tag = fetch_updater_latest_tag()
            except Exception:
                tag = None
            self.updater_update_available = bool(
                updater_update_available(tag))

        threading.Thread(target=worker, daemon=True).start()

    def launch_game(self) -> tuple:
        """Launch the client detached.

        Returns ``(ok, dxvk_notice)``: ``ok`` is False when the client can't
        be launched (a LogMessage explains why) and ``dxvk_notice`` is True
        when the one-time DXVK first-launch notice should be shown. Consumes
        the notice flag, the clear-wdb and the launch itself; Windows-only —
        the client is a Windows binary.
        """
        if not can_launch_client():
            self._dispatcher.post(LogMessage(
                "Game launch is only available on Windows (the client is a "
                "Windows binary).\n", "err"))
            return False, False
        import subprocess
        client_dir = (self._get_out_dir() or "").strip()
        cfg = load_config()
        vf_state = cfg.get("mods", {}).get("VanillaFixes", {})
        vf_installed = (vf_state.get("enabled") and
                        vf_state.get("installed_version") and
                        os.path.exists(
                            os.path.join(client_dir, "VanillaFixes.exe")))
        if vf_installed:
            exe = os.path.join(client_dir, "VanillaFixes.exe")
            exe_lbl = "VanillaFixes.exe"
        else:
            exe = os.path.join(client_dir, "WoW.exe")
            exe_lbl = "WoW.exe"
        if not os.path.exists(exe):
            self._dispatcher.post(LogMessage(
                f"{exe_lbl} not found at: {exe}\n", "err"))
            return False, False

        dxvk_notice = False
        if cfg.get("dxvk_notice_pending"):
            update_config(lambda c: c.pop("dxvk_notice_pending", None))
            dxvk_notice = True
        if cfg.get("clear_wdb_on_launch", False):
            remove_wdb(client_dir)

        try:
            flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
            try:
                subprocess.Popen([exe], cwd=client_dir,
                                 creationflags=flags, close_fds=True)
            except OSError:
                # The job object doesn't permit breakaway — retry without it.
                flags &= ~getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
                subprocess.Popen([exe], cwd=client_dir,
                                 creationflags=flags, close_fds=True)
            self._dispatcher.post(LogMessage(f"Launched {exe_lbl}!\n", "ok"))
            return True, dxvk_notice
        except Exception as e:
            self._dispatcher.post(LogMessage(
                f"Failed to launch {exe_lbl}: {e}\n", "err"))
            return False, dxvk_notice

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

        `addons_installing` is owned by the addons controller; the UI passes
        its own flag so the button stays disabled while addons download,
        exactly like the mods flow.
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
