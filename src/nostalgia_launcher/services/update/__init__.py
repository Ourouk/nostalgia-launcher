"""Update subsystem: transports + staged workflow.

``workflow`` is the orchestrator facade (``VerifyWorker`` /
``UpdateWorker`` keep their public API); the stage modules hold the
per-phase logic: ``ownership`` (server-base vs community-overlay),
``stages_initial`` (one-off acquisition), ``stages_manifest``,
``stages_static`` and ``stages_torrent`` (incremental strategies).
"""

from . import (
    ownership,
    stages_initial,
    stages_manifest,
    stages_static,
    stages_torrent,
)
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
    "ownership",
    "recovery_available",
    "stages_initial",
    "stages_manifest",
    "stages_static",
    "stages_torrent",
    "torrent_recovery_available",
]
