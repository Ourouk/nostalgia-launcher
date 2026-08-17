"""HTTP client update backend: manifest verification and incremental update.

`VerifyWorker` fetches the manifest from the selected download source and
reports which files differ. `UpdateWorker` downloads (resumably) the changed
files, verifies SHA-1s, and clears the WDB cache. When the manifest cannot be
fetched but the active source advertises a ``torrent_url`` (and libtorrent is
available) the update falls back to a full BitTorrent recovery download of the
client files, verified against the torrent's piece hashes. Both speak to the
GUI exclusively through the log/progress queues using the documented message
protocol.
"""

import hashlib
import json
import os
import queue
import shutil
import time
import urllib.request
from typing import NamedTuple
from urllib.error import HTTPError

from ...core.config_store import load_cache, save_cache
from ...core.constants import (
    DOWNLOAD_RETRY,
    DOWNLOAD_TIMEOUT,
    UA,
)
from ...core.filesystem import (
    already_updated,
    cached_sha1,
    get_client_version,
    remove_wdb,
    sha1_file,
)
from ...core.helpers import fmt_size, fmt_speed
from ...core.security_http import allowed_download_hosts, secure_urlopen
from ..tweaks import write_config_wtf


class DownloadSource(NamedTuple):
    """The resolved endpoints of the active download source."""

    manifest_url: str
    client_url: str
    torrent_url: str | None = None


