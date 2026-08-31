"""HTTP client update backend: manifest verification and incremental update.

Compatibility shim: the workflow now lives in ``services.update.workflow``
with transports ``services.update.http`` / ``torrent`` and planning in
``planner`` / ``manifest``. This module re-exports the public surface so
existing imports (and test monkeypatches) keep working.
"""

from __future__ import annotations

import os  # noqa: F401  (exposed for test monkeypatch)

from ...core.config_store import load_cache, save_cache
from ...core.security_http import (
    allowed_download_hosts,
    read_capped,
    secure_urlopen,
)
from ..tweaks import write_config_wtf, write_realmlist_wtf
from ..update.http import download_file, fetch_manifest

# New canonical locations
from ..update.manifest import checked_node_rel as _checked_node_rel
from ..update.manifest import checked_node_size as _checked_node_size
from ..update.torrent import is_available as _torrent_available
from ..update.torrent import safe_identity as _safe_identity
from ..update.torrent import torrent_identity as _torrent_identity
from ..update.workflow import (
    TORRENT_VALIDATION_CACHE_KEY,
    UpdateWorker,
    VerifyWorker,
    torrent_recovery_available,
)
from .sources import DownloadSource, _download_source
from .worker_base import WorkerBase

__all__ = [
    "DownloadSource",
    "UpdateWorker",
    "VerifyWorker",
    "TORRENT_VALIDATION_CACHE_KEY",
    "torrent_recovery_available",
    "_checked_node_rel",
    "_checked_node_size",
    "_torrent_available",
    "_torrent_identity",
    "_safe_identity",
    "_download_source",
    "WorkerBase",
    "load_cache",
    "save_cache",
    "secure_urlopen",
    "allowed_download_hosts",
    "read_capped",
    "write_config_wtf",
    "write_realmlist_wtf",
    "fetch_manifest",
    "download_file",
]
