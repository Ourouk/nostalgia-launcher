"""Self-update checks against this repo's GitHub releases (cached daily)."""

import json
import time
import urllib.error
import urllib.request

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
        req = urllib.request.Request(
            f"{GITHUB_API}/repos/{UPDATER_REPO}/releases/latest",
            headers={"User-Agent": UA},
        )
        with secure_urlopen(req, timeout=10) as r:
            tag = json.loads(read_capped(r, 2 * 1024 * 1024)).get("tag_name")
    except urllib.error.HTTPError as e:
        # A 404 means the repo has no releases yet: clear any stale cache
        # entry so a later in-TTL read can't resurrect an old tag.
        if e.code == 404:
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


def updater_update_available(latest_tag: str | None) -> bool:
    if not latest_tag:
        return False
    a, b = parse_version(latest_tag), parse_version(UPDATER_VERSION)
    n = max(len(a), len(b))  # zero-pad so 1.1 == 1.1.0
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b
