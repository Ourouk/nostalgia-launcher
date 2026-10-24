"""Addons engine: catalog, Git commit resolution and archive installation.

Addons are installed directly from Git hosts (GitHub, GitLab, Gitea,
Codeberg, plus any community host a distribution lists in its launcher
config) by downloading the repo archive pinned to a commit SHA — no git
client needed. Commit resolution and archive fetching are delegated to the
shared ``git_archive`` source backend (`services/sources`); this module
keeps the addon-specific catalog machinery and the folder unpacking
call.

There is no hardcoded addon list — the ADDONS tab comes from the addon
catalog URLs (launcher-configured or user-set), the local addons repo
(`local_addons_repo.json`: server-imported entries written by the config
import plus user-added customs), optional entries embedded in the launcher
config, and the per-user custom file, so a distribution decides what it
ships.
"""

import os
import time
import urllib.request

from ..core import config_store as _config_store
from ..core import launcher
from ..core.config_store import load_config, update_config
from ..core.constants import UA
from ..core.log_sink import log
from ..core.safety import https_url
from ..core.security_http import read_capped, secure_urlopen
from . import catalog
from .sources import deploy as _sources_deploy
from .sources.git_archive import (
    _ARCHIVE_MAX_BYTES,
    GitArchiveBackend,
)

_GIT_BACKEND = GitArchiveBackend()

# Catalogs refresh at most weekly (shared catalog.CATALOG_TTL); the
# per-URL timestamp lives in the config file.
ADDONS_VERIFY_TTL = 300  # skip re-verify on tab switches within this

# The per-user custom addon file (a JSON list, one entry per addon). Written
# empty on first use via Settings → Catalog registries.

# Recommended addon folder names ({folder: git_url}) for the star badge and
# the one-shot auto-install. Empty by default — a distribution may flag
# addons as recommended via its catalog's "recommended" flag instead.
RECOMMENDED_ADDONS: dict = {}

# Never shown in the updater, even when the catalog carries them. Empty by
# default — a distribution may populate it via its catalog's "blocked" flag.
BLOCKED_ADDONS = set()


def addons_path(client_dir: str) -> str:
    return os.path.join(client_dir, "Interface", "AddOns")


def is_allowed_git_url(url: str) -> bool:
    """Whether the URL's host is on the git-host allowlist (base hosts plus
    the launcher config's extras). Delegates to the git_archive backend."""
    from .sources.git_archive import is_allowed_git_url as _allowed

    return _allowed(url)


def _custom_validator(entry: dict) -> dict | None:
    """Validate a custom addon entry and enforce the git-host allowlist
    for git-kind entries (direct entries carry a download URL instead)."""
    cleaned = catalog.validate_addon(entry)
    if cleaned is None:
        return None
    if (cleaned.get("source_type") or "git") == "direct":
        return cleaned
    if cleaned["git"] and not is_allowed_git_url(cleaned["git"]):
        return None
    return cleaned


def validate_custom_entry(entry: dict) -> dict | None:
    """Public hook for controllers persisting user-built addon entries —
    same rules as every other custom source (validate_addon + git-host
    allowlist)."""
    return _custom_validator(entry)


def fetch_addons_catalog(force=False) -> list:
    """The ordered addon catalog: every configured registry URL is fetched
    (or served from its own cache entry) and merged in order — a later
    registry overrides an earlier one by addon folder name. Cached per URL
    for a day ({"addons_catalog_cache": {url: {"timestamp", "catalog"}}}).
    A failed URL falls back to its last cached copy; an unconfigured URL
    list returns an empty list."""
    urls = registry_urls()
    if not urls:
        log("Addon catalog URL is not configured.", "err")
        return []
    now = time.time()
    cache = load_config().get("addons_catalog_cache", {})
    if isinstance(cache, dict) and "catalog" in cache and urls[0] not in cache:
        # Legacy single-URL cache shape → re-key it under the first
        # configured URL so the per-URL lookup keeps working.
        update_config(
            lambda c, u=urls[0]: c.setdefault(
                "addons_catalog_cache", {}
            ).__setitem__(u, c["addons_catalog_cache"])
        )
    merged = []
    for url in urls:
        entry = _cache_entry(url)
        fresh = (
            entry.get("catalog") is not None
            and (now - entry.get("timestamp", 0)) < catalog.CATALOG_TTL
        )
        try:
            part = catalog.fetch_url_catalog(
                "addons",
                _custom_validator,
                url,
                force=force or not fresh,
                # Route through this module's seam (patched by tests
                # as `addons.secure_urlopen`) instead of the toolkit's
                # own import.
                urlopen=secure_urlopen,
            )
            if part is None:
                part = entry.get("catalog") or []
        except Exception:
            # offline — serve the last good cached copy
            # for this URL
            part = entry.get("catalog") or []
        merged = catalog.merge_addons(merged, part)
    return merged


