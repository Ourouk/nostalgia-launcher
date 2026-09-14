"""Addons engine: catalog, Git commit resolution and archive installation.

Addons are installed directly from Git hosts (GitHub, GitLab, Gitea,
Codeberg, plus any community host a distribution lists in its launcher
config) by downloading the repo archive pinned to a commit SHA — no git
client needed. Commit resolution and archive fetching are delegated to the
shared ``git_archive`` source backend (`services/sources`); this module
keeps the addon-specific catalog machinery, the folder unpacking call and
the pfUI "Default" profile patch.

There is no hardcoded addon list — the ADDONS tab comes from the addon
catalog URLs (launcher-configured or user-set), the local addons repo
(`local_addons_repo.json`: server-imported entries written by the config
import plus user-added customs), optional entries embedded in the launcher
config, and the per-user custom file, so a distribution decides what it
ships.
"""

import json
import os
import time

from ..core import config_store as _config_store
from ..core import launcher
from ..core.config_store import load_config, update_config
from ..core.log_sink import log
from ..core.security_http import secure_urlopen
from . import catalog
from .sources import deploy as _sources_deploy
from .sources.git_archive import (
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
    """Validate a custom addon entry and enforce the git-host allowlist."""
    cleaned = catalog.validate_addon(entry)
    if cleaned is None:
        return None
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


def catalog_last_updated() -> float | None:
    """The newest per-URL catalog fetch timestamp (epoch), or None when no
    catalog was ever fetched. Network-free."""
    cache = _config_store.load_config().get("addons_catalog_cache", {}) or {}
    stamps: list[float] = []
    for e in cache.values():
        if isinstance(e, dict):
            ts = e.get("timestamp")
            if isinstance(ts, (int, float)):
                stamps.append(float(ts))
    return max(stamps) if stamps else None


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


def addons_registry_default_urls() -> list[str]:
    """The launcher-configured addon catalog URLs, in override order ('' list
    when not configured). Kept for tests — explicit only."""

    return launcher.addons_registry_urls()


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
        # Single-addon repo (e.g. pfUI, ModernMapMarkers): the whole tree
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
    those are installed; otherwise (a catalog row naming the pack rather
    than a folder) every discovered addon is installed. A repo with its
    `.toc` at the top level is a single addon — legacy whole-tree unpack
    into ``folder`` applies.
    """
    log(f"  Downloading {folder} @ {sha[:10]}…")
    data = _GIT_BACKEND.fetch_archive(git_url, sha)
    try:
        discovered = discover_repo_addons(data, launcher.client_version())
    except Exception:
        discovered = []
    if not discovered:
        _sources_deploy.unpack_folder(
            data, os.path.join(addons_path(client_dir), folder)
        )
        log(f"  Installed addon {folder}")
        return [folder]
    by_name = {name: prefix for name, prefix in discovered}
    if wanted:
        selected = [n for n in wanted if n in by_name]
        names = selected or [name for name, _prefix in discovered]
    else:
        names = (
            [folder]
            if folder in by_name
            else [name for name, _prefix in discovered]
        )
    installed = []
    for name in names:
        _sources_deploy.unpack_prefix(
            data, by_name[name], os.path.join(addons_path(client_dir), name)
        )
        log(f"  Installed addon {name}")
        installed.append(name)
    return installed


# ── pfUI "Default" profile patch ─────────────────────────────────────────────
# pfUI ships a set of built-in design profiles. After every pfUI install/update
# we add a curated "Default" profile and make it the firstrun default. Because
# an update overwrites pfUI's files, the patch is re-applied each time and is
# idempotent (marked blocks are replaced, not duplicated).

# The curated profile (JSON captured from a configured pfUI, profile renamed to
# "Default"). Loaded as a Python dict and emitted as a Lua table at patch time.
PFUI_DEFAULT_PROFILE = json.loads(r"""
{"appearance":{"border":{"default":"-1"},"castbar":{"castbarcolor":"1,0.796,0.251,0.8"},"cd":{"debuffs":"1","font":"Interface\\AddOns\\pfUI\\fonts\\Myriad-Pro.ttf","milliseconds":"0"},"infight":{"health":"0"},"minimap":{"arrowscale":"2"}},"buffs":{"hidelist":"","showoverflow":"1","showspillover":"1"},"castbar":{"focus":{"showicon":"1","showtimer":"0"},"player":{"hide_blizz":"0","hide_pfui":"1","showtimer":"0"},"target":{"showicon":"1","showtimer":"0"}},"character":{"inventory":{"durability":"0"},"reputation":{"repRequired":"0"}},"disabled":{"actionbar":"1","addonbuttons":"0","addoncompat":"0","addons":"0","afkcam":"0","autoshift":"0","autovendor":"0","bags":"1","bgscore":"0","bubbles":"1","buff":"1","buffwatch":"0","castbar":"0","chat":"1","chatcopy":"0","combopoints":"0","cooldown":"0","custom":"0","easteregg":"0","energytick":"0","eqcompare":"0","equipmentmanager":"0","farmmode":"0","feigndeath":"0","firstrun":"0","focus":"0","gm":"0","group":"0","gryphons":"0","hdgraphic":"0","hoverbind":"0","hunterbar":"0","infight":"0","innervatecall":"0","itemclick":"0","itemcount":"1","loot":"1","macrotweak":"0","map":"0","mapcolors":"0","mapreveal":"0","marktracking":"0","minimap":"0","mirrortimers":"0","mouseover":"0","nameplates":"0","nampower":"0","panel":"0","pet":"0","pettarget":"0","pixelperfect":"0","player":"0","questitem":"0","raid":"0","roll":"1","screenshot":"0","sellvalue":"0","share":"0","skin":"0","skin_Auctionhouse":"0","skin_Barbershop":"0","skin_Battlefield":"0","skin_Battlefield Minimap":"0","skin_Battlefield Score":"0","skin_Books":"0","skin_Character":"0","skin_Coin Pickup":"0","skin_Color Picker":"0","skin_Dress Up Frame":"0","skin_Everlook Broadcasting":"0","skin_Flightmaster":"1","skin_Friends":"1","skin_GM Survey":"0","skin_Game Menu":"0","skin_Gossip and Quest":"1","skin_Guild Registrar":"0","skin_Guild Tabard":"0","skin_Help":"0","skin_Inspect":"0","skin_KeyBindings":"0","skin_Macro":"0","skin_Mailbox":"1","skin_Merchant":"1","skin_Opacity":"0","skin_Outline":"0","skin_Player":"1","skin_Quest":"1","skin_Quest Tracker":"0","skin_Reputation":"1","skin_Social":"0","skin_TradeSkill":"1","skin_Trainer":"1","skin_Tutorials":"0","skin_Unitframe":"1","timerbar":"0","tooltip":"0","tracker":"0","unitframes":"0"},"equipment":{"durability":"0"},"nameplates":{"clickthrough":"0","hidelist":"","showonlyname":"0"},"panels":{"fpsloc":"Right","hidelist":"","lootannounce":"0","mouseover":"0"},"reputation":{"repRequired":"0"},"skins":{"dark":"1","font":"Interface\\AddOns\\pfUI\\fonts\\Myriad-Pro.ttf","fontscale":"1"},"tooltips":{"hideincombat":"0","hidelist":"","mousefollow":"0"},"unitframes":{"clickthrough":"0","hidelist":"","petbars":"1","showstagger":"0"}}
""")


def _lua_value(v, indent: int = 0) -> str:
    """Serialize a JSON-derived value to a pfUI-style Lua literal."""
    if isinstance(v, dict):
        pad, cpad = " " * (indent + 2), " " * indent
        items = "".join(
            f'{pad}["{k}"] = {_lua_value(val, indent + 2)},\n'
            for k, val in v.items()
        )
        return "{\n" + items + cpad + "}"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


_PFUI_MARK_BEGIN = "-- OCTO_UPDATER_DEFAULT_PROFILE_BEGIN"
_PFUI_MARK_END = "-- OCTO_UPDATER_DEFAULT_PROFILE_END"
_PFUI_CHAT_BEGIN = "-- OCTO_UPDATER_CHAT_SKIP_BEGIN"
_PFUI_CHAT_END = "-- OCTO_UPDATER_CHAT_SKIP_END"

# Strips any Nostalgia Launcher injected block, regardless of which marker pair.
_PFUI_STRIP_RE = (
    r"[ \t]*-- OCTO_UPDATER_[A-Z_]+?_BEGIN.*?-- OCTO_UPDATER_[A-Z_]+?_END\n?"
)


def patch_pfui_default_profile(client_dir: str):
    """Add the curated 'Default' profile to a freshly installed/updated pfUI
    and make it the firstrun default. Idempotent; degrades gracefully if
    pfUI's file layout has changed."""
    import re

    base = os.path.join(addons_path(client_dir), "pfUI")
    profiles_lua = os.path.join(base, "env", "profiles.lua")
    firstrun_lua = os.path.join(base, "modules", "firstrun.lua")
    if not os.path.exists(profiles_lua):
        return

    # 1) profiles.lua — append (or replace) a marked block defining Default.
    block = (
        f"{_PFUI_MARK_BEGIN}\n"
        f"local octo_default = {_lua_value(PFUI_DEFAULT_PROFILE)}\n"
        f'pfUI_profiles["Default"] = octo_default\n'
        f"{_PFUI_MARK_END}\n"
    )
    try:
        with open(profiles_lua, encoding="utf-8", errors="replace") as f:
            txt = f.read()
        txt = re.sub(
            re.escape(_PFUI_MARK_BEGIN)
            + r".*?"
            + re.escape(_PFUI_MARK_END)
            + r"\n?",
            "",
            txt,
            flags=re.S,
        )
        with open(profiles_lua, "w", encoding="utf-8") as f:
            f.write(txt.rstrip() + "\n\n" + block)
        log("  pfUI: 'Default' profile installed.")
    except OSError as e:
        log(f"  pfUI: could not patch profiles.lua ({e})")
        return

    # 2) pfUI.lua — use 'Default' (not 'Modern') as the fresh-install config,
    #    so the very first login already lands in the Default profile.
    pfui_lua = os.path.join(base, "pfUI.lua")
    if os.path.exists(pfui_lua):
        try:
            with open(pfui_lua, encoding="utf-8", errors="replace") as f:
                pf = f.read()
            old = 'CopyTable(pfUI_profiles["Modern"]) or {}'
            if old in pf:
                pf = pf.replace(
                    old, 'CopyTable(pfUI_profiles["Default"]) or {}', 1
                )
                with open(pfui_lua, "w", encoding="utf-8") as f:
                    f.write(pf)
                log("  pfUI: 'Default' set as the fresh-install profile.")
        except OSError as e:
            log(f"  pfUI: could not patch pfUI.lua ({e})")

    # 3) firstrun.lua — add a 'Default' button, make it the fallback profile,
    #    and skip the chat wizard steps whenever the chat module is disabled.
    if not os.path.exists(firstrun_lua):
        return
    try:
        with open(firstrun_lua, encoding="utf-8", errors="replace") as f:
            fr = f.read()

        # Remove any previous injections (idempotent re-apply after updates).
        fr = re.sub(_PFUI_STRIP_RE, "", fr, flags=re.S)

        # When the chat module is disabled (e.g. the "Default" profile), the
        # chat firstrun steps can't apply anything, so pre-mark them done to
        # keep them from showing. Injected right after the step table is made.
        chat_skip = (
            f"  {_PFUI_CHAT_BEGIN}\n"
            "  if pfUI_config and pfUI_config.disabled"
            ' and pfUI_config.disabled.chat == "1" then\n'
            "    pfUI_init = pfUI_init or {}\n"
            '    pfUI_init["chat_right"] = true\n'
            '    pfUI_init["chat_position"] = true\n'
            '    pfUI_init["chat_channels"] = true\n'
            "  end\n"
            f"  {_PFUI_CHAT_END}\n"
        )
        chat_anchor = "  pfUI.firstrun.steps = {}\n"
        if chat_anchor in fr:
            fr = fr.replace(chat_anchor, chat_anchor + chat_skip, 1)

        # Insert a Default button just before the built-in "Modern" button.
        button = (
            f"    {_PFUI_MARK_BEGIN}\n"
            '    f.Default = CreateFrame("Button", nil, f, "UIPanelButtonTemplate")\n'
            "    f.Default:SetWidth(250)\n"
            "    f.Default:SetHeight(20)\n"
            '    f.Default:SetPoint("BOTTOM", 0, 125)\n'
            "    f.Default:SetTextColor(1,1,1)\n"
            '    f.Default:SetText("Default (recommended)")\n'
            '    f.Default:SetScript("OnClick", function()\n'
            '      _G["pfUI_config"] = CopyTable(pfUI_profiles["Default"])\n'
            '      pfUI_init.selected_profile = "Default"\n'
            "      pfUI:LoadConfig()\n"
            "      ReloadUI()\n"
            "    end)\n"
            "    SkinButton(f.Default)\n"
            f"    {_PFUI_MARK_END}\n\n"
        )
        anchor = '    f.Modern = CreateFrame("Button"'
        if anchor in fr:
            fr = fr.replace(anchor, button + anchor, 1)

        # Make Default the profile used when the user doesn't pick one.
        fr = fr.replace(
            'pfUI_init.selected_profile or "Modern"',
            'pfUI_init.selected_profile or "Default"',
        )

        with open(firstrun_lua, "w", encoding="utf-8") as f:
            f.write(fr)
        log("  pfUI: 'Default' added to the firstrun profile picker.")
    except OSError as e:
        log(f"  pfUI: could not patch firstrun.lua ({e})")
