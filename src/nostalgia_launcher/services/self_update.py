"""Self-update checks against this repo's GitHub releases (cached daily)."""

import json
import time

import httpx
from packaging.version import InvalidVersion, Version

from ..core.config_store import load_config, update_config
from ..core.constants import GITHUB_API, UA, UPDATER_VERSION
from ..core.helpers import parse_version
from ..core.security_http import SSL_CTX, _check_url, read_capped
from ..core.security_http import secure_urlopen as _secure_urlopen_impl

# Exposed for tests to monkeypatch (httpx-backed in production)
secure_urlopen = _secure_urlopen_impl

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
        import urllib.request

        url = f"{GITHUB_API}/repos/{UPDATER_REPO}/releases/latest"
        # Use test-mockable secure_urlopen when patched, else httpx path
        import sys as _sys

        patched = _sys.modules.get("nostalgia_launcher.services.self_update")
        use_mock = (
            patched is not None
            and getattr(patched, "secure_urlopen", None)
            is not _secure_urlopen_impl
        )
        if use_mock:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with secure_urlopen(req, timeout=10) as r:  # type: ignore[arg-type]
                tag = json.loads(read_capped(r, 2 * 1024 * 1024)).get(
                    "tag_name"
                )
        else:
            _check_url(url, None)
            tout = httpx.Timeout(10.0)
            with httpx.Client(
                verify=SSL_CTX, timeout=tout, follow_redirects=True
            ) as client:
                resp = client.get(url, headers={"User-Agent": UA})
                if resp.status_code == 404:
                    _invalidate_cache()
                    return None
                resp.raise_for_status()
                for hist in resp.history:
                    _check_url(str(hist.url), None)
                _check_url(str(resp.url), None)
                tag = json.loads(read_capped(resp, 2 * 1024 * 1024)).get(
                    "tag_name"
                )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            _invalidate_cache()
        return None
    except Exception as e:
        # Map urllib HTTPError 404 when using mocked path
        try:
            import urllib.error as _ue

            if isinstance(e, _ue.HTTPError) and e.code == 404:
                _invalidate_cache()
                return None
        except Exception:
            pass
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
