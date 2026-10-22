"""Server-configured launcher logo: fetch + disk cache.

The header wordmark can be replaced by a logo URL from the launcher config's
``theme.logo``. The logo is downloaded with the same hardened HTTP path as
every other download and cached in the cache directory, so an offline launch
(or a fetch failure) still shows the last good logo. Pure stdlib; the Qt
layer turns the returned file path into a pixmap.

The cache is TTL-gated (``LOGO_CACHE_TTL``): a fresh cached file is served
without any network, so normal launches never contact the logo host; only
a missing or stale cache triggers a download.
"""

import os
import time
import urllib.request

from ..core import profiles
from ..core.constants import UA
from ..core.filesystem import atomic_write_bytes
from ..core.log_sink import log
from ..core.security_http import read_capped, secure_urlopen

# Logo refetches at most weekly: startup serves the persisted cache
# instantly, and only a cache older than this TTL (or a missing one) hits
# the network again.
LOGO_CACHE_TTL = 7 * 86400


def logo_cache_path() -> str:
    """Where the downloaded logo is cached (active profile's cache)."""
    return profiles.active().logo_path()


def cached_logo() -> str | None:
    """The cached logo path when a previous fetch left one behind (offline
    fallback), else None."""
    path = logo_cache_path()
    return path if os.path.isfile(path) else None


def cache_is_fresh(now: float | None = None) -> bool:
    """Whether the cached logo exists and is younger than `LOGO_CACHE_TTL`.

    Network-free: missing cache (or a clock-skewed future mtime treated
    as fresh) answers without touching the logo host.
    """
    path = logo_cache_path()
    if not os.path.isfile(path):
        return False
    try:
        age = (now if now is not None else time.time()) - os.path.getmtime(
            path
        )
    except OSError:
        return False
    return age < LOGO_CACHE_TTL


def fetch_logo(url: str, force: bool = False) -> str | None:
    """Download the logo to the cache dir and return its local path.

    A fresh cache is served without any network (unless ``force``); only
    a missing or stale cache triggers a download. On any failure
    (unreachable, non-https, empty body) the existing cached file is
    returned when there is one, else None. Never raises — a broken logo
    must not stop the launcher.
    """
    dest = logo_cache_path()
    if not force and cache_is_fresh():
        return dest
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=10) as r:
            data = read_capped(r, 8 * 1024 * 1024)
        if not data:
            raise RuntimeError("empty logo response")
        atomic_write_bytes(dest, data)
        log(f"  Downloaded launcher logo ({len(data) // 1024} KB).")
        return dest
    except Exception as e:
        log(f"  Launcher logo unavailable: {e}", "err")
        return cached_logo()
