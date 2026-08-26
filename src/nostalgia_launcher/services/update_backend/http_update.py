"""HTTP client update backend: manifest verification and incremental update.

`VerifyWorker` fetches the manifest from the selected download source and
reports which files differ. `UpdateWorker` downloads (resumably) the changed
files, verifies SHA-1s, and clears the WDB cache. When the manifest cannot be
files over HTTPS. `UpdateWorker` downloads (resumably) the changed files,
verifies SHA-1s, and clears the WDB cache. When the manifest cannot be
fetched but the active source advertises a BitTorrent snapshot (an HTTPS
``torrent_url`` or a ``torrent_magnet``, with libtorrent available) the
update falls back to a full BitTorrent recovery download of the client
files, verified against the torrent's piece hashes. Both speak to the GUI
exclusively through the log/progress queues using the documented message
protocol.
"""

import hashlib
import json
import os
import queue
import shutil
import time
import urllib.request

from ...core.config_store import load_cache, save_cache
from ...core.constants import (
    DOWNLOAD_RETRY,
    DOWNLOAD_TIMEOUT,
    UA,
)
from ...core.filesystem import (
    get_client_version,
    remove_wdb,
    sha1_file,
)
from ...core.helpers import fmt_size, fmt_speed
from ...core.security_http import (
    allowed_download_hosts,
    read_capped,
    secure_urlopen,
)
from ..tweaks import write_config_wtf, write_realmlist_wtf
from . import markers
from .sources import DownloadSource, _download_source
from .worker_base import WorkerBase

# Re-exported for compatibility: controllers and tests resolve these through
# this module (and monkeypatch `_download_source` on it).
__all__ = [
    "DownloadSource",
    "UpdateWorker",
    "VerifyWorker",
    "torrent_recovery_available",
    "_download_source",
]


TORRENT_VALIDATION_CACHE_KEY = "__torrent_validation__"


def _torrent_identity(snapshot) -> dict:
    """The identity fields a torrent validation-cache record is keyed on."""
    return {
        "content_hash": snapshot.content_hash,
        "info_hash": snapshot.info_hash or "",
    }


def _safe_identity(snapshot) -> "dict | None":
    """``_torrent_identity`` that never raises: a snapshot with malformed
    metadata yields ``None`` so callers can route to their failure path
    instead of dying mid-fallback."""
    try:
        return _torrent_identity(snapshot)
    except Exception:
        return None


def _torrent_available() -> bool:
    """Whether the BitTorrent backend can run (libtorrent installed)."""
    try:
        from .torrent_update import available

        return available()
    except Exception:
        return False


def torrent_recovery_available() -> bool:
    """Whether a manifest-less full re-download via BitTorrent is possible:
    some configured source advertises a torrent snapshot (HTTPS URL or
    server magnet) and libtorrent is importable. Network-free (no mirror
    probing) so it's safe to call from the readiness path."""
    from ...core import launcher

    cfg = launcher.config()
    return bool(cfg and cfg.has_torrent() and _torrent_available())