def _source_reachable(url: str) -> bool:
    """Whether a download source answers at `url`. Any HTTP response — even an
    error status (4xx/5xx) — proves the host is reachable; only transport
    failures (DNS, refused, timeout) count as down."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with secure_urlopen(req, timeout=5) as r:
            r.read(1)
        return True
    except HTTPError:
        return True
    except Exception:
        return False


def _download_source() -> "DownloadSource | None":
    """Resolve the active download source: mirrors are tried in order
    (automatic failover, probed via their client-files endpoint) and the
    server is the fallback. Returns None when the launcher configuration is
    missing."""
    from ...core import launcher

    cfg = launcher.config()
    server = cfg.server_url if cfg else ""
    if not server:
        return None
    for mirror in cfg.mirrors if cfg else []:
        if _source_reachable(mirror.client_url):
            return DownloadSource(
                mirror.manifest_url, mirror.client_url, mirror.torrent_url
            )
    return DownloadSource(cfg.manifest_url, cfg.client_url, cfg.torrent_url)


def _torrent_available() -> bool:
    """Whether the BitTorrent backend can run (libtorrent installed)."""
    try:
        from .torrent_update import available

        return available()
    except Exception:
        return False


def torrent_recovery_available() -> bool:
    """Whether a manifest-less full re-download via BitTorrent is possible:
    some configured source advertises a ``torrent_url`` and libtorrent is
    importable. Network-free (no mirror probing) so it's safe to call from
    the readiness path."""
    from ...core import launcher

    cfg = launcher.config()
    return bool(cfg and cfg.has_torrent() and _torrent_available())


class VerifyWorker:
    def __init__(
        self,
        out_dir: str,
        log_q: queue.Queue,
        prog_q: queue.Queue,
        overwrite_config: bool = False,
    ):
        self.out_dir = out_dir
        self.log_q = log_q
        self.prog_q = prog_q
        self._cancel = False
        self.overwrite_config = overwrite_config
        self._cache: dict = load_cache()

    def cancel(self):
        self._cancel = True

    def log(self, msg, tag=""):
        self.log_q.put((msg, tag))

    def progress(self, value, label=""):
        self.prog_q.put((value, label))

    def _file_ok(self, dest, server_hash):
        if not os.path.exists(dest):
            return False
        local_hash = cached_sha1(dest, self._cache)
        if local_hash == server_hash:
            return True
        return False

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
            self.progress(0.02, "Fetching manifest...")
            self.log("Verifying files...", "acct")
            src = _download_source()
            if src is None:
                raise RuntimeError("No download source configured.")
            req = urllib.request.Request(
                src.manifest_url, headers={"User-Agent": UA}
            )
            with secure_urlopen(req, timeout=DOWNLOAD_TIMEOUT) as r:
                manifest = json.load(r)
            manifest_ok = True
            self.log_q.put(("__MANIFEST_AVAILABLE__", ""))
            self.progress(0.5, "Checking...")

            stale_nodes = [
                c
                for child in manifest["root"].get("files", [])
                if (c := self._traverse(child, [])) is not None
            ]

            self.progress(1.0, "")
            save_cache(self._cache)

            # Config.wtf isn't part of the manifest — it's user game config.
            # Create it when missing, or overwrite it when the user
            # committed to this folder.
            cfg_wtf = os.path.join(self.out_dir, "WTF", "Config.wtf")
            if self.overwrite_config or not os.path.exists(cfg_wtf):
                write_config_wtf(self.out_dir)

            if stale_nodes:
                self.log("Update available.", "acct")
                self.log_q.put(("__UPDATE_NEEDED__", ""))
                self.log_q.put(("__DIFF_TREE__", stale_nodes))
            else:
                self.log("Everything is up to date!", "ok")
                self.log_q.put(("__UP_TO_DATE__", ""))
        except Exception as e:
            self.log(f"Verification failed: {e}", "err")
            # A failed manifest fetch must not masquerade as "update needed":
            # the controller uses __MANIFEST_UNAVAILABLE__ to gray out the
            # update button. Failures *after* the manifest parsed are a
            # genuine "update needed" verdict.
            if not manifest_ok:
                if self._torrent_verify(src):
                    return
                self.log_q.put(("__MANIFEST_UNAVAILABLE__", ""))
                if torrent_recovery_available():
                    self.log(
                        "Manifest unavailable — a full re-download via "
                        "BitTorrent is available (UPDATE).",
                        "dim",
                    )
            else:
                self.log_q.put(("__UPDATE_NEEDED__", ""))
            self.log_q.put(("__DIFF_TREE__", None))

    def _torrent_verify(self, src) -> bool:
        """When no manifest is available, verify the client against the
        torrent's piece hashes (libtorrent recheck) and report the stale
        files. Returns True when the torrent check ran and its verdict was
        posted; False when it's not possible and the caller should fall back
        to the plain manifest-unavailable path.

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
        if src is None or not src.torrent_url:
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
        )

        try:
            verifier = TorrentVerifier(self.out_dir, self.log_q, self.prog_q)
            self.log(
                "Manifest unavailable — verifying client against the "
                "BitTorrent snapshot…",
                "acct",
            )
            stale = verifier.verify(src.torrent_url)
        except TorrentCorruptError as e:
            if self._cancel:
                self.log("\nVerify cancelled.", "err")
            else:
                self.log(f"Torrent file corrupt: {e}", "err")
                self.log(
                    "No usable torrent snapshot — update unavailable.",
                    "err",
                )
            self.log_q.put(("__TORRENT_CORRUPT__", str(e)))
            return True
        except TorrentFetchError as e:
            if self._cancel:
                self.log("\nVerify cancelled.", "err")
            else:
                self.log(f"BitTorrent snapshot unreachable: {e}", "err")
                self.log(
                    "No manifest and no reachable torrent — update "
                    "unavailable via BitTorrent.",
                    "err",
                )
            self.log_q.put(("__TORRENT_UNREACHABLE__", str(e)))
            return True
        except TorrentStalledError as e:
            if self._cancel:
                self.log("\nVerify cancelled.", "err")
            else:
                self.log(f"BitTorrent verification stalled: {e}", "err")
                self.log(
                    "No manifest and torrent verification stalled — "
                    "update unavailable.",
                    "err",
                )
            self.log_q.put(("__TORRENT_STALLED__", str(e)))
            return True
        except TorrentSessionError as e:
            if self._cancel:
                self.log("\nVerify cancelled.", "err")
            else:
                self.log(f"BitTorrent session error: {e}", "err")
                self.log(
                    "No manifest and torrent session error — "
                    "update unavailable.",
                    "err",
                )
            self.log_q.put(("__TORRENT_SESSION_ERROR__", str(e)))
            return True
        except TorrentDiskError as e:
            if self._cancel:
                self.log("\nVerify cancelled.", "err")
            else:
                self.log(f"Disk I/O error: {e}", "err")
                self.log(
                    "No manifest and disk error — update unavailable.",
                    "err",
                )
            self.log_q.put(("__TORRENT_DISK_ERROR__", str(e)))
            return True
        except Exception as e:
            if self._cancel:
                self.log("\nVerify cancelled.", "err")
            else:
                self.log(f"BitTorrent verification failed: {e}", "err")
                self.log(
                    "Torrent snapshot fetched but verification failed — "
                    "update unavailable via BitTorrent.",
                    "err",
                )
            self.log_q.put(("__TORRENT_VERIFY_FAILED__", str(e)))
            return True
        self.log_q.put(("__TORRENT_REACHABLE__", ""))
        if not stale:
            self.log("Everything is up to date (BitTorrent snapshot).", "ok")
            self.log_q.put(("__TORRENT_UP_TO_DATE__", ""))
        else:
            self.log(
                f"Update available — {len(stale)} stale file(s) vs the "
                "BitTorrent snapshot.",
                "acct",
            )
            self.log_q.put(("__TORRENT_DIFF__", sorted(stale)))
        return True


