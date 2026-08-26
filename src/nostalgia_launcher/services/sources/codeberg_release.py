"""Codeberg release backend.

Subclasses the GitHub backend: asset picking, version derivation,
slimming, the persistent release cache and the fetch flow are all shared —
only the "latest release" API call differs (Codeberg's Gitea API returns a
list and needs prerelease/draft filtering).
"""

import json
import urllib.request

from ...core.constants import UA
from ...core.errors import describe_net_error
from ...core.security_http import read_capped, secure_urlopen
from .github_release import _API_MAX_BYTES, GitHubReleaseBackend


def codeberg_latest(owner: str, repo: str, raise_errors=False) -> dict | None:
    url = (
        "https://codeberg.org/api/v1/repos/"
        f"{owner}/{repo}/releases?limit=10&pre-release=false"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=10) as r:
            releases = json.loads(read_capped(r, _API_MAX_BYTES))
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


class CodebergReleaseBackend(GitHubReleaseBackend):
    KIND = "codeberg_release"
    HOST = "Codeberg"

    def latest_release(
        self, source: dict, raise_errors: bool = False
    ) -> dict | None:
        return codeberg_latest(source["owner"], source["repo"], raise_errors)


from .base import register  # noqa: E402

register(CodebergReleaseBackend())
