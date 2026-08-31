"""Git-archive backend — addon-style repo snapshots.

Resolves a git host URL (github/gitlab/gitea-family hosts only — see
`core.launcher.ADDON_GIT_HOSTS` plus per-config extras) to its latest
commit SHA and fetches the host's archive zip at that SHA. No clone, no
git binary requirement for the API path; ``git ls-remote`` is only a
fallback that stays optional.

Dedicated public API (used by `services/addons.py`, whose entries carry
{git, branch, ref} rather than a ``source.kind``) plus the generic
SourceBackend surface on top.
"""

import json
import os
import subprocess
import time
from urllib.parse import quote, urlsplit

from ...core.config_store import load_config, update_config
from ...core.constants import GITHUB_API
from ...core.errors import describe_net_error
from ...core.log_sink import log
from ...core.security_http import _check_url, make_secure_client
from .base import FetchResult, SourceBackend, register

ADDON_SHA_CACHE_TTL = 3600
# Repo-archive zips buffer in memory for extraction — cap them like every
# other transfer; API responses are small JSON documents.
_ARCHIVE_MAX_BYTES = 512 * 1024 * 1024
_API_MAX_BYTES = 2 * 1024 * 1024


def is_allowed_git_url(url: str) -> bool:
    """Whether ``url`` points at an allowlisted git host (exact host or a
    subdomain of one): the base `ADDON_GIT_HOSTS` plus any community-
    supplied hosts from the active launcher config."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    from ...core.launcher import ADDON_GIT_HOSTS

    host = (parts.hostname or "").lower()
    hosts = set(ADDON_GIT_HOSTS)
    cfg_hosts = _config_git_hosts()
    if cfg_hosts is not None:
        hosts |= {h.lower() for h in cfg_hosts}
    return any(host == h or host.endswith("." + h) for h in hosts)


def git_parts(git_url: str):
    """→ (kind, repo_url, owner, repo, api_base); kind ∈ github/gitlab/gitea.
    Handles path prefixes like <host>/git/<owner>/<repo>."""
    parts = urlsplit(git_url)
    host = (parts.hostname or "").lower()
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 2:
        raise ValueError(f"Unsupported git URL: {git_url}")
    owner, repo = segs[-2], segs[-1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    prefix = "/".join(segs[:-2])
    origin = f"https://{parts.netloc}"
    repo_url = origin + (f"/{prefix}" if prefix else "") + f"/{owner}/{repo}"
    if host == "github.com" or host.endswith(".github.com"):
        return "github", repo_url, owner, repo, GITHUB_API
    if host == "gitlab.com" or host.endswith(".gitlab.com"):
        return "gitlab", repo_url, owner, repo, f"{origin}/api/v4"
    api = origin + (f"/{prefix}" if prefix else "") + "/api/v1"
    return "gitea", repo_url, owner, repo, api


def _api_json(url: str, timeout=10):
    _check_url(url, None)
    with make_secure_client(timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        if len(resp.content) > _API_MAX_BYTES:
            raise RuntimeError(
                f"Response exceeded the {_API_MAX_BYTES // 1024} KiB limit."
            )
        return json.loads(resp.content)


def _config_git_hosts() -> set | None:
    from ...core import launcher as launcher_module

    cfg = launcher_module.config()
    return cfg.addon_git_host_set() if cfg is not None else None


def ls_remote_sha(git_url: str, pin: str | None) -> str | None:
    """Commit sha via ``git ls-remote`` — the smart-HTTP fallback that
    sidesteps the git hosts' REST API quota. No clone, no worktree
    mutation. Returns None when git is missing, the command fails/times
    out, or the requested ref can't be resolved — never raises."""
    args = ["git", "ls-remote", git_url]
    args.append(pin if pin else "HEAD")
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=15, env=env
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    lines = [line.partition("\t") for line in proc.stdout.splitlines()]
    if pin:
        exact = [
            sha
            for sha, _, ref in lines
            if ref in (f"refs/heads/{pin}", f"refs/tags/{pin}")
        ]
        if exact:
            return exact[0]
        loose = [sha for sha, _, ref in lines if ref.endswith("/" + pin)]
        return loose[0] if loose else None
    for sha, _, ref in lines:
        if ref == "HEAD":
            return sha
    return None


