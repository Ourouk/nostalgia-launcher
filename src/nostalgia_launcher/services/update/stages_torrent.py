"""Torrent stage: base-client folder recheck + selective download.

The authoritative base-client pipeline: offline piece-hash recheck
against the BitTorrent snapshot (full folder), then selective
download of stale pieces. Detects torrent-listed files only — extra
on-disk files (user mods, MPQs, ``Interface/``) are ignored by the
recheck, never flagged stale, never removed. Incremental updates and
full first-install downloads both flow through here; the caller picks
the scope (``wanted=None`` = whole client).

Note: magnet sources are first-install only — a magnet's content is
immutable once published (info-hash addressed), so it cannot carry
updates. Only a refreshed ``torrent_url`` supports incremental.
"""

from __future__ import annotations


def torrent_allowed(cfg) -> bool:
    """Whether incremental torrent updates are enabled by config."""
    if cfg is None:
        return True
    try:
        return bool(cfg.torrent_update_allowed())
    except Exception:
        return True
