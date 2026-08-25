"""Codeberg release backend.

Same shape as the GitHub backend; only the "latest release" API call
differs (Codeberg's Gitea API returns a list and needs prerelease/draft
filtering). Asset picking, version derivation, slimming and the persistent
release cache are shared with `github_release`.
"""

import json
import urllib.request

from ...core.constants import UA
from ...core.errors import describe_net_error
from ...core.log_sink import log
from ...core.security_http import secure_urlopen
from .base import FetchResult, SourceBackend
from .github_release import (
    _validate_release_source,
    fetch_bytes,
    fetch_release_cached,
    pick_asset,
    release_version,
)


def codeberg_latest(owner: str, repo: str, raise_errors=False) -> dict | None:
    url = (
        "https://codeberg.org/api/v1/repos/"
        f"{owner}/{repo}/releases?limit=10&pre-release=false"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=10) as r:
            releases = json.load(r)
        for rel in releases:
            if not rel.get("prerelease", False) and not rel.get(
                "draft", False
            ):
                return rel
        return releases[0] if releases else None
    except Exception as e:
        if raise_errors:
            raise RuntimeError(describe_net_error(e)) from e
        return None


class CodebergReleaseBackend(SourceBackend):
    KIND = "codeberg_release"

    def validate(self, source: dict) -> dict | None:
        cleaned = _validate_release_source(source)
        if cleaned is not None:
            cleaned["kind"] = self.KIND
        return cleaned

    def latest_release(
        self, source: dict, raise_errors: bool = False
    ) -> dict | None:
        return codeberg_latest(source["owner"], source["repo"], raise_errors)

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
            raise RuntimeError("no release found on Codeberg")
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


from .base import register  # noqa: E402

register(CodebergReleaseBackend())
