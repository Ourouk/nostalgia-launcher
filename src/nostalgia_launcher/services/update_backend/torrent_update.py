"""BitTorrent update backend for client updates (libtorrent).

`TorrentDownloader` fetches a ``.torrent`` over HTTPS (through the same
hardened, allowlisted transport as the HTTP downloads) or resolves a
``magnet:`` URI from its swarm, and uses libtorrent to bulk-download the
files the manifest flagged as stale. Peers in the swarm are untrusted — a
malicious peer can only inject data that fails the piece hashes embedded in
the ``.torrent`` (which itself came over TLS) or in magnet-resolved metadata
(which libtorrent only accepts when its info section hashes to the info-hash
embedded in the configured magnet URI).

Integrity layering: when a manifest diff tree exists, the caller re-verifies
the delivered files' SHA-1s against the manifest and re-fetches any mismatch
over HTTPS, so the torrent backend cannot weaken the manifest's guarantee. In
the manifest-less recovery path there is no per-file hash list to check
against — there, the torrent's piece hashes are the integrity guarantee by
themselves.

The session otherwise follows libtorrent's default storage and connection
configuration. The torrent is paused and removed from the session once every
wanted piece is in place.
"""

import hashlib
import os
import queue
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from ...core import profiles
from ...core.constants import DOWNLOAD_TIMEOUT, UA
from ...core.filesystem import atomic_write_bytes as _atomic_write_bytes
from ...core.helpers import fmt_size, fmt_speed, redact_url
from ...core.security_http import allowed_download_hosts, secure_urlopen
from .worker_base import WorkerBase

# Inactivity guard: if no wanted bytes arrive for this long, the swarm is dead
# and the caller should fall back to per-file HTTP downloads.
STALL_TIMEOUT = 60
# Grace period before the stall check kicks in, allowing time for DHT
# bootstrap, tracker announces, peer discovery, and the first piece transfer.
DISCOVERY_TIMEOUT = 180
# Known DHT bootstrap nodes — accelerates initial peer discovery.
DHT_BOOTSTRAP_NODES = (
    "router.libtorrent.org:6881,"
    "router.bittorrent.com:6881,"
    "dht.transmissionbt.com:6881"
)
# Use OS-assigned ephemeral ports to avoid conflicts; server can use fixed
# ports if needed. The default is empty to let the OS pick.
LISTEN_INTERFACES = "0.0.0.0:0"
ALERT_POLL_MS = 250
# upload_rate_limit is in bytes/sec; 0 (and -1) mean unlimited in libtorrent.
# A near-zero value (e.g. 1) starves upload, so peers choke the client under
# BitTorrent's tit-for-tat and the download hangs at 0 B/s despite connected
# peers. If a good-citizen cap is ever wanted, use a real bytes/sec value
# (KB/s * 1024, per Deluge's _on_set_max_upload_speed), never ~0.
UPLOAD_RATE_LIMIT = -1
# Alert categories we care about: error, storage (for failures), status (for
# state changes), and tracker/DHT if we want more detail.
ALERT_MASK = (
    1  # error_notification
    | 8  # storage_notification
    | 16  # tracker_notification
    | 64  # status_notification
    | 1024  # dht_notification
)
VERIFIER_ALERT_MASK = (
    1  # error_notification
    | 8  # storage_notification
    | 64  # status_notification
)


def _network_session_settings() -> dict:
    """Settings for a networked libtorrent session, shared by the magnet
    resolver and the downloader: ephemeral listen port, DHT bootstrap
    nodes, UPnP/NAT-PMP, and the full alert mask."""
    return {
        "listen_interfaces": LISTEN_INTERFACES,
        "user_agent": UA,
        "upload_rate_limit": UPLOAD_RATE_LIMIT,
        "enable_dht": True,
        "dht_bootstrap_nodes": DHT_BOOTSTRAP_NODES,
        "enable_lsd": False,
        "enable_upnp": True,
        "enable_natpmp": True,
        "alert_mask": ALERT_MASK,
    }


def available() -> bool:
    """Whether the libtorrent python module can be imported and used.
    Probed lazily so the app degrades gracefully to HTTP when it isn't installed
    or cannot be loaded. Side-effect free — does not create a session."""
    try:
        import libtorrent as lt

        # Verify the module has the symbols we use at runtime without
        # constructing a session or binding ports.
        _ = lt.session
        _ = lt.add_torrent_params
        _ = lt.torrent_info
        _ = lt.torrent_status
        _ = lt.alert.category_t.error_notification
        return True
    except (ImportError, ValueError, OSError, RuntimeError, AttributeError):
        return False


class TorrentFetchError(Exception):
    """Raised when the ``.torrent`` file cannot be fetched (HTTP error,
    DNS failure, allowlist rejection, etc.).  Distinguishes *network*
    failures from libtorrent verification failures so the caller can
    mark the snapshot as unreachable vs. simply failed."""

    pass


