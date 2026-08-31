"""Update subsystem: transports + workflow (torrent-only incremental, zip fallback)."""

from .http import download_file
from .torrent import (
    is_available,
    recovery_available,
    safe_identity,
    torrent_identity,
)
from .workflow import (
    TORRENT_VALIDATION_CACHE_KEY,
    UpdateWorker,
    VerifyWorker,
    torrent_recovery_available_compat,
)

__all__ = [
    "download_file",
    "is_available",
    "recovery_available",
    "safe_identity",
    "torrent_identity",
    "VerifyWorker",
    "UpdateWorker",
    "TORRENT_VALIDATION_CACHE_KEY",
    "torrent_recovery_available_compat",
]
