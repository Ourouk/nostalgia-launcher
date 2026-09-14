"""Download-source resolution for the client update backends.

The active `DownloadSource` is now derived solely from the server's
``server.download`` block (no mirror failover): the optional HTTP
fallback (single zip) and the optional BitTorrent ``torrent_url`` /
``magnet``. Kept separate from the worker engines so both
`VerifyWorker` and `UpdateWorker` share one definition; the workers
re-export these names through `services.update.workflow`.
"""

from dataclasses import dataclass

from ...core.log_sink import debug_emit
from ...core.security_http import (
    secure_urlopen,  # noqa: F401  (test compat shim after _source_reachable removal)
)


@dataclass(frozen=True, init=False)
class DownloadSource:
    """The resolved endpoints of the active download source (torrent
    primary, HTTP fallback)."""

    torrent_url: str | None = None
    fallback_url: str = ""
    # Server-only alternative to torrent_url
    torrent_magnet: str | None = None

    def __init__(
        self,
        torrent_url: str | None = None,
        fallback_url: str = "",
        torrent_magnet: str | None = None,
    ) -> None:
        object.__setattr__(self, "torrent_url", torrent_url)
        object.__setattr__(self, "fallback_url", fallback_url or "")
        object.__setattr__(self, "torrent_magnet", torrent_magnet)

    @property
    def torrent_locator(self) -> "str | None":
        return self.torrent_url or self.torrent_magnet


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
        cfg.download_torrent_url,
        cfg.download_fallback_url or "",
        cfg.download_torrent_magnet,
    )
