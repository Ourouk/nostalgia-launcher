"""Mods engine: release lookup, install/uninstall, DLL wiring.

Mods are installed from their release archives and registered in dlls.txt.
There is no bundled registry — the mod list comes entirely from the mod
catalog (launcher-configured or user-set URL) merged with the per-user custom
file, so a distribution decides what it ships.
"""

import json
import os
import time
import urllib.request

from ..core.config_store import load_config, update_config
from ..core.constants import GITHUB_API, MOD_UA
from ..core.errors import describe_net_error
from ..core.log_sink import log
from ..core.security_http import secure_urlopen, allowed_download_hosts
from . import catalog

# The per-user custom mod file (a JSON list, one entry per mod, using the
# same shape the mod catalog uses). Written empty on first use via Settings.
CUSTOM_FILE_TEMPLATE = "[\n]\n"


def fetch_mods_catalog(force=False) -> list | None:
    """Mod catalog, cached in the config file ({"mods_catalog_cache":
    {"timestamp": epoch, "catalog": […]}}).

    Non-forced calls never hit the network when a cached copy exists, and
    return None (→ an empty registry) when there is none yet, so a first run
    is fully offline-safe. Forced calls (Settings → Reload) always fetch and
    raise when the URL is unset or the network fails with nothing cached.
    """
    now = time.time()
    entry = load_config().get("mods_catalog_cache", {})
    cached = entry.get("catalog")
    if not force:
        return cached if cached is not None else None
    url = registry_url()
    if not url:
        raise RuntimeError("Mod catalog URL is not configured.")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MOD_UA})
        with secure_urlopen(req, timeout=10) as r:
            raw = json.load(r)
    except Exception:
        if cached is not None:
            return cached
        raise
    validated = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        cleaned = catalog.validate_mod(e)
        if cleaned is not None:
            validated.append(cleaned)
    update_config(lambda c: c.__setitem__(
        "mods_catalog_cache", {"timestamp": now, "catalog": validated}))
    return validated


def mods_registry(force=False) -> list:
    """The effective mod registry: the remote/cached catalog merged with the
    per-user custom file. Empty when nothing is configured yet."""
    remote = fetch_mods_catalog(force=force)
    base = [] if remote is None else remote
    return catalog.merge_mods(base, catalog.load_custom(
        "mods", catalog.validate_mod))


def registry_url() -> str:
    """The active mod catalog URL: a user override (Settings), else the
    launcher-configured URL, else ''."""
    return catalog.get_registry_url("mods") or mods_registry_default_url()


def mods_registry_default_url() -> str:
    """The launcher-configured mod catalog URL ('' when not configured)."""
    from ..core import launcher
    return launcher.mods_registry_url()


def set_registry_url(url: str) -> str | None:
    """Validate and store a per-user catalog URL override (empty clears it);
    returns an error string or None on success."""
    return catalog.set_registry_url("mods", url)


def reset_registry_url():
    """Drop the per-user override so the launcher-configured URL is used."""
    catalog.reset_registry_url("mods")


def custom_file() -> str:
    """Path of the per-user custom mod JSON file."""
    return catalog.custom_file("mods")


def open_custom_file() -> bool:
    """Create the custom mod file (with the template) when missing."""
    return catalog.write_custom_template("mods", CUSTOM_FILE_TEMPLATE)


def clear_custom_file() -> bool:
    """Delete the custom mod file. True when something was removed."""
    return catalog.clear_custom("mods")


def _codeberg_latest(owner: str, repo: str, raise_errors=False) -> dict | None:
    url = f"https://codeberg.org/api/v1/repos/{owner}/{repo}/releases?limit=10&pre-release=false"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MOD_UA})
        with secure_urlopen(req, timeout=10) as r:
            releases = json.load(r)
        for rel in releases:
            if not rel.get("prerelease", False) and not rel.get("draft", False):
                return rel
        return releases[0] if releases else None
    except Exception as e:
        if raise_errors:
            raise RuntimeError(describe_net_error(e)) from e
        return None


DXVK_CONF_CONTENT = """# Low latency - limit queued frames - helps input lag
d3d9.maxFrameLatency = 1
# Forces clamp for AF through DXVK - if you see grass textures shimmering or shaking try false
d3d9.clampNegativeLodBias = True
# Disable logging for performance
dxvk.logLevel = none
# Triple buffering (needed for smooth G-SYNC + RTSS capping) can try lowering backbuffers to 2 if want
dxvk.presentInterval = 0
dxvk.numBackBuffers = 3
# Use hardware mouse for responsiveness
d3d9.cursor = 1
# VanillaFix handles DPI awareness; avoid double-scaling
d3d9.dpiAware = False
# Enable GPL if supported to reduce stuttering (NVIDIA 473.33+, AMD 24.6.1+)
dxvk.enableGraphicsPipelineLibrary = Auto
# Track pipeline lifetimes to reduce memory usage
dxvk.trackPipelineLifetime = True
# Limit compiler threads to reduce memory usage
dxvk.numCompilerThreads = 2
"""