class UpdateWorker:
    def __init__(
        self,
        out_dir: str,
        log_q: queue.Queue,
        prog_q: queue.Queue,
    ):
        self.out_dir = out_dir
        self.log_q = log_q
        self.prog_q = prog_q
        self._cancel = False
        self._cache: dict = load_cache()
        self._source: DownloadSource | None = None

    def cancel(self):
        self._cancel = True

    def log(self, msg: str, tag: str = ""):
        self.log_q.put((msg, tag))

    def progress(self, value: float, label: str = "", **details):
        item = (value, label, details) if details else (value, label)
        self.prog_q.put(item)

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
                                self.progress(
                                    downloaded / size,
                                    f"{name}   •   {fmt_size(downloaded)}"
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

    def _skip_download(self, node, dest, name) -> bool:
        """Whether a file node can be skipped because the local copy already
        matches the manifest."""
        return already_updated(dest, node["hash"])

    def _torrent_download(self, nodes) -> bool:
        """Bulk-download the stale files via BitTorrent when the active source
        advertises a ``torrent_url`` and libtorrent is available. Returns True
        when the torrent backend ran; ``traverse()`` still re-verifies every
        file afterwards and HTTP-resumes anything the torrent didn't cover."""
        src = self._source
        if src is None or not src.torrent_url:
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
            f"\nDownloading {len(wanted)} stale file(s) via BitTorrent…",
            "acct",
        )
        try:
            from .torrent_update import TorrentDownloader

            dl = TorrentDownloader(self.out_dir, self.log_q, self.prog_q)
            dl.download(src.torrent_url, wanted)
            self.log("  BitTorrent download complete.", "ok")
            return True
        except RuntimeError as e:
            if self._cancel:
                raise
            self.log(f"  BitTorrent download failed: {e}", "err")
            self.log("  Falling back to HTTP downloads.", "err")
            return False

    def _collect_wanted(self, node, path_parts, wanted):
        """Collect the relative paths of stale file/mpq nodes for the torrent
        backend, reusing the same up-to-date checks as `traverse`."""
        if self._cancel:
            return
        t = node["type"]
        name = node["name"]
        cur = path_parts + [name]
        if t == "dir":
            for child in node.get("files", []):
                self._collect_wanted(child, cur, wanted)
        elif t == "file":
            rel = "/".join(cur)
            dest = os.path.join(self.out_dir, rel)
            if not self._skip_download(node, dest, name):
                wanted.add(rel)
        elif t == "mpq":
            rel = "/".join(path_parts + [name + ".mpq"])
            dest = os.path.join(self.out_dir, rel)
            if not self._skip_download(node, dest, name + ".mpq"):
                wanted.add(rel)

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

        elif t == "file":
            self.log(f"[file] {rel}", "acct")
            url = f"{src.client_url}/{'/'.join(cur)}"

            if self._skip_download(node, dest, name):
                self.log("  Already up to date.", "dim")
                return

            got_hash = self.download(url, dest, node["size"], rel)
            if (got_hash or sha1_file(dest)) != node["hash"]:
                self.log("  Hash mismatch — retrying", "err")
                os.remove(dest)
                got_hash = self.download(url, dest, node["size"], rel)
                if (got_hash or sha1_file(dest)) != node["hash"]:
                    raise RuntimeError(
                        f"Hash mismatch after redownload: {rel}"
                    )

        elif t == "mpq":
            mpq_name = name + ".mpq"
            cur_mpq = path_parts + [mpq_name]
            rel = os.path.join(*cur_mpq)
            dest = os.path.join(self.out_dir, rel)
            url = f"{src.client_url}/{'/'.join(cur_mpq)}"
            self.log(f"[mpq]  {rel}", "acct")
            if self._skip_download(node, dest, mpq_name):
                self.log("  Already up to date.", "dim")
                return
            got_hash = self.download(url, dest, node["size"], rel)
            if (got_hash or sha1_file(dest)) != node["hash"]:
                self.log("  Hash mismatch — retrying", "err")
                os.remove(dest)
                got_hash = self.download(url, dest, node["size"], rel)
                if (got_hash or sha1_file(dest)) != node["hash"]:
                    raise RuntimeError(
                        f"Hash mismatch after redownload: {rel}"
                    )

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
        try:
            dl.download(self._source.torrent_url, wanted)
        except TorrentCorruptError as e:
            self.log(f"Torrent file corrupt: {e}", "err")
            self.log_q.put(("__TORRENT_CORRUPT__", str(e)))
            return
        except TorrentStalledError as e:
            self.log(f"BitTorrent download stalled: {e}", "err")
            self.log_q.put(("__TORRENT_STALLED__", str(e)))
            return
        except TorrentSessionError as e:
            self.log(f"BitTorrent session error: {e}", "err")
            self.log_q.put(("__TORRENT_SESSION_ERROR__", str(e)))
            return
        except TorrentDiskError as e:
            self.log(f"Disk I/O error: {e}", "err")
            self.log_q.put(("__TORRENT_DISK_ERROR__", str(e)))
            return
        except TorrentFetchError as e:
            self.log(f"Torrent unreachable during download: {e}", "err")
            self.log_q.put(("__TORRENT_UNREACHABLE__", str(e)))
            return
        except Exception as e:
            self.log(f"BitTorrent download failed: {e}", "err")
            self.log_q.put(("__TORRENT_VERIFY_FAILED__", str(e)))
            return
        if self._cancel:
            self.log("\nUpdate cancelled.", "err")
            self.progress(0.0, "Cancelled")
            self.log_q.put(("__ERROR__", ""))
            return
        self.log("  BitTorrent recovery download complete.", "ok")
        remove_wdb(self.out_dir)
        # A fresh recovery install has no Config.wtf — create it (a regular
        # update never touches user config, but this path has no verify step
        # to seed it).
        cfg_wtf = os.path.join(self.out_dir, "WTF", "Config.wtf")
        if not os.path.exists(cfg_wtf):
            write_config_wtf(self.out_dir)
        self.progress(1.0, "")
        save_cache(self._cache)
        self.log(
            "\n✓  Client installed via BitTorrent (no manifest — files "
            "verified against the torrent's piece hashes).",
            "ok",
        )
        client_ver = get_client_version(self.out_dir)
        if client_ver:
            self.log(f"Client version: {client_ver}", "dim")
            self.log_q.put((f"__VERSION__{client_ver}", ""))
        else:
            self.log("Could not read client version from WoW.exe", "dim")
        self.log_q.put(("__TORRENT_RECOVERY_DONE__", ""))

    def run(self, diff_nodes=None, torrent_wanted: set[str] | None = None):
        try:
            torrent_recovery = False
            if diff_nodes is not None:
                self.log("\nStarting client update…\n")
                self.progress(0.05, "Downloading…", phase="Downloading")
                nodes = diff_nodes
            elif torrent_wanted is not None:
                # A prior torrent verification already established the stale
                # paths. Do not probe the manifest again: this is explicitly
                # the manifest-less BitTorrent update path.
                self.progress(
                    0.02,
                    "Downloading via BitTorrent…",
                    phase="BitTorrent",
                    transport="BitTorrent",
                )
                self.log("\nStarting BitTorrent update…\n", "acct")
                self._source = _download_source()
                if self._source is None or not self._source.torrent_url:
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
                    with secure_urlopen(req, timeout=DOWNLOAD_TIMEOUT) as r:
                        manifest = json.load(r)
                except Exception:
                    # Manifest unavailable — fall back to a BitTorrent
                    # recovery download instead of failing outright.
                    if self._source.torrent_url and _torrent_available():
                        self.log(
                            "\nManifest unavailable — downloading the client "
                            "via BitTorrent…",
                            "acct",
                        )
                        torrent_recovery = True
                    else:
                        raise
                if not torrent_recovery:
                    self.log_q.put(("__MANIFEST_AVAILABLE__", ""))
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
            self._torrent_download(nodes)
            for child in nodes:
                self.traverse(child, [])

            if self._cancel:
                self.log("\nUpdate cancelled.", "err")
                self.progress(0.0, "Cancelled")
                self.log_q.put(("__ERROR__", ""))
                return

            self.log("\nDownload complete.", "ok")
            remove_wdb(self.out_dir)

            self.progress(1.0, "")
            save_cache(self._cache)
            self.log("\n✓  Everything is up to date!", "ok")
            client_ver = get_client_version(self.out_dir)
            if client_ver:
                self.log(f"Client version: {client_ver}", "dim")
                self.log_q.put((f"__VERSION__{client_ver}", ""))
            else:
                self.log("Could not read client version from WoW.exe", "dim")
            self.log_q.put(("__DONE__", ""))

        except Exception as e:
            self.log(f"\n✗  {e}", "err")
            self.progress(0.0, "")
            self.log_q.put(("__ERROR__", ""))
