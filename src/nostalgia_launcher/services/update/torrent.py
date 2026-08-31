"""BitTorrent transport: thin wrappers over libtorrent.

No policy. The workflow decides when to use torrent, this module just
executes the transfer and reports results or typed errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

TORRENT_VALIDATION_CACHE_KEY = "__torrent_validation__"


def is_available() -> bool:
    """Whether the BitTorrent backend can run (libtorrent installed)."""
    try:
        from ..update_backend.torrent_update import available

        return available()
    except Exception:
        return False


def recovery_available() -> bool:
    """Whether a manifest-less full re-download via BitTorrent is possible."""
    from ...core import launcher

    cfg = launcher.config()
    return bool(cfg and cfg.has_torrent() and is_available())


def torrent_identity(snapshot) -> dict:
    return {
        "content_hash": snapshot.content_hash,
        "info_hash": snapshot.info_hash or "",
    }


def safe_identity(snapshot) -> dict | None:
    try:
        return torrent_identity(snapshot)
    except Exception:
        return None