class TorrentCorruptError(TorrentFetchError):
    """Raised when the downloaded ``.torrent`` file is malformed or cannot
    be parsed by libtorrent (e.g. truncated, not a valid bencoded dict)."""

    pass


class TorrentStalledError(RuntimeError):
    """Raised when libtorrent verification/download makes no progress for
    STALL_TIMEOUT seconds. Includes peer count for diagnostics."""

    def __init__(self, peers: int):
        self.peers = peers
        super().__init__(f"Stalled ({peers} peers)")


class TorrentSessionError(RuntimeError):
    """Raised when libtorrent session creation or add_torrent fails
    (port binding, resource limits, invalid torrent for session)."""

    pass


class TorrentDiskError(RuntimeError):
    """Raised when disk I/O fails during torrent operations (disk full,
    permission denied, etc.)."""

    pass


class TorrentSnapshotMismatchError(RuntimeError):
    """Raised when the fetched torrent snapshot no longer contains a wanted
    file path (the torrent was replaced between verify and download). The
    caller must re-verify against the new snapshot rather than trust an old
    local file the new snapshot cannot validate."""

    pass


@dataclass
class TorrentSnapshot:
    """One fetched and parsed ``.torrent``, with its identity.

    ``content_hash`` is the SHA-256 of the raw ``.torrent`` bytes (any change
    to the file — trackers, web seeds, metadata — changes it); ``info_hash``
    is the torrent's content identity from libtorrent. Together they let the
    launcher detect a snapshot that changed at the same URL. ``torrent_info``
    is the parsed libtorrent object; ``torrent_bytes`` the raw payload.
    """

    url: str
    content_hash: str
    info_hash: str | None
    torrent_bytes: bytes
    torrent_info: object


def _info_hash_hex(ti) -> str | None:
    """Best-effort hex info-hash of a parsed torrent (v1 preferred, then v2).

    Returns None when the binding doesn't expose an info hash (e.g. exotic
    torrents or a stubbed module in tests) — callers treat that as
    "identity unavailable" and simply never cache by identity."""
    try:
        ih = ti.info_hashes()
    except Exception:
        ih = None
    if ih is not None:
        for attr in ("v1", "v2"):
            try:
                value = str(getattr(ih, attr, None) or "")
            except Exception:
                continue
            if value and value != "0" * len(value):
                return value
    try:
        return str(ti.info_hash()) or None
    except Exception:
        return None


# ── torrent metadata persistence (identity reuse) ──────────────────────────


def torrent_cache_dir() -> str:
    """Per-profile cache directory for torrent metadata. Kept out of the
    game folder so reinstall/move never wipes it."""
    return profiles.active().torrents_dir()


def torrent_path(info_hash: str) -> str:
    return os.path.join(torrent_cache_dir(), f"{info_hash}.torrent")


def resume_path(info_hash: str) -> str:
    return os.path.join(torrent_cache_dir(), f"{info_hash}.resume")


def write_torrent_atomically(info_hash: str, data: bytes):
    _atomic_write_bytes(torrent_path(info_hash), data)


def remove_resume_data(info_hash: str):
    try:
        os.remove(resume_path(info_hash))
    except OSError:
        pass


def _fetch_torrent(
    torrent_locator: str,
    log,
    cancel=None,
) -> "TorrentSnapshot":
    """Fetch or resolve the configured BitTorrent snapshot locator.

    An HTTPS locator fetches the ``.torrent`` over the allowlisted
    transport; a ``magnet:`` locator resolves its metadata from the swarm
    (see :func:`_resolve_magnet`). Either way the returned
    :class:`TorrentSnapshot` carries the raw bytes (empty when
    serialization is unavailable), their SHA-256 content hash, and the
    torrent's info hash.

    Network/security failures (HTTP errors, connection refused, DNS, TLS,
    allowlist rejection) are wrapped in :class:`TorrentFetchError` so the
    caller can distinguish a *missing* snapshot from a *failed* verification.

    ``cancel`` is an optional zero-arg callable polled by the magnet
    resolution loop; raising matches the workers' cancellation semantics.

    The raw bytes are persisted under the launcher cache (keyed by info hash)
    on a best-effort basis, so re-verifying the same snapshot can skip the
    fetch.
    """
    if torrent_locator.startswith("magnet:"):
        return _resolve_magnet(torrent_locator, log, cancel=cancel)
    return _fetch_torrent_url(torrent_locator, log)


