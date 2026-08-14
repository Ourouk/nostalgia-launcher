"""Mods panel controller.

Owns the MODS-panel business logic: the background latest-version fetch, the
update-available count, the pending checkbox/ignore changes, and the
install/uninstall/update worker. Publishes snapshots as ModsLoaded and the
worker outcome as OperationFinished on the shared EventDispatcher; the Qt
Mods panel renders them. No GUI toolkit.
"""

import os
import threading

from ..core import config_store
from ..services import mods
from ..core.constants import DEFAULT_OUT_DIR
from ..core.errors import describe_install_error
from ..state.events import (
    EventDispatcher,
    LogMessage,
    ModsLoaded,
    OperationFinished,
    ProgressChanged,
    StatusChanged,
)
from ..state.models import ModPending, ModsState, ModState


class ModsController:
    """Owns the mods lifecycle; speaks to the UI only through events.

    `get_out_dir` is an optional zero-arg callable returning the current game
    folder (the Qt UI supplies its path field's getter). When omitted the
    controller reads ``out_dir`` from the on-disk config, mirroring the
    UI's default.
    """

    def __init__(self, dispatcher: EventDispatcher, get_out_dir=None):
        self._dispatcher = dispatcher
        self.state = ModsState()
        self.state.records = self._load_records()
        self._default_mods_install_started = False
        self._busy = False
        if get_out_dir is None:
            get_out_dir = lambda: config_store.load_config().get(
                "out_dir", DEFAULT_OUT_DIR)
        self._get_out_dir = get_out_dir

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def registry(self):
        """The full mod registry (name, id, description, repo_url, …) the
        panel renders from — the UI keeps its mods import-free. The remote
        catalog (when loaded) is merged with the custom file and the bundled
        fallback."""
        return mods.mods_registry()

    @property
    def updates_count(self) -> int:
        return self.state.updates_count

    @property
    def busy(self) -> bool:
        return self._busy

    def load_latest_versions(self):
        """Background-fetch every registry mod's latest release and publish a
        ModsLoaded snapshot so the panel can re-render and refresh its badge."""

        def worker():
            latest = {}
            for mod in mods.mods_registry():
                try:
                    v = mods.fetch_mod_latest_version_cached(mod)
                except Exception:
                    v = None
                if v:
                    latest[mod["id"]] = v
            self.state.latest_versions = latest
            self._refresh_updates_count()
            self._dispatcher.post(ModsLoaded(self.state))

        threading.Thread(target=worker, daemon=True).start()

    def toggle(self, mod_id: str, enabled: bool):
        self.state.pending.setdefault(mod_id, ModPending()).enabled = enabled

    def set_ignore(self, mod_id: str, ignore_updates: bool):
        self.state.pending.setdefault(
            mod_id, ModPending()).ignore_updates = ignore_updates

    def action_for(self, mod_id: str) -> str | None:
        """'retry' when the mod is in an error state, 'update' when a newer
        version is available, else None."""
        rec = self.state.records.get(mod_id)
        if rec is not None and rec.error:
            return "retry"
        mod = next((m for m in mods.mods_registry() if m["id"] == mod_id), None)
        if mod is None:
            return None
        live = {"latest_version": self.state.latest_versions.get(mod_id)}
        if mods.mod_update_available(mod, self._state_dict(mod_id) or {}, live):
            return "update"
        return None

    def apply(self, only_mod_id: str | None = None) -> bool:
        if self._busy:
            return False
        out = (self._get_out_dir() or "").strip()
        if not out:
            return False
        self._busy = True
        threading.Thread(target=self._apply_worker, args=(out, only_mod_id),
                         daemon=True).start()
        return True

    def maybe_install_essential_mods(self) -> bool:
        """One-shot auto-install of every essential mod for a fresh game
        folder. Re-armed by reset() (a game-folder change). Returns True when
        an install actually started."""
        if self._default_mods_install_started:
            return False

        cfg = config_store.load_config()
        if cfg.get("mods"):
            return False  # already initialized for this folder

        out = (self._get_out_dir() or "").strip()
        if not out or not os.path.exists(os.path.join(out, "WoW.exe")):
            return False  # game isn't actually installed here yet

        self._default_mods_install_started = True

        # "Install essential mods" (Settings → General) gates the essential
        # set. Every mod comes from the configured catalog — nothing is
        # hardcoded.
        auto_mods = cfg.get("auto_install_mods", True)
        for mod in mods.mods_registry():
            if mod.get("essential", False) and auto_mods:
                self.state.pending.setdefault(
                    mod["id"], ModPending()).enabled = True

        self._dispatcher.post(LogMessage("\nInstalling essential mods...\n",
                                         "acct"))
        self.apply()
        return True

    def invalidate(self):
        """Drop the cached latest versions; the next load refetches."""
        self.state.latest_versions = {}

    def reset(self):
        """Clear pending changes, drop cached versions and re-arm the one-shot
        auto-install (called when the game folder changes)."""
        self.state.pending = {}
        self.state.latest_versions = {}
        self.state.updates_count = 0
        self._default_mods_install_started = False
        self.state.records = self._load_records()

    # ── internals ───────────────────────────────────────────────────────────

    def _load_records(self) -> dict[str, ModState]:
        records = {}
        for mid, rec in config_store.load_config().get("mods", {}).items():
            records[mid] = ModState(
                enabled=rec.get("enabled", False),
                installed_version=rec.get("installed_version"),
                installed_files=list(rec.get("installed_files", [])),
                ignore_updates=rec.get("ignore_updates", False),
                error=rec.get("error"),
            )
        return records

    def _state_dict(self, mid: str) -> dict | None:
        """The config-record dict shape mod_update_available expects, from the
        controller's ModState (None when the mod has no record)."""
        rec = self.state.records.get(mid)
        if rec is None:
            return None
        return {
            "enabled": rec.enabled,
            "installed_version": rec.installed_version,
            "installed_files": rec.installed_files,
            "ignore_updates": rec.ignore_updates,
            "error": rec.error,
        }

    def _refresh_updates_count(self):
        count = 0
        for mod in mods.mods_registry():
            state = self._state_dict(mod["id"])
            if state is None or state.get("error"):
                continue
            live = {"latest_version": self.state.latest_versions.get(mod["id"])}
            if mods.mod_update_available(mod, state, live):
                count += 1
        self.state.updates_count = count

    def _apply_worker(self, client_dir: str, only_mod_id: str | None = None):
        try:
            self._dispatcher.post(ProgressChanged(0.0, ""))
            self._dispatcher.post(StatusChanged("Downloading mods…"))

            # Work on a detached copy of the "mods" section; it's merged back
            # into the live config atomically at the end (update_config).
            mods_cfg = config_store.load_config().get("mods", {})

            # The registry's declared order is install order.
            ordered = mods.mods_registry()

            pending = self.state.pending
            # Arm the one-time "DXVK first launch" notice if dxvk gets
            # (re)installed this run — the new d3d9.dll invalidates the shader
            # cache.
            set_dxvk_notice = False

            for mod in ordered:
                mid = mod["id"]
                if only_mod_id is not None and mid != only_mod_id:
                    continue
                state = mods_cfg.get(mid, {})

                # Read enabled/ignore from pending UI changes first, fall back
                # to saved config. No registry-default fallback here: a mod is
                # only ever "enabled" because the user (or the one-time
                # default-mods seed) explicitly said so.
                pend = pending.get(mid)
                enabled = (pend.enabled
                           if pend is not None and pend.enabled is not None
                           else state.get("enabled", False))
                ignore_upd = (pend.ignore_updates
                              if pend is not None
                              and pend.ignore_updates is not None
                              else state.get("ignore_updates", False))

                # A targeted single-mod update/retry always means "install this
                # mod". Without this, retrying a failed install is a no-op: the
                # error handler recorded enabled=False, so needs_install stays
                # False and the mod is skipped (only its error gets cleared).
                if only_mod_id is not None and mid == only_mod_id:
                    enabled = True

                installed_ver = state.get("installed_version")
                is_installed = (bool(installed_ver) and
                                mods.mod_installed_files_present(mod,
                                                                 client_dir))

                needs_install   = enabled and not is_installed
                needs_uninstall = not enabled and is_installed

                needs_version_lookup = needs_install or (
                    enabled and is_installed and not ignore_upd)
                latest_ver  = None
                mod_release = None
                if needs_version_lookup:
                    try:
                        if mod["source"]["kind"] in ("github_release",
                                                     "codeberg_release"):
                            mod_release = mods._fetch_release_cached(mod)
                            latest_ver = (mods._release_version(mod, mod_release)
                                          if mod_release else None)
                        else:
                            latest_ver = mods.fetch_mod_latest_version_cached(mod)
                    except Exception:
                        pass

                update_avail = (is_installed and
                                latest_ver is not None and
                                latest_ver != installed_ver and
                                not ignore_upd)
                needs_update = enabled and update_avail

                if not (needs_install or needs_uninstall or needs_update):
                    if mid in pending:
                        mods_cfg.setdefault(mid, {}).update({
                            "enabled":        enabled,
                            "ignore_updates": ignore_upd,
                        })
                    # A previously failed mod that the user leaves disabled on
                    # a later Apply counts as dismissed — clear the error so it
                    # stops blocking the PLAY button.
                    if not enabled and state.get("error"):
                        mods_cfg.setdefault(mid, {})["error"] = None
                    continue

                action = ("Installing" if needs_install else
                          "Updating" if needs_update else "Removing")
                self._dispatcher.post(
                    StatusChanged(f"{action} {mod['name']}…"))

                try:
                    if needs_install:
                        self._dispatcher.post(LogMessage(
                            f"\nInstalling {mod['name']} {latest_ver}..."))
                        written = mods.install_mod(
                            mod, client_dir, release=mod_release)
                        if mod.get("register_dll"):
                            mods.add_dll(client_dir, mod["register_dll"])
                        resolved_ver = (mod.pop("_resolved_version", None)
                                        or latest_ver or "unknown")
                        mods_cfg[mid] = {
                            "enabled":           True,
                            "installed_version": resolved_ver,
                            "installed_files":   written,
                            "ignore_updates":    ignore_upd,
                            "error":             None,
                        }
                        if mid == "dxvk":
                            set_dxvk_notice = True
                        self._dispatcher.post(LogMessage(
                            f"  \u2713 {mod['name']} installed."))

                    elif needs_uninstall:
                        self._dispatcher.post(LogMessage(
                            f"\nUninstalling {mod['name']}..."))
                        mods.uninstall_mod(mod, client_dir)
                        if mod.get("register_dll"):
                            mods.remove_dll(client_dir, mod["register_dll"])
                        mods_cfg[mid] = {
                            "enabled":           False,
                            "installed_version": None,
                            "installed_files":   [],
                            "ignore_updates":    ignore_upd,
                            "error":             None,
                        }
                        self._dispatcher.post(LogMessage(
                            f"  \u2713 {mod['name']} uninstalled."))

                    elif needs_update:
                        self._dispatcher.post(LogMessage(
                            f"\nUpdating {mod['name']} "
                            f"{installed_ver} \u2192 {latest_ver}..."))
                        mods.uninstall_mod(mod, client_dir)
                        written = mods.install_mod(
                            mod, client_dir, release=mod_release)
                        if mod.get("register_dll"):
                            mods.add_dll(client_dir, mod["register_dll"])
                        mods_cfg[mid] = {
                            "enabled":           True,
                            "installed_version": latest_ver,
                            "installed_files":   written,
                            "ignore_updates":    ignore_upd,
                            "error":             None,
                        }
                        if mid == "dxvk":
                            set_dxvk_notice = True
                        self._dispatcher.post(LogMessage(
                            f"  \u2713 {mod['name']} updated."))

                except Exception as e:
                    err = describe_install_error(e)
                    self._dispatcher.post(LogMessage(
                        f"  \u2717 {mod['name']}: {err}"))
                    mods_cfg[mid] = {
                        "enabled":           False,
                        "installed_version": None,
                        "installed_files":   [],
                        "ignore_updates":    ignore_upd,
                        "error":             err,
                    }

            # Merge just the "mods" key into the current on-disk config (which
            # other threads may have written to during this long install),
            # rather than saving our stale whole-config snapshot.
            sorted_mods = dict(sorted(mods_cfg.items(),
                                      key=lambda kv: kv[0].lower()))

            def _merge(c):
                c["mods"] = sorted_mods
                if set_dxvk_notice:
                    c["dxvk_notice_pending"] = True

            config_store.update_config(_merge)
            # Keep pending checkbox toggles on a targeted single-mod update —
            # they were never applied and would be lost otherwise.
            if only_mod_id is None:
                self.state.pending = {}
            self.state.records = self._load_records()
            self._refresh_updates_count()

            self._dispatcher.post(ProgressChanged(1.0, ""))
            self._dispatcher.post(ModsLoaded(self.state))
            self._busy = False
            self._dispatcher.post(OperationFinished("mods", True, ""))

        except Exception as e:
            self._busy = False
            self._dispatcher.post(ProgressChanged(0.0, ""))
            self._dispatcher.post(OperationFinished("mods", False, str(e)))
