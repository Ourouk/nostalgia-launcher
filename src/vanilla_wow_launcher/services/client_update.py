"""Client update engine: manifest verification and incremental update.

`VerifyWorker` fetches the manifest from the selected download source and
reports which files differ. `UpdateWorker` downloads (resumably) the changed
files, verifies SHA-1s, patches WoW.exe, and clears the WDB cache. Both speak
to the GUI exclusively through the log/progress queues using the documented
message protocol.
"""

import hashlib
import json
import os
import queue
import shutil
import struct
import time
import urllib.request
from typing import NamedTuple
from urllib.error import HTTPError

from ..core.config_store import load_cache, save_cache
from ..core.constants import (
    DOWNLOAD_RETRY,
    DOWNLOAD_TIMEOUT,
    UA,
)
from ..core.filesystem import (
    already_updated,
    cached_sha1,
    get_client_version,
    remove_wdb,
    sha1_file,
)
from ..core.helpers import fmt_size, fmt_speed
from ..core.platform_support import can_patch_client
from ..core.security_http import allowed_download_hosts, secure_urlopen
from .tweaks import build_tweaks, write_config_wtf


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
    from ..core import launcher

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
        from .torrent_download import available

        return available()
    except Exception:
        return False


class VerifyWorker:
    def __init__(
        self,
        out_dir: str,
        log_q: queue.Queue,
        prog_q: queue.Queue,
        expected_patched_wow_hash: str = "",
        original_server_wow_hash: str = "",
        overwrite_config: bool = False,
    ):
        self.out_dir = out_dir
        self.log_q = log_q
        self.prog_q = prog_q
        self._cancel = False
        self.expected_patched_wow_hash = expected_patched_wow_hash
        self.original_server_wow_hash = original_server_wow_hash
        self.overwrite_config = overwrite_config
        self._cache: dict = load_cache()

    def cancel(self):
        self._cancel = True

    def log(self, msg, tag=""):
        self.log_q.put((msg, tag))

    def progress(self, value, label=""):
        self.prog_q.put((value, label))

    def _file_ok(self, dest, server_hash, name):
        if not os.path.exists(dest):
            return False
        local_hash = cached_sha1(dest, self._cache)
        if local_hash == server_hash:
            return True
        if name == "WoW.exe" and self.expected_patched_wow_hash:
            return (
                local_hash == self.expected_patched_wow_hash
                and server_hash == self.original_server_wow_hash
            )
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
            return None if self._file_ok(dest, node["hash"], name) else node

        if t == "mpq":
            mpq_dest = os.path.join(
                self.out_dir, os.path.join(*(path_parts + [name + ".mpq"]))
            )
            return (
                None
                if self._file_ok(mpq_dest, node["hash"], name + ".mpq")
                else node
            )

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
            self.log_q.put(
                (
                    "__MANIFEST_UNAVAILABLE__"
                    if not manifest_ok
                    else "__UPDATE_NEEDED__",
                    "",
                )
            )
            self.log_q.put(("__DIFF_TREE__", None))


class UpdateWorker:
    def __init__(
        self,
        out_dir: str,
        log_q: queue.Queue,
        prog_q: queue.Queue,
        expected_patched_wow_hash: str = "",
    ):
        self.out_dir = out_dir
        self.log_q = log_q
        self.prog_q = prog_q
        self._cancel = False
        self._cache: dict = load_cache()
        self.expected_patched_wow_hash = expected_patched_wow_hash
        self.original_server_wow_hash = ""
        self._source: DownloadSource | None = None

    def cancel(self):
        self._cancel = True

    def log(self, msg: str, tag: str = ""):
        self.log_q.put((msg, tag))

    def progress(self, value: float, label: str = ""):
        self.prog_q.put((value, label))

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
                    )
                    self.log(f"  Retrying in {wait} s…", "dim")
                    time.sleep(wait)
        raise RuntimeError(
            f"Download failed after {DOWNLOAD_RETRY} attempts: {url}"
        )

    def _skip_download(self, node, dest, name) -> bool:
        """Whether a file node can be skipped because the local copy already
        matches the manifest (or is the expected patched WoW.exe)."""
        if name == "WoW.exe" and self.expected_patched_wow_hash:
            server_hash = node["hash"]
            original_server_hash = self.original_server_wow_hash
            local_hash = sha1_file(dest) if os.path.exists(dest) else ""
            return (
                local_hash == self.expected_patched_wow_hash
                and server_hash == original_server_hash
            )
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
            from .torrent_download import TorrentDownloader

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

    def patch_exe(self, tweaks: dict | None = None):
        exe = os.path.join(self.out_dir, "WoW.exe")
        if not os.path.exists(exe):
            raise RuntimeError(f"WoW.exe not found in {self.out_dir}")
        self.log("\nApplying binary tweaks to WoW.exe…")
        original_hash = sha1_file(exe)
        self.log_q.put((f"__ORIGINAL_HASH__{original_hash}", ""))
        with open(exe, "rb") as f:
            buf = bytearray(f.read())
        for label, kind, offset, value in build_tweaks(buf, tweaks):
            self.log(f"  {label}", "dim")
            if kind == "float":
                struct.pack_into("<f", buf, offset, value)
            elif kind == "int8":
                struct.pack_into("<b", buf, offset, value)
            elif kind == "uint16":
                struct.pack_into("<H", buf, offset, value)
            elif kind == "bytes":
                for off, data in value:
                    buf[off : off + len(data)] = data
        with open(exe, "wb") as f:
            f.write(buf)
        self.log("WoW.exe patched.", "ok")

        patched_hash = sha1_file(exe)
        self.log_q.put((f"__PATCHED_HASH__{patched_hash}", ""))

    @staticmethod
    def _nodes_contain_wow_exe(nodes) -> bool:
        if nodes is None:
            return True
        for node in nodes:
            if node.get("type") == "file" and node.get("name") == "WoW.exe":
                return True
            if node.get("type") == "dir":
                if UpdateWorker._nodes_contain_wow_exe(node.get("files", [])):
                    return True
        return False

    def run(self, diff_nodes=None):
        try:
            if diff_nodes is not None:
                self.log("\nStarting client update…\n")
                self.progress(0.05, "Downloading…")
                nodes = diff_nodes
            else:
                self.progress(0.02, "Fetching manifest…")
                self.log("Fetching manifest.json…")
                self._source = _download_source()
                if self._source is None:
                    raise RuntimeError("No download source configured.")
                req = urllib.request.Request(
                    self._source.manifest_url, headers={"User-Agent": UA}
                )
                with secure_urlopen(req, timeout=DOWNLOAD_TIMEOUT) as r:
                    manifest = json.load(r)
                self.log_q.put(("__MANIFEST_AVAILABLE__", ""))
                self.log("Manifest received.", "ok")
                self.progress(0.05, "Downloading…")
                self.log("\nStarting client update…\n")
                nodes = manifest["root"].get("files", [])

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

            wow_exe_updated = self._nodes_contain_wow_exe(diff_nodes)
            if wow_exe_updated and can_patch_client():
                self.progress(0.92, "Patching…")
                self.patch_exe()
            elif wow_exe_updated:
                self.log("\nWoW.exe patching skipped (Windows-only).", "dim")
                self.progress(0.95, "")
            else:
                self.log("\nWoW.exe unchanged — skipping patch.", "dim")
                self.progress(0.95, "")

            # Config.wtf is user config — never written here. It's created when
            # missing during verification and overwritten only on a folder
            # change; a regular update must never touch it.
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
