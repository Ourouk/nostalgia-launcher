"""Server-agnostic upstream file-manifest reader.

A distribution may publish a JSON manifest listing the files it owns
(``server.download.manifest`` in the launcher config). This module fetches
that document over HTTPS, extracts the file list using the config's
``schema`` reader-map, routes each path through the config's ``routes``
globs, and builds launcher-catalog-shaped bundled entries for the
mods/assets verticals.

No per-distribution strings live here — the mapping is owned by the
distribution's own config. Whole-file ``direct_file`` fetch; staleness
via the manifest ``version`` cursor plus existence checks. Each entry's
``sha256`` is parsed and shape-validated but not yet enforced on
download or used for staleness.
"""

from __future__ import annotations

import fnmatch
import json
from typing import TYPE_CHECKING
from urllib import request as urllib_request

from ..core.constants import UA
from ..core.log_sink import log
from ..core.safety import safe_relpath
from ..core.security_http import read_capped, secure_urlopen

if TYPE_CHECKING:
    pass

MANIFEST_FETCH_CAP = 2 * 1024 * 1024
MANIFEST_CACHE_KEY = "__manifest_snapshot__"


def _manifest_block() -> dict | None:
    """The validated ``server.download.manifest`` reader map, or None."""
    from ..core import launcher as _launcher

    cfg = _launcher.config()
    block = getattr(cfg, "download_manifest", None)
    return dict(block) if isinstance(block, dict) else None


def _get_key(doc: object, key: str) -> object:
    """Minimal dotted-key lookup (single level is all schemas need)."""
    if not isinstance(key, str) or not key.strip():
        return None
    if not isinstance(doc, dict):
        return None
    return doc.get(key.strip())


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def parse_manifest(doc: object, schema: dict) -> tuple[str, list[dict]]:
    """Extract ``(version, entries)`` from a manifest document.

    ``entries`` are ``{"path", "size", "sha256", "download_url"}`` dicts.
    Raises ``ValueError`` on a malformed document or schema mismatch.
    """
    if not isinstance(doc, dict):
        raise ValueError("manifest document must be a JSON object")
    if not isinstance(schema, dict):
        raise ValueError("manifest schema must be an object")
    files_key = _text(schema.get("files"))
    version_key = _text(schema.get("version"))
    base_key = _text(schema.get("base_url"))
    url_template = _text(schema.get("download_url"))
    entry_map = schema.get("entry")
    if not files_key or not version_key or not base_key or not url_template:
        raise ValueError("manifest schema is missing required keys")
    if not isinstance(entry_map, dict):
        raise ValueError("manifest schema.entry must be an object")
    path_key = _text(entry_map.get("path"))
    size_key = _text(entry_map.get("size"))
    hash_key = _text(entry_map.get("sha256"))
    if not path_key or not size_key or not hash_key:
        raise ValueError("manifest schema.entry is missing required keys")

    version_raw = _get_key(doc, version_key)
    version = version_raw.strip() if isinstance(version_raw, str) else ""
    if not version:
        raise ValueError("manifest is missing its version field")
    base_raw = _get_key(doc, base_key)
    if not isinstance(base_raw, str) or not base_raw.strip():
        raise ValueError("manifest is missing its base URL field")
    base_url = base_raw.strip()
    if not base_url.startswith("https://"):
        raise ValueError("manifest base URL must be https")
    if not base_url.endswith("/"):
        base_url += "/"

    raw_files = _get_key(doc, files_key)
    if not isinstance(raw_files, list):
        raise ValueError("manifest file list is missing or not a list")
    entries: list[dict] = []
    for i, raw in enumerate(raw_files):
        if not isinstance(raw, dict):
            raise ValueError(f"manifest files[{i}] must be an object")
        path_raw = raw.get(path_key)
        if not isinstance(path_raw, str) or not safe_relpath(path_raw):
            raise ValueError(f"manifest files[{i}] has an unsafe path")
        path = path_raw.strip()
        size_raw = raw.get(size_key)
        if (
            isinstance(size_raw, bool)
            or not isinstance(size_raw, (int, float))
            or int(size_raw) <= 0
        ):
            raise ValueError(f"manifest files[{i}] has an invalid size")
        sha_raw = raw.get(hash_key)
        sha = (
            sha_raw.strip().lower()
            if isinstance(sha_raw, str) and sha_raw.strip()
            else ""
        )
        if sha and (
            len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)
        ):
            raise ValueError(f"manifest files[{i}] has an invalid sha256")
        try:
            download_url = url_template.format(base_url=base_url, path=path)
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(
                f"manifest download_url template failed: {e}"
            ) from e
        if not download_url.startswith("https://"):
            raise ValueError("manifest download URL must be https")
        entries.append(
            {
                "path": path,
                "size": int(size_raw),
                "sha256": sha,
                "download_url": download_url,
            }
        )
    return version, entries


def route_entry(path: str, routes: list) -> dict | None:
    """First matching route for ``path`` (case-sensitive glob), or None."""
    for route in routes:
        if not isinstance(route, dict):
            continue
        match = route.get("match")
        if not isinstance(match, str) or not match:
            continue
        if fnmatch.fnmatchcase(path, match):
            return route
    return None


def bundled_assets(snapshot_entries: list[dict] | None = None) -> list:
    """Manifest-routed asset entries (validated, catalog-shaped)."""
    return _bundled("asset", snapshot_entries)


def bundled_mods(snapshot_entries: list[dict] | None = None) -> list:
    """Manifest-routed mod entries (validated, catalog-shaped)."""
    return _bundled("mod", snapshot_entries)


