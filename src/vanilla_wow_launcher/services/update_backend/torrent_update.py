"""BitTorrent update backend for client updates (libtorrent).

`TorrentDownloader` fetches a ``.torrent`` over HTTPS (through the same
hardened, allowlisted transport as the HTTP downloads) and uses libtorrent to
bulk-download the files the manifest flagged as stale. Peers in the swarm are
untrusted — a malicious peer can only inject data that fails the piece hashes
embedded in the ``.torrent`` (which itself came over TLS) — and the caller
still re-verifies every file against the manifest's SHA-1 afterwards, so the
torrent backend cannot weaken the integrity guarantee of the HTTP path.

Uploads are limited to a minimal rate (1 B/s) and the torrent is paused and
removed from the session once every wanted piece is in place.
"""

import os
import queue
import tempfile
import time
import urllib.error
import urllib.request

from ...core.constants import DOWNLOAD_TIMEOUT, UA
from ...core.helpers import fmt_size, fmt_speed
from ...core.security_http import allowed_download_hosts, secure_urlopen

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
# Minimal upload rate (1 B/s) — libtorrent treats 0 as unlimited, not disabled.
UPLOAD_RATE_LIMIT = 1
# Alert categories we care about: error, storage (for failures), status (for
# state changes), and tracker/DHT if we want more detail.
ALERT_MASK = (
    1  # error_notification
    | 8  # storage_notification
    | 16  # tracker_notification
    | 64  # status_notification
    | 1024  # dht_notification
)


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


def _fetch_torrent(torrent_url: str, log):
    """Fetch the ``.torrent`` over the allowlisted HTTPS transport and parse
    it with libtorrent. Returns the ``torrent_info``.

    Network/security failures (HTTP errors, connection refused, DNS, TLS,
    allowlist rejection) are wrapped in :class:`TorrentFetchError` so the
    caller can distinguish a *missing* snapshot from a *failed* verification.
    """
    import libtorrent as lt

    log(f"  Fetching torrent: {torrent_url}", "dim")
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

    fd, tmp = tempfile.mkstemp(suffix=".torrent")
    try:
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(data)
        except OSError as e:
            raise TorrentDiskError(f"Failed to write torrent file: {e}") from e
        try:
            return lt.torrent_info(tmp)
        except Exception as e:
            raise TorrentCorruptError(f"Failed to parse torrent: {e}") from e
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class TorrentLayoutError(TorrentCorruptError):
    """Raised when the torrent's file layout cannot be mapped to the local
    client directory (missing or duplicate WoW.exe, path traversal, etc.)."""

    pass


def _detect_torrent_root(
    files,
) -> tuple[str, dict[str, str]]:
    """Detect the torrent root directory from the unique ``WoW.exe`` position.

        Scans every file path in the torrent (case-insensitive) looking for the
        single entry whose basename is ``WoW.exe``.  The parent directory of that
        entry is the *root* — all other torrent paths are expected to live under
        the same root.

        Returns ``(torrent_root, {torrent_path: local_path})`` where:

        * ``torrent_root`` is the leading directory to strip (e.g. ``client``).
        * ``local_path`` is the path relative to the selected WoW folder
          (e.g. ``Data/foo.mpq``).

        Raises :class:`TorrentLayoutError` when:

    * no ``WoW.exe`` is found,
        * multiple ``WoW.exe`` entries exist, or
        * any file escapes the detected root directory."""
    num = files.num_files()
    exe_indices: list[int] = []
    normalized: list[str] = []
    for i in range(num):
        p = files.file_path(i).replace("\\", "/")
        normalized.append(p)
        if p.rsplit("/", 1)[-1].lower() == "wow.exe":
            exe_indices.append(i)

    if not exe_indices:
        raise TorrentLayoutError(
            "Torrent contains no WoW.exe — cannot detect root directory"
        )
    if len(exe_indices) > 1:
        paths = [normalized[i] for i in exe_indices]
        raise TorrentLayoutError(
            f"Torrent contains multiple WoW.exe entries: {paths}"
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
) -> dict[str, str]:
    """Convenience wrapper around :func:`_detect_torrent_root` that returns
    only the ``{torrent_path: local_path}`` mapping."""
    _, mapping = _detect_torrent_root(files)
    return mapping


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


