"""Self-update checks against this repo's GitHub releases (cached daily)."""

import json
import time
import urllib.error
import urllib.request

from packaging.version import InvalidVersion, Version

from ..core.config_store import load_config, update_config
from ..core.constants import GITHUB_API, UA, UPDATER_VERSION
from ..core.helpers import parse_version
from ..core.security_http import read_capped, secure_urlopen

# Self-update: the updater checks its own GitHub releases once a day.
UPDATER_REPO = "Ourouk/nostalgia-launcher"
UPDATER_CHECK_TTL = 86400  # 1 day, cached in the config file


def _invalidate_cache():
    """Drop any stored release tag so a stale comparison can't resurface."""
    update_config(lambda c: c.pop("updater_release_cache", None))


def _response_status(resp) -> int | None:
    """HTTP status of a response, or None when the transport doesn't say
    (test fakes that only serve a body are treated as success)."""
    status = getattr(resp, "status", None)
    if isinstance(status, int):
        return status
    getcode = getattr(resp, "getcode", None)
    if callable(getcode):
        try:
            code = getcode()
        except Exception:
            return None
        return code if isinstance(code, int) else None
    return None


def fetch_updater_latest_tag(force: bool = False) -> str | None:
    """Latest release tag of the updater's own repo, cached for a day. Returns
    None when there are no releases yet (GitHub 404) or on any error.

    The cache is keyed to the current app version: a version reset or
    downgrade invalidates any previously stored tag, so an old comparison
    (for example a pre-reset ``v1.x`` tag after restarting at ``0.0.1``)
    can never be served back as "an update is available".
    """
    now = time.time()
    if not force:
        entry = load_config().get("updater_release_cache", {})
        if (
            entry.get("version") == UPDATER_VERSION
            and entry.get("tag") is not None
            and (now - entry.get("timestamp", 0)) < UPDATER_CHECK_TTL
        ):
            return entry["tag"]
    try:
        url = f"{GITHUB_API}/repos/{UPDATER_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        # Single transport: secure_urlopen is HTTPS-only with verified TLS;
        # tests monkeypatch this module attribute with body-serving fakes.
        with secure_urlopen(req, timeout=10) as r:
            status = _response_status(r)
            if status == 404:
                _invalidate_cache()
                return None
            if status is not None and status >= 400:
                return None
            tag = json.loads(read_capped(r, 2 * 1024 * 1024)).get("tag_name")
    except Exception as e:
        # Map urllib HTTPError 404 to a stale-cache clear.
        if isinstance(e, urllib.error.HTTPError) and e.code == 404:
            _invalidate_cache()
        return None
    if tag:
        update_config(
            lambda c: c.__setitem__(
                "updater_release_cache",
                {"timestamp": now, "tag": tag, "version": UPDATER_VERSION},
            )
        )
    else:
        # A 200 with no tag_name (unusual) — clear to avoid a phantom result.
        _invalidate_cache()
    return tag


def updater_update_available(latest_tag: str | None) -> bool:
    if not latest_tag:
        return False
    # Prefer PEP 440 semantics via packaging.version; fall back to the
    # lenient tuple parser for non-PEP440 tags (e.g. "rc1", "").
    try:
        return Version(latest_tag.lstrip("vV")) > Version(
            UPDATER_VERSION.lstrip("vV")
        )
    except InvalidVersion:
        a, b = parse_version(latest_tag), parse_version(UPDATER_VERSION)
        n = max(len(a), len(b))  # zero-pad so 1.1 == 1.1.0
        a += (0,) * (n - len(a))
        b += (0,) * (n - len(b))
        return a > b
