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

import json
import os
import time
import urllib.request

from ..core.config_store import load_config, update_config
from ..core.constants import UA
from ..core.filesystem import cached_sha1
from ..core.log_sink import log
from ..core.security_http import (
    allowed_download_hosts,
    read_capped,
    secure_urlopen,
)
from . import catalog


def _checked_rel(dest_rel) -> str:
    """Validate a client-dir-relative install target before it is joined
    onto `client_dir` (a compromised catalog must not write outside the
    client folder)."""
    if not catalog.safe_relpath(dest_rel):
        raise RuntimeError(f"Refusing unsafe install path: {dest_rel!r}")
    return dest_rel


# ── registry loading ─────────────────────────────────────────────────────────


def catalog_timestamp() -> float | None:
    """When the assets catalog cache was last fetched (epoch), or None."""
    entry = load_config().get("assets_catalog_cache", {})
    ts = entry.get("timestamp")
    return ts if isinstance(ts, (int, float)) and ts > 0 else None


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
    `catalog.CATALOG_TTL`. Always False when nothing is fetchable but assets
    are embedded in the launcher config. Network-free."""
    if not has_remote_catalog() and embedded_assets():
        return False
    ts = catalog_timestamp()
    if ts is None:
        return True
    now = now if now is not None else time.time()
    return (now - ts) >= catalog.CATALOG_TTL


def fetch_assets_catalog(force=False) -> list | None:
    """Assets catalog, cached in the config file ({"assets_catalog_cache":
    {"timestamp": epoch, "catalog": [...]}}).

    Non-forced calls never hit the network when a cached copy exists, and
    return None (→ an empty registry) when there is none yet, so a first run
    is fully offline-safe. Forced calls always fetch and raise when the URL
    is unset or the network fails with nothing cached.
    """
    now = time.time()
    entry = load_config().get("assets_catalog_cache", {})
    cached = entry.get("catalog")
    if not force:
        return cached if cached is not None else None
    url = registry_url()
    if not url:
        raise RuntimeError("Assets catalog URL is not configured.")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=10) as r:
            raw = json.loads(read_capped(r, 2 * 1024 * 1024))
    except Exception:
        if cached is not None:
            return cached
        raise
    validated = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        cleaned = catalog.validate_asset(e)
        if cleaned is not None:
            validated.append(cleaned)
    update_config(
        lambda c: c.__setitem__(
            "assets_catalog_cache", {"timestamp": now, "catalog": validated}
        )
    )
    return validated


def registry_url() -> str:
    """The active assets catalog URL: a user override, else the
    launcher-configured URL, else ''."""
    return catalog.get_registry_url("assets") or (_configured_registry_url())


def _configured_registry_url() -> str:
    from ..core import launcher

    return launcher.assets_registry_url()


def assets_registry(force=False) -> list:
    """The effective asset registry: the remote/cached catalog merged with
    the launcher config's embedded assets (embedded ids override catalog
    ids), then the per-user custom file on top. Empty when nothing is
    configured yet."""
    remote = fetch_assets_catalog(force=force)
    base = [] if remote is None else remote
    return catalog.merge_assets(
        catalog.merge_assets(base, embedded_assets()),
        catalog.load_custom("assets", catalog.validate_asset),
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
    """Delete previously installed asset files (best-effort per file)."""
    for rel in installed_files or []:
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
        req = urllib.request.Request(
            url, headers={"User-Agent": UA}, method="HEAD"
        )
        with secure_urlopen(
            req,
            timeout=10,
            allowed_hosts=allowed_download_hosts(),
        ) as r:
            h = r.headers
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


def recorded_probe_state(asset_id: str) -> dict | None:
    """The probe state captured when this asset was last installed."""
    return load_config().get("asset_probe_cache", {}).get(asset_id)


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
    asset: dict, rec: dict | None, client_dir: str
) -> tuple[bool, str]:
    """Whether an installed asset is stale, judged ONLY by the update
    information the entry provides — strict precedence:

    1. ``version``   → compare against the recorded installed_version.
    2. ``sha1``      → hash the local file against the pin.
    3. ``size``      → compare the local file size.
    4. ``probe``     → HEAD the URL and compare against the install-time
                       snapshot (only comparable headers count; any probe
                       failure is conservative: never stale).
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

    if asset.get("probe"):
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