class TorrentVerifier:
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

    def __init__(self, out_dir: str, log_q: queue.Queue, prog_q: queue.Queue):
        self.out_dir = out_dir
        self.log_q = log_q
        self.prog_q = prog_q
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def log(self, msg: str, tag: str = ""):
        self.log_q.put((msg, tag))

    def progress(self, value: float, label: str = "", **details):
        item = (value, label, details) if details else (value, label)
        self.prog_q.put(item)

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
                "alert_mask": ALERT_MASK,
            }
        )

    def _stale_files(self, h, files, piece_length: int) -> list[str]:
        """Files whose covering pieces are not all present after the recheck,
        with the torrent root directory stripped to match the manifest
        layout.  The root is auto-detected from the unique WoW.exe position."""
        mapping = _map_torrent_paths(files)
        ranges = _file_piece_ranges(files, piece_length)
        stale = []
        for i, pieces in enumerate(ranges):
            if pieces and not all(h.have_piece(p) for p in pieces):
                tp = files.file_path(i).replace("\\", "/")
                stale.append(mapping[tp])
        return stale

    def verify(self, torrent_url: str) -> list[str]:
        """Hash-check the local files against the torrent and return the stale
        (missing or differing) file paths. Raises RuntimeError on failure or
        cancellation. Never downloads or seeds — read-only."""
        import libtorrent as lt

        ti = _fetch_torrent(torrent_url, self.log)
        files = ti.files()
        piece_length = ti.piece_length()
        total_pieces = ti.num_pieces()

        try:
            ses = self._session()
        except Exception as e:
            raise TorrentSessionError(
                f"Failed to create libtorrent session: {e}"
            ) from e

        h = None
        try:
            atp = lt.add_torrent_params()
            atp.ti = ti
            atp.save_path = self.out_dir
            atp.file_priorities = [0] * files.num_files()  # verify only
            try:
                h = ses.add_torrent(atp)
            except Exception as e:
                raise TorrentSessionError(
                    f"Failed to add torrent to session: {e}"
                ) from e
            h.force_recheck()
            self._wait_for_recheck(ses, h, total_pieces)
            return self._stale_files(h, files, piece_length)
        except OSError as e:
            if e.errno in (28, 13):  # ENOSPC, EACCES
                raise TorrentDiskError(f"Disk I/O error: {e}") from e
            raise
        finally:
            if h is not None:
                try:
                    h.pause()
                    ses.remove_torrent(h)
                except Exception:
                    pass
            self._cleanup_part_files()

    def _wait_for_recheck(self, ses, h, total_pieces: int):
        """Block until libtorrent's hash recheck of the existing files is done
        (or the swarm/folder can't produce a finished recheck), honouring
        cancel and a stall guard."""
        import libtorrent as lt

        last_move = time.monotonic()
        last_checked = 0
        # In libtorrent 2.1, checking can be in multiple states
        checking_states = {
            lt.torrent_status.states.checking_files,
            lt.torrent_status.states.checking_resume_data,
            lt.torrent_status.states.queued_for_checking,
        }
        while not self._cancel:
            for a in ses.pop_alerts():
                if a.category() & lt.alert.category_t.error_notification:
                    self.log(
                        f"  {type(a).__name__}: {getattr(a, 'message', str)()}",
                        "dim",
                    )
                # Storage errors (disk full, permission denied, etc.)
                # Only handle actual failure alerts, not successful ones like
                # file_completed_alert.
                if a.category() & lt.alert.category_t.storage_notification:
                    # Check if it's a failure alert (not read_piece_alert —
                    # that fires on explicit read_piece() calls and is normal).
                    if type(a).__name__ in (
                        "file_error_alert",
                        "storage_moved_failed_alert",
                        "save_resume_data_failed_alert",
                    ):
                        self.log(
                            f"  Storage error: {type(a).__name__}: {getattr(a, 'message', str)()}",
                            "err",
                        )
                        raise TorrentDiskError(
                            f"Storage error: {type(a).__name__}: {getattr(a, 'message', str)()}"
                        )
            s = h.status()
            # libtorrent 2.1: use verified_pieces for checking progress,
            # and state to detect if checking is still active.
            # verified_pieces is a list[bool] - count the True values.
            done = sum(s.verified_pieces) if s.verified_pieces else 0
            if done != last_checked:
                last_checked = done
                last_move = time.monotonic()
            if total_pieces and done >= total_pieces:
                return
            if s.state not in checking_states and done:
                # Recheck finished (some sources report a partial count).
                return
            self.progress(
                min(1.0, done / total_pieces) if total_pieces else 0.0,
                "Verifying client against torrent…",
                phase="Verifying",
                transport="BitTorrent",
                downloaded=done,
                total=total_pieces,
            )
            if time.monotonic() - last_move > STALL_TIMEOUT:
                raise TorrentStalledError(peers=s.num_peers)
            ses.wait_for_alert(ALERT_POLL_MS)
        raise RuntimeError("Cancelled")

    def _cleanup_part_files(self):
        pad = os.path.join(self.out_dir, ".torrents")
        try:
            if os.path.isdir(pad) and not os.listdir(pad):
                os.rmdir(pad)
        except OSError:
            pass