def _cache_entry(url: str) -> dict:
    """The cached catalog record for a URL (the per-URL
    ``{url: {timestamp, catalog}}`` shape). Read through `_config_store` so
    the controller's offline fallback honors test monkeypatches of
    `config_store.load_config`."""
    cache = _config_store.load_config().get("addons_catalog_cache", {}) or {}
    if isinstance(cache, dict) and url in cache:
        return cache[url]
    return {}


def embedded_addons() -> list:
    """Addons defined inline in the active launcher config (top-level
    "addons": […]), sanitized through the same validator as remote entries
    (including the git-host allowlist); unusable entries are skipped with a
    logged warning. Network-free."""
    return catalog.validate_entries(
        launcher.embedded_addons(), _custom_validator, "launcher config"
    )


def addons_catalog(force=False) -> list:
    """The effective addon catalog, in override order (later wins by folder
    name): the remote/cached catalogs < the local repo's server-imported
    entries < the launcher config's embedded addons < the repo's
    user-custom entries."""
    remote = fetch_addons_catalog(force=force)
    repo = catalog.read_local_repo("addons")
    return catalog.merge_addons(
        catalog.merge_addons(
            catalog.merge_addons(
                remote,
                catalog.validate_entries(
                    repo["server"],
                    _custom_validator,
                    "local addons repo",
                ),
            ),
            embedded_addons(),
        ),
        catalog.validate_entries(
            repo["custom"], _custom_validator, "local addons repo"
        ),
    )


def catalog_from_cache() -> list:
    """Every local layer merged without any network — used as the offline
    fallback when a fresh fetch fails. Same override order as
    `addons_catalog`."""
    urls = registry_urls()
    parts = []
    if urls:
        for url in urls:
            entry = _cache_entry(url)
            if entry.get("catalog"):
                parts.append(entry["catalog"])
    repo = catalog.read_local_repo("addons")
    parts.append(
        catalog.validate_entries(
            repo["server"], _custom_validator, "local addons repo"
        )
    )
    parts.append(embedded_addons())
    parts.append(
        catalog.validate_entries(
            repo["custom"], _custom_validator, "local addons repo"
        )
    )
    merged = []
    for part in parts:
        merged = catalog.merge_addons(merged, part)
    return merged


def _default_enabled() -> bool:
    try:
        val = load_config().get("addons_default_enabled")
    except Exception:
        return True
    if val is None:
        return True
    return bool(val)


def _effective_addons_urls() -> list[str]:
    """Server explicit URLs, else community default for ``client_version``."""
    explicit = launcher.addons_registry_urls()
    non_empty = [u for u in explicit if u and u.strip()]
    if non_empty:
        return non_empty
    if not _default_enabled():
        return []
    default = launcher.default_addons_url_for_version(
        launcher.client_version()
    )
    return [default] if default else []


def registry_url() -> str:
    """The addon catalog URL shown in Settings: a per-user override, else the
    first effective URL (server explicit or community default), else ''."""
    override = catalog.get_registry_url("addons")
    if override:
        return override
    urls = _effective_addons_urls()
    return urls[0] if urls else ""


def registry_urls() -> list[str]:
    """The ordered list of addon catalog URLs in effect: a per-user override
    (Settings) replaces the whole list with itself; otherwise the
    effective list (server explicit or community default) is used."""
    override = catalog.get_registry_url("addons")
    if override:
        return [override]
    return _effective_addons_urls()


def addons_default_available() -> bool:
    from ..core import launcher as _launcher

    return _launcher.has_default_addons_for_version(_launcher.client_version())


