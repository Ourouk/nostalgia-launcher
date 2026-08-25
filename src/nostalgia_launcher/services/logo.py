"""Server-configured launcher logo: fetch + disk cache.

The header wordmark can be replaced by a logo URL from the launcher config's
``theme.logo``. The logo is downloaded with the same hardened HTTP path as
every other download and cached in the cache directory, so an offline launch
(or a fetch failure) still shows the last good logo. Pure stdlib; the Qt
layer turns the returned file path into a pixmap.
"""

import os
import urllib.request
from urllib.parse import urlsplit

from ..core import profiles
from ..core.constants import UA
from ..core.filesystem import atomic_write_bytes
from ..core.log_sink import log
from ..core.security_http import (
    allowed_download_hosts,
    read_capped,
    secure_urlopen,
)

# The cached logo filename in the cache directory. Content-agnostic (any
# pixmap format Qt can read); a new fetch replaces it wholesale.
LOGO_FILE = "launcher_logo.img"


def logo_cache_path() -> str:
    """Where the downloaded logo is cached (active profile's cache)."""
    return profiles.active().logo_path()


def cached_logo() -> str | None:
    """The cached logo path when a previous fetch left one behind (offline
    fallback), else None."""
    path = logo_cache_path()
    return path if os.path.isfile(path) else None


def fetch_logo(url: str) -> str | None:
    """Download the logo to the cache dir and return its local path.

    Returns the path on success; on any failure (unreachable, non-https,
    disallowed host, empty body) the existing cached file is returned when
    there is one, else None. Never raises — a broken logo must not stop the
    launcher. The logo's own host is allowed in addition to the regular
    download allowlist, so a distribution may serve it from a separate CDN.
    """
    dest = logo_cache_path()
    hosts = set(allowed_download_hosts())
    try:
        host = urlsplit(url).hostname
        if host:
            hosts.add(host.lower())
    except ValueError:
        host = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with secure_urlopen(
            req, timeout=10, allowed_hosts=frozenset(hosts)
        ) as r:
            data = read_capped(r, 8 * 1024 * 1024)
        if not data:
            raise RuntimeError("empty logo response")
        atomic_write_bytes(dest, data)
        log(f"  Downloaded launcher logo ({len(data) // 1024} KB).")
        return dest
    except Exception as e:
        log(f"  Launcher logo unavailable: {e}", "err")
        return cached_logo()
