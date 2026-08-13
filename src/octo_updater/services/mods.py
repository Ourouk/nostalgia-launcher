"""Mods registry and engine: release lookup, install/uninstall, DLL wiring.

A curated set of client modifications installed from their official
GitHub/Codeberg releases and registered in dlls.txt. Registry order is
install order — VanillaFixes first (it provides the loader the other mods
rely on).
"""

import json
import os
import time
import urllib.request

from ..core.config_store import load_config, update_config
from ..core.constants import GITHUB_API, MOD_UA
from ..core.errors import describe_net_error
from ..core.log_sink import log
from ..core.security_http import secure_urlopen, ALLOWED_DOWNLOAD_HOSTS

# Registry order == install order: VanillaFixes first (it provides the
# loader the other mods rely on). The UI sorts alphabetically for display.
MODS_REGISTRY = [
    {
        "id":          "VanillaFixes",
        "essential": True,
        "name":        "VanillaFixes",
        "description": "Eliminates stuttering and animation lag. REQUIRED BY OTHER MODS.",
        "repo_url":    "https://github.com/hannesmann/vanillafixes",
        "source": {
            "kind":          "github_release",
            "owner":         "hannesmann",
            "repo":          "vanillafixes",
            "asset_pattern": "vanillafixes-*.zip",
            "prefer_no":     "-dxvk",
            "extract_map":   {"VfPatcher.dll": "VfPatcher.dll",
                              "VanillaFixes.exe": "VanillaFixes.exe"},
        },
        "register_dll":    "VfPatcher.dll",
        "installed_files": ["VfPatcher.dll", "VanillaFixes.exe"],
    },
    {
        "id":          "ClassicAPI",
        "essential": True,
        "name":        "ClassicAPI",
        "description": "Adds Lua API calls from later WoW versions. Required by addons.",
        "repo_url":    "https://github.com/brues-code/ClassicAPI",
        "source": {
            "kind":          "github_release",
            "owner":         "brues-code",
            "repo":          "ClassicAPI",
            "asset_pattern": "ClassicAPI.dll",
            "prefer_no":     None,
            "extract_map":   None,
        },
        "register_dll":    "ClassicAPI.dll",
        "installed_files": ["ClassicAPI.dll"],
    },
    {
        "id":          "dxvk",
        "essential": True,
        "name":        "dxvk",
        "description": "Enables Vulkan-based rendering for improved performance.",
        "repo_url":    "https://github.com/doitsujin/dxvk",
        "source": {
            "kind":          "github_release",
            "owner":         "doitsujin",
            "repo":          "dxvk",
            "asset_pattern": "dxvk-[0-9]*.tar.gz",
            "prefer_no":     "-native",
            "extract_map":   {"dxvk-*/x32/d3d9.dll": "d3d9.dll"},
            "post_install":  ["write_dxvk_conf"],
        },
        "register_dll":    "dxvk",
        "installed_files": ["d3d9.dll", "dxvk.conf"],
    },
    {
        "id":          "nampower",
        "essential": True,
        "name":        "nampower",
        "description": "A client modification that minimizes your input lag if you have higher latency.",
        "repo_url":    "https://github.com/Emyrk/nampower",
        "source": {
            "kind":          "github_release",
            "owner":         "Emyrk",
            "repo":          "nampower",
            "asset_pattern": "nampower.dll",
            "prefer_no":     None,
            "extract_map":   None,
        },
        "register_dll":    "nampower.dll",
        "installed_files": ["nampower.dll"],
    },
    {
        "id":          "SuperWoW",
        "essential": True,
        "name":        "SuperWoW",
        "description": "Expands the client API with backported features from later WoW versions. Required by addons.",
        "repo_url":    "https://github.com/balakethelock/SuperWoW",
        "source": {
            "kind":          "github_release",
            "owner":         "balakethelock",
            "repo":          "SuperWoW",
            "asset_pattern": "SuperWoW*.zip",
            "prefer_no":     None,
            # SuperWoW keeps a static "Release" tag and edits it in place, so
            # the version comes from the asset filename (e.g. …2.2.zip), not
            # the tag.
            "version_from":  "asset",
            "extract_map":   {"SuperWoWhook.dll": "SuperWoWhook.dll"},
        },
        "register_dll":    "SuperWoWhook.dll",
        "installed_files": ["SuperWoWhook.dll"],
    },
    {
        "id":          "transmogfix",
        "essential": True,
        "name":        "transmogfix",
        "description": "A client-side fix that eliminates frame drops caused by the server transmogrification durability workaround.",
        "repo_url":    "https://codeberg.org/MarcelineVQ/WeirdUtils",
        "source": {
            "kind":          "direct_file",
            "url":           "https://codeberg.org/MarcelineVQ/WeirdUtils/releases/download/v0.7.0/transmogfix.dll",
            "dest":          "transmogfix.dll",
            "pinned_version": "v0.7.0",
        },
        "register_dll":    "transmogfix.dll",
        "installed_files": ["transmogfix.dll"],
    },
    {
        "id":          "UnitXP_SP3",
        "essential": True,
        "name":        "UnitXP_SP3",
        "description": "Introduces modern quality-of-life features and improvements.",
        "repo_url":    "https://codeberg.org/konaka/UnitXP_SP3",
        "source": {
            "kind":          "codeberg_release",
            "owner":         "konaka",
            "repo":          "UnitXP_SP3",
            "asset_pattern": "UnitXP_SP3 v*.zip",
            "prefer_no":     "-debug",
            "extract_map":   {"UnitXP_SP3.dll": "UnitXP_SP3.dll"},
        },
        "register_dll":    "UnitXP_SP3.dll",
        "installed_files": ["UnitXP_SP3.dll"],
    },
    {
        "id":          "VanillaHelpers",
        "essential": True,
        "name":        "VanillaHelpers",
        "description": "Increases the maximum supported texture resolution and improves memory allocation.",
        "repo_url":    "https://github.com/isfir/VanillaHelpers",
        "source": {
            "kind":          "github_release",
            "owner":         "isfir",
            "repo":          "VanillaHelpers",
            "asset_pattern": "VanillaHelpers.dll",
            "prefer_no":     None,
            "extract_map":   None,
        },
        "register_dll":    "VanillaHelpers.dll",
        "installed_files": ["VanillaHelpers.dll"],
    },
    {
        "id":          "VanillaMultiMonitorFix",
        "essential": False,
        "name":        "VanillaMultiMonitorFix",
        "description": "Fixes the client misbehaving on multi-monitor setups with differing resolutions.",
        "repo_url":    "https://github.com/Mates1500/VanillaMultiMonitorFix",
        "source": {
            "kind":          "github_release",
            "owner":         "Mates1500",
            "repo":          "VanillaMultiMonitorFix",
            "asset_pattern": "release.zip",
            "prefer_no":     None,
            "extract_map":   {"VanillaMultiMonitorFix.dll":
                              "VanillaMultiMonitorFix.dll",
                              "VMMFix_preferred_monitor.txt":
                              "VMMFix_preferred_monitor.txt"},
        },
        "register_dll":    "VanillaMultiMonitorFix.dll",
        "installed_files": ["VanillaMultiMonitorFix.dll",
                            "VMMFix_preferred_monitor.txt"],
    },
]


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
                            allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
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
                            allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
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
                            allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
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
                            allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
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