def set_registry_url(url: str) -> str | None:
    """Validate and store a per-user catalog URL override (empty clears it);
    returns an error string or None on success."""
    return catalog.set_registry_url("addons", url)


def reset_registry_url():
    """Drop the per-user override so the launcher-configured URL is used."""
    catalog.reset_registry_url("addons")


def snapjaw_cache_repo_urls(addons_dir: str) -> set[str]:
    """Git origin URLs of snapjaw's persistent clones under
    ``{addons_dir}/.snapjaw_cache``.

    Read-only: parses each clone's `.git/config` for its
    `[remote "origin"]` URL — no git binary needed. Lets discovery treat
    a catalog repo as relevant when the user previously managed it with
    snapjaw (its folders are on disk under names the catalog doesn't
    list). Never raises.
    """
    urls: set[str] = set()
    try:
        cache_root = os.path.join(addons_dir or "", ".snapjaw_cache")
        if not os.path.isdir(cache_root):
            return urls
        entries = os.listdir(cache_root)
    except OSError:
        return urls
    for entry in entries:
        config_path = os.path.join(cache_root, entry, ".git", "config")
        try:
            with open(config_path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        in_origin = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_origin = stripped.lower() == '[remote "origin"]'
                continue
            if in_origin and stripped.lower().startswith("url ="):
                url = stripped[5:].strip()
                if url:
                    urls.add(url)
                break
    return urls


# Accepted `## Interface:` ranges per declared client version (mirrors
# snapjaw's expansion filter: vanilla 1.x <= 11200, wotlk 3.x 30000-30300).
INTERFACE_RANGE_BY_VERSION = {
    "1.12.1": (0, 11200),
    "2.4.3": (20000, 20400),
    "3.3.5a": (30000, 30300),
}


def interface_allowed(interface: int | None, client_version: str) -> bool:
    """Whether a `.toc` Interface value belongs to ``client_version``.

    A missing/unparseable Interface is accepted — the catalog is already
    per-version, so discovery must not drop addons whose `.toc` simply
    omits the line (several legacy modules do)."""
    if interface is None:
        return True
    bounds = INTERFACE_RANGE_BY_VERSION.get((client_version or "").strip())
    if bounds is None:
        return True
    low, high = bounds
    return low <= interface <= high


def _parse_interface_text(text: str) -> int | None:
    """The `## Interface:` value in `.toc` text, or None when absent.

    Only a leading digit run counts (``30300`` ok, ``abc``/empty absent).
    Requires whitespace after `##` so `##Interface:` never matches."""
    for raw_line in text.splitlines():
        line = raw_line.lstrip("\ufeff")
        if not line.startswith("##"):
            continue
        rest = line[2:]
        if rest[:1] not in (" ", "\t"):
            continue
        key, sep, value = rest.strip().partition(":")
        if not sep or key.strip() != "Interface":
            continue
        stripped = value.strip()
        digits = ""
        for char in stripped:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None
    return None


def discover_repo_addons(
    data: bytes, client_version: str | None = None
) -> list[tuple[str, str]]:
    """Every addon folder shipped inside a repo archive zip.

    Returns ``[(folder_name, stripped_prefix), ...]`` where
    ``stripped_prefix`` is the archive-root-relative directory ("/"
    separated, top-level "<repo>-<sha>/" already removed) holding
    ``<folder_name>.toc`` — ready for ``deploy.unpack_prefix``. A folder
    counts when it holds a ``<dirname>.toc`` (extension case-insensitive)
    whose Interface fits ``client_version`` (missing Interface accepted).
    A `.toc` sitting at the stripped root is a single-addon repo: its stem
    is the real folder name (often different from the catalog row, e.g.
    `ModernMapMarkers.toc` inside `ModernMapMarkers-WotLK`) and installs
    with an empty prefix (whole-tree unpack).

    Nested addons collapse to the shallowest claimant; once the root is
    claimed, deeper `.toc` files (bundled libs like LibStub) are skipped.
    Raises ``zipfile.BadZipFile`` on corrupt input — callers decide
    whether to fall back.
    """
    import zipfile
    from io import BytesIO

    version = client_version or ""

    def _toc_interface(zf, filename: str) -> int | None | bool:
        """Interface value of a zip member, False when not a .toc file."""
        stem, dot, ext = filename.rpartition(".")
        if not dot or ext.lower() != "toc":
            return False
        try:
            raw = zf.read(filename)
        except KeyError:
            return None
        try:
            text = raw.decode("utf-8-sig", errors="replace")
        except Exception:
            return None
        return _parse_interface_text(text)

    candidates: list[tuple[str, str, int | None]] = []
    root_stems: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = "/".join(
                p
                for p in info.filename.replace("\\", "/").split("/")[1:]
                if p not in ("", ".")
            )
            if not rel or ".." in rel.split("/"):
                continue
            parts = rel.split("/")
            if len(parts) == 1:
                interface = _toc_interface(zf, info.filename)
                if interface is False:
                    continue
                stem = parts[0].rpartition(".")[0]
                if interface_allowed(interface, version):
                    root_stems.append(stem)
                continue
            dirname = parts[-2]
            stem, dot, ext = parts[-1].rpartition(".")
            if not dot or ext.lower() != "toc":
                continue
            if stem != dirname and stem.lower() != dirname.lower():
                continue
            interface = _toc_interface(zf, info.filename)
            if interface is False:  # unreachable (checked above)
                continue
            if not interface_allowed(interface, version):
                continue
            candidates.append((dirname, "/".join(parts[:-1]), interface))
    if root_stems:
        # Single-addon repo (e.g. a lone UI overhaul): the whole tree
        # installs under each root stem; bundled nested libs are skipped.
        return [(stem, "") for stem in sorted(set(root_stems))]
    # Shallowest claimant wins; deeper nested duplicates are skipped.
    candidates.sort(key=lambda c: c[1].count("/"))
    claimed: list[str] = []
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, prefix, _interface in candidates:
        if name.lower() in seen:
            continue
        if any(prefix == c or prefix.startswith(c + "/") for c in claimed):
            continue
        claimed.append(prefix)
        seen.add(name.lower())
        found.append((name, prefix))
    return found


def discover_flat_addons(
    data: bytes, client_version: str | None = None
) -> list[tuple[str, str]]:
    """Like discover_repo_addons but for prebuilt zips whose members sit at
    the archive root (no "<repo>-<sha>/" wrapper). Same matching rules,
    same return shape."""
    import zipfile
    from io import BytesIO

    version = client_version or ""
    candidates: list[tuple[str, str, int | None]] = []
    root_stems: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = "/".join(
                p
                for p in info.filename.replace("\\", "/").split("/")
                if p not in ("", ".")
            )
            if not rel or ".." in rel.split("/"):
                continue
            parts = rel.split("/")
            if len(parts) == 1:
                interface = _toc_interface_flat(zf, info.filename)
                if interface is False:
                    continue
                stem = parts[0].rpartition(".")[0]
                if interface_allowed(interface, version):
                    root_stems.append(stem)
                continue
            dirname = parts[-2]
            stem, dot, ext = parts[-1].rpartition(".")
            if not dot or ext.lower() != "toc":
                continue
            if stem != dirname and stem.lower() != dirname.lower():
                continue
            interface = _toc_interface_flat(zf, info.filename)
            if interface is False:  # unreachable (checked above)
                continue
            if not interface_allowed(interface, version):
                continue
            candidates.append((dirname, "/".join(parts[:-1]), interface))
    if root_stems:
        return [(stem, "") for stem in sorted(set(root_stems))]
    candidates.sort(key=lambda c: c[1].count("/"))
    claimed: list[str] = []
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, prefix, _interface in candidates:
        if name.lower() in seen:
            continue
        if any(prefix == c or prefix.startswith(c + "/") for c in claimed):
            continue
        claimed.append(prefix)
        seen.add(name.lower())
        found.append((name, prefix))
    return found


def _toc_interface_flat(zf, filename: str) -> int | None | bool:
    """Interface value of a zip member, False when not a .toc file."""
    stem, dot, ext = filename.rpartition(".")
    if not dot or ext.lower() != "toc":
        return False
    try:
        raw = zf.read(filename)
    except KeyError:
        return None
    try:
        text = raw.decode("utf-8-sig", errors="replace")
    except Exception:
        return None
    return _parse_interface_text(text)


def read_toc_file(path: str) -> dict:
    """Parse '## Key: Value' metadata lines from a WoW addon .toc file."""
    toc = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return toc
    if content.startswith("\ufeff"):  # strip UTF-8 BOM
        content = content[1:]
    for line in content.splitlines():
        if not line.startswith("## "):
            continue
        key, sep, value = line[3:].partition(":")
        if sep:
            toc[key.strip()] = value.strip()
    return toc


def find_toc_for_folder(
    dir_path: str, folder_name: str
) -> tuple[str | None, str]:
    """Locate the loadable .toc for an installed addon folder.

    Returns ``(path, kind)`` with ``kind`` one of ``exact`` (folder-
    named ``<folder>.toc``, case-insensitive), ``variant`` (a flavor
    ``<folder>_<Suffix>.toc`` — the client probes these before the
    default name), ``single`` (exactly one mismatched ``*.toc``, e.g.
    ``WIM.toc`` inside ``WIM (WoW Instant Messenger)`` — parseable
    but unloadable in-game until the folder is renamed),
    ``missing`` (no ``*.toc`` at all) or ``ambiguous`` (several
    mismatched candidates). One ``os.scandir``, never raises."""
    try:
        with os.scandir(dir_path) as it:
            tocs = [
                e.name
                for e in it
                if e.is_file(follow_symlinks=True)
                and e.name.lower().endswith(".toc")
            ]
    except OSError:
        return None, "missing"
    if not tocs:
        return None, "missing"
    norm = folder_name.casefold()
    for leaf in sorted(tocs):
        if leaf[:-4].casefold() == norm:
            return os.path.join(dir_path, leaf), "exact"
    for leaf in sorted(tocs):
        stem = leaf[:-4]
        if stem.casefold().startswith(norm + "_"):
            return os.path.join(dir_path, leaf), "variant"
    if len(tocs) == 1:
        return os.path.join(dir_path, tocs[0]), "single"
    return None, "ambiguous"


# In-memory TOC cache keyed by absolute .toc path: {path: (mtime_ns,
# size, toc)}. Parsing is cheap but rescans (tab switches, retries,
# settings reloads) re-read every file; unchanged files are served from
# the cache. Process-local only — no persistence, no staleness across
# restarts. Bounded with best-effort eviction; never raises.
_TOC_CACHE: dict = {}
_TOC_CACHE_MAX = 2000


def read_toc_file_cached(path: str) -> dict:
    """Cached variant of read_toc_file: reuse the parsed dict when the
    file's mtime/size are unchanged, re-parse otherwise."""
    try:
        st = os.stat(path)
    except OSError:
        _TOC_CACHE.pop(path, None)
        return {}
    key = (st.st_mtime_ns, st.st_size)
    try:
        hit = _TOC_CACHE.get(path)
    except Exception:
        hit = None
    if hit is not None and hit[0] == key:
        try:
            return dict(hit[1])
        except Exception:
            pass
    toc = read_toc_file(path)
    try:
        if len(_TOC_CACHE) >= _TOC_CACHE_MAX and path not in _TOC_CACHE:
            _TOC_CACHE.pop(next(iter(_TOC_CACHE)), None)
        _TOC_CACHE[path] = (key, dict(toc))
    except Exception:
        pass
    return toc


def prune_toc_cache(addons_dir: str = "") -> None:
    """Drop cached TOC entries that no longer belong to ``addons_dir``."""
    try:
        entries = list(_TOC_CACHE)
    except Exception:
        return
    if not addons_dir:
        _TOC_CACHE.clear()
        return
    prefix = os.path.join(addons_dir, "") if addons_dir else ""
    for path in entries:
        if not path.startswith(prefix) or not os.path.exists(path):
            _TOC_CACHE.pop(path, None)


def addon_remote_sha(
    git_url: str, branch=None, ref=None, force=False, raise_errors=False
) -> str | None:
    """Latest commit sha of a repo's branch (or pinned ref), cached in the
    config file so repeated verifies don't burn API quota. Delegates to the
    shared ``git_archive`` backend (API path with a ``git ls-remote``
    fallback; the allowlist gate lives there)."""
    return _GIT_BACKEND.remote_sha(
        git_url,
        branch=branch,
        ref=ref,
        force=force,
        raise_errors=raise_errors,
    )


def addon_cached_sha(git_url: str, branch=None, ref=None):
    """Cached remote sha regardless of age — never touches the network."""
    return _GIT_BACKEND.cached_sha(git_url, branch=branch, ref=ref)


def addon_repo_names(
    git_url: str, branch=None, ref=None, force=False
) -> list[str]:
    """Addon folder names shipped by a repo (multi-addon repos included).

    Resolves the current remote sha (cheap: API/`git ls-remote` with the
    hourly ``addon_sha_cache``) and returns the cached discovery while the
    sha is unchanged — the archive itself is fetched only when the repo
    actually moved. Offline (or a failed fetch) serves the last cached
    names. Never raises: [] means "unknown, keep single-folder behavior".
    """
    if not git_url or not is_allowed_git_url(git_url):
        return []
    key = f"{git_url}#{ref or branch or ''}"
    try:
        remote = addon_remote_sha(git_url, branch, ref, force=force)
    except Exception:
        remote = None
    try:
        cache = load_config().get("addon_repo_cache", {}) or {}
    except Exception:
        cache = {}
    entry = cache.get(key) if isinstance(cache, dict) else None
    cached_names = (
        [n for n in entry.get("names", []) if isinstance(n, str)]
        if isinstance(entry, dict)
        else []
    )
    if remote and entry and entry.get("sha") == remote and cached_names:
        return cached_names
    if not remote:
        return cached_names
    try:
        data = _GIT_BACKEND.fetch_archive(git_url, remote)
        names = [
            name
            for name, _prefix in discover_repo_addons(
                data, launcher.client_version()
            )
        ]
    except Exception as e:
        log(f"  Could not list addons in {git_url} ({e})", "dim")
        return cached_names
    try:
        update_config(
            lambda c, k=key, s=remote, n=names: c.setdefault(
                "addon_repo_cache", {}
            ).__setitem__(k, {"sha": s, "names": n, "timestamp": time.time()})
        )
    except Exception:
        pass
    return names


def install_addon_direct(
    client_dir: str,
    folder: str,
    url: str,
    wanted: list[str] | None = None,
) -> list[str]:
    """Download a prebuilt addon archive and unpack it into
    Interface/AddOns, like install_addon_files but from a plain HTTPS URL
    instead of a git-host archive. Same multi-addon discovery applies:
    every discovered ``<folder>/<folder>.toc`` installs into its own
    directory; a bare tree unpacks into ``folder``.

    Prebuilt zips have no "<repo>-<sha>/" wrapper directory — discovery
    and unpack fall back to the unstripped layout when stripping yields
    nothing. Raises RuntimeError on non-HTTPS URLs, corrupt input, or
    when the archive ships no addons.
    """
    log(f"  Downloading {folder} (direct)…")
    clean = https_url(url)
    if not clean:
        raise RuntimeError("Addon download URL must use https")
    req = urllib.request.Request(clean, headers={"User-Agent": UA})
    with secure_urlopen(req, timeout=120) as r:
        data = read_capped(r, _ARCHIVE_MAX_BYTES)
    log(f"  Downloaded {len(data)} bytes for {folder}")
    return _install_archive_bytes(
        client_dir, folder, data, wanted=wanted, git_style=False
    )


def _install_archive_bytes(
    client_dir: str,
    folder: str,
    data: bytes,
    wanted: list[str] | None = None,
    git_style: bool = True,
) -> list[str]:
    """Unpack archive bytes into Interface/AddOns with multi-addon
    discovery. ``git_style`` archives wrap everything under one top-level
    directory; prebuilt direct zips usually don't — fall back to the
    unstripped layout so both install.

    Two repo layouts are handled:

    * contains-dirs — the repo ships ``<Dir>/<Dir>.toc`` subdirs; each
      installs into its own ``Interface/AddOns/<Dir>`` (subdir mapping,
      never nested inside the catalog folder).
    * root-is-addon — the repo root IS the addon (``<Stem>.toc`` at the
      archive root, e.g. ``ACP.toc`` for catalog folder ``Addon
      Control Panel``); the whole tree is unpacked into
      ``Interface/AddOns/<folder>`` (rename-on-clone). The toc stem
      need not match the folder — the catalog's ``folder`` is the
      explicit install slot.

    Every requested folder is verified after unpack: the install
    raises unless ``<folder>/<folder>.toc`` (case-insensitive) is on
    disk."""
    flat = False
    try:
        discovered = discover_repo_addons(data, launcher.client_version())
    except Exception:
        discovered = []
    if not discovered and not git_style:
        try:
            discovered = discover_flat_addons(data, launcher.client_version())
        except Exception:
            discovered = []
        flat = bool(discovered)
    if not discovered:
        _sources_deploy.unpack_folder(
            data, os.path.join(addons_path(client_dir), folder)
        )
        log(f"  Installed addon {folder}")
        return [folder]
    by_name = {name: prefix for name, prefix in discovered}
    if wanted:
        # Explicit multi-folder request (controller group install):
        # install each requested folder plus any co-shipped siblings
        # from the same archive (DragonUI ⇒ DragonUI_Options) — a
        # repo installs as a unit. A request for the single
        # root-is-addon tree (empty prefix) renames the whole tree
        # into the catalog folder; a pack-level row naming no
        # discovered folder still installs the discovered set.
        names = [n for n in wanted if n in by_name]
        if not names and folder in (wanted or []):
            root_stems = [n for n, p in discovered if p == ""]
            if root_stems and folder not in by_name:
                names = [folder]
                by_name[folder] = by_name[root_stems[0]]
        if not names:
            names = [name for name, _prefix in discovered]
        for name, _prefix in discovered:
            if name not in names:
                names.append(name)
    else:
        # Single entry install: the catalog folder is the slot.
        # A contains-dirs repo that ships the folder installs the
        # folder plus its co-shipped siblings; a pack-level row (or
        # a repo that moved) installs everything discovered; a
        # root-is-addon repo renames the whole tree into the catalog
        # folder even when the toc stem differs (ACP vs
        # addon-control-panel).
        if folder in by_name:
            names = [name for name, _prefix in discovered]
        elif any(p == "" for _n, p in discovered):
            first_root = next(n for n, p in discovered if p == "")
            names = [folder]
            by_name[folder] = by_name[first_root]
        else:
            names = [name for name, _prefix in discovered]
    installed_names = []
    for name in names:
        if flat:
            _sources_deploy.unpack_flat_prefix(
                data,
                by_name[name],
                os.path.join(addons_path(client_dir), name),
            )
        else:
            _sources_deploy.unpack_prefix(
                data,
                by_name[name],
                os.path.join(addons_path(client_dir), name),
            )
        log(f"  Installed addon {name}")
        installed_names.append(name)
    if wanted:
        # Group install: verify only folders actually shipped by the
        # repo (requested ∪ co-shipped siblings); a pack row naming
        # a not-discovered folder still installs the discovered set.
        for name in installed_names:
            if name not in (wanted or []) and name not in by_name:
                continue
            toc_path, _kind = find_toc_for_folder(
                os.path.join(addons_path(client_dir), name), name
            )
            if toc_path is None:
                raise RuntimeError(
                    f"Installed {name} but no {name}.toc found — "
                    "the repo layout is not supported"
                )
        return installed_names
    if folder in by_name or any(p == "" for _n, p in discovered):
        # Already renamed above into `folder` and verified there.
        return installed_names
    # contains-dirs repo that does not ship the folder, and no pack
    # fallback requested: the catalog row names the wrong install
    # slot — fail loudly instead of installing strangers.
    raise RuntimeError(
        f"Installed {folder} but no {folder}.toc found — the repo "
        "layout is not supported"
    )


def install_addon_files(
    client_dir: str,
    folder: str,
    git_url: str,
    sha: str,
    wanted: list[str] | None = None,
) -> list[str]:
    """Download the repo archive at `sha` and unpack it into
    Interface/AddOns, atomically replacing existing copies. Returns the
    installed folder names.

    One Git repo often ships several addons (e.g. AtlasLoot's seven
    modules): every discovered `<folder>/<folder>.toc` is installed into
    its own directory. When ``wanted`` names are all discovered, only
    those are installed; otherwise (a pack-level row) every discovered
    addon is installed — callers pass the sibling folder list as
    ``wanted``. A repo with its `.toc` at the top level is a single
    addon — the whole tree unpacks into ``folder`` (rename-on-clone)
    even when the toc stem differs (e.g. catalog ``Addon Control
    Panel`` vs repo ``ACP.toc``). The catalog folder is the install
    slot: a repo that neither ships the folder nor carries a root toc
    raises instead of installing strangers.
    """
    log(f"  Downloading {folder} @ {sha[:10]}…")
    if not git_url or not is_allowed_git_url(git_url):
        raise RuntimeError("Addon URL is not from an allowed git host")
    data = _GIT_BACKEND.fetch_archive(git_url, sha)
    return _install_archive_bytes(
        client_dir, folder, data, wanted=wanted, git_style=True
    )


# ── tracked-install index ─────────────────────────────────────────────────

INDEX_SCHEMA_VERSION = 1


def snapshot_toc_dir(addons_dir: str, folder: str) -> dict:
    """Snapshot ``{folder: {mtime_ns, size, title}}`` entries for the .toc
    of one installed addon folder (the whole-dir .toc, not per-file
    hashes). ``{}`` when the folder has no loadable .toc. Never raises —
    staleness is detected by comparing this snapshot on the next scan."""
    snap: dict = {}
    if not addons_dir or not folder:
        return snap
    dir_path = os.path.join(addons_dir, folder)
    try:
        toc_path, _kind = find_toc_for_folder(dir_path, folder)
    except Exception:
        return snap
    if not toc_path:
        return snap
    try:
        st = os.stat(toc_path)
    except OSError:
        return snap
    try:
        title = read_toc_file_cached(toc_path).get("Title")
    except Exception:
        title = None
    snap[folder] = {
        "mtime_ns": st.st_mtime_ns,
        "size": st.st_size,
        "title": title if isinstance(title, str) else "",
    }
    return snap


def snapshot_toc_folders(addons_dir: str, folders) -> dict:
    """Snapshot the .toc of every folder in ``folders`` into one dict."""
    snap: dict = {}
    for folder in folders or []:
        if not isinstance(folder, str) or not folder:
            continue
        try:
            snap.update(snapshot_toc_dir(addons_dir, folder))
        except Exception:
            continue
    return snap


def toc_snapshot_changed(
    addons_dir: str, folder: str, snapshot: dict | None
) -> bool:
    """Whether an installed folder's .toc differs from its index snapshot
    (mtime/size changed, snapshot missing, or toc unreadable). Never
    raises — on doubt reports changed so the scan re-reads."""
    if not isinstance(snapshot, dict):
        return True
    try:
        toc_path, _kind = find_toc_for_folder(
            os.path.join(addons_dir, folder), folder
        )
    except Exception:
        return True
    if not toc_path:
        return True
    try:
        st = os.stat(toc_path)
    except OSError:
        return True
    return (
        snapshot.get("mtime_ns") != st.st_mtime_ns
        or snapshot.get("size") != st.st_size
    )


def unit_for_folder(index: dict, folder: str) -> tuple[str | None, dict]:
    """The ``(entry-id, unit)`` owning ``folder`` in the tracked-install
    index, or ``(None, {})``. The single lookup the whole scan uses:
    catalog gives the id, the index maps id → folders. Never raises."""
    try:
        units = index.get("units")
        if isinstance(units, dict):
            for eid, unit in units.items():
                if not isinstance(unit, dict):
                    continue
                folders = unit.get("folders")
                if isinstance(folders, list) and folder in folders:
                    return eid, unit
    except Exception:
        pass
    return None, {}


def load_addons_index() -> dict:
    """Load the tracked-install index (``{"units", "orphans"}``); the
    corrupt-tolerant ``launcher`` loader never raises."""
    return launcher.load_local_addons_index()


def store_addons_index(index: dict) -> None:
    """Persist the tracked-install index. Best effort — a failed write
    only means the next scan rebuilds it."""
    try:
        launcher.store_local_addons_index(index)
    except Exception as e:
        log(f"  Could not save addon install index: {e}", "dim")