def _fetch_torrent_url(torrent_url: str, log) -> "TorrentSnapshot":
    """Fetch the ``.torrent`` at ``torrent_url`` over HTTPS, parse it with
    libtorrent, and return a :class:`TorrentSnapshot`."""
    log(f"  Fetching torrent: {redact_url(torrent_url)}", "dim")
    req = urllib.request.Request(torrent_url, headers={"User-Agent": UA})
    try:
        with secure_urlopen(
            req,
            timeout=DOWNLOAD_TIMEOUT,
            allowed_hosts=allowed_download_hosts(),
        ) as r:
            # Stream the torrent file with a size cap to avoid loading a
            # malicious oversized response into memory.
            max_size = 5 * 1024 * 1024  # 5 MiB cap for .torrent files
            data = bytearray()
            for chunk in iter(lambda: r.read(65536), b""):
                data.extend(chunk)
                if len(data) > max_size:
                    raise TorrentFetchError(
                        f"Torrent file exceeds maximum size of {max_size} bytes"
                    )
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        OSError,
        RuntimeError,
    ) as exc:
        raise TorrentFetchError(str(exc)) from exc

    data = bytes(data)
    content_hash = hashlib.sha256(data).hexdigest()
    fd, tmp = tempfile.mkstemp(suffix=".torrent")
    try:
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(data)
        except OSError as e:
            raise TorrentDiskError(f"Failed to write torrent file: {e}") from e
        try:
            # Imported only once parsing is actually reached: the fetch and
            # its error wrapping must work on installs without libtorrent.
            import libtorrent as lt

            ti = lt.torrent_info(tmp)
        except Exception as e:
            raise TorrentCorruptError(f"Failed to parse torrent: {e}") from e
        info_hash = _info_hash_hex(ti)
        if info_hash:
            try:
                write_torrent_atomically(info_hash, data)
            except OSError as e:
                log(f"  Failed to cache torrent metadata: {e}", "dim")
        return TorrentSnapshot(
            url=torrent_url,
            content_hash=content_hash,
            info_hash=info_hash,
            torrent_bytes=data,
            torrent_info=ti,
        )
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _resolve_magnet(
    magnet_uri: str,
    log,
    cancel=None,
) -> "TorrentSnapshot":
    """Resolve a ``magnet:`` URI into a :class:`TorrentSnapshot`.

    A magnet carries no metadata — libtorrent joins the swarm (DHT plus the
    trackers embedded in the URI) and downloads it via ut_metadata. That
    exchange is safe even though peers are untrusted: the metadata is only
    accepted when its info section hashes to the ``xt`` info-hash embedded
    in the configured URI, so the launcher-config origin of the magnet is
    what authenticates it (the same guarantee class as a TLS-fetched
    ``.torrent``'s piece hashes). Once resolved, verification and download
    run exactly as with an HTTPS-fetched snapshot — only this one-time
    metadata step uses networking.

    A throwaway save path backs the session so nothing is ever written into
    the game folder while resolving; upload mode (when the binding exposes
    it) additionally keeps libtorrent from writing payload at all. The
    resolved metadata is serialized back to ``.torrent`` bytes on a
    best-effort basis and persisted under the launcher cache keyed by info
    hash; identity reuse then keys on the info-hash alone (peers cannot
    alter metadata that must hash to the magnet's btih).

    Raises :class:`TorrentCorruptError` for an unparseable URI,
    :class:`TorrentSessionError` for session failures,
    :class:`TorrentStalledError` when the swarm yields nothing, and
    ``RuntimeError("Cancelled")`` when ``cancel`` fires.
    """
    import libtorrent as lt

    try:
        atp = lt.parse_magnet_uri(magnet_uri)
    except Exception as e:
        raise TorrentCorruptError(f"Failed to parse magnet URI: {e}") from e

    # Throwaway storage root: metadata resolution never targets the game
    # folder, and whatever stray bytes landed there are wiped below.
    tmp_dir = tempfile.mkdtemp(prefix="nostalgia-magnet-")
    try:
        ses = _wrap_session_error(
            lambda: lt.session(_network_session_settings()),
            "Failed to create libtorrent session",
        )
        h = None
        try:
            atp.save_path = tmp_dir
            h = _wrap_session_error(
                lambda: ses.add_torrent(atp),
                "Failed to add magnet to session",
            )
            # Upload mode (when available) makes the torrent read-only: peers
            # may deliver metadata, but no payload is ever written to disk.
            try:
                h.set_flags(lt.torrent_flags.upload_mode)
            except Exception:
                pass
            # The binding adds the torrent paused; resume() starts announce
            # and peer connections so the metadata can actually arrive.
            h.resume()
            log(f"  Resolving magnet: {magnet_uri}", "dim")
            ti = _wait_for_metadata(ses, h, log, cancel=cancel)
        finally:
            if h is not None:
                _release_handle(ses, h)
        info_hash = _info_hash_hex(ti)
        data = b""
        try:
            data = lt.bencode(lt.create_torrent(ti).generate())
        except Exception as e:
            log(f"  Could not serialize resolved metadata: {e}", "dim")
        if info_hash and data:
            try:
                write_torrent_atomically(info_hash, data)
            except OSError as e:
                log(f"  Failed to cache torrent metadata: {e}", "dim")
        return TorrentSnapshot(
            url=magnet_uri,
            content_hash=hashlib.sha256(data).hexdigest() if data else "",
            info_hash=info_hash,
            torrent_bytes=data,
            torrent_info=ti,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _wait_for_metadata(ses, h, log, cancel=None):
    """Block until the handle's metadata has been downloaded from the swarm;
    returns the parsed :class:`libtorrent.torrent_info`.

    Shares the downloader's stall discipline: DISCOVERY_TIMEOUT while no
    peer has connected (DHT bootstrap and tracker announces are slow),
    STALL_TIMEOUT once peers exist but stop talking."""
    last_move = time.monotonic()
    transfer_started = False
    best_peer_count = 0
    while True:
        if cancel is not None and cancel():
            raise RuntimeError("Cancelled")
        _drain_alerts(ses, log)
        s = h.status()
        if getattr(s, "has_metadata", False):
            ti = None
            try:
                ti = h.torrent_file()
            except Exception:
                ti = None
            if ti is not None:
                return ti
        # Only NEW connections push the stall timer out: a single zombie
        # peer that connects and then sends nothing must not pin the loop
        # forever — no metadata progress means the magnet is unreachable.
        peers_now = getattr(s, "num_peers", 0)
        if peers_now > best_peer_count:
            best_peer_count = peers_now
            transfer_started = True
            last_move = time.monotonic()
        peers = peers_now
        log(f"  Resolving magnet… {peers} peers", "dim")
        elapsed = time.monotonic() - last_move
        timeout = STALL_TIMEOUT if transfer_started else DISCOVERY_TIMEOUT
        if elapsed > timeout:
            raise TorrentStalledError(peers=peers)
        ses.wait_for_alert(ALERT_POLL_MS)


class TorrentLayoutError(TorrentCorruptError):
    """Raised when the torrent's file layout cannot be mapped to the local
    client directory (missing or duplicate WoW.exe, path traversal, etc.)."""

    pass


def _detect_torrent_root(
    files,
    root_marker: str = "WoW.exe",
) -> tuple[str, dict[str, str]]:
    """Detect the torrent root directory from the unique ``root_marker``.

    Scans every file path in the torrent (case-insensitive) looking for the
    single entry whose basename equals ``root_marker``. The parent directory
    of that entry is the *root*; all other torrent paths are expected to live
    under the same root. Defaults to ``WoW.exe`` for Vanilla WoW client
    compatibility.

    Returns ``(torrent_root, {torrent_path: local_path})`` where:

    * ``torrent_root`` is the leading directory to strip (e.g. ``client``).
    * ``local_path`` is the path relative to the selected client folder
      (e.g. ``Data/foo.mpq``).

    Raises :class:`TorrentLayoutError` when:

    * no ``root_marker`` file is found,
    * multiple ``root_marker`` entries exist, or
    * any file escapes the detected root directory.
    """
    num = files.num_files()
    exe_indices: list[int] = []
    normalized: list[str] = []
    target = root_marker.lower()
    for i in range(num):
        p = files.file_path(i).replace("\\", "/")
        normalized.append(p)
        if p.rsplit("/", 1)[-1].lower() == target:
            exe_indices.append(i)

    if not exe_indices:
        raise TorrentLayoutError(
            f"Torrent contains no {root_marker} — cannot detect root directory"
        )
    if len(exe_indices) > 1:
        paths = [normalized[i] for i in exe_indices]
        raise TorrentLayoutError(
            f"Torrent contains multiple {root_marker} entries: {paths}"
        )

    parts = normalized[exe_indices[0]].split("/")
    root = "/".join(parts[:-1])  # "" when WoW.exe is at the top level
    prefix = root + "/" if root else ""
    mapping: dict[str, str] = {}
    for p in normalized:
        if prefix and not p.startswith(prefix):
            raise TorrentLayoutError(
                f"File {p!r} is outside detected torrent root {root!r}"
            )
        local = p[len(prefix) :]
        if ".." in local.split("/"):
            raise TorrentLayoutError(f"Path traversal in torrent file: {p!r}")
        mapping[p] = local
    return root, mapping


def _map_torrent_paths(
    files,
    root_marker: str = "WoW.exe",
) -> dict[str, str]:
    """Convenience wrapper around :func:`_detect_torrent_root` that returns
    only the ``{torrent_path: local_path}`` mapping."""
    _, mapping = _detect_torrent_root(files, root_marker)
    return mapping


def _configured_root_marker() -> str:
    """The root marker filename from the active launcher config, defaulting to
    ``WoW.exe`` when no config is loaded."""
    from ...core import launcher

    cfg = launcher.config()
    return cfg.torrent_root_marker if cfg else "WoW.exe"


def _remap_torrent_to_out_dir(
    ti,
    out_dir: str,
    root_marker: str = "WoW.exe",
) -> None:
    """Strip the auto-detected torrent root so the snapshot's files resolve
    directly under ``out_dir`` (e.g. torrent ``client/WoW.exe`` ->
    ``out_dir/WoW.exe``).

    libtorrent maps a torrent file ``client/WoW.exe`` to
    ``save_path/client/WoW.exe``. With ``save_path == out_dir`` that reads and
    writes at ``out_dir/client/...`` — a double prefix — so every real file
    looks missing and the whole client is reported stale. Remapping the torrent
    file paths to ``out_dir/local`` (root stripped) fixes the read/write target
    while leaving piece hashes and the info hash untouched.

    The root is auto-detected from the unique WoW.exe position (see
    :func:`_detect_torrent_root`); only the leading root directory is removed.
    Real libtorrent exposes ``torrent_info.remap_files``; the test fakes do
    not, so this is a no-op under unit tests."""
    if not hasattr(ti, "remap_files"):
        return
    import libtorrent as lt

    files = ti.files()
    mapping = _map_torrent_paths(files, root_marker)
    fs = lt.file_storage()
    for i in range(files.num_files()):
        rel = mapping[files.file_path(i).replace("\\", "/")]
        fs.add_file(os.path.join(out_dir, rel), files.file_size(i))
    ti.remap_files(fs)


def _file_piece_ranges(files, piece_length: int) -> list[list[int]]:
    """Map each torrent file to the indices of the pieces covering it.

    Returns one list per file (in torrent order) of piece indices, derived
    from each file's byte ``offset`` and ``size`` and the torrent's fixed
    ``piece_length``."""
    ranges = []
    for i in range(files.num_files()):
        start = files.file_offset(i)
        size = files.file_size(i)
        if size <= 0:
            ranges.append([])
            continue
        first = start // piece_length
        last = (start + size - 1) // piece_length
        ranges.append(list(range(first, last + 1)))
    return ranges


def _cleanup_part_files(out_dir: str):
    """Remove the empty `.torrents` piece-padding dir libtorrent may have
    left behind (a non-empty one holds incomplete pieces — keep it so a
    later run can resume from it)."""
    pad = os.path.join(out_dir, ".torrents")
    try:
        if os.path.isdir(pad) and not os.listdir(pad):
            os.rmdir(pad)
    except OSError:
        pass


def _drain_alerts(ses, log) -> None:
    """Drain the session's pending alerts: error notifications are logged as
    dim lines, and storage errors (disk full, permission denied, etc.) are
    turned into :class:`TorrentDiskError`. Only actual failure alerts are
    handled, not successful ones like file_completed_alert — read_piece_alert
    fires on explicit read_piece() calls and is normal."""
    import libtorrent as lt

    for a in ses.pop_alerts():
        if a.category() & lt.alert.category_t.error_notification:
            log(
                f"  {type(a).__name__}: {getattr(a, 'message', str)()}",
                "dim",
            )
        if a.category() & lt.alert.category_t.storage_notification:
            if type(a).__name__ in (
                "file_error_alert",
                "file_rename_failed_alert",
                "torrent_delete_failed_alert",
                "storage_moved_failed_alert",
                "save_resume_data_failed_alert",
            ):
                log(
                    f"  Storage error: {type(a).__name__}: {getattr(a, 'message', str)()}",
                    "err",
                )
                raise TorrentDiskError(
                    f"Storage error: {type(a).__name__}: {getattr(a, 'message', str)()}"
                )


def _checking_states(lt):
    """The libtorrent torrent states during which a hash recheck is running
    (in libtorrent 2.1 checking can be in multiple states)."""
    return {
        lt.torrent_status.states.checking_files,
        lt.torrent_status.states.checking_resume_data,
        lt.torrent_status.states.queued_for_checking,
    }


def _translate_disk_error(e) -> None:
    """Raise :class:`TorrentDiskError` when an OSError is a disk-space or
    permission failure (ENOSPC/EACCES); return otherwise so the caller can
    re-raise the original error."""
    if e.errno in (28, 13):  # ENOSPC, EACCES
        raise TorrentDiskError(f"Disk I/O error: {e}") from e


def _release_handle(ses, h) -> None:
    """Pause and remove the torrent handle from the session, ignoring any
    teardown errors (best-effort cleanup)."""
    try:
        h.pause()
        ses.remove_torrent(h)
    except Exception:
        pass


def _wrap_session_error(fn, msg: str):
    """Run a libtorrent session call, wrapping any failure in a
    :class:`TorrentSessionError` (shared by session creation and
    add_torrent on both the verifier and downloader paths)."""
    try:
        return fn()
    except Exception as e:
        raise TorrentSessionError(f"{msg}: {e}") from e


class TorrentVerifier(WorkerBase):
    """Torrent-only integrity check (no manifest needed).

    Fetches the ``.torrent`` and asks libtorrent to hash-check the existing
    files against the embedded piece hashes, without downloading anything
    (every file priority is 0). After the recheck completes, a file is stale
    when any piece covering it is not present on disk. Returns the stale file
    paths; an empty list means the client is already up to date.

    Like ``TorrentDownloader`` this verifies at *piece* granularity — a
    ``.torrent`` carries per-piece hashes, not per-file SHA-1s — so a stale
    piece that straddles two files can mark both. The follow-up download only
    fetches the missing pieces, and the piece hashes still guarantee the
    final integrity.
    """

    def __init__(
        self,
        out_dir: str,
        log_q: queue.Queue | None = None,
        prog_q: queue.Queue | None = None,
        *,
        dispatcher=None,
    ):
        super().__init__(out_dir, log_q, prog_q, dispatcher=dispatcher)
        self.snapshot: TorrentSnapshot | None = None

    def _session(self):
        import libtorrent as lt

        return lt.session(
            {
                "listen_interfaces": "",
                "user_agent": UA,
                "upload_rate_limit": UPLOAD_RATE_LIMIT,
                "enable_dht": False,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
                "alert_mask": VERIFIER_ALERT_MASK,
            }
        )

    def _stale_files(
        self, h, files, piece_length: int, root_marker: str = "WoW.exe"
    ) -> list[str]:
        """Files whose covering pieces are not all present after the recheck,
        with the torrent root directory stripped to match the manifest
        layout.  The root is auto-detected from the unique root marker
        position."""
        mapping = _map_torrent_paths(files, root_marker)
        ranges = _file_piece_ranges(files, piece_length)
        stale = []
        for i, pieces in enumerate(ranges):
            if pieces and not all(h.have_piece(p) for p in pieces):
                tp = files.file_path(i).replace("\\", "/")
                stale.append(mapping[tp])
        return stale

    def verify(
        self,
        torrent_url: str,
        snapshot=None,
        root_marker: str | None = None,
    ) -> list[str]:
        """Hash-check the local files against the torrent and return the stale
        (missing or differing) file paths. Raises RuntimeError on failure or
        cancellation. Never downloads or seeds — read-only. This is a
        torrent-piece check; the update controller performs the authoritative
        manifest hash check afterwards.

        ``snapshot`` may be a pre-fetched :class:`TorrentSnapshot`; when given,
        it is used directly instead of fetching the ``.torrent`` again (the
        caller fetches once to compare identity before deciding whether a
        recheck is even needed). When ``None``, the snapshot is fetched here as
        before.

        The fetched/pre-fetched :class:`TorrentSnapshot` is stored on
        ``self.snapshot`` so the caller can persist its identity alongside the
        verdict."""
        import libtorrent as lt

        if snapshot is None:
            snapshot = _fetch_torrent(
                torrent_url, self.log, cancel=lambda: self._cancel
            )
        self.snapshot = snapshot
        ti = snapshot.torrent_info
        marker = root_marker or _configured_root_marker()
        _remap_torrent_to_out_dir(ti, self.out_dir, marker)
        files = ti.files()
        piece_length = ti.piece_length()
        total_pieces = ti.num_pieces()

        ses = _wrap_session_error(
            self._session, "Failed to create libtorrent session"
        )

        h = None
        try:
            atp = lt.add_torrent_params()
            atp.ti = ti
            atp.save_path = self.out_dir
            # Pieces must be "wanted" (priority > 0) for force_recheck() to
            # hash the on-disk files against the torrent's piece hashes. A
            # priority of 0 skips both download and verification, which would
            # leave every piece's verified state False and stall the recheck.
            # The verifier session is fully offline (empty listen_interfaces,
            # DHT/LSD/UPnP/NAT-PMP off, no trackers), so max priority only
            # triggers a read-only hash check — no peer connections or writes.
            atp.file_priorities = [7] * files.num_files()
            h = _wrap_session_error(
                lambda: ses.add_torrent(atp),
                "Failed to add torrent to session",
            )
            h.force_recheck()
            # Deluge's proven pattern: resume() after force_recheck() so the
            # recheck actually proceeds even if the torrent was added paused
            # (some bindings add it paused by default). The recheck is a
            # read-only hash of the on-disk files; resume() does not start any
            # peer connection in this offline session.
            h.resume()
            self._wait_for_recheck(ses, h, total_pieces)
            return self._stale_files(h, files, piece_length, marker)
        except OSError as e:
            _translate_disk_error(e)
            raise
        finally:
            if h is not None:
                _release_handle(ses, h)
            _cleanup_part_files(self.out_dir)

    def _wait_for_recheck(self, ses, h, total_pieces: int):
        """Block until libtorrent's hash recheck of the existing files is done
        (or the swarm/folder can't produce a finished recheck), honouring
        cancel and a stall guard."""
        import libtorrent as lt

        last_move = time.monotonic()
        last_checked = 0
        seen_checking = False
        # In libtorrent 2.1, checking can be in multiple states
        checking_states = _checking_states(lt)
        while not self._cancel:
            _drain_alerts(ses, self.log)
            # status() is synchronous in the Python binding. This worker is
            # isolated from the UI thread, so the direct snapshot is simpler
            # than coordinating post_status/state_update_alert callbacks.
            s = h.status()
            # The authoritative "pieces verified so far" during a force_recheck()
            # is the live "have" bitfield. In libtorrent 2.x status().pieces is a
            # list[bool] (sum() counts the verified pieces) and it advances as each
            # piece is hashed — this is what drives progress. torrent_status
            # .verified_pieces is ONLY populated in seed mode (the verifier is NOT
            # in seed mode), so it stays empty and must never be used. status
            # ().progress may also lag a recheck, so we take the max of the
            # have-count and progress.
            have = 0
            pieces = getattr(s, "pieces", None)
            if pieces is not None:
                have = sum(pieces)
            elif getattr(s, "num_pieces", None):
                have = s.num_pieces
            checked = (
                int(round(s.progress * total_pieces)) if total_pieces else 0
            )
            done = max(have, checked)
            if s.state in checking_states:
                # Actively hashing: never false-stall a slow multi-GB recheck,
                # and remember we entered checking so we only finish once it ends.
                seen_checking = True
                last_move = time.monotonic()
            elif seen_checking:
                # Recheck has left the checking states -> it is done. have_piece()
                # (used by _stale_files) is authoritative for the verdict, so we
                # don't require a non-zero count here. Emit a final 100% so the
                # progress label doesn't freeze below completion.
                self.progress(
                    1.0,
                    f"Verifying client against torrent…  {total_pieces} / "
                    f"{total_pieces} pieces",
                    phase="Verifying",
                    transport="BitTorrent",
                    verified_pieces=total_pieces,
                    total_pieces=total_pieces,
                )
                return
            if total_pieces and done >= total_pieces:
                # All pieces present (verified or assumed): recheck finished.
                # Emit a final 100% before returning so the bar reaches 100%.
                self.progress(
                    1.0,
                    f"Verifying client against torrent…  {total_pieces} / "
                    f"{total_pieces} pieces",
                    phase="Verifying",
                    transport="BitTorrent",
                    verified_pieces=total_pieces,
                    total_pieces=total_pieces,
                )
                return
            if done != last_checked:
                last_checked = done
                last_move = time.monotonic()
            self.progress(
                min(1.0, done / total_pieces) if total_pieces else 0.0,
                f"Verifying client against torrent…  {done} / "
                f"{total_pieces} pieces",
                phase="Verifying",
                transport="BitTorrent",
                verified_pieces=done,
                total_pieces=total_pieces,
            )
            if time.monotonic() - last_move > STALL_TIMEOUT:
                raise TorrentStalledError(peers=s.num_peers)
            ses.wait_for_alert(ALERT_POLL_MS)
        self._raise_cancelled(h)


class TorrentDownloader(WorkerBase):
    def __init__(
        self,
        out_dir: str,
        log_q: queue.Queue | None = None,
        prog_q: queue.Queue | None = None,
        *,
        dispatcher=None,
    ):
        super().__init__(out_dir, log_q, prog_q, dispatcher=dispatcher)
        self.snapshot: TorrentSnapshot | None = None

    def _priorities(
        self, ti, wanted: set[str] | None, root_marker: str = "WoW.exe"
    ) -> list[int]:
        """Per-file priorities: stale files at max priority, everything else
        skipped (0) so only the pieces covering the stale files download.
        ``wanted=None`` means the whole torrent (every file at max priority)
        — used by the no-manifest recovery path.  Uses the auto-detected
        root-marker root for path mapping.

        A wanted path absent from the snapshot is a hard mismatch
        (:class:`TorrentSnapshotMismatchError`) — the torrent was replaced
        between verify and download, so the client can never be reported
        recovered against a snapshot that no longer contains it."""
        files = ti.files()
        mapping = _map_torrent_paths(files, root_marker)
        n = files.num_files()
        if wanted is None:
            return [7] * n
        local_to_index = {
            mapping[files.file_path(i).replace("\\", "/")]: i for i in range(n)
        }
        missing = sorted(w for w in wanted if w not in local_to_index)
        if missing:
            self.log(
                f"[torrent] {len(missing)} wanted file(s) absent from this "
                f"snapshot: {', '.join(missing)}",
                "err",
            )
            raise TorrentSnapshotMismatchError(
                f"Torrent replaced — {len(missing)} wanted file(s) not in "
                f"the new snapshot: {', '.join(missing)}"
            )
        return [
            7
            if mapping[files.file_path(i).replace("\\", "/")] in wanted
            else 0
            for i in range(n)
        ]

    def _session(self):
        import libtorrent as lt

        return lt.session(_network_session_settings())

    def download(
        self,
        torrent_url: str,
        wanted: set[str] | None,
        root_marker: str | None = None,
    ) -> list[str]:
        """Download the wanted files from the torrent at ``torrent_url`` into
        ``out_dir``. ``wanted=None`` downloads the whole torrent. Returns an
        empty list on success and raises RuntimeError on failure or
        cancellation. The caller already knows the wanted paths. Completed
        files are still rechecked against the update manifest by the HTTP
        update worker.

        Resume data is intentionally not persisted — libtorrent re-derives
        piece state from disk on add (see BITTORRENT_UPDATER_NOTES.md P5).
        The fetched :class:`TorrentSnapshot` is stored on ``self.snapshot``
        so its identity can be cached for later verifies."""
        import libtorrent as lt

        if wanted is not None and not wanted:
            return []
        snapshot = _fetch_torrent(
            torrent_url, self.log, cancel=lambda: self._cancel
        )
        self.snapshot = snapshot
        ti = snapshot.torrent_info
        marker = root_marker or _configured_root_marker()
        _remap_torrent_to_out_dir(ti, self.out_dir, marker)

        ses = _wrap_session_error(
            self._session, "Failed to create libtorrent session"
        )

        h = None
        try:
            atp = lt.add_torrent_params()
            priorities = self._priorities(ti, wanted, marker)
            atp.ti = ti
            atp.save_path = self.out_dir
            atp.file_priorities = priorities
            files = ti.files()
            total_wanted = sum(
                files.file_size(i)
                for i in range(files.num_files())
                if priorities[i] > 0
            )
            wanted_count = sum(1 for p in priorities if p > 0)
            h = _wrap_session_error(
                lambda: ses.add_torrent(atp),
                "Failed to add torrent to session",
            )
            # The binding adds the torrent paused; resume() starts it so it
            # checks the on-disk files and then downloads only the wanted
            # pieces (mirrors the verify path's force_recheck()+resume()).
            # Resume data is intentionally not loaded, so libtorrent re-derives
            # piece state from disk instead of trusting a possibly-stale cache.
            h.resume()
            return self._pump(
                ses,
                h,
                total_wanted=total_wanted,
                wanted_count=wanted_count,
            )
        except OSError as e:
            _translate_disk_error(e)
            raise
        finally:
            if h is not None:
                _release_handle(ses, h)
            _cleanup_part_files(self.out_dir)

    def _pump(
        self,
        ses,
        h,
        *,
        total_wanted: int,
        wanted_count: int,
    ) -> list[str]:
        """Alert loop: report progress, detect errors/stalls, honour cancel.
        Returns the wanted paths once the torrent is finished.

        *total_wanted* and *wanted_count* are pre-computed from the torrent
        file list and priorities — they provide a stable denominator from the
        first poll iteration without waiting for libtorrent's status."""
        import libtorrent as lt

        checking_states = _checking_states(lt)

        last_wanted_done = 0
        last_move = time.monotonic()
        transfer_started = False
        name = ""
        while not self._cancel:
            _drain_alerts(ses, self.log)
            # status() is synchronous in the Python binding. This worker is
            # isolated from the UI thread, so the direct snapshot is simpler
            # than coordinating post_status/state_update_alert callbacks.
            s = h.status()
            name = s.name or name
            wanted_done = s.total_wanted_done
            if wanted_done != last_wanted_done:
                last_wanted_done = wanted_done
                last_move = time.monotonic()
                transfer_started = True
            # Deliberately NOT refreshed by peer count alone: a swarm that
            # accepts connections but never delivers bytes is dead, and the
            # stall timeout must fire so the HTTP fallback can take over.
            # While libtorrent is hashing the on-disk files (the initial
            # recheck that re-derives piece state now that resume data is not
            # loaded), keep the stall timer alive so a slow multi-GB recheck
            # can't exceed DISCOVERY_TIMEOUT and raise TorrentStalledError
            # before any byte is downloaded. Mirrors _wait_for_recheck. Use
            # getattr so a status object lacking `.state` (e.g. a bare fake)
            # is treated as "not checking" rather than erroring.
            if getattr(s, "state", None) in checking_states:
                last_move = time.monotonic()
            if s.is_finished or (
                total_wanted > 0 and wanted_done >= total_wanted
            ):
                self.progress(
                    1.0,
                    name,
                    phase="Torrent complete",
                    transport="BitTorrent",
                    current_file=name,
                    downloaded=total_wanted,
                    total=total_wanted,
                    speed=s.download_rate,
                    peers=s.num_peers,
                )
                return []
            total = total_wanted or 1
            speed = fmt_speed(s.download_rate) if s.download_rate else ""
            peers = f"   •   {s.num_peers} peers" if s.num_peers else ""
            self.progress(
                min(1.0, wanted_done / total),
                f"{name}   •   {fmt_size(wanted_done)} / "
                f"{fmt_size(total_wanted)}"
                f"   •   {wanted_count} files"
                f"{'   •   ' + speed if speed else ''}{peers}",
                phase="Downloading",
                transport="BitTorrent",
                current_file=name,
                downloaded=wanted_done,
                total=total_wanted,
                speed=s.download_rate,
                peers=s.num_peers,
            )
            elapsed = time.monotonic() - last_move
            timeout = STALL_TIMEOUT if transfer_started else DISCOVERY_TIMEOUT
            if elapsed > timeout:
                raise TorrentStalledError(peers=s.num_peers)
            ses.wait_for_alert(ALERT_POLL_MS)
        self._raise_cancelled(h)
