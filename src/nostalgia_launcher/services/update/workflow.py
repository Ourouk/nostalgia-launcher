"""Update workflow: torrent-only incremental + single-zip fallback.

Orchestrates torrent verification and download, with a single HTTP zip
fallback for first-time installs only. No manifest/planner — all
incremental updates go through BitTorrent; HTTP is only for the initial
client archive when no WoW.exe is present and torrent is unavailable.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...state.events import EventDispatcher

from ...core.config_store import load_cache as _load_cache_impl
from ...core.config_store import save_cache as _save_cache_impl
from ...core.filesystem import get_client_version, remove_wdb
from ...core.security_http import allowed_download_hosts as _allowed_hosts_impl
from ...core.security_http import read_capped as _read_capped_impl
from ...core.security_http import secure_urlopen as _secure_urlopen_impl
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
from ..tweaks import write_config_wtf as _write_config_wtf_impl
from ..tweaks import write_realmlist_wtf as _write_realmlist_wtf_impl
from .http import download_file
from .torrent import is_available as _torrent_available_impl
from .torrent import recovery_available as torrent_recovery_available
from .torrent import safe_identity as _safe_identity

TORRENT_VALIDATION_CACHE_KEY = "__torrent_validation__"


def _hu_attr(name: str, fallback):
    """Patch-aware lookup: tests monkeypatch http_update.<name>."""
    try:
        import sys

        m = sys.modules.get(
            "nostalgia_launcher.services.update_backend.http_update"
        )
        if m is not None and hasattr(m, name):
            return getattr(m, name)
    except Exception:
        pass
    return fallback


def _get_download_source():
    from ..update_backend.sources import _download_source as _impl

    return _hu_attr("_download_source", _impl)


def _get_secure_urlopen():
    return _hu_attr("secure_urlopen", _secure_urlopen_impl)


def _get_load_cache():
    return _hu_attr("load_cache", _load_cache_impl)


def _get_save_cache():
    return _hu_attr("save_cache", _save_cache_impl)


def _get_allowed_hosts():
    return _hu_attr("allowed_download_hosts", _allowed_hosts_impl)


def _get_read_capped():
    return _hu_attr("read_capped", _read_capped_impl)


def _get_write_config_wtf():
    return _hu_attr("write_config_wtf", _write_config_wtf_impl)


def _get_write_realmlist_wtf():
    return _hu_attr("write_realmlist_wtf", _write_realmlist_wtf_impl)


def _get_torrent_available():
    return _hu_attr("_torrent_available", _torrent_available_impl)


def torrent_recovery_available_compat() -> bool:
    return torrent_recovery_available()


class VerifyWorker:
    """Verify local files against the BitTorrent snapshot only."""

    def __init__(
        self,
        out_dir: str,
        dispatcher: EventDispatcher,
        overwrite_config: bool = False,
        source=None,
    ) -> None:
        from ..update_backend.worker_base import WorkerBase as _WB

        self.out_dir: str = out_dir
        self._dispatcher: EventDispatcher = dispatcher
        self._cancel_event = threading.Event()
        self._cache: dict[str, object] = _get_load_cache()()
        self.overwrite_config: bool = overwrite_config
        self._source = source
        _wb = _WB(out_dir, dispatcher)
        _wb._cache = self._cache
        self.log = _wb.log  # type: ignore
        self.progress = _wb.progress  # type: ignore
        self.file_matches = _wb.file_matches  # type: ignore
        self._raise_cancelled = _wb._raise_cancelled  # type: ignore
        self._wb = _wb

    @property
    def _cancel(self) -> bool:
        return self._cancel_event.is_set() or self._wb._cancel

    @_cancel.setter
    def _cancel(self, value: bool) -> None:
        if value:
            self._cancel_event.set()
            self._wb._cancel = True
        else:
            self._cancel_event.clear()
            self._wb._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        src = (
            self._source
            if self._source is not None
            else _get_download_source()()
        )
        try:
            self.progress(0.0, "Verifying…", phase="Verifying")
            self.log("Verifying files...", "acct")
            src = (
                self._source
                if self._source is not None
                else _get_download_source()()
            )
            if src is None:
                raise RuntimeError("No download source configured.")
            cfg_wtf = os.path.join(self.out_dir, "WTF", "Config.wtf")
            if self.overwrite_config or not os.path.exists(cfg_wtf):
                _get_write_config_wtf()(self.out_dir)
                _get_write_realmlist_wtf()(self.out_dir)
            if self._cancel:
                self._cancel_torrent_verify()
                return
            # No torrent configured → fallback-only for first install.
            if not src.torrent_locator:
                self._handle_no_torrent("No BitTorrent source configured.")
                return
            if not _get_torrent_available()():
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

        For an existing install (WoW.exe present) treat as up-to-date
        w.r.t. incremental updates (HTTP diff is forbidden). For a fresh
        folder (no WoW.exe) signal TorrentUnavailable so the controller
        can offer the single-zip HTTP fallback via UpdateWorker.
        """
        if self._cancel:
            self._cancel_torrent_verify()
            return
        has_exe = os.path.isfile(os.path.join(self.out_dir, "WoW.exe"))
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
                _get_save_cache()(self._cache)
            except Exception:
                pass
            self._dispatcher.post(VerificationUpToDate())
        else:
            # First-install folder: fallback zip will handle it.
            fallback = ""
            src = self._source or _get_download_source()()
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
        if not _get_torrent_available()():
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
            _get_save_cache()(self._cache)
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


