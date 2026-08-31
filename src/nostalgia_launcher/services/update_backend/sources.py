"""Download-source resolution for the client update backends.

The active `DownloadSource` is now derived solely from the server's
``server.download`` block (no mirror failover): the optional HTTP
fallback (single zip) and the optional BitTorrent ``torrent_url`` /
``magnet``. Kept separate from the worker engines so both
`VerifyWorker` and `UpdateWorker` share one definition;
`http_update` re-exports these names for compatibility.
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
        *args: object,
        torrent_url: str | None = None,
        fallback_url: str = "",
        torrent_magnet: str | None = None,
        manifest_url: str | None = None,  # noqa: ARG002
        client_url: str | None = None,  # noqa: ARG002
        **_kw: object,
    ) -> None:
        # Legacy positional: (manifest, client, torrent, fallback, magnet)
        if args:
            if len(args) == 5:
                # (manifest, client, torrent, fallback, magnet)
                torrent_url = args[2]  # type: ignore[assignment]
                fallback_url = args[3]  # type: ignore[assignment]
                torrent_magnet = args[4]  # type: ignore[assignment]
            elif len(args) == 4:
                # (manifest, client, torrent, fallback_or_magnet)
                torrent_url = args[2]  # type: ignore[assignment]
                fallback_url = args[3]  # type: ignore[assignment]
            elif len(args) == 3:
                torrent_url, fallback_url, torrent_magnet = args  # type: ignore[assignment]
            elif len(args) == 2:
                torrent_url, fallback_url = args  # type: ignore[assignment]
            elif len(args) == 1:
                torrent_url = args[0]  # type: ignore[assignment]
        object.__setattr__(self, "torrent_url", torrent_url)
        object.__setattr__(self, "fallback_url", fallback_url or "")
        object.__setattr__(self, "torrent_magnet", torrent_magnet)
        # Backward compat: 4-arg where fallback is magnet
        if (
            isinstance(self.fallback_url, str)
            and self.fallback_url.startswith("magnet:")
            and self.torrent_magnet is None
        ):
            object.__setattr__(self, "torrent_magnet", self.fallback_url)
            object.__setattr__(self, "fallback_url", "")

    @property
    def torrent_locator(self) -> "str | None":
        return self.torrent_url or self.torrent_magnet

    # Allow tuple unpacking for backward compat with old NamedTuple tests
    def __iter__(self):  # type: ignore[override]
        yield self.torrent_url
        yield self.fallback_url
        yield self.torrent_magnet

    def __getitem__(self, idx):  # type: ignore[override]
        return (self.torrent_url, self.fallback_url, self.torrent_magnet)[idx]

    def __len__(self):
        return 3


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