class GitArchiveBackend(SourceBackend):
    KIND = "git_archive"

    # ── SourceBackend surface ─────────────────────────────────────────────

    def validate(self, source: dict) -> dict | None:
        git = source.get("git")
        git = git.strip() if isinstance(git, str) else ""
        branch = source.get("branch")
        ref = source.get("ref")
        cleaned: dict = {"kind": self.KIND, "git": git or None}
        if isinstance(branch, str) and branch.strip():
            cleaned["branch"] = branch.strip()
        if isinstance(ref, str) and ref.strip():
            cleaned["ref"] = ref.strip()
        return cleaned if cleaned["git"] else None

    def resolve_version(
        self, entry: dict, *, force: bool = False
    ) -> str | None:
        """The remote commit sha for an entry carrying {git, branch, ref}."""
        src = entry.get("source") or entry
        return self.remote_sha(
            src.get("git"),
            branch=src.get("branch"),
            ref=src.get("ref"),
            force=force,
        )

    def fetch(
        self,
        entry: dict,
        *,
        client_dir: str | None = None,
        release: dict | None = None,
    ):
        src = entry.get("source") or entry
        git_url = src.get("git")
        sha = src.get("sha1") or src.get("sha")
        if not sha:
            raise RuntimeError(
                "git_archive requires a pinned commit sha to fetch"
            )
        data = self.fetch_archive(git_url, sha)
        return FetchResult(data=data, version=sha[:10], name=entry.get("id"))

    # ── dedicated API (addons vertical) ───────────────────────────────────

    def remote_sha(
        self,
        git_url: str,
        branch=None,
        ref=None,
        force=False,
        raise_errors=False,
    ) -> str | None:
        """Latest commit sha of a repo's branch (or pinned ref), cached in
        the config file so repeated verifies don't burn API quota. Returns
        None on failure — or raises with a readable cause when raise_errors
        is set. When the REST API fails (rate limit/outage) the call falls
        back to ``git ls-remote``; without git installed the cached/None
        path still applies, so the packaged launcher never hard-depends on
        a Git executable."""
        # Allowlist gate: never open an API connection nor spawn `git` for
        # a host outside the allowlist, whatever a catalog entry carries.
        if not is_allowed_git_url(git_url):
            return None
        key = f"{git_url}#{ref or branch or ''}"
        now = time.time()
        if not force:
            entry = load_config().get("addon_sha_cache", {}).get(key)
            if (
                entry
                and (now - entry.get("timestamp", 0)) < ADDON_SHA_CACHE_TTL
            ):
                return entry.get("sha")

        kind, _repo_url, owner, repo, api = git_parts(git_url)
        pin = ref or branch
        sha = None
        api_error = None
        try:
            if kind == "github":
                if pin:
                    sha = _api_json(
                        f"{api}/repos/{owner}/{repo}/commits/{pin}"
                    ).get("sha")
                else:
                    lst = _api_json(
                        f"{api}/repos/{owner}/{repo}/commits?per_page=1"
                    )
                    sha = lst[0].get("sha") if lst else None
            elif kind == "gitlab":
                proj = quote(f"{owner}/{repo}", safe="")
                if pin:
                    sha = _api_json(
                        f"{api}/projects/{proj}/repository/commits/"
                        f"{quote(pin, safe='')}"
                    ).get("id")
                else:
                    lst = _api_json(
                        f"{api}/projects/{proj}/repository/commits?per_page=1"
                    )
                    sha = lst[0].get("id") if lst else None
            else:  # gitea / codeberg
                q = f"?sha={pin}&limit=1" if pin else "?limit=1"
                lst = _api_json(f"{api}/repos/{owner}/{repo}/commits{q}")
                sha = lst[0].get("sha") if lst else None
        except Exception as e:
            api_error = e
            sha = None

        if not sha:
            sha = ls_remote_sha(git_url, pin)

        if sha is None and raise_errors:
            cause = api_error or RuntimeError(
                f"could not resolve remote commit for {git_url}"
            )
            raise RuntimeError(describe_net_error(cause)) from cause

        if sha is None and not raise_errors:
            api_cause = (
                describe_net_error(api_error)
                if api_error
                else "API returned no commits"
            )
            log(
                f"  Could not resolve remote commit for {git_url} — "
                f"{api_cause}; git ls-remote fallback also failed.",
                "dim",
            )

        if sha:
            update_config(
                lambda c: c.setdefault("addon_sha_cache", {}).__setitem__(
                    key, {"timestamp": now, "sha": sha}
                )
            )
        return sha

    def cached_sha(self, git_url: str, branch=None, ref=None):
        """Cached remote sha regardless of age — never touches network."""
        key = f"{git_url}#{ref or branch or ''}"
        entry = load_config().get("addon_sha_cache", {}).get(key)
        return entry.get("sha") if entry else None

    def zip_url(self, git_url: str, sha: str) -> str:
        kind, repo_url, _owner, repo, _api = git_parts(git_url)
        if kind == "gitlab":
            return f"{repo_url}/-/archive/{sha}/{repo}-{sha}.zip"
        return f"{repo_url}/archive/{sha}.zip"

    def fetch_archive(self, git_url: str, sha: str) -> bytes:
        """The repo archive zip at ``sha``, through the hardened transport
        with the addon archive-CDN allowlist extended by config hosts."""
        url = self.zip_url(git_url, sha)
        hosts = set(ADDON_ZIP_HOSTS) | (_config_git_hosts() or set())
        _check_url(url, hosts)
        with make_secure_client(timeout=120) as client:
            resp = client.get(url)
            resp.raise_for_status()
            if len(resp.content) > _ARCHIVE_MAX_BYTES:
                raise RuntimeError(
                    f"Response exceeded the {_ARCHIVE_MAX_BYTES // 1024} KiB limit."
                )
            return resp.content


# The zip-archive hosts extend the git-host allowlist with the Git hosts'
# archive CDNs so an addon archive download (github.com → codeload) passes.
ADDON_ZIP_HOSTS = {
    "codeload.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}


register(GitArchiveBackend())
