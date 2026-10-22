"""Update subsystem: transports + workflow (torrent-only incremental, zip fallback)."""

from .http import download_file
from .workflow import (
    TORRENT_VALIDATION_CACHE_KEY,
    DownloadSource,
    UpdateWorker,
    VerifyWorker,
    torrent_recovery_available,
)
from .workflow import _torrent_available as is_available
from .workflow import torrent_recovery_available as recovery_available

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