class VerifyWorker(WorkerBase):
    def __init__(
        self,
        out_dir: str,
        log_q: queue.Queue,
        prog_q: queue.Queue,
        overwrite_config: bool = False,
    ):
        super().__init__(out_dir, log_q, prog_q)
        self.overwrite_config = overwrite_config
        self._cache: dict = load_cache()

    def _file_ok(self, dest, server_hash):
        return self.file_matches(dest, server_hash)

    def _traverse(self, node, path_parts):
        if self._cancel:
            return None
        t = node["type"]
        name = node["name"]
        cur = path_parts + [name]

        if t == "dir":
            stale = [
                c
                for child in node.get("files", [])
                if (c := self._traverse(child, cur)) is not None
            ]
            return {**node, "files": stale} if stale else None

        dest = os.path.join(self.out_dir, os.path.join(*cur))

        if t == "del":
            return node if os.path.exists(dest) else None

        if t == "file":
            return None if self._file_ok(dest, node["hash"]) else node

        if t == "mpq":
            mpq_dest = os.path.join(
                self.out_dir, os.path.join(*(path_parts + [name + ".mpq"]))
            )
            return None if self._file_ok(mpq_dest, node["hash"]) else node

        return None

    def run(self):
        manifest_ok = False
        try:
            self.progress(0.0, "Verifying…", phase="Verifying")
            self.log("Verifying files...", "acct")
            src = _download_source()
            if src is None:
                raise RuntimeError("No download source configured.")
            # Config.wtf isn't part of the manifest — it's user game config.
            # Create it when missing, or overwrite it when the user
            # committed to this folder. realmlist.wtf rides along so a
            # fresh client folder points at the configured realm too.
            # Deliberately done *before* the manifest fetch: a manifest-less
            # (torrent-verified or play-only) setup must still get its
            # realm configuration.
            cfg_wtf = os.path.join(self.out_dir, "WTF", "Config.wtf")
            if self.overwrite_config or not os.path.exists(cfg_wtf):
                write_config_wtf(self.out_dir)
                write_realmlist_wtf(self.out_dir)
            req = urllib.request.Request(
                src.manifest_url, headers={"User-Agent": UA}
            )
            with secure_urlopen(req, timeout=DOWNLOAD_TIMEOUT) as r:
                manifest = json.loads(read_capped(r, 16 * 1024 * 1024))
            manifest_ok = True
            self.log_q.put((markers.MANIFEST_AVAILABLE, ""))
            self.progress(0.0, "Verifying…", phase="Verifying")

            stale_nodes = [
                c
                for child in manifest["root"].get("files", [])
                if (c := self._traverse(child, [])) is not None
            ]

            # The bar is reserved for the actual download of the files that
            # need updating; verification only reports its phase, never a 0→100
            # sweep over the whole client.
            self.progress(0.0, "", phase="Verified")
            save_cache(self._cache)

            if stale_nodes:
                self.log("Update available.", "acct")
                self.log_q.put((markers.UPDATE_NEEDED, ""))
                self.log_q.put((markers.DIFF_TREE, stale_nodes))
            else:
                self.log("Everything is up to date!", "ok")
                self.log_q.put((markers.UP_TO_DATE, ""))
        except Exception as e:
            self.log(f"Verification failed: {e}", "err")
            # A failed manifest fetch must not masquerade as "update needed":
            # the controller uses __MANIFEST_UNAVAILABLE__ to gray out the
            # update button. Failures *after* the manifest parsed are a
            # genuine "update needed" verdict.
            if not manifest_ok:
                if self._torrent_verify(src):
                    return
                self.log_q.put((markers.MANIFEST_UNAVAILABLE, ""))
                if torrent_recovery_available():
                    self.log(
                        "Manifest unavailable — a full re-download via "
                        "BitTorrent is available (UPDATE).",
                        "dim",
                    )
            else:
                self.log_q.put((markers.UPDATE_NEEDED, ""))
            self.log_q.put((markers.DIFF_TREE, None))

    def _torrent_verify(self, src) -> bool:
        """When no manifest is available, verify the client against the
        torrent's piece hashes (libtorrent recheck) and report the stale
        files. Returns True when the torrent check ran and its verdict was
        posted; False when it's not possible and the caller should fall back
        to the plain manifest-unavailable path.

        The cached validation record is identity-aware: a verdict is reused
        verbatim — skipping the expensive on-disk recheck — when the freshly
        fetched snapshot carries an already-verified identity for this game
        folder: identical raw bytes (content hash) or, more leniently, an
        identical info hash, so byte-level metadata churn (trackers, creation
        date) and mirror failover to another URL serving the same snapshot do
        not trigger a rescan. When the identity differs — or no comparable
        verdict is cached — the full libtorrent recheck runs, the reason is
        logged, and resume data for a replaced identity is discarded.
        Trade-off: on-disk drift between verifies is only detected once the
        torrent's identity changes.

        Posting:
        * reachable snapshot with stale files → ``__TORRENT_REACHABLE__``
          + ``__TORRENT_DIFF__``
        * reachable snapshot, nothing stale → ``__TORRENT_REACHABLE__``
          + ``__TORRENT_UP_TO_DATE__``
        * snapshot cannot be fetched (network/TLS/allowlist) →
          ``__TORRENT_UNREACHABLE__``
        * snapshot fetched but libtorrent recheck failed →
          ``__TORRENT_VERIFY_FAILED__``
        * snapshot corrupt → ``__TORRENT_CORRUPT__``
        * verification stalled → ``__TORRENT_STALLED__``
        * session error → ``__TORRENT_SESSION_ERROR__``
        * disk I/O error → ``__TORRENT_DISK_ERROR__``"""
        if src is None or not src.torrent_locator:
            return False
        if not _torrent_available():
            return False
        from .torrent_update import (
            TorrentCorruptError,
            TorrentDiskError,
            TorrentFetchError,
            TorrentSessionError,
            TorrentStalledError,
            TorrentVerifier,
            _fetch_torrent,
            remove_resume_data,
        )

        cached = self._cache.get(TORRENT_VALIDATION_CACHE_KEY)
        cache_matches = isinstance(cached, dict) and cached.get(
            "out_dir"
        ) == os.path.abspath(self.out_dir)
        # Built from the fetched snapshot when a comparable verdict exists;
        # left as None for the no-cache path and recomputed after the recheck.
        identity: dict | None = None

        # A cached verdict for this game folder exists, so the snapshot
        # identity can be checked cheaply before deciding whether the
        # expensive on-disk recheck is needed. The URL is deliberately not
        # part of the match: mirror failover may serve the same snapshot
        # from another address.
        if cache_matches:
            # Step 1 — fetch the latest snapshot to compare its identity
            # against the cached verdict. Fetch failures here are reported as
            # snapshot-availability outcomes (corrupt / unreachable).
            try:
                snapshot = _fetch_torrent(
                    src.torrent_locator,
                    self.log,
                    cancel=lambda: self._cancel,
                )
            except TorrentCorruptError as e:
                return self._post_torrent_error(
                    markers.TORRENT_CORRUPT,
                    e,
                    f"Torrent file corrupt: {e}",
                    "No usable torrent snapshot — update unavailable.",
                )
            except TorrentFetchError as e:
                return self._post_torrent_error(
                    markers.TORRENT_UNREACHABLE,
                    e,
                    f"BitTorrent snapshot unreachable: {e}",
                    "No manifest and no reachable torrent — update "
                    "unavailable via BitTorrent.",
                )

            identity: dict | None = _safe_identity(snapshot)
            if identity is None:
                # Metadata oddities must not escape into VerifyWorker.run():
                # an exception raised this far down would kill the worker
                # thread mid-fallback (run() already consumed the manifest
                # failure). Degrade to a posted verify-failure instead so
                # the controller gets a coherent verdict.
                return self._torrent_recheck_failed(
                    RuntimeError("snapshot metadata could not be parsed")
                )

            same_content = (
                cached.get("content_hash") == identity["content_hash"]
            )
            old_hash = cached.get("info_hash")
            new_hash = identity["info_hash"]
            # Equal info hashes imply identical piece hashes and file
            # layout, so byte-level metadata churn (trackers, creation date)
            # doesn't invalidate a cached verdict.
            same_identity = (
                bool(old_hash) and bool(new_hash) and (old_hash == new_hash)
            )

            # Step 2 — snapshot with an already-verified identity for this
            # game folder: skip the on-disk recheck entirely and reuse the
            # cached verdict (the client can only have drifted if the torrent
            # itself changed).
            if same_content or same_identity:
                if not same_content:
                    self.log(
                        "[torrent] Snapshot metadata bytes changed but its "
                        "identity is unchanged.",
                        "dim",
                    )
                self.log(
                    "[torrent] Snapshot unchanged since last verify — "
                    "skipping re-scan and reusing cached verdict.",
                    "dim",
                )
                stale = sorted(cached.get("stale", []))
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

            # Step 3 — snapshot replaced (or the cached record lacks a usable
            # identity): discard stale resume data for a replaced identity,
            # say why the re-scan runs, then run the full recheck (reusing
            # the already-fetched snapshot so it isn't downloaded twice).
            if old_hash and new_hash and old_hash != new_hash:
                try:
                    remove_resume_data(old_hash)
                except Exception as e:
                    # Cleanup is best-effort: a leftover resume blob only
                    # costs disk space, it must not abort the re-scan.
                    self.log(
                        f"[torrent] Could not discard old resume data: {e}",
                        "dim",
                    )
                self.log(
                    "[torrent] Snapshot changed at URL — discarding old "
                    f"validation state and resume data ({old_hash[:12]}… "
                    f"→ {new_hash[:12]}…).",
                    "dim",
                )
            elif old_hash and not new_hash:
                self.log(
                    "[torrent] Snapshot identity unavailable — validation "
                    "state cleared.",
                    "dim",
                )
            else:
                self.log(
                    "[torrent] Cached verdict has no usable identity — "
                    "full re-scan required.",
                    "dim",
                )
        else:
            # No comparable cached verdict: the recheck fetches the snapshot
            # itself (preserving the original error semantics for every
            # fetch/verify failure path).
            snapshot = None
            self.log(
                "[torrent] No cached verdict — full re-scan required.",
                "dim",
            )

        # Run the full libtorrent recheck of the on-disk files (None snapshot
        # lets the verifier fetch it; a pre-fetched snapshot is reused).
        try:
            verifier = TorrentVerifier(self.out_dir, self.log_q, self.prog_q)
            self.log(
                "Manifest unavailable — verifying client against the "
                "BitTorrent snapshot…",
                "acct",
            )
            if snapshot is None:
                stale = verifier.verify(src.torrent_locator)
            else:
                stale = verifier.verify(src.torrent_locator, snapshot)
        except TorrentCorruptError as e:
            return self._post_torrent_error(
                markers.TORRENT_CORRUPT,
                e,
                f"Torrent file corrupt: {e}",
                "No usable torrent snapshot — update unavailable.",
            )
        except TorrentFetchError as e:
            return self._post_torrent_error(
                markers.TORRENT_UNREACHABLE,
                e,
                f"BitTorrent snapshot unreachable: {e}",
                "No manifest and no reachable torrent — update "
                "unavailable via BitTorrent.",
            )
        except TorrentStalledError as e:
            return self._torrent_verify_failed(
                e,
                markers.TORRENT_STALLED,
                f"BitTorrent verification stalled: {e}",
                "No manifest and torrent verification stalled — "
                "update unavailable.",
            )
        except TorrentSessionError as e:
            return self._torrent_verify_failed(
                e,
                markers.TORRENT_SESSION_ERROR,
                f"BitTorrent session error: {e}",
                "No manifest and torrent session error — update unavailable.",
            )
        except TorrentDiskError as e:
            return self._torrent_verify_failed(
                e,
                markers.TORRENT_DISK_ERROR,
                f"Disk I/O error: {e}",
                "No manifest and disk error — update unavailable.",
            )
        except Exception as e:
            return self._torrent_verify_failed(
                e,
                markers.TORRENT_VERIFY_FAILED,
                f"BitTorrent verification failed: {e}",
                "Torrent snapshot fetched but verification failed — "
                "update unavailable via BitTorrent.",
            )

        if identity is None:
            snapshot = getattr(verifier, "snapshot", None)
            if snapshot is not None:
                # A malformed post-recheck snapshot must not discard an
                # otherwise valid verdict — cache it without identity, so
                # the next verify re-scans instead of trusting a reuse.
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
        """Generic-Exception landing for steps that run outside the recheck
        ladder (snapshot-identity parsing). Same message + marker pairing
        as the ladder tail so every failure posts a coherent verdict."""
        return self._torrent_verify_failed(
            err,
            markers.TORRENT_VERIFY_FAILED,
            f"BitTorrent verification failed: {err}",
            "Torrent snapshot fetched but verification failed — "
            "update unavailable via BitTorrent.",
        )

    def _persist_torrent_validation(self):
        """Best-effort cache flush: a failing disk must not kill an
        otherwise finished (or reused) verification verdict."""
        try:
            save_cache(self._cache)
        except Exception as e:
            self.log(
                f"[torrent] Could not persist validation cache: {e}", "err"
            )

    def _post_torrent_error(
        self, marker, err, err_log: str, notice: str
    ) -> bool:
        """Log a torrent failure, post its ``marker``, and return ``True``.

        Shared by the duplicate ``TorrentCorruptError``/``TorrentFetchError``
        handlers (pre-fetch and recheck) so the exact same message + marker
        pairing lives in one place."""
        if self._cancel:
            return self._cancel_torrent_verify()
        self.log(err_log, "err")
        self.log(notice, "err")
        self.log_q.put((marker, str(err)))
        return True

    def _torrent_verify_failed(
        self, err, marker, err_log: str, notice: str
    ) -> bool:
        """A recheck failure that leaves the client untouched: log the error
        and its consequence, post ``marker``, report the update unavailable.
        The generic-Exception ladder tail shares this path."""
        if self._cancel:
            return self._cancel_torrent_verify()
        self.log(err_log, "err")
        self.log(notice, "err")
        self.log_q.put((marker, str(err)))
        return True

    def _post_torrent_verdict(self, stale: list[str]):
        self.log_q.put((markers.TORRENT_REACHABLE, ""))
        if not stale:
            self.log("Everything is up to date (BitTorrent snapshot).", "ok")
            self.log_q.put((markers.TORRENT_UP_TO_DATE, ""))
        else:
            self.log(
                f"Update available — {len(stale)} stale file(s) vs the "
                "BitTorrent snapshot.",
                "acct",
            )
            self.log_q.put((markers.TORRENT_DIFF, sorted(stale)))

    def _cancel_torrent_verify(self) -> bool:
        """Finish a cancelled torrent verify without reporting a failure."""
        self.log("\nVerify cancelled.", "err")
        self.log_q.put((markers.ERROR, ""))
        return True


