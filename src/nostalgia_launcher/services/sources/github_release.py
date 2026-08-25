"""GitHub release backend — the shared release plumbing lives here too.

`codeberg_release.py` reuses `pick_asset`, `release_version`, `slim_release`
and `fetch_release_cached` from this module; only the "latest release" API
call differs between the two hosts.

Release-version semantics: normally the tag name, but mods that keep a
static tag and edit the release in place (e.g. SuperWoW) declare
``version_from: "asset"`` so the version is derived from the matched asset
filename instead.
"""

import json
import re
import time
import urllib.request

from ...core.config_store import load_config, update_config
from ...core.constants import GITHUB_API, UA
from ...core.errors import describe_net_error
from ...core.log_sink import log
from ...core.security_http import allowed_download_hosts, secure_urlopen
from .base import FetchResult, SourceBackend, register
from .safety import safe_slug, valid_extract_map

_MOD_VERSION_CACHE_TTL = 3600


def github_latest(owner: str, repo: str, raise_errors=False) -> dict | None:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/releases/latest"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as e:
        if raise_errors:
            raise RuntimeError(describe_net_error(e)) from e
        return None


def pick_asset(assets: list, pattern: str, prefer_no) -> dict | None:
    """The release asset matching ``pattern``; when ``prefer_no`` is set,
    candidates containing that substring lose against ones without it."""
    import fnmatch

    candidates = [a for a in assets if fnmatch.fnmatch(a["name"], pattern)]
    if prefer_no:
        preferred = [a for a in candidates if prefer_no not in a["name"]]
        if preferred:
            candidates = preferred
    return candidates[0] if candidates else None


def release_version(source: dict, rel: dict | None) -> str | None:
    """Version string for a release. Normally the tag name — but some mods
    keep a static tag and edit the release in place, so their tag never
    changes. For those (`version_from: "asset"`), derive the version from
    the matched asset filename, which embeds the real version."""
    if rel is None:
        return None
    if source.get("version_from") == "asset":
        asset = pick_asset(
            rel.get("assets", []),
            source.get("asset_pattern", ""),
            source.get("prefer_no"),
        )
        if asset and asset.get("name"):
            m = re.search(r"\d+(?:[._]\d+)+", asset["name"])
            return m.group(0) if m else asset["name"]
    return rel.get("tag_name")


def slim_release(rel: dict) -> dict:
    """Reduce an API release object to the fields the updater actually uses,
    so the persisted cache stays small."""
    return {
        "tag_name": rel.get("tag_name"),
        "assets": [
            {
                "name": a.get("name"),
                "size": a.get("size", 0),
                "browser_download_url": a.get("browser_download_url"),
            }
            for a in rel.get("assets", [])
        ],
    }


def fetch_bytes(url: str) -> bytes:
    """Download one artifact through the hardened transfer layer (the base
    git-host allowlist plus the launcher config's own hosts)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with secure_urlopen(
        req, timeout=120, allowed_hosts=allowed_download_hosts()
    ) as r:
        return r.read()


def fetch_release_cached(
    entry_id: str, fetcher, force: bool = False
) -> dict | None:
    """Latest-release lookup backed by a persistent cache in the config file
    ({"mod_release_cache": {id: {"timestamp": epoch, "release": {…}}}}),
    so restarts within the TTL don't re-hit the GitHub/Codeberg APIs.
    ``fetcher()`` performs the raw API call; the slimmed result is cached.
    """
    now = time.time()
    if not force:
        entry = load_config().get("mod_release_cache", {}).get(entry_id)
        if (
            entry
            and (now - entry.get("timestamp", 0)) < _MOD_VERSION_CACHE_TTL
        ):
            return entry.get("release")
    rel = fetcher()
    if rel is None:
        return None
    rel = slim_release(rel)
    update_config(
        lambda c: c.setdefault("mod_release_cache", {}).__setitem__(
            entry_id, {"timestamp": now, "release": rel}
        )
    )
    return rel


def _validate_release_source(source: dict) -> dict | None:
    """Shared sanitization for both release kinds."""
    owner = safe_slug(source.get("owner"))
    repo = safe_slug(source.get("repo"))
    pattern = source.get("asset_pattern")
    if not owner or not repo or not isinstance(pattern, str) or not pattern:
        return None
    raw_emap = source.get("extract_map")
    emap = valid_extract_map(raw_emap)
    if raw_emap is not None and emap is None:
        return None  # a map was given but nothing in it is usable
    version_from = source.get("version_from")
    prefer_no = source.get("prefer_no")
    return {
        "kind": source.get("kind"),
        "owner": owner,
        "repo": repo,
        "asset_pattern": pattern,
        "prefer_no": prefer_no if isinstance(prefer_no, str) else None,
        "extract_map": emap,
        "version_from": version_from if version_from == "asset" else None,
    }


class GitHubReleaseBackend(SourceBackend):
    KIND = "github_release"
    HOST = "GitHub"

    def validate(self, source: dict) -> dict | None:
        cleaned = _validate_release_source(source)
        if cleaned is not None:
            cleaned["kind"] = self.KIND
        return cleaned

    def latest_release(
        self, source: dict, raise_errors: bool = False
    ) -> dict | None:
        return github_latest(source["owner"], source["repo"], raise_errors)

    def resolve_version(
        self, entry: dict, *, force: bool = False
    ) -> str | None:
        src = entry["source"]
        rel = fetch_release_cached(
            entry["id"],
            lambda: self.latest_release(src),
            force=force,
        )
        return release_version(src, rel)

    def fetch(
        self, entry: dict, *, client_dir: str | None = None, release=None
    ) -> FetchResult:
        src = entry["source"]
        rel = release if release is not None else self.latest_release(src)
        if not rel:
            raise RuntimeError(f"no release found on {self.HOST}")
        asset = pick_asset(
            rel.get("assets", []), src["asset_pattern"], src.get("prefer_no")
        )
        if not asset:
            raise RuntimeError(
                f"No matching asset '{src['asset_pattern']}' in "
                f"{entry['id']} release"
            )
        log(
            f"  Downloading {asset['name']} "
            f"({int(asset.get('size', 0)) // 1024} KB)..."
        )
        data = fetch_bytes(asset["browser_download_url"])
        return FetchResult(
            data=data,
            version=release_version(src, rel),
            name=asset.get("name"),
        )


register(GitHubReleaseBackend())
