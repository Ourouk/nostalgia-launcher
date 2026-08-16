"""BitTorrent download backend for client updates (libtorrent).

`TorrentDownloader` fetches a ``.torrent`` over HTTPS (through the same
hardened, allowlisted transport as the HTTP downloads) and uses libtorrent to
bulk-download the files the manifest flagged as stale. Peers in the swarm are
untrusted — a malicious peer can only inject data that fails the piece hashes
embedded in the ``.torrent`` (which itself came over TLS) — and the caller
still re-verifies every file against the manifest's SHA-1 afterwards, so the
torrent backend cannot weaken the integrity guarantee of the HTTP path.

The torrent is never seeded: uploads are rate-limited to zero and the torrent
is paused and removed from the session once every wanted piece is in place.
"""

import os
import queue
import tempfile
import time
import urllib.request

from ..core.constants import DOWNLOAD_TIMEOUT, UA
from ..core.helpers import fmt_size, fmt_speed
from ..core.security_http import allowed_download_hosts, secure_urlopen

# Inactivity guard: if no wanted bytes arrive for this long, the swarm is dead
# and the caller should fall back to per-file HTTP downloads.
STALL_TIMEOUT = 60
LISTEN_INTERFACES = "0.0.0.0:6881,6889"
ALERT_POLL_MS = 250


def available() -> bool:
    """Whether the libtorrent python module can be imported. Probed lazily so
    the app degrades gracefully to HTTP when it isn't installed."""
    import importlib.util

    try:
        return importlib.util.find_spec("libtorrent") is not None
    except (ValueError, ImportError):
        return False


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

    def progress(self, value: float, label: str = ""):
        self.prog_q.put((value, label))

    @staticmethod
    def _strip_root(paths: list[str]) -> list[str]:
        """Drop a single leading directory shared by every torrent file (the
        typical ``<torrent-name>/...`` wrapper) so the paths match the
        manifest layout."""
        if not paths:
            return paths
        root = paths[0].split("/")[0]
        if root and all(p.startswith(root + "/") for p in paths):
            return [p[len(root) + 1 :] for p in paths]
        return paths

    def _priorities(self, ti, wanted: set[str]) -> list[int]:
        """Per-file priorities: stale files at max priority, everything else
        skipped (0) so only the pieces covering the stale files download."""
        files = ti.files()
        paths = self._strip_root(
            [
                files.file_path(i).replace("\\", "/")
                for i in range(files.num_files())
            ]
        )
        return [7 if p in wanted else 0 for p in paths]

    def download(self, torrent_url: str, wanted: set[str]) -> list[str]:
        """Download the wanted files from the torrent at ``torrent_url`` into
        ``out_dir``. Returns the sorted wanted paths on success and raises
        RuntimeError on failure or cancellation."""
        import libtorrent as lt

        if not wanted:
            return []
        self.log(f"  Fetching torrent: {torrent_url}", "dim")
        req = urllib.request.Request(torrent_url, headers={"User-Agent": UA})
        with secure_urlopen(
            req,
            timeout=DOWNLOAD_TIMEOUT,
            allowed_hosts=allowed_download_hosts(),
        ) as r:
            data = r.read()

        fd, tmp = tempfile.mkstemp(suffix=".torrent")
        try:
            os.close(fd)
            with open(tmp, "wb") as f:
                f.write(data)
            ti = lt.torrent_info(tmp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

        ses = lt.session(
            {
                "listen_interfaces": LISTEN_INTERFACES,
                "user_agent": UA,
                "upload_rate_limit": 0,
                "enable_dht": True,
                "enable_pex": True,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
            }
        )
        h = None
        try:
            atp = lt.add_torrent_params()
            atp.ti = ti
            atp.save_path = self.out_dir
            atp.file_priorities = self._priorities(ti, wanted)
            h = ses.add_torrent(atp)
            return self._pump(ses, h)
        finally:
            if h is not None:
                try:
                    h.pause()
                    ses.remove_torrent(h)
                except Exception:
                    pass
            self._cleanup_part_files()

    def _pump(self, ses, h) -> list[str]:
        """Alert loop: report progress, detect errors/stalls, honour cancel.
        Returns the wanted paths once the torrent is finished."""
        import libtorrent as lt

        last_wanted_done = 0
        last_move = time.monotonic()
        name = ""
        while not self._cancel:
            for a in ses.pop_alerts():
                if a.category() & lt.alert.category_t.error_notification:
                    self.log(
                        f"  {type(a).__name__}: {getattr(a, 'message', str)()}",
                        "dim",
                    )
            s = h.status()
            name = s.name or name
            wanted_done = s.total_wanted_done
            if wanted_done != last_wanted_done:
                last_wanted_done = wanted_done
                last_move = time.monotonic()
            if s.is_finished or (
                s.total_wanted > 0 and wanted_done >= s.total_wanted
            ):
                self.progress(1.0, name)
                return []
            total = s.total_wanted or 1
            speed = fmt_speed(s.download_rate) if s.download_rate else ""
            peers = f"   •   {s.num_peers} peers" if s.num_peers else ""
            self.progress(
                min(1.0, wanted_done / total),
                f"{name}   •   {fmt_size(wanted_done)} / "
                f"{fmt_size(s.total_wanted)}"
                f"{'   •   ' + speed if speed else ''}{peers}",
            )
            if time.monotonic() - last_move > STALL_TIMEOUT:
                raise RuntimeError(
                    "BitTorrent stalled — no data received for "
                    f"{STALL_TIMEOUT} s ({s.num_peers} peers)."
                )
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
