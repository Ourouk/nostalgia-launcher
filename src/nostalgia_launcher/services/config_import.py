"""Explicit launcher-config import over HTTPS.

The launcher has no built-in server directory and never fetches one. A
configuration reaches the launcher in exactly two ways: the user selects a
local file, or the user types a URL and this module fetches it. Both paths
end in the same validation (`core.launcher.validate_path` /
`validate_dict`) and the same explicit summary-and-confirm step in the
first-launch wizard before anything is persisted.

The fetch is a hardened HTTPS GET through `core.security_http`: TLS
verification, HTTPS-only redirects, a 15 s timeout and a 1 MiB response cap
(a launcher configuration is a small JSON document; anything larger is not
one).
"""

import json
from urllib.parse import urlsplit

from ..core.security_http import _check_url, make_secure_client

CONFIG_FETCH_TIMEOUT = 15
CONFIG_FETCH_MAX_BYTES = 1024 * 1024
_FETCH_UA = "NostalgiaLauncher"


class ConfigUrlError(Exception):
    """A user-entered configuration URL was rejected before/at fetch time."""


def check_config_url(url: str) -> str:
    """Validate a user-entered configuration URL. Returns the normalized
    URL or raises ConfigUrlError. Only https is accepted."""
    url = (url or "").strip().rstrip("/")
    try:
        parts = urlsplit(url)
    except ValueError as e:
        # Malformed URLs (e.g. broken IPv6 literals) must surface through
        # the documented error type, not a raw ValueError the wizard's
        # except-clause can't catch.
        raise ConfigUrlError(
            "The configuration URL must be an https:// URL."
        ) from e
    if parts.scheme != "https" or not parts.hostname:
        raise ConfigUrlError("The configuration URL must be an https:// URL.")
    return url


def _https_get_capped(
    url: str,
    timeout: int = CONFIG_FETCH_TIMEOUT,
    max_bytes: int = CONFIG_FETCH_MAX_BYTES,
) -> str:
    """HTTPS GET the configuration document, refusing responses larger than
    `max_bytes`."""
    _check_url(url, None)
    with make_secure_client(timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        if len(resp.content) > max_bytes:
            raise ConfigUrlError(
                f"The configuration is larger than "
                f"{max_bytes // 1024} KiB and was rejected."
            )
        data = resp.content
    return data.decode("utf-8")


def fetch_config_url(url: str) -> tuple[dict | None, str | None, str]:
    """Fetch and parse a launcher configuration from a user-entered URL.

    Returns ``(data, raw_text, error)``; exactly one of ``data`` / ``error``
    is set. ``raw_text`` is the exact JSON text, suitable for persisting to
    disk once validated. A non-https URL is rejected without any network
    activity.
    """
    try:
        url = check_config_url(url)
    except ConfigUrlError as e:
        return None, None, str(e)
    try:
        raw = _https_get_capped(url)
        data = json.loads(raw)
    except ConfigUrlError as e:
        return None, None, str(e)
    except Exception as e:
        return None, None, f"Could not fetch the configuration: {e}"
    if not isinstance(data, dict):
        return None, None, "The configuration is not a JSON object."
    return data, raw, ""