class UpdateWorker(WorkerBase):
    def __init__(
        self,
        out_dir: str,
        log_q: queue.Queue,
        prog_q: queue.Queue,
    ):
        super().__init__(out_dir, log_q, prog_q)
        self._cache: dict = load_cache()
        self._source: DownloadSource | None = None
        # Total bytes of the files that actually need downloading, and how many
        # have been fetched so far. The update progress bar spans 0→100 across
        # exactly these (the files that need updating), not the whole client.
        self._total = 0
        self._downloaded = 0
        # Bytes already counted toward ``_downloaded`` per destination, so a
        # hash-mismatch retry (which re-downloads the same file) isn't double
        # counted.
        self._counted: dict = {}
        # Relative paths the torrent backend delivered in the last bulk
        # download (empty when HTTP was used or no manifest tree exists).
        self._torrent_wanted: set[str] = set()

    def download(self, url, dest, size, name=""):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        name = name or os.path.basename(dest)
        total_str = fmt_size(size) if size else "?"

        for attempt in range(1, DOWNLOAD_RETRY + 1):
            if self._cancel:
                raise RuntimeError("Cancelled")
            try:
                # Resume a previous partial download when one is present.
                got = os.path.getsize(tmp) if os.path.exists(tmp) else 0
                if size and got >= size:
                    os.remove(tmp)  # oversized/stale leftover — start clean
                    got = 0

                headers = {"User-Agent": UA}
                mode = "wb"
                if got:
                    headers["Range"] = f"bytes={got}-"
                    mode = "ab"
                    self.log(f"  Resuming ({fmt_size(got)} / {total_str})…")
                else:
                    self.log(f"  Downloading ({total_str})…")

                req = urllib.request.Request(url, headers=headers)
                downloaded = got
                # Hash on the fly when starting from byte 0 — saves a full
                # re-read of the file for verification. A resumed download
                # can't be hashed incrementally (the prefix wasn't seen).
                hasher = hashlib.sha1() if not got else None
                # Speed sampling over a short sliding window.
                t0 = time.monotonic()
                bytes_at_t0 = downloaded
                speed_str = ""
                with secure_urlopen(
                    req,
                    timeout=DOWNLOAD_TIMEOUT,
                    allowed_hosts=allowed_download_hosts(),
                ) as r:
                    status = getattr(r, "status", None) or r.getcode()
                    if got and status != 206:
                        # Server ignored the Range header — start over.
                        downloaded, mode = 0, "wb"
                        hasher = hashlib.sha1()
                        bytes_at_t0 = 0
                    with open(tmp, mode) as f:
                        while True:
                            if self._cancel:
                                raise RuntimeError("Cancelled")
                            chunk = r.read(256 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            if hasher is not None:
                                hasher.update(chunk)
                            downloaded += len(chunk)
                            now = time.monotonic()
                            dt = now - t0
                            if dt >= 0.5:
                                speed_str = "   •   " + fmt_speed(
                                    (downloaded - bytes_at_t0) / dt
                                )
                                t0, bytes_at_t0 = now, downloaded
                            if size:
                                if self._total:
                                    agg = self._downloaded + downloaded
                                    self.progress(
                                        agg / self._total,
                                        f"{name}   •   "
                                        f"{fmt_size(downloaded)}"
                                        f" / {total_str}{speed_str}",
                                        phase="Downloading",
                                        transport="HTTP",
                                        current_file=name,
                                        downloaded=agg,
                                        total=self._total,
                                        speed=(downloaded - bytes_at_t0) / dt
                                        if dt > 0
                                        else 0.0,
                                    )
                                else:
                                    self.progress(
                                        downloaded / size,
                                        f"{name}   •   "
                                        f"{fmt_size(downloaded)}"
                                        f" / {total_str}{speed_str}",
                                        phase="Downloading",
                                        transport="HTTP",
                                        current_file=name,
                                        downloaded=downloaded,
                                        total=size,
                                        speed=(downloaded - bytes_at_t0) / dt
                                        if dt > 0
                                        else 0.0,
                                    )

                # A dropped connection looks like a clean EOF — never accept
                # a short file as a finished download.
                if size and downloaded != size:
                    raise OSError(
                        "connection lost at "
                        f"{fmt_size(downloaded)} / {total_str}"
                    )

                shutil.move(tmp, dest)
                if self._total:
                    prev = self._counted.get(dest, 0)
                    self._counted[dest] = size
                    self._downloaded += size - prev
                    self.progress(
                        min(1.0, self._downloaded / self._total),
                        "Downloading…",
                        phase="Downloading",
                        transport="HTTP",
                        downloaded=self._downloaded,
                        total=self._total,
                    )
                if hasher is not None:
                    digest = hasher.hexdigest().upper()
                    try:
                        # Seed the verify cache so the next verify pass
                        # doesn't need to rehash this file either.
                        self._cache[dest] = [digest, os.path.getmtime(dest)]
                    except OSError:
                        self._cache.pop(dest, None)
                    return digest
                self._cache.pop(dest, None)
                return None
            except Exception as e:
                if self._cancel:
                    raise RuntimeError("Cancelled") from None
                # Keep tmp — the next attempt resumes from where this one
                # stopped instead of redownloading from zero.
                self.log(f"  Attempt {attempt} failed: {e}", "err")
                if attempt < DOWNLOAD_RETRY:
                    wait = min(2**attempt, 10)
                    part = os.path.getsize(tmp) if os.path.exists(tmp) else 0
                    self.progress(
                        (part / size) if size else 0.0,
                        f"{name} — retrying ({attempt}/{DOWNLOAD_RETRY})…",
                        phase="Retrying",
                        transport="HTTP",
                        current_file=name,
                        downloaded=part,
                        total=size,
                    )
                    self.log(f"  Retrying in {wait} s…", "dim")
                    time.sleep(wait)
        raise RuntimeError(
            f"Download failed after {DOWNLOAD_RETRY} attempts: {url}"
        )

    def _skip_download(self, node, dest) -> bool:
        """Whether a file node can be skipped because the local copy already
        matches the manifest."""
        return self.file_matches(dest, node["hash"])

    def _torrent_download(self, nodes) -> bool:
        """Bulk-download the stale files via BitTorrent when the active source
        advertises a torrent snapshot (HTTPS ``.torrent`` URL or server
        magnet) and libtorrent is available. Returns True
        when the torrent backend ran; the delivered paths are remembered in
        ``_torrent_wanted`` for the caller's manifest SHA-1 re-check
        (``_reverify_torrent_files``). Returns False when the torrent backend
        was not used (no snapshot advertised, libtorrent missing, or the
        download failed), in which case the caller falls back to
        ``traverse()`` for HTTP downloads with manifest re-verification."""
        src = self._source
        if src is None or not src.torrent_locator:
            return False
        if not _torrent_available():
            self.log("  libtorrent not available — using HTTP.", "dim")
            return False
        wanted: set[str] = set()
        if nodes is not None:
            for child in nodes:
                self._collect_wanted(child, [], wanted)
        if not wanted:
            return False
        self.log(
            f"\n[torrent] Downloading {len(wanted)} stale file(s): "
            f"{', '.join(sorted(wanted))}"
            "\n",
            "acct",
        )
        try:
            from .torrent_update import TorrentDownloader

            dl = TorrentDownloader(self.out_dir, self.log_q, self.prog_q)
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

    def _collect_wanted(self, node, path_parts, wanted):
        """Collect the relative paths of stale file/mpq nodes for the torrent
        backend, reusing the same up-to-date checks as `traverse`."""
        if self._cancel:
            return
        t = node["type"]
        cur = path_parts + [node["name"]]
        if t == "dir":
            for child in node.get("files", []):
                self._collect_wanted(child, cur, wanted)
        elif t == "file":
            rel = "/".join(cur)
            dest = os.path.join(self.out_dir, rel)
            if not self._skip_download(node, dest):
                wanted.add(rel)
        elif t == "mpq":
            rel = "/".join(cur) + ".mpq"
            dest = os.path.join(self.out_dir, rel)
            if not self._skip_download(node, dest):
                wanted.add(rel)

    def _reverify_torrent_files(self, nodes):
        """SHA-1 re-check of exactly the files BitTorrent delivered, against
        the manifest's hashes. Anything missing or mismatched is re-fetched
        over HTTPS with ``download``'s resume/retry, so a torrent bulk
        download cannot weaken the manifest's integrity guarantee. No-op when
        no manifest tree exists (the recovery path — there the torrent's
        piece hashes, received over TLS, are the only guarantee)."""
        wanted = self._torrent_wanted
        if not wanted:
            return

        suspects: list[tuple[dict, str]] = []

        def walk(node, cur):
            if self._cancel:
                return
            t = node["type"]
            if t == "dir":
                for child in node.get("files", []):
                    walk(child, cur + [node["name"]])
            elif t == "file":
                rel = "/".join(cur + [node["name"]])
                if rel in wanted:
                    suspects.append((node, rel))
            elif t == "mpq":
                rel = "/".join(cur + [node["name"]]) + ".mpq"
                if rel in wanted:
                    suspects.append((node, rel))

        for child in nodes:
            walk(child, [])

        self._total = sum(node["size"] for node, _ in suspects)
        self._downloaded = 0
        self.log(
            f"[torrent] Re-verifying {len(suspects)} file(s) against "
            "the manifest…",
            "acct",
        )
        src = self._source
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

    def _sum_needed_bytes(self, nodes) -> int:
        """Total bytes of the files that actually need downloading (those not
        already matching the manifest), so the update progress bar can span
        0→100 across exactly the files that need updating — not the whole
        client."""
        total = 0

        def walk(node, path_parts):
            nonlocal total
            if self._cancel:
                return
            t = node["type"]
            cur = path_parts + [node["name"]]
            if t == "dir":
                for child in node.get("files", []):
                    walk(child, cur)
            elif t == "file":
                dest = os.path.join(self.out_dir, os.path.join(*cur))
                if not self._skip_download(node, dest):
                    total += node["size"]
            elif t == "mpq":
                dest = os.path.join(
                    self.out_dir, os.path.join(*cur, node["name"] + ".mpq")
                )
                if not self._skip_download(node, dest):
                    total += node["size"]

        for n in nodes:
            walk(n, [])
        return total

    def _download_verified(self, node, url: str, dest: str, rel: str):
        """Download one manifest node and enforce its SHA-1: on mismatch the
        partial file is removed and fetched once more before giving up."""
        got_hash = self.download(url, dest, node["size"], rel)
        if (got_hash or sha1_file(dest)) == node["hash"]:
            return
        self.log("  Hash mismatch — retrying", "err")
        os.remove(dest)
        got_hash = self.download(url, dest, node["size"], rel)
        if (got_hash or sha1_file(dest)) != node["hash"]:
            raise RuntimeError(f"Hash mismatch after redownload: {rel}")

    def _cancelled_abort(self) -> bool:
        """Standard cancelled-update bail-out: log it, reset progress and
        post ``__ERROR__``. Always True so callers can ``return`` directly."""
        self.log("\nUpdate cancelled.", "err")
        self.progress(0.0, "Cancelled")
        self.log_q.put((markers.ERROR, ""))
        return True

    def _recovery_failed(self, err, marker, err_log: str):
        """A recovery-download failure that leaves the client as it was:
        re-raise on cancellation (the caller's handler turns that into an
        ``__ERROR__``), otherwise log and post ``marker``."""
        if self._cancel:
            raise err
        self.log(err_log, "err")
        self.log_q.put((marker, str(err)))

    def _report_client_version(self):
        """Log + post the WoW.exe version read after a finished update."""
        client_ver = get_client_version(self.out_dir)
        if client_ver:
            self.log(f"Client version: {client_ver}", "dim")
            self.log_q.put((markers.VERSION_PREFIX + client_ver, ""))
        else:
            self.log("Could not read client version from WoW.exe", "dim")

    def traverse(self, node, path_parts):

        if self._cancel:
            return
        if self._source is None:
            self._source = _download_source()
        src = self._source
        if src is None:
            raise RuntimeError("No download source configured.")
        t = node["type"]
        name = node["name"]
        cur = path_parts + [name]

        rel = os.path.join(*cur)
        dest = os.path.join(self.out_dir, rel)

        if t == "dir":
            for child in node.get("files", []):
                self.traverse(child, cur)

        elif t in ("file", "mpq"):
            # MPQ nodes sit at <name>.mpq regardless of their tree name.
            fname = f"{name}.mpq" if t == "mpq" else name
            cur = path_parts + [fname]
            rel = os.path.join(*cur)
            dest = os.path.join(self.out_dir, rel)
            url = f"{src.client_url}/{'/'.join(cur)}"
            self.log(f"[{t}]".ljust(7) + f"{rel}", "acct")

            if self._skip_download(node, dest):
                self.log("  Already up to date.", "dim")
                return
            self._download_verified(node, url, dest, rel)

        elif t == "del":
            self.log(f"[del]  {rel}", "dim")
            if os.path.exists(dest):
                os.remove(dest)

    def _recovery_download(self, wanted: set[str] | None = None):
        """Manifest-less recovery: download the client via BitTorrent.

        ``wanted`` is the set of stale file paths (from a prior torrent
        verify) to download; None means the whole torrent. The files are
        verified against the torrent's embedded piece hashes (the ``.torrent``
        itself arrived over TLS); there is no per-file manifest SHA-1 to check
        against in this degraded path. Raises on failure/cancellation, which
        the caller's except block turns into an ``__ERROR__``."""
        from .torrent_update import (
            TorrentCorruptError,
            TorrentDiskError,
            TorrentDownloader,
            TorrentFetchError,
            TorrentSessionError,
            TorrentStalledError,
        )

        dl = TorrentDownloader(self.out_dir, self.log_q, self.prog_q)
        scope = "full client" if wanted is None else f"{len(wanted)} file(s)"
        self.log(f"[torrent] Starting recovery download ({scope}).", "acct")
        try:
            dl.download(self._source.torrent_locator, wanted)
        except TorrentCorruptError as e:
            self._recovery_failed(
                e, markers.TORRENT_CORRUPT, f"Torrent file corrupt: {e}"
            )
            return
        except TorrentStalledError as e:
            self._recovery_failed(
                e,
                markers.TORRENT_STALLED,
                f"BitTorrent download stalled: {e}",
            )
            return
        except TorrentSessionError as e:
            self._recovery_failed(
                e,
                markers.TORRENT_SESSION_ERROR,
                f"BitTorrent session error: {e}",
            )
            return
        except TorrentDiskError as e:
            self._recovery_failed(
                e, markers.TORRENT_DISK_ERROR, f"Disk I/O error: {e}"
            )
            return
        except TorrentFetchError as e:
            self._recovery_failed(
                e,
                markers.TORRENT_UNREACHABLE,
                f"Torrent unreachable during download: {e}",
            )
            return
        except Exception as e:
            self._recovery_failed(
                e,
                markers.TORRENT_VERIFY_FAILED,
                f"BitTorrent download failed: {e}",
            )
            return
        if self._cancel:
            return self._cancelled_abort()
        # The stale set came from an earlier snapshot; if any wanted file is
        # still missing after a selective download, fall back to the whole
        # torrent so a snapshot change can't leave the client half-installed.
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
                    "[torrent] Recovery incomplete — re-downloading the "
                    f"full client ({len(missing)} file(s) missing).",
                    "err",
                )
                try:
                    dl.download(self._source.torrent_locator, None)
                except (RuntimeError, OSError) as e:
                    self._recovery_failed(
                        e,
                        markers.TORRENT_VERIFY_FAILED,
                        f"BitTorrent recovery failed: {e}",
                    )
                    return
                if self._cancel:
                    return self._cancelled_abort()
        # A recovered client without WoW.exe is useless — never mark ready.
        exe = os.path.join(self.out_dir, "WoW.exe")
        if not os.path.isfile(exe):
            self.log("Recovered client has no WoW.exe — update failed.", "err")
            self.log_q.put((markers.ERROR, ""))
            return
        self.log("  BitTorrent recovery download complete.", "ok")
        remove_wdb(self.out_dir)
        # A fresh recovery install has no Config.wtf — create it (a regular
        # update never touches user config, but this path has no verify step
        # to seed it). realmlist.wtf rides along for the same reason.
        cfg_wtf = os.path.join(self.out_dir, "WTF", "Config.wtf")
        if not os.path.exists(cfg_wtf):
            write_config_wtf(self.out_dir)
            write_realmlist_wtf(self.out_dir)
        self.progress(1.0, "")
        snapshot = getattr(dl, "snapshot", None)
        identity = _torrent_identity(snapshot) if snapshot is not None else {}
        self._cache[TORRENT_VALIDATION_CACHE_KEY] = {
            **identity,
            "url": self._source.torrent_locator,
            "out_dir": os.path.abspath(self.out_dir),
            "stale": [],
        }
        save_cache(self._cache)
        self.log("[torrent] Recovery validation cached.", "dim")
        self.log(
            "\n✓  Client installed via BitTorrent (no manifest — files "
            "verified against the torrent's piece hashes).",
            "ok",
        )
        self._report_client_version()
        self.log_q.put((markers.TORRENT_RECOVERY_DONE, ""))

    def run(self, diff_nodes=None, torrent_wanted: set[str] | None = None):

        try:
            torrent_recovery = False
            if diff_nodes is not None:
                self.log("\nStarting client update…\n")
                self.progress(0.05, "Downloading…", phase="Downloading")
                self._cache.pop(TORRENT_VALIDATION_CACHE_KEY, None)
                nodes = diff_nodes
            elif torrent_wanted is not None:
                # A prior torrent verification already established the stale
                # paths. Do not probe the manifest again: this is explicitly
                # the manifest-less BitTorrent update path.
                if not torrent_wanted:
                    self.log(
                        "[torrent] No stale files; torrent update skipped.",
                        "ok",
                    )
                    self.progress(1.0, "")
                    self.log_q.put((markers.TORRENT_UP_TO_DATE, ""))
                    return
                self.progress(
                    0.02,
                    "Downloading via BitTorrent…",
                    phase="BitTorrent",
                    transport="BitTorrent",
                )
                self.log("\nStarting BitTorrent update…\n", "acct")
                self._source = _download_source()
                if self._source is None or not self._source.torrent_locator:
                    raise RuntimeError("No BitTorrent source configured.")
                self._recovery_download(torrent_wanted)
                return
            else:
                self.progress(
                    0.02, "Fetching manifest…", phase="Fetching manifest"
                )
                self.log("Fetching manifest.json…")
                self._source = _download_source()
                if self._source is None:
                    raise RuntimeError("No download source configured.")
                try:
                    req = urllib.request.Request(
                        self._source.manifest_url,
                        headers={"User-Agent": UA},
                    )
                    with secure_urlopen(
                        req,
                        timeout=DOWNLOAD_TIMEOUT,
                        allowed_hosts=allowed_download_hosts(),
                    ) as r:
                        manifest = json.loads(read_capped(r, 16 * 1024 * 1024))
                except Exception:
                    # Manifest unavailable — fall back to a BitTorrent
                    # recovery download instead of failing outright.
                    if self._source.torrent_locator and _torrent_available():
                        self.log(
                            "\nManifest unavailable — downloading the client "
                            "via BitTorrent…",
                            "acct",
                        )
                        torrent_recovery = True
                    else:
                        raise
                if not torrent_recovery:
                    self._cache.pop(TORRENT_VALIDATION_CACHE_KEY, None)
                    self.log_q.put((markers.MANIFEST_AVAILABLE, ""))
                    self.log("Manifest received.", "ok")
                    self.progress(0.05, "Downloading…", phase="Downloading")
                    self.log("\nStarting client update…\n")
                    nodes = manifest["root"].get("files", [])

            if torrent_recovery:
                self._recovery_download(torrent_wanted)
                return

            if self._source is None:
                self._source = _download_source()
            if self._source is None:
                raise RuntimeError("No download source configured.")
            ran_torrent = self._torrent_download(nodes)
            if ran_torrent:
                # Piece hashes prove what peers sent; when a manifest exists
                # its SHA-1s remain the final word — re-check just the
                # delivered files and HTTP-refetch any mismatch.
                self._reverify_torrent_files(nodes)
            else:
                # The BitTorrent backend didn't fetch the files, so fall back
                # to the per-file HTTP download (which re-verifies each file
                # against the manifest). The update progress bar spans 0→100
                # across exactly the files that need updating.
                self._total = self._sum_needed_bytes(nodes)
                self._downloaded = 0
                for child in nodes:
                    self.traverse(child, [])

            if self._cancel:
                return self._cancelled_abort()

            self.log("\nDownload complete.", "ok")
            remove_wdb(self.out_dir)

            self.progress(1.0, "")
            save_cache(self._cache)
            self.log("\n✓  Everything is up to date!", "ok")
            self._report_client_version()
            self.log_q.put((markers.DONE, ""))

        except Exception as e:
            self.log(f"\n✗  {e}", "err")
            self.progress(0.0, "")
            self.log_q.put((markers.ERROR, ""))
