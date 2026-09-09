"""Update subsystem: transports + workflow (torrent-only incremental, zip fallback)."""

from .http import download_file
from .torrent import is_available, recovery_available
from .workflow import (
    TORRENT_VALIDATION_CACHE_KEY,
    DownloadSource,
    UpdateWorker,
    VerifyWorker,
    torrent_recovery_available,
)

__all__ = [
    "DownloadSource",
    "TORRENT_VALIDATION_CACHE_KEY",
    "UpdateWorker",
    "VerifyWorker",
    "download_file",
    "is_available",
    "recovery_available",
    "torrent_recovery_available",
]
