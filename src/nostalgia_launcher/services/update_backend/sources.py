"""Download-source resolution for the client update backends.

The active `DownloadSource` is now derived solely from the server's
``server.download`` block (no mirror failover): the explicit HTTP manifest /
client URLs and the optional BitTorrent ``torrent_url`` / ``magnet``. Kept
separate from the worker engines so both `VerifyWorker` and `UpdateWorker`
share one definition; `http_update` re-exports these names for compatibility.
"""

import urllib.request
from typing import NamedTuple
from urllib.error import HTTPError

from ...core.constants import UA
from ...core.log_sink import debug_emit
from ...core.security_http import allowed_download_hosts, secure_urlopen


class DownloadSource(NamedTuple):
    """The resolved endpoints of the active download source."""

    manifest_url: str
    client_url: str
    torrent_url: str | None = None
    # Server-only alternative to torrent_url (a torrent has one swarm, so
    # mirrors — an HTTP-download concept — never carry a magnet).
    torrent_magnet: str | None = None

    @property
    def torrent_locator(self) -> "str | None":
        """The advertised torrent snapshot locator: the HTTPS ``.torrent``
        URL when one exists (the stronger guarantee), else the server's
        ``magnet:`` URI."""
        return self.torrent_url or self.torrent_magnet


def _source_reachable(url: str) -> bool:
    """Whether a download source answers at `url`. Any HTTP response — even an
    error status (4xx/5xx) — proves the host is reachable; only transport
    failures (DNS, refused, timeout) count as down."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with secure_urlopen(
            req,
            timeout=5,
            allowed_hosts=allowed_download_hosts(),
        ) as r:
            r.read(1)
        return True
    except HTTPError:
        return True
    except Exception:
        return False


def _download_source() -> "DownloadSource | None":
    """Resolve the active download source from the server's ``download`` block.

    Returns None when the launcher configuration is missing.
    A torrent-only source (no HTTP endpoints) is a valid download source."""
    from ...core import launcher

    cfg = launcher.config()
    if cfg is None:
        return None
    debug_emit(
        f"[torrent] selected server {cfg.server_name} "
        f"(torrent={'yes' if cfg.download_torrent_url else 'no'}, "
        f"magnet={'yes' if cfg.download_torrent_magnet else 'no'})"
    )
    return DownloadSource(
        cfg.download_manifest_url or "",
        cfg.download_client_url or "",
        cfg.download_torrent_url,
        cfg.download_torrent_magnet,
    )