class TorrentDownloader:
    def __init__(self, out_dir: str, log_q: queue.Queue, prog_q: queue.Queue):
        self.out_dir = out_dir
        self.log_q = log_q
        self.prog_q = prog_q
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def log(self, msg: str, tag: str = ""):
        self.log_q.put((msg, tag))

    def progress(self, value: float, label: str = "", **details):
        item = (value, label, details) if details else (value, label)
        self.prog_q.put(item)

    def _priorities(self, ti, wanted: set[str] | None) -> list[int]:
        """Per-file priorities: stale files at max priority, everything else
        skipped (0) so only the pieces covering the stale files download.
        ``wanted=None`` means the whole torrent (every file at max priority)
        — used by the no-manifest recovery path.  Uses the auto-detected
        WoW.exe root for path mapping."""
        files = ti.files()
        mapping = _map_torrent_paths(files)
        return [
            7
            if wanted is None
            or mapping[files.file_path(i).replace("\\", "/")] in wanted
            else 0
            for i in range(files.num_files())
        ]

    def _session(self):
        import libtorrent as lt

        return lt.session(
            {
                "listen_interfaces": LISTEN_INTERFACES,
                "user_agent": UA,
                "upload_rate_limit": UPLOAD_RATE_LIMIT,
                "enable_dht": True,
                "dht_nodes": DHT_BOOTSTRAP_NODES,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
                "alert_mask": ALERT_MASK,
            }
        )

    def download(self, torrent_url: str, wanted: set[str] | None) -> list[str]:
        """Download the wanted files from the torrent at ``torrent_url`` into
        ``out_dir``. ``wanted=None`` downloads the whole torrent. Returns the
        sorted wanted paths on success and raises RuntimeError on failure or
        cancellation."""
        import libtorrent as lt

        if wanted is not None and not wanted:
            return []
        ti = _fetch_torrent(torrent_url, self.log)

        try:
            ses = self._session()
        except Exception as e:
            raise TorrentSessionError(
                f"Failed to create libtorrent session: {e}"
            ) from e

        h = None
        try:
            atp = lt.add_torrent_params()
            atp.ti = ti
            atp.save_path = self.out_dir
            priorities = self._priorities(ti, wanted)
            atp.file_priorities = priorities
            files = ti.files()
            total_wanted = sum(
                files.file_size(i)
                for i in range(files.num_files())
                if priorities[i] > 0
            )
            wanted_count = sum(1 for p in priorities if p > 0)
            try:
                h = ses.add_torrent(atp)
            except Exception as e:
                raise TorrentSessionError(
                    f"Failed to add torrent to session: {e}"
                ) from e
            return self._pump(
                ses,
                h,
                total_wanted=total_wanted,
                wanted_count=wanted_count,
            )
        except OSError as e:
            if e.errno in (28, 13):  # ENOSPC, EACCES
                raise TorrentDiskError(f"Disk I/O error: {e}") from e
            raise
        finally:
            if h is not None:
                try:
                    h.pause()
                    ses.remove_torrent(h)
                except Exception:
                    pass
            self._cleanup_part_files()

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

        last_wanted_done = 0
        last_move = time.monotonic()
        transfer_started = False
        name = ""
        while not self._cancel:
            for a in ses.pop_alerts():
                if a.category() & lt.alert.category_t.error_notification:
                    self.log(
                        f"  {type(a).__name__}: {getattr(a, 'message', str)()}",
                        "dim",
                    )
                # Storage errors (disk full, permission denied, etc.)
                # Only handle actual failure alerts, not successful ones like
                # file_completed_alert.
                if a.category() & lt.alert.category_t.storage_notification:
                    if type(a).__name__ in (
                        "file_error_alert",
                        "storage_moved_failed_alert",
                        "save_resume_data_failed_alert",
                    ):
                        self.log(
                            f"  Storage error: {type(a).__name__}: {getattr(a, 'message', str)()}",
                            "err",
                        )
                        raise TorrentDiskError(
                            f"Storage error: {type(a).__name__}: {getattr(a, 'message', str)()}"
                        )
            s = h.status()
            name = s.name or name
            wanted_done = s.total_wanted_done
            if wanted_done != last_wanted_done:
                last_wanted_done = wanted_done
                last_move = time.monotonic()
                transfer_started = True
            # Reset stall timer when peers connect — the session is alive.
            if s.num_peers > 0:
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
        try:
            h.cancel()
        except Exception:
            pass
        raise RuntimeError("Cancelled")

    def _cleanup_part_files(self):
        """Remove the empty `.torrents` piece-padding dir libtorrent may have
        left behind (a non-empty one holds incomplete pieces — keep it so a
        later run can resume from it)."""
        pad = os.path.join(self.out_dir, ".torrents")
        try:
            if os.path.isdir(pad) and not os.listdir(pad):
                os.rmdir(pad)
        except OSError:
            pass