def _write_dxvk_conf(client_dir: str):
    path = os.path.join(client_dir, "dxvk.conf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(DXVK_CONF_CONTENT)
    log("  Wrote dxvk.conf")


def _github_latest(owner: str, repo: str, raise_errors=False) -> dict | None:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/releases/latest"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MOD_UA})
        with secure_urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as e:
        if raise_errors:
            raise RuntimeError(describe_net_error(e)) from e
        return None


def _pick_asset(assets: list, pattern: str, prefer_no) -> dict | None:
    import fnmatch
    candidates = [a for a in assets if fnmatch.fnmatch(a["name"], pattern)]
    if prefer_no:
        preferred = [a for a in candidates if prefer_no not in a["name"]]
        if preferred:
            candidates = preferred
    return candidates[0] if candidates else None


def _release_version(mod: dict, rel: dict) -> str | None:
    """Version string for a github/codeberg release. Normally the tag name —
    but some mods (e.g. SuperWoW) keep a static tag and edit the release in
    place, so their tag never changes. For those, derive the version from the
    matched asset instead: its filename embeds the real version."""
    src = mod["source"]
    if src.get("version_from") == "asset":
        asset = _pick_asset(rel.get("assets", []), src["asset_pattern"],
                            src.get("prefer_no"))
        if asset and asset.get("name"):
            import re
            m = re.search(r"\d+(?:[._]\d+)+", asset["name"])
            return m.group(0) if m else asset["name"]
    return rel.get("tag_name")


def fetch_mod_latest_version(mod: dict) -> str | None:
    src  = mod["source"]
    kind = src["kind"]
    if kind == "github_release":
        rel = _github_latest(src["owner"], src["repo"])
        if rel:
            return _release_version(mod, rel)
    elif kind == "codeberg_release":
        rel = _codeberg_latest(src["owner"], src["repo"])
        if rel:
            return _release_version(mod, rel)
    elif kind in ("direct_file", "direct_tar"):
        return src.get("pinned_version")
    return None


_MOD_VERSION_CACHE_TTL = 3600


def _slim_release(rel: dict) -> dict:
    """Reduce an API release object to the fields the updater actually uses,
    so the persisted cache stays small."""
    return {
        "tag_name": rel.get("tag_name"),
        "assets": [{"name": a.get("name"),
                    "size": a.get("size", 0),
                    "browser_download_url": a.get("browser_download_url")}
                   for a in rel.get("assets", [])],
    }


def _fetch_release_cached(mod: dict, force: bool = False) -> dict | None:
    """Latest-release lookup backed by a persistent cache in the config file
    ({"mod_release_cache": {mod_id: {"timestamp": epoch, "release": {…}}}}),
    so restarts within the TTL don't re-hit the GitHub/Codeberg APIs."""
    src  = mod["source"]
    kind = src["kind"]
    if kind not in ("github_release", "codeberg_release"):
        return None
    mid = mod["id"]
    now = time.time()
    if not force:
        entry = load_config().get("mod_release_cache", {}).get(mid)
        if entry and (now - entry.get("timestamp", 0)) < _MOD_VERSION_CACHE_TTL:
            return entry.get("release")
    if kind == "github_release":
        rel = _github_latest(src["owner"], src["repo"])
    else:
        rel = _codeberg_latest(src["owner"], src["repo"])
    if rel is None:
        return None
    rel = _slim_release(rel)
    update_config(lambda c: c.setdefault("mod_release_cache", {}).__setitem__(
        mid, {"timestamp": now, "release": rel}))
    return rel


def fetch_mod_latest_version_cached(mod: dict, force: bool = False) -> str | None:
    src  = mod["source"]
    kind = src["kind"]
    if kind in ("direct_file", "direct_tar"):
        return src.get("pinned_version")
    rel = _fetch_release_cached(mod, force=force)
    if rel:
        return _release_version(mod, rel)
    return None


def install_mod(mod: dict, client_dir: str, release: dict | None = None) -> list:
    src     = mod["source"]
    written = []

    if src["kind"] == "codeberg_release":
        rel = release if release is not None else \
            _codeberg_latest(src["owner"], src["repo"], raise_errors=True)
        if not rel:
            raise RuntimeError("no release found on Codeberg")
        import fnmatch
        assets = rel.get("assets", [])
        asset  = next(
            (a for a in assets if fnmatch.fnmatch(a["name"], src["asset_pattern"])
             and (not src.get("prefer_no") or src["prefer_no"] not in a["name"])),
            None
        )
        if not asset:
            raise RuntimeError(
                f"No matching asset '{src['asset_pattern']}' in {mod['id']} release")
        log(f"  Downloading {asset['name']} ({asset['size']//1024} KB)...")
        req = urllib.request.Request(asset["browser_download_url"],
                                     headers={"User-Agent": MOD_UA})
        with secure_urlopen(req, timeout=120,
                            allowed_hosts=allowed_download_hosts()) as r:
            data = r.read()
        if src.get("extract_map") is None:
            dest_rel = asset["name"]
            dest     = os.path.join(client_dir, dest_rel)
            os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            written.append(dest_rel)
            log(f"  Installed {dest_rel}")
        else:
            import zipfile
            tmp_path = os.path.join(client_dir, f"_mod_tmp_{mod['id']}.zip")
            try:
                with open(tmp_path, "wb") as f:
                    f.write(data)
                with zipfile.ZipFile(tmp_path) as zf:
                    for zip_path, dest_rel in src["extract_map"].items():
                        try:
                            zip_data = zf.read(zip_path)
                        except KeyError:
                            log(f"  Warning: {zip_path} not in zip, skipping")
                            continue
                        dest = os.path.join(client_dir, dest_rel)
                        os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                        with open(dest, "wb") as f:
                            f.write(zip_data)
                        written.append(dest_rel)
                        log(f"  Installed {dest_rel}")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    elif src["kind"] == "github_release":
        rel = release if release is not None else \
            _github_latest(src["owner"], src["repo"], raise_errors=True)
        if not rel:
            raise RuntimeError("no release found on GitHub")
        asset = _pick_asset(rel.get("assets", []), src["asset_pattern"], src["prefer_no"])
        if not asset:
            raise RuntimeError(
                f"No matching asset '{src['asset_pattern']}' in {mod['id']} release")
        log(f"  Downloading {asset['name']} ({asset['size']//1024} KB)...")
        req = urllib.request.Request(asset["browser_download_url"],
                                     headers={"User-Agent": MOD_UA})
        with secure_urlopen(req, timeout=120,
                            allowed_hosts=allowed_download_hosts()) as r:
            data = r.read()

        if src.get("extract_map") is None:
            dest_rel = asset["name"]
            dest     = os.path.join(client_dir, dest_rel)
            os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            written.append(dest_rel)
            log(f"  Installed {dest_rel}")
        elif asset["name"].endswith(".tar.gz") or asset["name"].endswith(".tgz"):
            import tarfile, io, fnmatch as _fnmatch
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                all_names = tf.getnames()
                for pattern, dest_rel in src["extract_map"].items():
                    matched = (
                        pattern if pattern in all_names
                        else next(
                            (n for n in all_names if _fnmatch.fnmatch(n, pattern)),
                            None
                        )
                    )
                    if matched is None:
                        log(f"  Warning: no file matching '{pattern}' in tar, skipping")
                        continue
                    f_obj    = tf.extractfile(tf.getmember(matched))
                    tar_data = f_obj.read()
                    dest = os.path.join(client_dir, dest_rel)
                    os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                    with open(dest, "wb") as f:
                        f.write(tar_data)
                    written.append(dest_rel)
                    log(f"  Installed {dest_rel}")
        else:
            import zipfile
            tmp_path = os.path.join(client_dir, f"_mod_tmp_{mod['id']}.zip")
            try:
                with open(tmp_path, "wb") as f:
                    f.write(data)
                with zipfile.ZipFile(tmp_path) as zf:
                    for zip_path, dest_rel in src["extract_map"].items():
                        try:
                            zip_data = zf.read(zip_path)
                        except KeyError:
                            log(f"  Warning: {zip_path} not found in zip, skipping")
                            continue
                        dest = os.path.join(client_dir, dest_rel)
                        os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                        with open(dest, "wb") as f:
                            f.write(zip_data)
                        written.append(dest_rel)
                        log(f"  Installed {dest_rel}")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    elif src["kind"] == "direct_tar":
        log(f"  Downloading {src['url'].split('/')[-1]}...")
        req = urllib.request.Request(src["url"], headers={"User-Agent": MOD_UA})
        with secure_urlopen(req, timeout=120,
                            allowed_hosts=allowed_download_hosts()) as r:
            data = r.read()
        import tarfile, io as _io, fnmatch as _fnmatch
        with tarfile.open(fileobj=_io.BytesIO(data), mode="r:gz") as tf:
            all_names = tf.getnames()
            for pattern, dest_rel in src["extract_map"].items():
                matched = pattern if pattern in all_names else next(
                    (n for n in all_names if _fnmatch.fnmatch(n, pattern)), None)
                if matched is None:
                    log(f"  Warning: no file matching '{pattern}' in tar, skipping")
                    continue
                tar_data = tf.extractfile(tf.getmember(matched)).read()
                dest = os.path.join(client_dir, dest_rel)
                os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(tar_data)
                written.append(dest_rel)
                log(f"  Installed {dest_rel}")
        if src.get("pinned_version"):
            mod["_resolved_version"] = src["pinned_version"]

    elif src["kind"] == "direct_file":
        url = src["url"]
        log(f"  Downloading {url.rsplit('/', 1)[-1]}...")
        req = urllib.request.Request(url, headers={"User-Agent": MOD_UA})
        with secure_urlopen(req, timeout=120,
                            allowed_hosts=allowed_download_hosts()) as r:
            data = r.read()

        if src.get("extract_map") is None:
            dest_rel = src["dest"]
            dest     = os.path.join(client_dir, dest_rel)
            os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            written.append(dest_rel)
            log(f"  Installed {dest_rel}")
        elif url.endswith(".tar.gz") or url.endswith(".tgz"):
            import tarfile, io, fnmatch as _fnmatch
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                all_names = tf.getnames()
                for pattern, dest_rel in src["extract_map"].items():
                    matched = (
                        pattern if pattern in all_names
                        else next(
                            (n for n in all_names if _fnmatch.fnmatch(n, pattern)),
                            None
                        )
                    )
                    if matched is None:
                        log(f"  Warning: no file matching '{pattern}' in tar, skipping")
                        continue
                    f_obj    = tf.extractfile(tf.getmember(matched))
                    tar_data = f_obj.read()
                    dest = os.path.join(client_dir, dest_rel)
                    os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                    with open(dest, "wb") as f:
                        f.write(tar_data)
                    written.append(dest_rel)
                    log(f"  Installed {dest_rel}")
        else:
            import zipfile
            tmp_path = os.path.join(client_dir, f"_mod_tmp_{mod['id']}.zip")
            try:
                with open(tmp_path, "wb") as f:
                    f.write(data)
                with zipfile.ZipFile(tmp_path) as zf:
                    for zip_path, dest_rel in src["extract_map"].items():
                        try:
                            zip_data = zf.read(zip_path)
                        except KeyError:
                            log(f"  Warning: {zip_path} not in zip, skipping")
                            continue
                        dest = os.path.join(client_dir, dest_rel)
                        os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                        with open(dest, "wb") as f:
                            f.write(zip_data)
                        written.append(dest_rel)
                        log(f"  Installed {dest_rel}")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        if src.get("pinned_version"):
            mod["_resolved_version"] = src["pinned_version"]

    for hook in src.get("post_install", []):
        if hook == "write_dxvk_conf":
            _write_dxvk_conf(client_dir)
            written.append("dxvk.conf")

    return written


def uninstall_mod(mod: dict, client_dir: str):
    cfg   = load_config()
    state = cfg.get("mods", {}).get(mod["id"], {})
    files = state.get("installed_files", mod.get("installed_files", []))
    for rel in files:
        full = os.path.join(client_dir, rel)
        if os.path.exists(full):
            os.remove(full)
            log(f"  Removed {rel}")


def _dlls_txt_path(client_dir: str) -> str:
    return os.path.join(client_dir, "dlls.txt")


def add_dll(client_dir: str, name: str):
    path  = _dlls_txt_path(client_dir)
    lines = open(path).read().splitlines() if os.path.exists(path) else []
    if any(l.strip().lower() == name.lower() for l in lines):
        return
    lines = [l for l in lines if l.strip()] + [name]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def remove_dll(client_dir: str, name: str):
    path = _dlls_txt_path(client_dir)
    if not os.path.exists(path):
        return
    lines = [l for l in open(path).read().splitlines()
             if l.strip().lower() != name.lower()]
    if not lines:
        os.remove(path)
    else:
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")


def mod_installed_files_present(mod: dict, client_dir: str) -> bool:
    cfg   = load_config()
    state = cfg.get("mods", {}).get(mod["id"], {})
    files = state.get("installed_files", [])
    return bool(files) and all(
        os.path.exists(os.path.join(client_dir, f)) for f in files)


def mod_supports_update_check(mod: dict) -> bool:
    return mod["source"]["kind"] not in ("direct_file", "direct_tar")


def mod_update_available(mod: dict, state: dict, live: dict | None) -> bool:
    if not mod_supports_update_check(mod):
        return False
    if not state.get("enabled", False):
        return False
    if state.get("ignore_updates", False):
        return False
    installed_ver = state.get("installed_version")
    if not installed_ver:
        return False
    latest_ver = (live or {}).get("latest_version")
    if not latest_ver:
        return False
    return latest_ver != installed_ver
