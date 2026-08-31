"""Self-update checks against this repo's GitHub releases (cached daily)."""

import json
import time

import httpx
from packaging.version import InvalidVersion, Version

from ..core.config_store import load_config, update_config
from ..core.constants import GITHUB_API, UPDATER_VERSION
from ..core.security_http import _check_url, make_secure_client

# Self-update: the updater checks its own GitHub releases once a day.
UPDATER_REPO = "Ourouk/nostalgia-launcher"
UPDATER_CHECK_TTL = 86400  # 1 day, cached in the config file


def _invalidate_cache():
    """Drop any stored release tag so a stale comparison can't resurface."""
    update_config(lambda c: c.pop("updater_release_cache", None))


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
        _check_url(url, None)
        with make_secure_client(timeout=10) as client:
            resp = client.get(url)
            resp.raise_for_status()
            if len(resp.content) > 2 * 1024 * 1024:
                raise RuntimeError("Response exceeded the 2048 KiB limit.")
            tag = json.loads(resp.content).get("tag_name")
    except httpx.HTTPStatusError as e:
        # A 404 means the repo has no releases yet: clear any stale cache
        # entry so a later in-TTL read can't resurrect an old tag.
        if e.response.status_code == 404:
            _invalidate_cache()
        return None
    except Exception:
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


def updater_update_available(latest_tag: str) -> bool:
    if not latest_tag:
        return False
    # Delegate version comparison to packaging (PEP 440) instead of the
    # bespoke tuple logic in helpers.parse_version. Packaging handles
    # pre-release/build metadata and zero-padding (1.1 == 1.1.0) correctly.
    try:
        return Version(latest_tag) > Version(UPDATER_VERSION)
    except InvalidVersion:
        # Fallback for non-PEP-440 tags (e.g. "v1.2rc1" with leading 'v')
        # — normalize by stripping a leading v/V and retry.
        try:
            a = Version(latest_tag.lstrip("vV"))
            b = Version(UPDATER_VERSION.lstrip("vV"))
            return a > b
        except InvalidVersion:
            return False
