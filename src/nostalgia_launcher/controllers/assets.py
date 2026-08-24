"""Assets panel controller.

Owns the ASSETS-panel business logic: the background staleness refresh
(probes run off-thread), the update-available count, pending checkbox
changes and the install/uninstall/update worker. Publishes snapshots as
AssetsLoaded and the worker outcome as OperationFinished("assets", …) on
the shared EventDispatcher; the Qt panel renders them. No GUI toolkit.
"""

import os
import threading

from ..core import config_store
from ..core.errors import describe_install_error
from ..services import assets
from ..state.events import (
    AssetsLoaded,
    EventDispatcher,
    LogMessage,
    OperationFinished,
    ProgressChanged,
    StatusChanged,
)
from ..state.models import AssetPending, AssetsState, AssetState


class AssetsController:
    """Owns the assets lifecycle; speaks to the UI only through events.

    `get_out_dir` is an optional zero-arg callable returning the current
    game folder (the Qt UI supplies its path field's getter). When omitted
    the controller reads ``out_dir`` from the on-disk config, mirroring the
    UI's default.
    """

    def __init__(self, dispatcher: EventDispatcher, get_out_dir=None):
        self._dispatcher = dispatcher
        self.state = AssetsState()
        self._busy = False
        if get_out_dir is None:

            def get_out_dir():
                return config_store.load_config().get("out_dir", "")

        self._get_out_dir = get_out_dir
        self.state.records = self._load_records()

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def registry(self):
        """The full asset registry (id, name, url, dest, update metadata…)
        the panel renders from — the UI keeps its assets import-free."""
        return assets.assets_registry()

    @property
    def updates_count(self) -> int:
        return self.state.updates_count

    @property
    def busy(self) -> bool:
        return self._busy

    def refresh_verdicts(self):
        """Background-refresh every asset's staleness verdict (a probe may
        hit the network) and publish an AssetsLoaded snapshot so the panel
        re-renders and its badge updates. The catalog itself is served from
        the persisted cache and only refetched once it is older than the
        weekly `catalog.CATALOG_TTL`; failed fetches fall back to it."""

        def worker():
            try:
                registry = assets.assets_registry(
                    force=assets.catalog_is_stale()
                )
            except Exception:
                try:
                    registry = assets.assets_registry() or []
                except Exception:
                    registry = []
            out = (self._get_out_dir() or "").strip()
            count = 0
            for asset in registry:
                stale, _reason = self._verdict(asset, out)
                if stale:
                    count += 1
            self.state.updates_count = count
            self._dispatcher.post(AssetsLoaded(self.state))

        threading.Thread(target=worker, daemon=True).start()

    def toggle(self, asset_id: str, enabled: bool):
        self.state.pending.setdefault(
            asset_id, AssetPending()
        ).enabled = enabled

    def reload_catalog(self) -> bool:
        """Force-refetch the assets catalog and republish the snapshot.
        Returns True when the worker actually started. When the launcher
        embeds its asset list and no registry URL is configured there is
        nothing to refetch: republish instantly instead of failing."""
        if self._busy:
            return False
        if not assets.has_remote_catalog() and assets.embedded_assets():
            self._dispatcher.post(
                LogMessage(
                    "Using the assets embedded in the launcher config.\n",
                    "dim",
                )
            )
            self._dispatcher.post(AssetsLoaded(self.state))
            return True

        def worker():
            try:
                assets.assets_registry(force=True)
            except Exception as e:
                self._dispatcher.post(
                    LogMessage(f"✗ Asset catalog reload failed: {e}\n", "err")
                )
                return
            finally:
                self._busy = False
            self._dispatcher.post(AssetsLoaded(self.state))

        self._busy = True
        threading.Thread(target=worker, daemon=True).start()
        return True

    def action_for(self, asset_id: str) -> str | None:
        """'retry' when the asset is in an error state, 'update' when a
        staleness verdict fired, else None."""
        rec = self.state.records.get(asset_id)
        if rec is not None and rec.error:
            return "retry"
        asset = next(
            (a for a in assets.assets_registry() if a["id"] == asset_id),
            None,
        )
        if asset is None:
            return None
        stale, _ = self._verdict(asset, (self._get_out_dir() or "").strip())
        return "update" if stale else None

    def apply(self, only_asset_id: str | None = None) -> bool:
        if self._busy:
            return False
        out = (self._get_out_dir() or "").strip()
        if not out:
            self._dispatcher.post(
                LogMessage("✗  Please set the game folder first.\n", "err")
            )
            return False
        self._busy = True
        threading.Thread(
            target=self._apply_worker, args=(out, only_asset_id), daemon=True
        ).start()
        return True

    def invalidate(self):
        pass

    def apply_essential_assets(self) -> bool:
        """Install every essential asset not already present. Returns True
        when an install actually started."""
        if self._busy:
            return False
        out = (self._get_out_dir() or "").strip()
        if not out or not os.path.exists(os.path.join(out, "WoW.exe")):
            return False
        records_cfg = config_store.load_config().get("assets", {})
        pending = False
        for asset in assets.assets_registry():
            if not asset.get("essential", False):
                continue
            state = records_cfg.get(asset["id"], {})
            if state.get("installed_files") and self._files_present(
                state.get("installed_files"), out
            ):
                continue  # already installed
            self.toggle(asset["id"], True)
            pending = True
        if not pending:
            return False
        self._dispatcher.post(
            LogMessage("\nInstalling essential assets...\n", "acct")
        )
        return self.apply()

    def reset(self):
        """Clear pending changes and verdicts (a game-folder change)."""
        self.state.pending = {}
        self.state.updates_count = 0
        self.state.records = self._load_records()

    # ── internals ───────────────────────────────────────────────────────────

    def _verdict(self, asset: dict, client_dir: str) -> tuple[bool, str]:
        """The staleness verdict for one asset against its stored record;
        (False, '') when uninstalled or undeterminable."""
        rec = config_store.load_config().get("assets", {}).get(asset["id"])
        if rec is None:
            stored = self.state.records.get(asset["id"])
            rec = (
                {
                    "installed_version": stored.installed_version,
                    "installed_files": list(stored.installed_files),
                    "probe_state": dict(stored.probe_state),
                    "error": stored.error,
                }
                if stored is not None
                else None
            )
        try:
            return assets.asset_update_available(asset, rec, client_dir)
        except Exception:
            return False, ""

    def _load_records(self) -> dict[str, AssetState]:
        """Config "assets" records reconciled against the filesystem: a
        record whose declared files are all present counts as installed."""
        records = {}
        for aid, rec in config_store.load_config().get("assets", {}).items():
            state = AssetState(
                enabled=rec.get("enabled", False),
                installed_version=rec.get("installed_version"),
                installed_files=list(rec.get("installed_files", [])),
                probe_state=dict(rec.get("probe_state") or {}),
                error=rec.get("error"),
            )
            out = (self._get_out_dir() or "").strip()
            state.present = bool(
                out
                and state.installed_files
                and self._files_present(state.installed_files, out)
            )
            records[aid] = state
        return records

    @staticmethod
    def _files_present(files: list, client_dir: str) -> bool:
        return all(
            os.path.exists(os.path.join(client_dir, f)) for f in files or []
        )

    def _record_dict(self, aid: str) -> dict | None:
        rec = self.state.records.get(aid)
        if rec is None:
            return None
        return {
            "enabled": rec.enabled,
            "installed_version": rec.installed_version,
            "installed_files": list(rec.installed_files),
            "probe_state": dict(rec.probe_state),
            "error": rec.error,
        }

    def _refresh_updates_count(self):
        try:
            registry = assets.assets_registry()
        except Exception:
            registry = []
        out = (self._get_out_dir() or "").strip()
        count = 0
        for asset in registry:
            stale, _ = self._verdict(asset, out)
            if stale:
                count += 1
        self.state.updates_count = count

    def _apply_worker(self, client_dir: str, only_asset_id: str | None = None):
        try:
            self._dispatcher.post(ProgressChanged(0.0, ""))
            self._dispatcher.post(StatusChanged("Downloading assets…"))

            assets_cfg = config_store.load_config().get("assets", {})
            ordered = assets.assets_registry()
            pending = self.state.pending

            for asset in ordered:
                aid = asset["id"]
                if only_asset_id is not None and aid != only_asset_id:
                    continue
                state = assets_cfg.get(aid, {})

                pend = pending.get(aid)
                enabled = (
                    pend.enabled
                    if pend is not None and pend.enabled is not None
                    else state.get("enabled", False)
                )
                # A targeted single-asset install/update always means "do it".
                if only_asset_id is not None and aid == only_asset_id:
                    enabled = True

                installed_files = state.get("installed_files") or []
                is_installed = bool(installed_files) and self._files_present(
                    installed_files, client_dir
                )

                needs_install = enabled and not is_installed
                needs_uninstall = not enabled and is_installed
                stale, reason = (
                    self._verdict(asset, client_dir)
                    if enabled and is_installed
                    else (False, "")
                )
                needs_update = enabled and is_installed and stale

                if not (needs_install or needs_uninstall or needs_update):
                    if aid in pending:
                        assets_cfg.setdefault(aid, {})["enabled"] = enabled
                    if not enabled and state.get("error"):
                        assets_cfg.setdefault(aid, {})["error"] = None
                    continue

                action = (
                    "Installing"
                    if needs_install
                    else "Updating"
                    if needs_update
                    else "Removing"
                )
                self._dispatcher.post(
                    StatusChanged(f"{action} {asset['name']}…")
                )

                try:
                    if needs_uninstall:
                        self._dispatcher.post(
                            LogMessage(f"\nUninstalling {asset['name']}...")
                        )
                        assets.remove_asset_files(installed_files, client_dir)
                        assets.forget_probe_state(aid)
                        assets_cfg[aid] = {
                            "enabled": False,
                            "installed_version": None,
                            "installed_files": [],
                            "error": None,
                        }
                        self._dispatcher.post(
                            LogMessage(f"  ✓ {asset['name']} uninstalled.")
                        )
                    else:
                        label = (
                            f"Updating {asset['name']}..."
                            if needs_update
                            else f"Installing {asset['name']}..."
                        )
                        why = f" ({reason})" if needs_update and reason else ""
                        self._dispatcher.post(LogMessage(f"\n{label}{why}"))
                        if is_installed:
                            assets.remove_asset_files(
                                installed_files, client_dir
                            )
                        result = assets.install_asset(asset, client_dir)
                        assets.remember_probe_state(aid, result["probe"])
                        assets_cfg[aid] = {
                            "enabled": True,
                            "installed_version": (
                                assets.resolved_version(asset)
                            ),
                            "installed_files": result["installed_files"],
                            "probe_state": result["probe"],
                            "error": None,
                        }
                        verb = "updated" if needs_update else "installed"
                        self._dispatcher.post(
                            LogMessage(f"  ✓ {asset['name']} {verb}.")
                        )

                except Exception as e:
                    err = describe_install_error(e)
                    self._dispatcher.post(
                        LogMessage(f"  ✗ {asset['name']}: {err}")
                    )
                    keep = [] if not is_installed else installed_files
                    assets_cfg[aid] = {
                        "enabled": False,
                        "installed_version": (
                            state.get("installed_version") if keep else None
                        ),
                        "installed_files": keep,
                        "error": err,
                    }

            sorted_assets = dict(
                sorted(assets_cfg.items(), key=lambda kv: kv[0].lower())
            )
            config_store.update_config(
                lambda c: c.__setitem__("assets", sorted_assets)
            )
            if only_asset_id is None:
                self.state.pending = {}
            self.state.records = self._load_records()
            self._refresh_updates_count()

            self._dispatcher.post(ProgressChanged(1.0, ""))
            self._dispatcher.post(AssetsLoaded(self.state))
            self._busy = False
            self._dispatcher.post(OperationFinished("assets", True, ""))

        except Exception as e:
            self._busy = False
            self._dispatcher.post(ProgressChanged(0.0, ""))
            self._dispatcher.post(OperationFinished("assets", False, str(e)))
