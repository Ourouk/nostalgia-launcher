"""Update workflow: staged client update (initial → incremental).

Orchestrator facade: ``VerifyWorker``/``UpdateWorker`` keep their public
API (constructor, ``run()``, ``cancel()``) and delegate each phase to
the stage modules:

- :mod:`stages_initial` — one-off base acquisition (has_exe check,
  fallback availability);
- :mod:`stages_manifest` — manifest-overlay path split/tag helpers;
- :mod:`stages_torrent` — incremental torrent policy;
- :mod:`stages_static` — server-bundled registry entry paths;
- :mod:`ownership` — server-base vs community-overlay file ownership
  (server wins collisions, user paths never touched by server syncs).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...state.events import EventDispatcher

from ...core.config_store import load_cache, save_cache
from ...core.filesystem import get_client_version, remove_wdb
from ...state.events import (
    ClientVersionReady,
    Event,
    TorrentCorrupt,
    TorrentDiffReady,
    TorrentDiskError,
    TorrentReachable,
    TorrentRecoveryDone,
    TorrentSessionError,
    TorrentStalled,
    TorrentUnavailable,
    TorrentUpToDate,
    TorrentVerifyFailed,
    UpdateCompleted,
    UpdateFailed,
    VerificationUpToDate,
)
from ..tweaks import write_config_wtf, write_realmlist_wtf
from ..update_backend.sources import (
    DownloadSource,  # noqa: F401 (re-export)
    _download_source,
)
from ..update_backend.torrent_update import available as _torrent_lib_available
from ..update_backend.worker_base import WorkerBase
from . import stages_manifest as _manifest_stage
from . import stages_torrent as _torrent_stage
from .http import download_file
from .ownership import (
    check_server_write as _check_server_write,
)
from .ownership import (
    collisions as _collisions,
)
from .ownership import (
    snapshot_manifest_paths as _snapshot_manifest_paths,
)
from .ownership import (
    snapshot_user_records as _snapshot_user_records,
)
from .ownership import (
    user_paths_from_records as _user_paths_from_records,
)

TORRENT_VALIDATION_CACHE_KEY = "__torrent_validation__"


def _torrent_available() -> bool:
    """Whether the BitTorrent backend can run (libtorrent installed)."""
    try:
        return _torrent_lib_available()
    except Exception:
        return False


def torrent_recovery_available() -> bool:  # noqa: F401 (re-export)
    """Whether a manifest-less full re-download via BitTorrent is possible."""
    from ...core import launcher

    cfg = launcher.config()
    return bool(cfg and cfg.has_torrent() and _torrent_available())


def _safe_identity(snapshot) -> dict | None:
    try:
        return {
            "content_hash": snapshot.content_hash,
            "info_hash": snapshot.info_hash or "",
        }
    except Exception:
        return None


class VerifyWorker(WorkerBase):
    """Verify local files against the BitTorrent snapshot only."""

    def __init__(
        self,
        out_dir: str,
        dispatcher: EventDispatcher,
        overwrite_config: bool = False,
        source=None,
    ) -> None:
        super().__init__(out_dir, dispatcher)
        self._cache: dict[str, object] = load_cache()
        self.overwrite_config: bool = overwrite_config
        self._source = source

    def run(self) -> None:
        try:
            self.progress(0.0, "Verifying…", phase="Verifying")
            self.log("Verifying files...", "acct")
            src = (
                self._source
                if self._source is not None
                else _download_source()
            )
            if src is None:
                raise RuntimeError("No download source configured.")
            cfg_wtf = os.path.join(self.out_dir, "WTF", "Config.wtf")
            if self.overwrite_config or not os.path.exists(cfg_wtf):
                write_config_wtf(self.out_dir)
                write_realmlist_wtf(self.out_dir)
            if self._cancel:
                self._cancel_torrent_verify()
                return
            # Manifest overlay: server-owned files over the base client.
            # Runs before/alongside torrent verify; posts TorrentDiffReady
            # with manifest: paths so the update path is uniform.
            if self._manifest_verify():
                return
            # No torrent configured → fallback-only for first install.
            if not src.torrent_locator:
                self._handle_no_torrent("No BitTorrent source configured.")
                return
            if not _torrent_available():
                self._handle_no_torrent("libtorrent not available.")
                return
            # Torrent path (offline verification, cache-aware).
            if self._torrent_verify(src):
                return
            # _torrent_verify always returns True when it handled
            # the verification (including error posts).
            self._handle_no_torrent("Torrent verification unavailable.")
        except Exception as e:
            if self._cancel:
                self._cancel_torrent_verify()
                return
            self.log(f"Verification failed: {e}", "err")
            self._dispatcher.post(UpdateFailed(message=str(e), op="verify"))

    def _handle_no_torrent(self, message: str) -> None:
        """No torrent incremental path — signal fallback-only.

        For an existing install (game exe present) treat as up-to-date
        w.r.t. incremental updates (HTTP diff is forbidden). For a fresh
        folder (no game exe) signal TorrentUnavailable so the controller
        can offer the single-zip HTTP fallback via UpdateWorker.
        """
        if self._cancel:
            self._cancel_torrent_verify()
            return
        from ...core import launcher as _launcher0
        from ...core.filesystem import game_executable_exists as _gee0

        has_exe = _gee0(
            self.out_dir, _launcher0.client_executable() or "WoW.exe"
        )
        self.log(f"{message} — torrent unavailable.", "err")
        self._dispatcher.post(TorrentUnavailable(message=message))
        if has_exe:
            self.log(
                "No incremental BitTorrent source — "
                "client updates via torrent unavailable.",
                "dim",
            )
            self.progress(0.0, "", phase="Verified")
            try:
                save_cache(self._cache)
            except Exception:
                pass
            self._dispatcher.post(VerificationUpToDate())
        else:
            # First-install folder: fallback zip will handle it.
            fallback = ""
            src = self._source or _download_source()
            if src is not None:
                fallback = getattr(src, "fallback_url", "") or ""
            if fallback:
                self.log(
                    "First install — HTTP fallback zip available via UPDATE.",
                    "dim",
                )
            else:
                self.log(
                    "No download source for first install.",
                    "err",
                )
            self.progress(0.0, "", phase="Verified")

    def _torrent_verify(self, src) -> bool:
        if src is None or not src.torrent_locator:
            return False
        if not _torrent_available():
            return False
        from ..update_backend.torrent_update import (
            TorrentCorruptError,
            TorrentFetchError,
            TorrentStalledError,
            TorrentVerifier,
            _fetch_torrent,
            remove_resume_data,
        )
        from ..update_backend.torrent_update import (
            TorrentDiskError as TorrentDiskErrorExc,
        )
        from ..update_backend.torrent_update import (
            TorrentSessionError as TorrentSessionErrorExc,
        )

        cached = self._cache.get(TORRENT_VALIDATION_CACHE_KEY)
        cache_matches = isinstance(cached, dict) and cached.get(
            "out_dir"
        ) == os.path.abspath(self.out_dir)
        identity: dict | None = None
        if cache_matches:
            try:
                snapshot = _fetch_torrent(
                    src.torrent_locator,
                    self.log,
                    cancel=lambda: self._cancel,
                )
            except TorrentCorruptError as e:
                return self._post_torrent_error(
                    TorrentCorrupt(message=str(e)),
                    e,
                    f"Torrent file corrupt: {e}",
                    "No usable torrent snapshot — update unavailable.",
                )
            except TorrentFetchError as e:
                return self._post_torrent_error(
                    TorrentUnavailable(message=str(e)),
                    e,
                    f"BitTorrent snapshot unreachable: {e}",
                    "No reachable torrent — "
                    "update unavailable via BitTorrent.",
                )
            identity = _safe_identity(snapshot)
            if identity is None:
                return self._torrent_recheck_failed(
                    RuntimeError("snapshot metadata could not be parsed")
                )
            assert isinstance(cached, dict)
            same_content = (
                cached.get("content_hash") == identity["content_hash"]
            )
            old_hash = cached.get("info_hash")
            new_hash = identity["info_hash"]
            same_identity = (
                bool(old_hash) and bool(new_hash) and (old_hash == new_hash)
            )
            if same_content or same_identity:
                if not same_content:
                    self.log(
                        "[torrent] Snapshot metadata bytes "
                        "changed but its identity is unchanged.",
                        "dim",
                    )
                self.log(
                    "[torrent] Snapshot unchanged since last "
                    "verify — skipping re-scan and reusing "
                    "cached verdict.",
                    "dim",
                )
                assert isinstance(cached, dict)
                _stale_raw = cached.get("stale", [])
                if isinstance(_stale_raw, list):
                    stale = sorted(s for s in _stale_raw if isinstance(s, str))
                else:
                    stale = []
                self._cache[TORRENT_VALIDATION_CACHE_KEY] = {
                    **identity,
                    "url": src.torrent_locator,
                    "out_dir": os.path.abspath(self.out_dir),
                    "stale": stale,
                }
                self._persist_torrent_validation()
                self.log(
                    "[torrent] Validation verdict reused from cache.",
                    "dim",
                )
                self._post_torrent_verdict(stale)
                return True
            if old_hash and new_hash and old_hash != new_hash:
                try:
                    remove_resume_data(old_hash)
                except Exception as e:
                    self.log(
                        f"[torrent] Could not discard old resume data: {e}",
                        "dim",
                    )
                self.log(
                    "[torrent] Snapshot changed at URL — "
                    "discarding old validation state and "
                    "resume data "
                    f"({old_hash[:12]}… → {new_hash[:12]}…).",
                    "dim",
                )
            elif old_hash and not new_hash:
                self.log(
                    "[torrent] Snapshot identity unavailable — "
                    "validation state cleared.",
                    "dim",
                )
            else:
                self.log(
                    "[torrent] Cached verdict has no usable "
                    "identity — full re-scan required.",
                    "dim",
                )
        else:
            snapshot = None
            self.log(
                "[torrent] No cached verdict — full re-scan required.",
                "dim",
            )
        try:
            verifier = TorrentVerifier(
                self.out_dir, dispatcher=self._dispatcher
            )
            self.log(
                "Verifying client against the BitTorrent snapshot…",
                "acct",
            )
            if snapshot is None:
                stale = verifier.verify(src.torrent_locator)
            else:
                stale = verifier.verify(src.torrent_locator, snapshot)
        except TorrentCorruptError as e:
            return self._post_torrent_error(
                TorrentCorrupt(message=str(e)),
                e,
                f"Torrent file corrupt: {e}",
                "No usable torrent snapshot — update unavailable.",
            )
        except TorrentFetchError as e:
            return self._post_torrent_error(
                TorrentUnavailable(message=str(e)),
                e,
                f"BitTorrent snapshot unreachable: {e}",
                "No reachable torrent — update unavailable via BitTorrent.",
            )
        except TorrentStalledError as e:
            return self._torrent_verify_failed(
                e,
                TorrentStalled(message=str(e)),
                f"BitTorrent verification stalled: {e}",
                "Torrent verification stalled — update unavailable.",
            )
        except TorrentSessionErrorExc as e:
            return self._torrent_verify_failed(
                e,
                TorrentSessionError(message=str(e)),
                f"BitTorrent session error: {e}",
                "Torrent session error — update unavailable.",
            )
        except TorrentDiskErrorExc as e:
            return self._torrent_verify_failed(
                e,
                TorrentDiskError(message=str(e)),
                f"Disk I/O error: {e}",
                "Disk error — update unavailable.",
            )
        except Exception as e:
            return self._torrent_verify_failed(
                e,
                TorrentVerifyFailed(message=str(e)),
                f"BitTorrent verification failed: {e}",
                "Torrent snapshot fetched but verification "
                "failed — update unavailable via BitTorrent.",
            )
        if identity is None:
            snapshot = getattr(verifier, "snapshot", None)
            if snapshot is not None:
                identity = _safe_identity(snapshot) or {}
                if not identity.get("info_hash"):
                    self.log(
                        "[torrent] Snapshot identity unavailable — "
                        "verdict cached without it.",
                        "dim",
                    )
        identity = identity or {}
        self._cache[TORRENT_VALIDATION_CACHE_KEY] = {
            **identity,
            "url": src.torrent_locator,
            "out_dir": os.path.abspath(self.out_dir),
            "stale": sorted(stale),
        }
        self._persist_torrent_validation()
        self.log("[torrent] Validation verdict cached.", "dim")
        self._post_torrent_verdict(stale)
        return True

    def _torrent_recheck_failed(self, err) -> bool:
        if self._cancel:
            return self._cancel_torrent_verify()
        msg = str(err)
        self.log(f"BitTorrent verification failed: {err}", "err")
        self.log(
            "Torrent snapshot fetched but verification failed — "
            "update unavailable via BitTorrent.",
            "err",
        )
        self._dispatcher.post(TorrentVerifyFailed(message=msg))
        return True

    def _persist_torrent_validation(self):
        try:
            save_cache(self._cache)
        except Exception as e:
            self.log(
                f"[torrent] Could not persist validation cache: {e}",
                "err",
            )

    def _post_torrent_error(
        self, event, err, err_log: str, notice: str
    ) -> bool:
        if self._cancel:
            return self._cancel_torrent_verify()
        self.log(err_log, "err")
        self.log(notice, "err")
        self._dispatcher.post(event)
        return True

    def _torrent_verify_failed(
        self, err, event, err_log: str, notice: str
    ) -> bool:
        if self._cancel:
            return self._cancel_torrent_verify()
        self.log(err_log, "err")
        self.log(notice, "err")
        self._dispatcher.post(event)
        return True

    def _post_torrent_verdict(self, stale: list[str]):
        self._dispatcher.post(TorrentReachable())
        if not stale:
            self.log(
                "Everything is up to date (BitTorrent snapshot).",
                "ok",
            )
            self._dispatcher.post(TorrentUpToDate())
        else:
            self.log(
                f"Update available — {len(stale)} stale file(s) "
                f"vs the BitTorrent snapshot.",
                "acct",
            )
            self._dispatcher.post(TorrentDiffReady(stale=sorted(stale)))

    def _cancel_torrent_verify(self) -> bool:
        self.log("\nVerify cancelled.", "err")
        self._dispatcher.post(UpdateFailed(message="", op="verify"))
        return True

    def _report_overlay_collisions(self) -> None:
        """Warn when server-owned paths overlap user-installed files.

        Server wins (authoritative base) but the overwrite is never
        silent — each collision is logged before any download lands.
        Network-free: manifest paths come from the cached snapshot,
        user paths from the mod/asset state records.
        """
        try:
            server_owned = _snapshot_manifest_paths()
            user_owned = _user_paths_from_records(_snapshot_user_records())
            for path in _collisions(server_owned, user_owned):
                self.log(
                    "[ownership] Server-owned file overlaps a "
                    f"user install and will win: {path}",
                    "err",
                )
        except Exception:
            pass

    def _manifest_verify(self) -> bool:
        """Verify the manifest overlay; True when it owned the verdict.

        Returns False when no manifest is configured so the torrent path
        runs as before. A reachable manifest posts TorrentReachable +
        TorrentDiffReady(manifest:...) / TorrentUpToDate and owns the
        verdict — the torrent base is a third-party client the server
        does not version, so manifest staleness is authoritative.
        Staleness is size + sha256 per entry; the version cursor is
        informational only. Unreachable manifest → False (fall through
        to torrent logic).
        """
        from .. import manifest as _manifest

        if _manifest._manifest_block() is None:
            return False
        self._report_overlay_collisions()
        snapshot = _manifest.fetch_snapshot()
        if snapshot is None or self._cancel:
            return False
        entries = snapshot.get("entries", [])
        if not isinstance(entries, list):
            return False
        self.log(
            f"[manifest] Snapshot v{snapshot.get('version', '?')} — "
            f"{len(entries)} file(s).",
            "dim",
        )
        self._dispatcher.post(TorrentReachable())
        stale = _manifest.stale_overlay_entries(
            self.out_dir, entries, self.log
        )
        update_avail, version = _manifest.manifest_update_available(snapshot)
        if update_avail and version:
            self.log(
                f"[manifest] Version cursor moved ({version}) — "
                "staleness decided by content hash.",
                "dim",
            )
        if not stale:
            self.log("Everything is up to date (manifest).", "ok")
            self._dispatcher.post(TorrentUpToDate())
        else:
            self.log(
                f"Update available — {len(stale)} stale file(s) "
                "vs the manifest.",
                "acct",
            )
            self._dispatcher.post(
                TorrentDiffReady(
                    stale=_manifest_stage.tag_manifest_paths(stale)
                )
            )
        return True


class UpdateWorker(WorkerBase):
    """Torrent-primary updater with manifest overlay + HTTP fallback.

    Manifest-owned files (``manifest:`` paths) download over HTTPS from
    the manifest's own URLs. Torrent still covers the base client;
    HTTP fallback (single zip/rar) stays first-install only.
    """

    def __init__(
        self, out_dir: str, dispatcher: EventDispatcher, source=None
    ) -> None:
        super().__init__(out_dir, dispatcher)
        self._cache: dict[str, object] = load_cache()
        self._source = source
        self._total: int = 0
        self._downloaded: int = 0
        self._counted: dict[str, int] = {}

    def download(
        self, url: str, dest: str, size: object, name: str = ""
    ) -> str | None:
        total_ref = {"total": self._total, "downloaded": self._downloaded}
        result = download_file(
            url,
            dest,
            size,
            name,
            dispatcher=self._dispatcher,
            cache=self._cache,
            is_cancelled=lambda: self._cancel,
            log=self.log,
            progress=self.progress,
            total_ref=total_ref,
            counted=self._counted,
        )
        self._total = total_ref["total"]
        self._downloaded = total_ref["downloaded"]
        return result

    def _cancelled_abort(self) -> bool:
        self.log("\nUpdate cancelled.", "err")
        self.progress(0.0, "Cancelled")
        self._dispatcher.post(UpdateFailed(message="", op="update"))
        return True

    def _recovery_failed(
        self, err: BaseException, event: Event, err_log: str
    ) -> None:
        if self._cancel:
            raise err
        self.log(err_log, "err")
        self._dispatcher.post(event)

    def _report_client_version(self):
        try:
            from ...core import launcher as _launcher

            declared = _launcher.client_version()
        except Exception:
            declared = ""
        client_ver = declared or get_client_version(self.out_dir)
        if client_ver:
            self.log(f"Client version: {client_ver}", "dim")
            self._dispatcher.post(ClientVersionReady(version=client_ver))
        else:
            self.log("Could not read client version from WoW.exe", "dim")

    def _recovery_download(self, wanted: set[str] | None = None) -> bool:
        from ..update_backend.torrent_update import (
            TorrentCorruptError,
            TorrentDownloader,
            TorrentFetchError,
            TorrentStalledError,
        )
        from ..update_backend.torrent_update import (
            TorrentDiskError as TorrentDiskErrorExc,
        )
        from ..update_backend.torrent_update import (
            TorrentSessionError as TorrentSessionErrorExc,
        )

        assert (
            self._source is not None
            and self._source.torrent_locator is not None
        )
        dl = TorrentDownloader(self.out_dir, dispatcher=self._dispatcher)
        scope = "full client" if wanted is None else f"{len(wanted)} file(s)"
        self.log(f"[torrent] Starting recovery download ({scope}).", "acct")
        try:
            dl.download(self._source.torrent_locator, wanted)
        except TorrentCorruptError as e:
            self._recovery_failed(
                e,
                TorrentCorrupt(message=str(e)),
                f"Torrent file corrupt: {e}",
            )
            return False
        except TorrentStalledError as e:
            self._recovery_failed(
                e,
                TorrentStalled(message=str(e)),
                f"BitTorrent download stalled: {e}",
            )
            return False
        except TorrentSessionErrorExc as e:
            self._recovery_failed(
                e,
                TorrentSessionError(message=str(e)),
                f"BitTorrent session error: {e}",
            )
            return False
        except TorrentDiskErrorExc as e:
            self._recovery_failed(
                e,
                TorrentDiskError(message=str(e)),
                f"Disk I/O error: {e}",
            )
            return False
        except TorrentFetchError as e:
            self._recovery_failed(
                e,
                TorrentUnavailable(message=str(e)),
                f"Torrent unreachable during download: {e}",
            )
            return False
        except Exception as e:
            self._recovery_failed(
                e,
                TorrentVerifyFailed(message=str(e)),
                f"BitTorrent download failed: {e}",
            )
            return False
        if self._cancel:
            self._cancelled_abort()
            return False
        if wanted is not None:
            missing = sorted(
                rel
                for rel in wanted
                if not os.path.isfile(
                    os.path.join(self.out_dir, rel.replace("/", os.sep))
                )
            )
            if missing:
                self.log(
                    "[torrent] Recovery incomplete — re-downloading "
                    "the full client ("
                    f"{len(missing)} file(s) missing).",
                    "err",
                )
                try:
                    dl.download(self._source.torrent_locator, None)
                except (RuntimeError, OSError) as e:
                    self._recovery_failed(
                        e,
                        TorrentVerifyFailed(message=str(e)),
                        f"BitTorrent recovery failed: {e}",
                    )
                    return False
                if self._cancel:
                    self._cancelled_abort()
                    return False
        from ...core import launcher as _launcher1
        from ...core.filesystem import game_executable_exists as _gee

        _exe1 = _launcher1.client_executable() or "WoW.exe"
        if not _gee(self.out_dir, _exe1):
            self.log(
                f"Recovered client has no {_exe1} — update failed.", "err"
            )
            self._dispatcher.post(
                UpdateFailed(message=f"no {_exe1}", op="update")
            )
            return False
        self.log("  BitTorrent recovery download complete.", "ok")
        remove_wdb(self.out_dir)
        # Realm injection moved to Play-time with consent; keep only
        # fresh-install seeding, never silently overwrite an existing realm.
        try:
            cfg_wtf = os.path.join(self.out_dir, "WTF", "Config.wtf")
            if not os.path.exists(cfg_wtf):
                write_config_wtf(self.out_dir)
                write_realmlist_wtf(self.out_dir)
        except Exception as e:
            self.log(f"Could not seed config: {e}", "err")
        self.progress(1.0, "")
        snapshot = getattr(dl, "snapshot", None)
        identity = (
            _safe_identity(snapshot) if snapshot is not None else None
        ) or {}
        self._cache[TORRENT_VALIDATION_CACHE_KEY] = {
            **identity,
            "url": self._source.torrent_locator,
            "out_dir": os.path.abspath(self.out_dir),
            "stale": [],
        }
        save_cache(self._cache)
        self.log("[torrent] Recovery validation cached.", "dim")
        self.log(
            "\n✓  Client installed via BitTorrent (no manifest — "
            "files verified against the torrent's piece hashes).",
            "ok",
        )
        self._extract_payload()
        self._report_client_version()
        self._dispatcher.post(TorrentRecoveryDone())
        return True

    def _extract_payload(self):
        from ...core import launcher
        from ..update_backend.extract import extract_client_payload

        try:
            extract_client_payload(
                self.out_dir, launcher.download_content_type()
            )
        except Exception as e:  # pragma: no cover
            self.log(f"[extract] payload extraction failed: {e}", "err")

    def _manifest_overlay_download(self, paths: set[str]) -> bool:
        """Download manifest-owned files over HTTPS into the client.

        Returns True when every path landed (caller continues with any
        remaining torrent work); posts UpdateFailed + False on error.
        Each downloaded file is sha256-gated against its manifest
        entry — the version is recorded on success so the next verify
        is quiet.
        """
        from .. import manifest as _manifest

        snapshot = _manifest.fetch_snapshot()
        if snapshot is None:
            self.log("[manifest] Snapshot unavailable.", "err")
            self._dispatcher.post(
                UpdateFailed(message="manifest unavailable", op="update")
            )
            return False
        by_path = {
            e["path"]: e
            for e in snapshot.get("entries", [])
            if isinstance(e, dict) and isinstance(e.get("path"), str)
        }
        total = len(paths)
        try:
            server_owned = _snapshot_manifest_paths()
            user_owned = _user_paths_from_records(_snapshot_user_records())
        except Exception:
            server_owned, user_owned = set(), set()
        for i, path in enumerate(sorted(paths)):
            if self._cancel:
                self._cancelled_abort()
                return False
            if not _check_server_write(
                path, server_owned, user_owned, self.log
            ):
                self.log(
                    f"[manifest] Skipping user-managed path: {path}.", "err"
                )
                self._dispatcher.post(
                    UpdateFailed(
                        message=f"refused user path: {path}", op="update"
                    )
                )
                return False
            entry = by_path.get(path)
            if entry is None:
                self.log(f"[manifest] No entry for {path}.", "err")
                self._dispatcher.post(
                    UpdateFailed(
                        message=f"manifest entry missing: {path}",
                        op="update",
                    )
                )
                return False
            url = entry.get("download_url", "")
            size = entry.get("size", 0)
            dest = os.path.join(self.out_dir, path.replace("/", os.sep))
            self.progress(
                i / max(total, 1),
                f"Downloading {path}…",
                phase="Downloading",
                transport="HTTPS",
                current_file=path,
            )
            self.log(f"[manifest] Downloading {path}…", "acct")
            try:
                self.download(url, dest, size, path)
            except Exception as e:
                if self._cancel:
                    self._cancelled_abort()
                    return False
                self.log(f"[manifest] Download failed ({path}): {e}", "err")
                self._dispatcher.post(
                    UpdateFailed(message=str(e), op="update")
                )
                return False
            want = entry.get("sha256", "")
            want = want.strip().lower() if isinstance(want, str) else ""
            if want:
                from ...core.filesystem import sha256_file as _sha256

                try:
                    got = _sha256(dest)
                except OSError as e:
                    self.log(f"[manifest] Could not hash {path}: {e}", "err")
                    self._dispatcher.post(
                        UpdateFailed(
                            message=f"manifest sha256 mismatch: {path}",
                            op="update",
                        )
                    )
                    return False
                if got != want:
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                    self.log(f"[manifest] sha256 mismatch: {path}", "err")
                    self._dispatcher.post(
                        UpdateFailed(
                            message=f"manifest sha256 mismatch: {path}",
                            op="update",
                        )
                    )
                    return False
        _manifest.remember_installed_version(
            snapshot.get("version", ""), snapshot.get("url", "")
        )
        remove_wdb(self.out_dir)
        self.log("[manifest] Overlay download complete.", "ok")
        return True

    def _http_fallback_download(self, fallback_url: str) -> bool:
        """Single-zip HTTP fallback for first-install only.
        Downloads the archive at ``fallback_url`` into the game folder
        and extracts it. Returns True on success (UpdateCompleted
        posted), False otherwise (UpdateFailed posted).
        """
        if self._cancel:
            return self._cancelled_abort() is True

        # HTTPS is enforced inside download_file.
        from ...core import launcher as _launcher
        from ...core.helpers import redact_url

        content_type = _launcher.download_content_type()
        # Fallback archive extension: honour content_type, default .zip.
        if content_type == "rar":
            ext = ".rar"
        else:
            ext = ".zip"
        # Download to a temp name at the game-folder root so
        # extract_client_payload can find it.
        dest = os.path.join(self.out_dir, f"_fallback{ext}")
        os.makedirs(self.out_dir, exist_ok=True)
        self.log(
            f"[http] Downloading client archive via fallback: "
            f"{redact_url(fallback_url)}",
            "acct",
        )
        self.progress(
            0.02,
            "Downloading via HTTP…",
            phase="Downloading",
            transport="HTTP",
        )
        try:
            # size 0 → unknown; download_file streams + retries.
            self.download(
                fallback_url,
                dest,
                0,
                os.path.basename(fallback_url) or f"client{ext}",
            )
        except Exception as e:
            if self._cancel:
                self._cancelled_abort()
                return False
            self.log(f"[http] Fallback download failed: {e}", "err")
            self._dispatcher.post(UpdateFailed(message=str(e), op="update"))
            return False
        if self._cancel:
            return self._cancelled_abort() is True
        if not os.path.isfile(dest):
            self.log("[http] Fallback archive missing after download.", "err")
            self._dispatcher.post(
                UpdateFailed(message="fallback missing", op="update")
            )
            return False
        # If content.type is folder but we fetched a zip, force zip
        # extraction so the payload still lands.
        effective_type = content_type
        if content_type == "folder" and dest.lower().endswith(
            (".zip", ".rar")
        ):
            effective_type = "zip" if dest.lower().endswith(".zip") else "rar"
            self.log(
                f"[extract] content.type is 'folder' but fallback "
                f"is {ext} — extracting as {effective_type}.",
                "dim",
            )
        try:
            from ..update_backend.extract import extract_client_payload

            ok = extract_client_payload(self.out_dir, effective_type)
            if not ok:
                self.log(
                    "[extract] No archive found for extraction "
                    f"(type={effective_type}).",
                    "err",
                )
                self._dispatcher.post(
                    UpdateFailed(message="extract failed", op="update")
                )
                return False
        except Exception as e:
            self.log(f"[extract] Fallback extraction failed: {e}", "err")
            self._dispatcher.post(UpdateFailed(message=str(e), op="update"))
            return False
        # Remove the archive after successful extraction (extract does
        # this, but be defensive if effective_type was folder).
        try:
            if os.path.isfile(dest):
                os.remove(dest)
        except OSError:
            pass
        from ...core import launcher as _launcher2
        from ...core.filesystem import game_executable_exists as _gee2

        _exe2 = _launcher2.client_executable() or "WoW.exe"
        # Also accept external-launcher executables as playable.
        has_playable = _gee2(self.out_dir, _exe2)
        if not has_playable:
            try:
                from ...core.filesystem import pick_game_executable
                from ...services import mods as _mods

                exe2, _ = pick_game_executable(
                    self.out_dir,
                    _mods.external_launcher_executables(self.out_dir),
                    exe_name=_exe2,
                )
                has_playable = os.path.isfile(exe2)
            except Exception:
                pass
        if not has_playable:
            self.log(
                f"Fallback extracted but no {_exe2} found — update failed.",
                "err",
            )
            self._dispatcher.post(
                UpdateFailed(message=f"no {_exe2}", op="update")
            )
            return False
        remove_wdb(self.out_dir)
        # Realm injection moved to Play-time with consent.
        try:
            cfg_wtf = os.path.join(self.out_dir, "WTF", "Config.wtf")
            if not os.path.exists(cfg_wtf):
                write_config_wtf(self.out_dir)
                write_realmlist_wtf(self.out_dir)
        except Exception as e:
            self.log(f"Could not seed config: {e}", "err")
        self.progress(1.0, "")
        save_cache(self._cache)
        self._extract_payload()
        self.log("\n✓  Client installed via HTTP fallback.", "ok")
        self._report_client_version()
        self._dispatcher.post(
            UpdateCompleted(version=get_client_version(self.out_dir) or None)
        )
        return True

    def run(
        self,
        torrent_wanted: set[str] | None = None,
        recovery_full: bool = False,
    ) -> None:
        """Dispatch to the initial or incremental path.

        Kept for API stability (controller + tests call ``run``):
        ``recovery_full=True`` (or ``torrent_wanted=None``) means
        initial acquisition; otherwise the stale set drives the
        incremental path. New callers should use ``run_initial`` /
        ``run_incremental`` directly.
        """
        if recovery_full or torrent_wanted is None:
            self.run_initial()
        else:
            self.run_incremental(set(torrent_wanted))

    def run_initial(self) -> None:
        """Initial acquisition: full base-client download, once.

        Precondition: no playable client yet (the caller decides —
        ``stages_initial.has_playable_client``). BitTorrent full
        download first, single-zip HTTP fallback second. No stale-set
        logic, no incremental policy: this path always fetches the
        whole base client.
        """
        try:
            self._source = _download_source()
            if self._source is None:
                raise RuntimeError("No download source configured.")
            from ...core import launcher as _launcher

            _exe = _launcher.client_executable() or "WoW.exe"
            fallback_url = getattr(self._source, "fallback_url", "") or ""
            can_torrent = bool(
                self._source.torrent_locator and _torrent_available()
            )
            if can_torrent:
                self.progress(
                    0.02,
                    "Downloading via BitTorrent…",
                    phase="BitTorrent",
                    transport="BitTorrent",
                )
                self.log(
                    "\nStarting BitTorrent client download…\n",
                    "acct",
                )
                if self._recovery_download(None):
                    return
                if fallback_url:
                    self.log(
                        "[torrent] Torrent failed — trying HTTP fallback.",
                        "err",
                    )
                    if self._http_fallback_download(fallback_url):
                        return
                    return
                self._dispatcher.post(
                    UpdateFailed(
                        message="BitTorrent download failed",
                        op="update",
                    )
                )
                return
            if fallback_url:
                self.log(
                    "[initial] No BitTorrent source — using HTTP "
                    "fallback for first install.",
                    "dim",
                )
                if self._http_fallback_download(fallback_url):
                    return
                return
            self.log(
                f"No download source for first install (no {_exe}).",
                "err",
            )
            self._dispatcher.post(
                UpdateFailed(
                    message="No download source configured.",
                    op="update",
                )
            )
        except Exception as e:
            if self._cancel:
                self._cancelled_abort()
                return
            self.log(f"\n✗  {e}", "err")
            self.progress(0.0, "")
            self._dispatcher.post(UpdateFailed(message=str(e), op="update"))

    def run_incremental(self, wanted: set[str]) -> None:
        """Incremental update: patch a playable client toward current.

        Precondition: a playable client exists (missing files are an
        update decision, not an acquisition). Manifest-owned paths
        download over HTTPS first; the torrent remainder follows only
        when the torrent stage policy allows it. Manifest-only stale
        sets complete without touching torrent.
        """
        try:
            self._source = _download_source()
            if self._source is None:
                raise RuntimeError("No download source configured.")
            # Manifest overlay first (incremental/manifest stage):
            # manifest: paths download over HTTPS from the manifest's
            # own URLs. The remainder (if any) flows to the torrent
            # stage below.
            manifest_paths: set[str] = set()
            torrent_paths: set[str] | None = set(wanted)
            if wanted:
                manifest_paths, torrent_paths = (
                    _manifest_stage.split_manifest_paths(set(wanted))
                )
                if manifest_paths and not self._manifest_overlay_download(
                    manifest_paths
                ):
                    return
            if not torrent_paths and not manifest_paths:
                self.log(
                    "[torrent] No stale files; torrent update skipped.",
                    "ok",
                )
                self.progress(1.0, "")
                self._dispatcher.post(TorrentUpToDate())
                return
            if not torrent_paths:
                # Manifest-only update — already applied above.
                self._report_client_version()
                self._dispatcher.post(
                    UpdateCompleted(
                        version=get_client_version(self.out_dir) or None
                    )
                )
                return
            from ...core import launcher as _launcher

            cfg = _launcher.config()
            # Incremental respects the torrent stage policy: a frozen
            # flag (or magnet-only) leaves torrent-owned paths
            # unpatched — the manifest channel is the only incremental
            # path there.
            torrent_allowed = _torrent_stage.torrent_allowed(cfg)
            can_torrent = bool(
                self._source.torrent_locator
                and _torrent_available()
                and torrent_allowed
            )
            if can_torrent:
                self.progress(
                    0.02,
                    "Downloading via BitTorrent…",
                    phase="BitTorrent",
                    transport="BitTorrent",
                )
                self.log("\nStarting BitTorrent update…\n", "acct")
                if self._recovery_download(torrent_paths):
                    return
                self._dispatcher.post(
                    UpdateFailed(
                        message="BitTorrent update failed",
                        op="update",
                    )
                )
                return
            # No torrent path for incremental on an existing install —
            # HTTP fallback is first-install only and never patches.
            if torrent_allowed:
                self.log(
                    "Incremental update unavailable — BitTorrent "
                    "source not configured or libtorrent missing "
                    "(HTTP fallback is first-install only).",
                    "err",
                )
            else:
                self.log(
                    "Incremental torrent update disabled for this "
                    "source (magnet-only carries no updates) and no "
                    "manifest covers these files.",
                    "err",
                )
            self._dispatcher.post(
                UpdateFailed(
                    message=("BitTorrent unavailable for incremental update"),
                    op="update",
                )
            )
        except Exception as e:
            if self._cancel:
                self._cancelled_abort()
                return
            self.log(f"\n✗  {e}", "err")
            self.progress(0.0, "")
            self._dispatcher.post(UpdateFailed(message=str(e), op="update"))