class UpdateWorker:
    """Torrent-primary updater with single-zip HTTP fallback.

    Incremental updates are torrent-only. HTTP fallback (single zip/rar
    via ``server.download.http.fallback``) is only for first-time
    installs when no WoW.exe is present and torrent is unavailable or
    failed.
    """

    def __init__(
        self, out_dir: str, dispatcher: EventDispatcher, source=None
    ) -> None:
        from ..update_backend.worker_base import WorkerBase as _WB

        self.out_dir: str = out_dir
        self._dispatcher: EventDispatcher = dispatcher
        self._cancel_event = threading.Event()
        self._cache: dict[str, object] = _get_load_cache()()
        self._source = source
        self._total: int = 0
        self._downloaded: int = 0
        self._counted: dict[str, int] = {}
        self._torrent_wanted: set[str] = set()
        _wb = _WB(out_dir, dispatcher)
        _wb._cache = self._cache
        self.log = _wb.log  # type: ignore
        self.progress = _wb.progress  # type: ignore
        self.file_matches = _wb.file_matches  # type: ignore
        self._raise_cancelled = _wb._raise_cancelled  # type: ignore
        self._wb = _wb

    @property
    def _cancel(self) -> bool:
        return self._cancel_event.is_set() or self._wb._cancel

    @_cancel.setter
    def _cancel(self, value: bool) -> None:
        if value:
            self._cancel_event.set()
            self._wb._cancel = True
        else:
            self._cancel_event.clear()
            self._wb._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def _sync_cache(self) -> None:
        self._cache = self._wb._cache
        self._wb._cache = self._cache

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
        self._sync_cache()
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
        client_ver = get_client_version(self.out_dir)
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
        exe = os.path.join(self.out_dir, "WoW.exe")
        if not os.path.isfile(exe):
            self.log("Recovered client has no WoW.exe — update failed.", "err")
            self._dispatcher.post(
                UpdateFailed(message="no WoW.exe", op="update")
            )
            return False
        self.log("  BitTorrent recovery download complete.", "ok")
        remove_wdb(self.out_dir)
        try:
            cfg_wtf = os.path.join(self.out_dir, "WTF", "Config.wtf")
            if not os.path.exists(cfg_wtf):
                _get_write_config_wtf()(self.out_dir)
            else:
                from ..tweaks import load_tweaks_config, update_config_wtf

                update_config_wtf(self.out_dir, load_tweaks_config())
            _get_write_realmlist_wtf()(self.out_dir)
        except Exception as e:
            self.log(f"Could not inject realm: {e}", "err")
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
        _get_save_cache()(self._cache)
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

    def _http_fallback_download(self, fallback_url: str) -> bool:
        """Single-zip HTTP fallback for first-install only.

        Downloads the archive at ``fallback_url`` into the game folder
        and extracts it. Returns True on success (UpdateCompleted
        posted), False otherwise (UpdateFailed posted).
        """
        if self._cancel:
            return self._cancelled_abort() is True

        # Per-hop host allowlist is enforced inside download_file.
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
        exe = os.path.join(self.out_dir, "WoW.exe")
        # Also accept external-launcher executables as playable.
        has_playable = os.path.isfile(exe)
        if not has_playable:
            try:
                from ...core.filesystem import pick_game_executable
                from ...services import mods as _mods

                exe2, _ = pick_game_executable(
                    self.out_dir,
                    _mods.external_launcher_executables(self.out_dir),
                )
                has_playable = os.path.isfile(exe2)
            except Exception:
                pass
        if not has_playable:
            self.log(
                "Fallback extracted but no WoW.exe found — update failed.",
                "err",
            )
            self._dispatcher.post(
                UpdateFailed(message="no WoW.exe", op="update")
            )
            return False
        remove_wdb(self.out_dir)
        try:
            cfg_wtf = os.path.join(self.out_dir, "WTF", "Config.wtf")
            if not os.path.exists(cfg_wtf):
                _get_write_config_wtf()(self.out_dir)
            else:
                from ..tweaks import load_tweaks_config, update_config_wtf

                update_config_wtf(self.out_dir, load_tweaks_config())
            _get_write_realmlist_wtf()(self.out_dir)
        except Exception as e:
            self.log(f"Could not inject realm: {e}", "err")
        self.progress(1.0, "")
        _get_save_cache()(self._cache)
        self._extract_payload()
        self.log("\n✓  Client installed via HTTP fallback.", "ok")
        self._report_client_version()
        self._dispatcher.post(
            UpdateCompleted(version=get_client_version(self.out_dir) or None)
        )
        return True

    def run(
        self,
        diff_nodes: list | None = None,
        torrent_wanted: set[str] | None = None,
        recovery_full: bool = False,
    ) -> None:
        # diff_nodes is legacy manifest diff — ignored (torrent-only).
        _ = diff_nodes
        try:
            self._source = _get_download_source()()
            if self._source is None:
                raise RuntimeError("No download source configured.")
            # torrent_wanted is the stale set from VerifyWorker.
            wanted: set[str] | None
            if recovery_full:
                wanted = None
            else:
                wanted = torrent_wanted
            if wanted is not None and len(wanted) == 0:
                self.log(
                    "[torrent] No stale files; torrent update skipped.",
                    "ok",
                )
                self.progress(1.0, "")
                self._dispatcher.post(TorrentUpToDate())
                return
            from ...core import launcher as _launcher

            cfg = _launcher.config()
            # Incremental respects torrent.update flag; full download
            # (first install) does not.
            torrent_allowed = True
            if not recovery_full and cfg is not None:
                torrent_allowed = cfg.torrent_update_allowed()
            can_torrent = bool(
                self._source.torrent_locator
                and _get_torrent_available()()
                and torrent_allowed
            )
            has_exe = os.path.isfile(os.path.join(self.out_dir, "WoW.exe"))
            # Also consider external launchers for playability.
            if not has_exe:
                try:
                    from ...core.filesystem import (
                        pick_game_executable,
                    )
                    from ...services import mods as _mods

                    exe2, _ = pick_game_executable(
                        self.out_dir,
                        _mods.external_launcher_executables(self.out_dir),
                    )
                    has_exe = os.path.isfile(exe2)
                except Exception:
                    pass
            fallback_url = getattr(self._source, "fallback_url", "") or ""
            if can_torrent:
                # Try torrent first (incremental or full).
                if recovery_full:
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
                else:
                    self.progress(
                        0.02,
                        "Downloading via BitTorrent…",
                        phase="BitTorrent",
                        transport="BitTorrent",
                    )
                    self.log("\nStarting BitTorrent update…\n", "acct")
                ok = self._recovery_download(wanted)
                if ok:
                    return
                # Torrent failed — HTTP fallback only for first
                # install.
                if not has_exe and fallback_url:
                    self.log(
                        "[torrent] Torrent failed — trying HTTP fallback.",
                        "err",
                    )
                    if self._http_fallback_download(fallback_url):
                        return
                    return
                if has_exe:
                    self._dispatcher.post(
                        UpdateFailed(
                            message="BitTorrent update failed",
                            op="update",
                        )
                    )
                return
            # No torrent path — fallback only for first install.
            if not has_exe and fallback_url:
                self.log(
                    "[torrent] No BitTorrent source — using HTTP "
                    "fallback for first install.",
                    "dim",
                )
                if self._http_fallback_download(fallback_url):
                    return
                return
            # Incremental without torrent on existing install → fail.
            if has_exe:
                self.log(
                    "Incremental update unavailable — BitTorrent "
                    "source not configured or libtorrent missing "
                    "(HTTP fallback is first-install only).",
                    "err",
                )
                self._dispatcher.post(
                    UpdateFailed(
                        message=(
                            "BitTorrent unavailable for incremental update"
                        ),
                        op="update",
                    )
                )
                return
            self.log(
                "No download source configured — cannot download client.",
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
