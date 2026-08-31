"""Assets engine: server content patches (MPQs) — registry and updates.

Assets are single files (typically ``Data/patch-N.MPQ``) fetched from pinned
HTTPS URLs into the client folder. The asset list comes from the launcher
config's embedded top-level ``assets`` entries plus the optional remote
assets catalog (``server.assets_registry_url`` or a user-set URL), merged
with the per-user custom file on top — embedded ids override catalog ids,
custom entries override both.

Every staleness verdict is derived ONLY from the update information the
entry provides, in strict precedence order (see `asset_update_available`):
the version pin, then the sha1 pin, then the declared size, then an opt-in
HTTP probe of the remote size/Last-Modified/ETag compared against the state
captured at install time. With no metadata an installed asset is never
reported stale; a missing file is a missing install, not an update.
"""

import os
import time

from ..core.config_store import update_config
from ..core.filesystem import cached_sha1
from ..core.log_sink import log
from ..core.security_http import (
    _check_url,
    allowed_download_hosts,
    make_secure_client,
)
from . import catalog
from .sources import deploy


def _checked_rel(dest_rel) -> str:
    """Validate a client-dir-relative install target before it is joined
    onto `client_dir` (a compromised catalog must not write outside the
    client folder). Delegates to the shared deploy validator."""
    return deploy.checked_rel(dest_rel)


# ── registry loading ─────────────────────────────────────────────────────────


def catalog_timestamp() -> float | None:
    """When the assets catalog cache was last fetched (epoch), or None."""
    return catalog.catalog_timestamp("assets")


def has_remote_catalog() -> bool:
    """Whether an assets catalog is actually fetchable: a user URL override,
    or an explicitly launcher-configured one. There is deliberately no
    base_url-derived default — a config without a registry URL has nothing
    to refetch."""
    if catalog.get_registry_url("assets"):
        return True
    from ..core import launcher

    return bool(launcher.assets_registry_url())


def embedded_assets() -> list:
    """Assets defined inline in the active launcher config (top-level
    ``assets``), sanitized with the exact same validator as remote catalog
    entries; unusable entries are skipped with a logged warning. Network-
    free."""
    from ..core import launcher

    out = []
    for entry in launcher.embedded_assets():
        cleaned = catalog.validate_asset(entry)
        if cleaned is None:
            log(
                "  Launcher config: skipping invalid embedded asset"
                f" {entry.get('id')!r}.",
                "err",
            )
            continue
        out.append(cleaned)
    return out


def catalog_is_stale(now: float | None = None) -> bool:
    """Whether the cached catalog is missing or older than the weekly
    `catalog.CATALOG_TTL`. Always False when nothing is fetchable but
    assets exist locally: embedded in the launcher config or in the local
    repo file. Network-free."""
    if not has_remote_catalog() and (
        embedded_assets() or catalog.local_repo_has_entries("assets")
    ):
        return False
    ts = catalog_timestamp()
    if ts is None:
        return True
    now = now if now is not None else time.time()
    return (now - ts) >= catalog.CATALOG_TTL


def fetch_assets_catalog(force=False) -> list | None:
    """Assets catalog, cached in the config file ({"assets_catalog_cache":
    {"timestamp": epoch, "catalog": [...]}}) — see
    ``catalog.fetch_url_catalog`` for the caching/offline semantics."""
    return catalog.fetch_url_catalog(
        "assets", catalog.validate_asset, registry_url(), force=force
    )


def registry_url() -> str:
    """The active assets catalog URL: a user override, else the
    launcher-configured URL, else ''."""
    return catalog.get_registry_url("assets") or (_configured_registry_url())


def _configured_registry_url() -> str:
    from ..core import launcher

    return launcher.assets_registry_url()


def assets_registry(force=False) -> list:
    """The effective asset registry — see ``catalog.layered_registry`` for
    the layer order. Empty when nothing is configured."""
    return catalog.layered_registry(
        "assets",
        catalog.validate_asset,
        catalog.ASSET_MERGE_FIELDS,
        embedded_assets(),
        remote=fetch_assets_catalog(force=force),
    )


# ── download / install ───────────────────────────────────────────────────────


def install_asset(asset: dict, client_dir: str) -> dict:
    """Download one asset over HTTPS into the client folder.

    Delegates to the shared ``direct_file`` backend, which streams the
    payload beside its destination with the SHA-1 computed on the fly and
    the declared size enforced; this wrapper renames it into place and
    returns the record fragment {"installed_files", "probe"} where
    ``probe`` carries the response headers for the opt-in drift probe
    (empty dict when the server sent none).
    """
    dest_rel = _checked_rel(asset["dest"])
    source: dict = {
        "kind": "direct_file",
        "url": asset["url"],
        "dest": dest_rel,
    }
    if asset.get("sha1"):
        source["sha1"] = asset["sha1"]
    if asset.get("size"):
        source["size"] = asset["size"]

    from .sources import get as _source_get
    from .sources.deploy import move_into_place

    result = _source_get("direct_file").fetch(
        {"id": asset["id"], "source": source}, client_dir=client_dir
    )
    file = result.file
    if file is None:
        raise RuntimeError("direct_file produced no streamed payload")
    written = move_into_place(file.path, client_dir, dest_rel)
    return {"installed_files": [written], "probe": dict(file.probe)}