def _bundled(target: str, snapshot_entries: list[dict] | None = None) -> list:
    from . import catalog as _catalog

    block = _manifest_block()
    if not block:
        return []
    routes = block.get("routes")
    if not isinstance(routes, list):
        return []
    entries = snapshot_entries
    version = ""
    if entries is None:
        snapshot = fetch_snapshot()
        if snapshot is None:
            return []
        entries = snapshot.get("entries", [])
        version = snapshot.get("version", "")
    out: list = []
    for entry in entries:
        path = entry.get("path", "")
        route = route_entry(path, routes)
        if route is None or route.get("as") != target:
            continue
        if target == "asset":
            built = _build_asset(entry, route, version)
            if _catalog.validate_asset(built) is not None:
                out.append(_catalog.validate_asset(built))
        else:
            built = _build_mod(entry, route, version)
            if _catalog.validate_mod(built) is not None:
                out.append(_catalog.validate_mod(built))
    return out


def _slug(path: str) -> str:
    slug = path.lower().replace("/", "-").replace("\\", "-")
    slug = "".join(c if c.isalnum() or c in "-_." else "-" for c in slug)
    return slug.strip("-") or "file"


def _build_asset(entry: dict, route: dict, version: str) -> dict:
    return {
        "id": f"manifest-{_slug(entry['path'])}",
        "name": entry["path"],
        "description": f"Server file (manifest v{version})"
        if version
        else "Server file",
        "url": entry["download_url"],
        "dest": entry["path"],
        "version": version or None,
        "probe": bool(route.get("probe", False)),
    }


def _build_mod(entry: dict, route: dict, version: str) -> dict:
    import os as _os

    dest = _os.path.basename(entry["path"])
    mod: dict = {
        "id": f"manifest-{_slug(entry['path'])}",
        "name": _os.path.basename(entry["path"]),
        "type": route.get("type", "mod"),
        "description": f"Server file (manifest v{version})"
        if version
        else "Server file",
        "source_type": "direct",
        "source": {
            "kind": "direct_file",
            "url": entry["download_url"],
            "dest": dest,
            "pinned_version": version or None,
        },
    }
    register_dll = route.get("register_dll")
    if isinstance(register_dll, list) and register_dll:
        mod["register_dll"] = [str(d) for d in register_dll]
        mod["installed_files"] = [str(d) for d in register_dll]
    else:
        mod["installed_files"] = [dest]
    executable = route.get("executable")
    if isinstance(executable, str) and executable.strip():
        mod["executable"] = executable.strip()
    return mod


def fetch_snapshot(*, force: bool = False) -> dict | None:
    """Fetch + parse the manifest, cached in the config store.

    Returns ``{"version", "entries", "url"}`` or None when no manifest
    is configured / unreachable with nothing cached. ``force=True``
    refetches; otherwise the cached snapshot is served network-free.
    """
    from ..core import config_store as _store

    block = _manifest_block()
    if not block:
        return None
    url = block.get("url", "")
    schema = block.get("schema", {})
    if not url or not isinstance(schema, dict):
        return None
    stored = _store.load_config().get(MANIFEST_CACHE_KEY)
    cached = stored if isinstance(stored, dict) else None
    if not force and cached and cached.get("url") == url:
        entries = cached.get("entries")
        if isinstance(entries, list):
            return cached
    try:
        req = urllib_request.Request(url, headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=30) as r:
            doc = json.loads(read_capped(r, MANIFEST_FETCH_CAP))
        version, entries = parse_manifest(doc, schema)
    except Exception as e:
        log(f"Manifest fetch failed: {e}", "err")
        if cached and cached.get("url") == url:
            return cached
        return None
    snapshot = {"url": url, "version": version, "entries": entries}
    try:
        _store.update_config(
            lambda c: c.__setitem__(MANIFEST_CACHE_KEY, snapshot)
        )
    except Exception as e:
        log(f"Could not cache manifest snapshot: {e}", "err")
    return snapshot


def manifest_update_available(
    snapshot: dict | None = None,
) -> tuple[bool, str]:
    """Whether the cached client predates the manifest version."""
    from ..core import config_store as _store

    snap = snapshot or fetch_snapshot()
    if snap is None:
        return False, ""
    version = snap.get("version", "")
    if not version:
        return False, ""
    installed = _store.load_config().get("__manifest_installed__")
    if not isinstance(installed, dict):
        return True, version
    if installed.get("url") != snap.get("url"):
        return True, version
    if installed.get("version") != version:
        return True, version
    return False, version


def stale_overlay_entries(
    out_dir: str,
    entries: list,
    log=None,
) -> list[str]:
    """Manifest-listed paths missing on disk (client-relative).

    Phase 1 staleness is existence-only: any listed path absent from
    the client is stale. Hash comparison lands with sha256 support.
    """
    import os as _os

    stale: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path", "")
        if not isinstance(path, str) or not path:
            continue
        full = _os.path.join(out_dir, path.replace("/", _os.sep))
        try:
            if not _os.path.isfile(full):
                stale.append(path)
        except OSError:
            stale.append(path)
    return sorted(stale)


def remember_installed_version(version: str, url: str) -> None:
    """Record the manifest version now present on disk."""
    from ..core import config_store as _store

    try:
        _store.update_config(
            lambda c: c.__setitem__(
                "__manifest_installed__",
                {"version": version, "url": url},
            )
        )
    except Exception as e:
        log(f"Could not record manifest version: {e}", "err")
