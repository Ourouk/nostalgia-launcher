"""Tweaks panel controller.

Owns the TWEAKS-panel business logic: reading the saved tweak values,
clamping the UI entries, deciding when Apply/Reset are offered, and the
apply/reset workers (WoW.exe patch on Windows + Config.wtf update + the
patched-hash bookkeeping). Speaks to the UI only through events on the shared
EventDispatcher: LogMessage for every log line, ProgressChanged when the
worker reports progress, and OperationFinished(kind="tweaks") at the end
(plus OperationFailed on an exception). No GUI toolkit.
"""

import os
import queue
import threading

from ..core import config_store, platform_support
from ..core.constants import DEFAULT_OUT_DIR
from ..core.filesystem import sha1_file
from ..services import client_update, tweaks
from ..services.tweaks import (
    TWEAKS_DEFAULTS,
    TWEAKS_LIMITS,
    fov_default_for_display,
)
from ..state.events import (
    EventDispatcher,
    LogMessage,
    OperationFailed,
    OperationFinished,
    ProgressChanged,
)


class TweaksController:
    """Owns the tweak values, their clamping and the apply/reset lifecycle.

    `get_out_dir` is an optional zero-arg callable returning the current game
    folder (the Qt UI supplies its path field's getter). When omitted the
    controller reads ``out_dir`` from the on-disk config, mirroring the
    UI's default.
    """

    def __init__(self, dispatcher: EventDispatcher, get_out_dir=None):
        self._dispatcher = dispatcher
        self._running = False
        if get_out_dir is None:

            def get_out_dir():
                return config_store.load_config().get(
                    "out_dir", DEFAULT_OUT_DIR
                )

        self._get_out_dir = get_out_dir

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        """True while an apply/reset worker is running — guards re-entry."""
        return self._running

    def values(self) -> dict:
        """The currently saved tweak values (defaults merged in)."""
        return tweaks.load_tweaks_config()

    def validate_entries(self, ui: dict) -> tuple[bool, dict]:
        """Clamp every numeric entry to its limits and report the bad ones.

        Clamps every numeric entry to its limits and flags out-of-range
        values: a value that fails to parse (or is outside [min, max])
        counts as "bad" and falls back to the tweak default / the closest
        limit. Returns (any_bad, clamped_ui).
        """
        any_bad = False
        result = {}
        for tid, raw in ui.items():
            if isinstance(TWEAKS_DEFAULTS.get(tid), bool):
                result[tid] = bool(raw)
                continue
            lo, hi = TWEAKS_LIMITS.get(tid, (None, None))
            try:
                v = int(float(raw))
            except (TypeError, ValueError):
                v = TWEAKS_DEFAULTS.get(tid, lo or 0)
                bad = True
            else:
                bad = (lo is not None and v < lo) or (
                    hi is not None and v > hi
                )
            if lo is not None:
                v = max(lo, v)
            if hi is not None:
                v = min(hi, v)
            any_bad = any_bad or bad
            result[tid] = v
        return any_bad, result

    def dirty_and_custom(self, ui: dict) -> tuple[bool, bool]:
        """The Apply/Reset button rules for a UI value snapshot.

        The norm/dirty/custom computation: dirty means the (clamped) UI
        differs from the saved config (or holds an out-of-range entry),
        custom means it differs from the defaults. Booleans are normalized
        via bool(), numbers via int()."""
        any_bad, clamped = self.validate_entries(ui)
        saved = tweaks.load_tweaks_config()
        defaults = dict(TWEAKS_DEFAULTS)
        defaults["fieldOfView"] = fov_default_for_display()

        def norm(d):
            return {
                k: (
                    bool(d.get(k))
                    if isinstance(TWEAKS_DEFAULTS.get(k), bool)
                    else int(d.get(k, 0))
                )
                for k in clamped
            }

        ui_n = norm(clamped)
        dirty = any_bad or ui_n != norm(saved)
        custom = any_bad or ui_n != norm(defaults)
        return dirty, custom

    def apply(self, ui: dict) -> bool:
        """Save the (clamped) tweak values and run the apply worker.

        Returns True when a worker was actually spawned (so the caller knows
        when to flip its busy chrome). Always persists first, then validates
        the game folder, logs, and starts the daemon thread.
        """
        if self._running:
            return False
        any_bad, clamped = self.validate_entries(ui)
        tweaks.save_tweaks_config(clamped)

        out = (self._get_out_dir() or "").strip()
        if not out:
            self._dispatcher.post(LogMessage("Game folder not set.\n", "err"))
            return False
        exe = os.path.join(out, "WoW.exe")
        if platform_support.can_patch_client() and not os.path.exists(exe):
            self._dispatcher.post(
                LogMessage("WoW.exe not found — run Update first.\n", "err")
            )
            return False

        self._dispatcher.post(
            LogMessage("\nApplying tweaks to WoW.exe...\n", "acct")
        )
        self._start_worker(clamped)
        return True

    def reset(self, defaults=None) -> bool:
        """Save the tweak defaults and re-apply them.

        When `defaults` is omitted it is built from TWEAKS_DEFAULTS with the
        display-detected FOV default. Returns True when a worker was spawned.
        No "Applying tweaks…" line is logged; on Windows the exe patch needs
        WoW.exe present, elsewhere any set folder suffices.
        """
        if self._running:
            return False
        if defaults is None:
            defaults = dict(TWEAKS_DEFAULTS)
            defaults["fieldOfView"] = fov_default_for_display()
        tweaks.save_tweaks_config(defaults)

        out = (self._get_out_dir() or "").strip()
        if platform_support.can_patch_client():
            needs_exe = os.path.exists(os.path.join(out, "WoW.exe"))
        else:
            needs_exe = bool(out)
        if not needs_exe:
            return False
        self._start_worker(defaults)
        return True

    # ── internals ───────────────────────────────────────────────────────────

    def _start_worker(self, tweak_values: dict):
        client_dir = (self._get_out_dir() or "").strip()
        self._running = True
        threading.Thread(
            target=self._run_apply_worker,
            args=(client_dir, tweak_values),
            daemon=True,
        ).start()

    def _run_apply_worker(self, client_dir: str, tweak_values: dict):
        """Thread wrapper that always clears the re-entry guard, even when a
        patched/stubbed worker body raises."""
        try:
            self._apply_worker(client_dir, tweak_values)
        finally:
            self._running = False

    def _apply_worker(self, client_dir: str, tweak_values: dict):
        """The tweaks worker: patch WoW.exe (Windows), write Config.wtf,
        update the patched-hash bookkeeping, and report the outcome through
        events."""
        log_q = queue.Queue()
        prog_q = queue.Queue()
        worker = client_update.UpdateWorker(client_dir, log_q, prog_q)
        try:
            exe_path = os.path.join(client_dir, "WoW.exe")

            fresh_cfg = config_store.load_config()
            expected_patched = fresh_cfg.get("expected_patched_wow_hash", "")
            original_server = fresh_cfg.get("original_server_wow_hash", "")
            local_before = (
                sha1_file(exe_path) if os.path.exists(exe_path) else ""
            )

            if platform_support.can_patch_client():
                worker.patch_exe(tweak_values)
                self._drain_logs(log_q)
                self._drain_progress(prog_q)
            else:
                # Binary tweaks target Windows offsets — on other platforms
                # only the Config.wtf settings are applied.
                self._dispatcher.post(
                    LogMessage(
                        "Binary WoW.exe tweaks are only applied on Windows; "
                        "writing Config.wtf only.\n",
                        "dim",
                    )
                )

            tweaks.update_config_wtf(client_dir, tweak_values)

            local_after = (
                sha1_file(exe_path) if os.path.exists(exe_path) else ""
            )

            def _set_hashes(c):
                c["expected_patched_wow_hash"] = local_after
                if local_before == expected_patched and original_server:
                    c["original_server_wow_hash"] = original_server
                else:
                    c.pop("original_server_wow_hash", None)

            config_store.update_config(_set_hashes)

            self._dispatcher.post(LogMessage("\nTweaks applied.\n", "ok"))
            self._dispatcher.post(OperationFinished("tweaks", True, ""))
        except Exception as e:
            self._drain_logs(log_q)
            self._dispatcher.post(
                LogMessage(f"\n✗ Tweak patch failed: {e}\n", "err")
            )
            self._dispatcher.post(OperationFailed("tweaks", str(e)))
            self._dispatcher.post(OperationFinished("tweaks", False, str(e)))

    def _drain_logs(self, log_q: queue.Queue):
        """Forward the UpdateWorker's log lines to the dispatcher as
        LogMessage events (skipping the __-prefixed protocol markers)."""
        try:
            while True:
                msg, tag = log_q.get_nowait()
                if msg not in ("__DONE__", "__ERROR__") and not msg.startswith(
                    "__"
                ):
                    self._dispatcher.post(LogMessage(msg, tag))
        except queue.Empty:
            pass

    def _drain_progress(self, prog_q: queue.Queue):
        """Forward any worker progress reports as ProgressChanged events."""
        try:
            while True:
                value, label = prog_q.get_nowait()
                self._dispatcher.post(ProgressChanged(value, label))
        except queue.Empty:
            pass
