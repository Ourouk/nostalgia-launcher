"""Codeberg release backend.

Subclasses the GitHub backend: asset picking, version derivation,
slimming, the persistent release cache and the fetch flow are all shared —
only the "latest release" API call differs (Codeberg's Gitea API returns a
list and needs prerelease/draft filtering).
"""

import json

from ...core.errors import describe_net_error
from ...core.security_http import _check_url, make_secure_client
from .github_release import _API_MAX_BYTES, GitHubReleaseBackend


def codeberg_latest(owner: str, repo: str, raise_errors=False) -> dict | None:
    url = (
        "https://codeberg.org/api/v1/repos/"
        f"{owner}/{repo}/releases?limit=10&pre-release=false"
    )
    try:
        _check_url(url, None)
        with make_secure_client(timeout=10) as client:
            resp = client.get(url)
            resp.raise_for_status()
            if len(resp.content) > _API_MAX_BYTES:
                raise RuntimeError(
                    f"Response exceeded the {_API_MAX_BYTES // 1024} KiB limit."
                )
            releases = json.loads(resp.content)
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
