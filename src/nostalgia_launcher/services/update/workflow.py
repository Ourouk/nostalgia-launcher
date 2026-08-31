"""Update workflow: policy for verify and incremental update.

Orchestrates manifests, torrent and HTTP transports, verification and
recovery. Transports do I/O; this module decides what needs updating,
whether torrent can satisfy, when to fallback, and how to verify.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...state.events import EventDispatcher

from ...core.config_store import load_cache as _load_cache_impl
from ...core.config_store import save_cache as _save_cache_impl
from ...core.constants import DOWNLOAD_TIMEOUT, UA
from ...core.filesystem import get_client_version, remove_wdb, sha1_file
from ...core.security_http import allowed_download_hosts as _allowed_hosts_impl
from ...core.security_http import read_capped as _read_capped_impl
from ...core.security_http import secure_urlopen as _secure_urlopen_impl
from ...state.events import (
    ClientVersionReady,
    DiffTreeReady,
    Event,
    ManifestAvailable,
    ManifestUnavailable,
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
    UpdateRequired,
    VerificationUpToDate,
)
from ...state.manifest import FileNode, Manifest, ManifestNode, MPQNode
from ..tweaks import write_config_wtf as _write_config_wtf_impl
from ..tweaks import write_realmlist_wtf as _write_realmlist_wtf_impl
from .http import download_file
from .manifest import checked_node_rel, checked_node_size, parse_manifest
from .planner import collect_wanted, sum_needed_bytes
from .torrent import is_available as _torrent_available_impl
from .torrent import recovery_available as torrent_recovery_available
from .torrent import safe_identity as _safe_identity

TORRENT_VALIDATION_CACHE_KEY = "__torrent_validation__"


def _hu_attr(name: str, fallback):
    """Patch-aware lookup: tests monkeypatch http_update.<name>.

    Transitional shim for monkeypatch compatibility — existing tests patch
    `services.update_backend.http_update.<name>` (e.g. secure_urlopen,
    _download_source). Workflow accepts explicit `source` injection for
    production; this fallback preserves the old import path until tests
    migrate to `services.update.workflow`. Will be removed after test
    migration.
    """
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
    """Verify local files against manifest or torrent snapshot."""

    def __init__(
        self,
        out_dir: str,
        dispatcher: EventDispatcher,
        overwrite_config: bool = False,
        source=None,
    ) -> None:
        from ..update_backend.worker_base import WorkerBase as _WB

        # Reuse WorkerBase plumbing via composition of its methods
        self.out_dir: str = out_dir
        self._dispatcher: EventDispatcher = dispatcher
        self._cancel_event = threading.Event()
        self._cache: dict[str, object] = _get_load_cache()()
        self.overwrite_config: bool = overwrite_config
        self._source = source
        # Reuse WorkerBase helpers via composition
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

    def _file_ok(self, dest: str, server_hash: str) -> bool:
        return self.file_matches(dest, server_hash)

    def _traverse(
        self, node: ManifestNode, path_parts: list[str]
    ) -> ManifestNode | None:
        # Delegates to planner but keeps unsafe-path logging
        if self._cancel:
            return None
        # Use planner stale_tree for dirs but file/del/mpq need dest check
        if node["type"] == "dir":
            cur = path_parts + [node["name"]]
            stale: list[ManifestNode] = []
            for child in node["files"]:
                c = self._traverse(child, cur)
                if c is not None:
                    stale.append(c)
            if stale:
                return {"type": "dir", "name": node["name"], "files": stale}
            return None
        if node["type"] == "del":
            rel = checked_node_rel(path_parts, node["name"])
            if rel is None:
                self.log(
                    f"  Refusing unsafe manifest path: {node['name']!r}", "err"
                )
                return None
            dest = os.path.join(self.out_dir, rel)
            return node if os.path.exists(dest) else None
        if node["type"] == "file":
            rel = checked_node_rel(path_parts, node["name"])
            if rel is None:
                self.log(
                    f"  Refusing unsafe manifest path: {node['name']!r}", "err"
                )
                return None
            dest = os.path.join(self.out_dir, rel)
            return None if self._file_ok(dest, node["hash"]) else node
        if node["type"] == "mpq":
            rel = checked_node_rel(path_parts, node["name"], ".mpq")
            if rel is None:
                self.log(
                    f"  Refusing unsafe manifest path: {node['name']!r}", "err"
                )
                return None
            mpq_dest = os.path.join(self.out_dir, rel)
            return None if self._file_ok(mpq_dest, node["hash"]) else node
        return None

    def run(self) -> None:

        manifest_ok = False
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
            if not src.manifest_url:
                from ...core import launcher as _launcher

                cfg = _launcher.config()
                _can = bool(cfg is not None and cfg.torrent_update_allowed())
                if _can and self._torrent_verify(src):
                    return
                self._dispatcher.post(ManifestUnavailable())
                if torrent_recovery_available():
                    self.log(
                        "Manifest unavailable — a full re-download via "
                        "BitTorrent is available (UPDATE).",
                        "dim",
                    )
                self._dispatcher.post(DiffTreeReady(tree=None))
                return
            req = urllib.request.Request(
                src.manifest_url, headers={"User-Agent": UA}
            )
            with _get_secure_urlopen()(
                req,
                timeout=DOWNLOAD_TIMEOUT,
                allowed_hosts=_get_allowed_hosts()(),
            ) as r:
                raw = json.loads(_get_read_capped()(r, 16 * 1024 * 1024))
                manifest = parse_manifest(raw)
            manifest_ok = True
            self._dispatcher.post(ManifestAvailable())
            self.progress(0.0, "Verifying…", phase="Verifying")
            stale_nodes: list[ManifestNode] = []
            for child in manifest["root"]["files"]:
                c = self._traverse(child, [])
                if c is not None:
                    stale_nodes.append(c)
            self.progress(0.0, "", phase="Verified")
            _get_save_cache()(self._cache)
            if stale_nodes:
                self.log("Update available.", "acct")
                self._dispatcher.post(UpdateRequired())
                self._dispatcher.post(DiffTreeReady(tree=stale_nodes))
            else:
                self.log("Everything is up to date!", "ok")
                self._dispatcher.post(VerificationUpToDate())
        except Exception as e:
            self.log(f"Verification failed: {e}", "err")
            if not manifest_ok:
                if self._torrent_verify(src):
                    return
                self._dispatcher.post(ManifestUnavailable(message=str(e)))
                if torrent_recovery_available():
                    self.log(
                        "Manifest unavailable — a full re-download via "
                        "BitTorrent is available (UPDATE).",
                        "dim",
                    )
            else:
                self._dispatcher.post(UpdateRequired())
            self._dispatcher.post(DiffTreeReady(tree=None))

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
                    src.torrent_locator, self.log, cancel=lambda: self._cancel
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
                    "No manifest and no reachable torrent — update unavailable via BitTorrent.",
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
                        "[torrent] Snapshot metadata bytes changed but its identity is unchanged.",
                        "dim",
                    )
                self.log(
                    "[torrent] Snapshot unchanged since last verify — skipping re-scan and reusing cached verdict.",
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
                    "[torrent] Validation verdict reused from cache.", "dim"
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
                    "[torrent] Snapshot changed at URL — discarding old validation state and resume data "
                    f"({old_hash[:12]}… → {new_hash[:12]}…).",
                    "dim",
                )
            elif old_hash and not new_hash:
                self.log(
                    "[torrent] Snapshot identity unavailable — validation state cleared.",
                    "dim",
                )
            else:
                self.log(
                    "[torrent] Cached verdict has no usable identity — full re-scan required.",
                    "dim",
                )
        else:
            snapshot = None
            self.log(
                "[torrent] No cached verdict — full re-scan required.", "dim"
            )
        try:
            verifier = TorrentVerifier(
                self.out_dir, dispatcher=self._dispatcher
            )
            self.log(
                "Manifest unavailable — verifying client against the BitTorrent snapshot…",
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
                "No manifest and no reachable torrent — update unavailable via BitTorrent.",
            )
        except TorrentStalledError as e:
            return self._torrent_verify_failed(
                e,
                TorrentStalled(message=str(e)),
                f"BitTorrent verification stalled: {e}",
                "No manifest and torrent verification stalled — update unavailable.",
            )
        except TorrentSessionErrorExc as e:
            return self._torrent_verify_failed(
                e,
                TorrentSessionError(message=str(e)),
                f"BitTorrent session error: {e}",
                "No manifest and torrent session error — update unavailable.",
            )
        except TorrentDiskErrorExc as e:
            return self._torrent_verify_failed(
                e,
                TorrentDiskError(message=str(e)),
                f"Disk I/O error: {e}",
                "No manifest and disk error — update unavailable.",
            )
        except Exception as e:
            return self._torrent_verify_failed(
                e,
                TorrentVerifyFailed(message=str(e)),
                f"BitTorrent verification failed: {e}",
                "Torrent snapshot fetched but verification failed — update unavailable via BitTorrent.",
            )
        if identity is None:
            snapshot = getattr(verifier, "snapshot", None)
            if snapshot is not None:
                identity = _safe_identity(snapshot) or {}
                if not identity.get("info_hash"):
                    self.log(
                        "[torrent] Snapshot identity unavailable — verdict cached without it.",
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
            "Torrent snapshot fetched but verification failed — update unavailable via BitTorrent.",
            "err",
        )
        self._dispatcher.post(TorrentVerifyFailed(message=msg))
        return True

    def _persist_torrent_validation(self):
        try:
            _get_save_cache()(self._cache)
        except Exception as e:
            self.log(
                f"[torrent] Could not persist validation cache: {e}", "err"
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
            self.log("Everything is up to date (BitTorrent snapshot).", "ok")
            self._dispatcher.post(TorrentUpToDate())
        else:
            self.log(
                f"Update available — {len(stale)} stale file(s) vs the BitTorrent snapshot.",
                "acct",
            )
            self._dispatcher.post(TorrentDiffReady(stale=sorted(stale)))

    def _cancel_torrent_verify(self) -> bool:
        self.log("\nVerify cancelled.", "err")
        self._dispatcher.post(UpdateFailed(message="", op="verify"))
        return True


class UpdateWorker:
    """Orchestrates incremental update: torrent-first, HTTP fallback, verify."""

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
        # Transport: delegate to http.download_file, maintain aggregate counters
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
        # Sync aggregate
        self._total = total_ref["total"]
        self._downloaded = total_ref["downloaded"]
        self._sync_cache()
        return result

    def _skip_download(self, node: FileNode | MPQNode, dest: str) -> bool:
        return self.file_matches(dest, node["hash"])

    def _torrent_download(self, nodes: list[ManifestNode] | None) -> bool:
        src = self._source
        if src is None:
            src = _get_download_source()()
            self._source = src
        if src is None or not src.torrent_locator:
            return False
        assert src.torrent_locator is not None
        if not _get_torrent_available()():
            self.log("  libtorrent not available — using HTTP.", "dim")
            return False
        wanted: set[str] = set()
        if nodes is not None:
            wanted = collect_wanted(
                nodes,
                self.file_matches,
                self.out_dir,
                self.log,
                lambda: self._cancel,
            )
        if not wanted:
            return False
        self.log(
            f"\n[torrent] Downloading {len(wanted)} stale file(s): {', '.join(sorted(wanted))}\n",
            "acct",
        )
        try:
            from ..update_backend.torrent_update import TorrentDownloader

            dl = TorrentDownloader(self.out_dir, dispatcher=self._dispatcher)
            dl.download(src.torrent_locator, wanted)
            self._torrent_wanted = wanted
            self.log("[torrent] BitTorrent download complete.", "ok")
            return True
        except RuntimeError as e:
            if self._cancel:
                raise
            self.log(f"[torrent] BitTorrent download failed: {e}", "err")
            self.log("[torrent] Falling back to HTTP downloads.", "err")
            return False

    def _collect_wanted(
        self, node: ManifestNode, path_parts: list[str], wanted: set[str]
    ) -> None:
        if self._cancel:
            return
        if node["type"] == "dir":
            cur = path_parts + [node["name"]]
            for child in node["files"]:
                self._collect_wanted(child, cur, wanted)
        elif node["type"] == "file":
            rel = checked_node_rel(path_parts, node["name"])
            if rel is None:
                self.log(
                    f"  Refusing unsafe manifest path: {node['name']!r}", "err"
                )
                return
            dest = os.path.join(self.out_dir, rel)
            if not self._skip_download(node, dest):
                wanted.add(rel)
        elif node["type"] == "mpq":
            rel = checked_node_rel(path_parts, node["name"], ".mpq")
            if rel is None:
                self.log(
                    f"  Refusing unsafe manifest path: {node['name']!r}", "err"
                )
                return
            dest = os.path.join(self.out_dir, rel)
            if not self._skip_download(node, dest):
                wanted.add(rel)

    def _reverify_torrent_files(
        self, nodes: list[ManifestNode] | None
    ) -> None:
        if nodes is None:
            return
        wanted = self._torrent_wanted
        if not wanted:
            return
        suspects: list[tuple[FileNode | MPQNode, str]] = []

        def walk(node: ManifestNode, cur: list[str]) -> None:
            if self._cancel:
                return
            if node["type"] == "dir":
                for child in node["files"]:
                    walk(child, cur + [node["name"]])
            elif node["type"] == "file":
                rel = "/".join(cur + [node["name"]])
                if rel in wanted:
                    suspects.append((node, rel))
            elif node["type"] == "mpq":
                rel = "/".join(cur + [node["name"]]) + ".mpq"
                if rel in wanted:
                    suspects.append((node, rel))

        for child in nodes:
            walk(child, [])
        self._total = sum(
            checked_node_size(node["size"]) for node, _ in suspects
        )
        self._downloaded = 0
        self.log(
            f"[torrent] Re-verifying {len(suspects)} file(s) against the manifest…",
            "acct",
        )
        src = self._source
        assert src is not None and src.client_url
        for node, rel in suspects:
            dest = os.path.join(self.out_dir, rel)
            if self._skip_download(node, dest):
                continue
            url = f"{src.client_url}/{rel}"
            got_hash = self.download(url, dest, node["size"], rel)
            if (got_hash or sha1_file(dest)) != node["hash"]:
                raise RuntimeError(
                    f"Hash mismatch after torrent download: {rel}"
                )
        self.log("[torrent] All files verified against manifest.", "ok")
        self._torrent_wanted = set()

    def _sum_needed_bytes(self, nodes: list[ManifestNode]) -> int:
        return sum_needed_bytes(
            nodes, self.file_matches, self.out_dir, lambda: self._cancel
        )

    def _download_verified(
        self, node: FileNode | MPQNode, url: str, dest: str, rel: str
    ) -> None:
        got_hash = self.download(url, dest, node["size"], rel)
        if (got_hash or sha1_file(dest)) == node["hash"]:
            return
        self.log("  Hash mismatch — retrying", "err")
        os.remove(dest)
        got_hash = self.download(url, dest, node["size"], rel)
        if (got_hash or sha1_file(dest)) != node["hash"]:
            raise RuntimeError(f"Hash mismatch after redownload: {rel}")

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

    def traverse(self, node: ManifestNode, path_parts: list[str]) -> None:
        if self._cancel:
            return
        if self._source is None:
            self._source = _get_download_source()()
        src = self._source
        if src is None:
            raise RuntimeError("No download source configured.")
        if node["type"] == "dir":
            cur = path_parts + [node["name"]]
            for child in node["files"]:
                self.traverse(child, cur)
            return
        if node["type"] == "file" or node["type"] == "mpq":
            t = node["type"]
            name = node["name"]
            fname = f"{name}.mpq" if t == "mpq" else name
            rel = checked_node_rel(path_parts, fname)
            if rel is None:
                self.log(f"  Refusing unsafe manifest path: {fname!r}", "err")
                return
            dest = os.path.join(self.out_dir, rel)
            if not src.client_url:
                raise RuntimeError("No HTTP client URL — use BitTorrent")
            url = f"{src.client_url}/{rel}"
            self.log(f"[{t}]".ljust(7) + f"{rel}", "acct")
            if self._skip_download(node, dest):
                self.log("  Already up to date.", "dim")
                return
            self._download_verified(node, url, dest, rel)
        elif node["type"] == "del":
            name = node["name"]
            rel = checked_node_rel(path_parts, name)
            if rel is None:
                self.log(f"  Refusing unsafe manifest path: {name!r}", "err")
                return
            self.log(f"[del]  {rel}", "dim")
            dest = os.path.join(self.out_dir, rel)
            if os.path.exists(dest):
                try:
                    os.remove(dest)
                except OSError as e:
                    self.log(f"  Could not remove {rel}: {e}", "err")

    def _recovery_download(self, wanted: set[str] | None = None):
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
                e, TorrentCorrupt(message=str(e)), f"Torrent file corrupt: {e}"
            )
            return
        except TorrentStalledError as e:
            self._recovery_failed(
                e,
                TorrentStalled(message=str(e)),
                f"BitTorrent download stalled: {e}",
            )
            return
        except TorrentSessionErrorExc as e:
            self._recovery_failed(
                e,
                TorrentSessionError(message=str(e)),
                f"BitTorrent session error: {e}",
            )
            return
        except TorrentDiskErrorExc as e:
            self._recovery_failed(
                e, TorrentDiskError(message=str(e)), f"Disk I/O error: {e}"
            )
            return
        except TorrentFetchError as e:
            self._recovery_failed(
                e,
                TorrentUnavailable(message=str(e)),
                f"Torrent unreachable during download: {e}",
            )
            return
        except Exception as e:
            self._recovery_failed(
                e,
                TorrentVerifyFailed(message=str(e)),
                f"BitTorrent download failed: {e}",
            )
            return
        if self._cancel:
            return self._cancelled_abort()
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
                    "[torrent] Recovery incomplete — re-downloading the full client ("
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
                    return
                if self._cancel:
                    return self._cancelled_abort()
        exe = os.path.join(self.out_dir, "WoW.exe")
        if not os.path.isfile(exe):
            self.log("Recovered client has no WoW.exe — update failed.", "err")
            self._dispatcher.post(
                UpdateFailed(message="no WoW.exe", op="update")
            )
            return
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
            "\n✓  Client installed via BitTorrent (no manifest — files verified against the torrent's piece hashes).",
            "ok",
        )
        self._extract_payload()
        self._report_client_version()
        self._dispatcher.post(TorrentRecoveryDone())

    def _extract_payload(self):
        from ...core import launcher
        from ..update_backend.extract import extract_client_payload

        try:
            extract_client_payload(
                self.out_dir, launcher.download_content_type()
            )
        except Exception as e:  # pragma: no cover
            self.log(f"[extract] payload extraction failed: {e}", "err")

    def run(
        self,
        diff_nodes: list[ManifestNode] | None = None,
        torrent_wanted: set[str] | None = None,
        recovery_full: bool = False,
    ) -> None:
        try:
            torrent_recovery = False
            nodes: list[ManifestNode] = []
            manifest: Manifest | None = None
            if diff_nodes is not None:
                self.log("\nStarting client update…\n")
                self.progress(0.05, "Downloading…", phase="Downloading")
                self._cache.pop(TORRENT_VALIDATION_CACHE_KEY, None)
                nodes = diff_nodes
            elif recovery_full:
                self.progress(
                    0.02,
                    "Downloading via BitTorrent…",
                    phase="BitTorrent",
                    transport="BitTorrent",
                )
                self.log("\nStarting BitTorrent client download…\n", "acct")
                self._source = _get_download_source()()
                if self._source is None or not self._source.torrent_locator:
                    raise RuntimeError("No BitTorrent source configured.")
                self._recovery_download(None)
                return
            elif torrent_wanted is not None:
                if not torrent_wanted:
                    self.log(
                        "[torrent] No stale files; torrent update skipped.",
                        "ok",
                    )
                    self.progress(1.0, "")
                    self._dispatcher.post(TorrentUpToDate())
                    return
                self.progress(
                    0.02,
                    "Downloading via BitTorrent…",
                    phase="BitTorrent",
                    transport="BitTorrent",
                )
                self.log("\nStarting BitTorrent update…\n", "acct")
                self._source = _get_download_source()()
                if self._source is None or not self._source.torrent_locator:
                    raise RuntimeError("No BitTorrent source configured.")
                self._recovery_download(torrent_wanted)
                return
            else:
                self.progress(
                    0.02, "Fetching manifest…", phase="Fetching manifest"
                )
                self.log("Fetching manifest.json…")
                self._source = _get_download_source()()
                if self._source is None:
                    raise RuntimeError("No download source configured.")
                if not self._source.manifest_url:
                    from ...core import launcher as _launcher2

                    cfg2 = _launcher2.config()
                    _can_upd = bool(
                        cfg2 is not None and cfg2.torrent_update_allowed()
                    )
                    if (
                        self._source.torrent_locator
                        and _get_torrent_available()()
                        and _can_upd
                    ):
                        torrent_recovery = True
                    else:
                        raise RuntimeError(
                            "No manifest and no BitTorrent update source."
                        )
                else:
                    try:
                        req = urllib.request.Request(
                            self._source.manifest_url,
                            headers={"User-Agent": UA},
                        )
                        with _get_secure_urlopen()(
                            req,
                            timeout=DOWNLOAD_TIMEOUT,
                            allowed_hosts=_get_allowed_hosts()(),
                        ) as r:
                            raw = json.loads(
                                _get_read_capped()(r, 16 * 1024 * 1024)
                            )
                            manifest = parse_manifest(raw)
                    except Exception:
                        if (
                            self._source.torrent_locator
                            and _get_torrent_available()()
                        ):
                            self.log(
                                "\nManifest unavailable — downloading the client via BitTorrent…",
                                "acct",
                            )
                            torrent_recovery = True
                        else:
                            raise
                if not torrent_recovery:
                    self._cache.pop(TORRENT_VALIDATION_CACHE_KEY, None)
                    self._dispatcher.post(ManifestAvailable())
                    self.log("Manifest received.", "ok")
                    self.progress(0.05, "Downloading…", phase="Downloading")
                    self.log("\nStarting client update…\n")
                    assert manifest is not None
                    nodes = manifest["root"]["files"]
            if torrent_recovery:
                self._recovery_download(torrent_wanted)
                return
            if self._source is None:
                self._source = _get_download_source()()
            if self._source is None:
                raise RuntimeError("No download source configured.")
            ran_torrent = self._torrent_download(nodes)
            if ran_torrent:
                self._reverify_torrent_files(nodes)
            else:
                self._total = self._sum_needed_bytes(nodes)
                self._downloaded = 0
                for child in nodes:
                    self.traverse(child, [])
            if self._cancel:
                self._cancelled_abort()
                return
            self.log("\nDownload complete.", "ok")
            remove_wdb(self.out_dir)
            try:
                from ..tweaks import load_tweaks_config, update_config_wtf
                from ..tweaks import write_config_wtf as _wcf
                from ..tweaks import write_realmlist_wtf as _wrl

                cfg_wtf = os.path.join(self.out_dir, "WTF", "Config.wtf")
                if not os.path.exists(cfg_wtf):
                    _wcf(self.out_dir)
                else:
                    update_config_wtf(self.out_dir, load_tweaks_config())
                _wrl(self.out_dir)
            except Exception as e:
                self.log(f"Could not inject realm: {e}", "err")
            self.progress(1.0, "")
            _get_save_cache()(self._cache)
            self._extract_payload()
            self.log("\n✓  Everything is up to date!", "ok")
            self._report_client_version()
            self._dispatcher.post(
                UpdateCompleted(
                    version=get_client_version(self.out_dir) or None
                )
            )
        except Exception as e:
            self.log(f"\n✗  {e}", "err")
            self.progress(0.0, "")
            self._dispatcher.post(UpdateFailed(message=str(e), op="update"))