def remove_asset_files(installed_files: list, client_dir: str):
    """Delete previously installed asset files (best-effort per file).
    Recorded paths are re-validated before joining onto the client dir —
    they are bookkeeping data, not trusted input."""
    for rel in installed_files or []:
        if not isinstance(rel, str) or not catalog.safe_relpath(rel):
            continue
        full = os.path.join(client_dir, rel)
        try:
            if os.path.exists(full):
                os.remove(full)
                log(f"  Removed {rel}")
        except OSError as e:
            log(f"  Could not remove {rel}: {e}", "err")


# ── remote drift probe ───────────────────────────────────────────────────────

_PROBE_KEYS = ("size", "last_modified", "etag")


def remote_probe_state(url: str) -> dict | None:
    """HEAD-probe the remote asset: {"size", "last_modified", "etag"}
    (whichever headers arrive). None on any failure — probes are advisory
    only and must never block anything."""
    try:
        _check_url(url, allowed_download_hosts())
        with make_secure_client(timeout=10) as client:
            resp = client.head(url)
            resp.raise_for_status()
            h = resp.headers
            state: dict = {}
            cl = h.get("Content-Length")
            if cl is not None:
                try:
                    state["size"] = int(cl)
                except ValueError:
                    pass
            lm = h.get("Last-Modified")
            if lm:
                state["last_modified"] = lm
            etag = h.get("ETag")
            if etag:
                state["etag"] = etag
            return state
    except Exception:
        return None


def remember_probe_state(asset_id: str, state: dict):
    """Persist the install-time probe state (merged into the live config so
    concurrent writers aren't clobbered)."""
    if not state:
        return
    update_config(
        lambda c: c.setdefault("asset_probe_cache", {}).__setitem__(
            asset_id, state
        )
    )


def forget_probe_state(asset_id: str):
    update_config(
        lambda c: c.setdefault("asset_probe_cache", {}).pop(asset_id, None)
    )


# ── staleness verdict ────────────────────────────────────────────────────────


def resolved_version(asset: dict) -> str:
    """The display/install version string derived from the entry itself:
    its explicit ``version``, else the sha1 pin prefix, else 'pinned'."""
    if asset.get("version"):
        return asset["version"]
    if asset.get("sha1"):
        return f"sha1:{asset['sha1'][:12]}"
    return "pinned"


def asset_update_available(
    asset: dict, rec: dict | None, client_dir: str, *, allow_probe: bool = True
) -> tuple[bool, str]:
    """Whether an installed asset is stale, judged ONLY by the update
    information the entry provides — strict precedence:

    1. ``version``   → compare against the recorded installed_version.
    2. ``sha1``      → hash the local file against the pin.
    3. ``size``      → compare the local file size.
    4. ``probe``     → HEAD the URL and compare against the install-time
                       snapshot (only comparable headers count; any probe
                       failure is conservative: never stale). Skipped when
                       ``allow_probe`` is False — a live network call must
                       never run on the GUI thread's render path.
    5. none provided → never stale.

    Returns (stale, reason). A record without installed files means the
    asset is not installed at all — that is an install decision, not an
    update, so the verdict is False here.
    """
    if not rec or not rec.get("installed_files"):
        return False, ""
    version = asset.get("version")
    if version:
        stale = rec.get("installed_version") != version
        return stale, "server version changed" if stale else ""

    path = os.path.join(client_dir, rec["installed_files"][0])
    sha1 = asset.get("sha1")
    if sha1:
        if not os.path.exists(path):
            return False, ""
        # cached_sha1 is uppercase (manifest convention); pins are stored
        # lowercase — compare case-insensitively.
        stale = cached_sha1(path, {}).lower() != sha1
        return stale, "checksum changed" if stale else ""

    declared_size = asset.get("size")
    if declared_size:
        if not os.path.exists(path):
            return False, ""
        stale = os.path.getsize(path) != declared_size
        return stale, "size changed" if stale else ""

    if asset.get("probe") and allow_probe:
        current = remote_probe_state(asset["url"])
        old = rec.get("probe_state") or {}
        if not current or not old:
            return False, ""
        comparable = [
            k
            for k in _PROBE_KEYS
            if current.get(k) is not None and old.get(k) is not None
        ]
        stale = bool(comparable) and any(
            current[k] != old[k] for k in comparable
        )
        return stale, "remote file changed" if stale else ""

    return False, ""
