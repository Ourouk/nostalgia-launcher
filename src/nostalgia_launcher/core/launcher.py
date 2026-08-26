"""Launcher configuration — the distribution's server, endpoints and mirrors.

Every endpoint the app talks to (client updates, news, mod/addon catalogs,
realm, downloads) comes from a single JSON file instead of hardcoded values,
so a distribution only needs to ship one file to point the launcher at its
own server.

The file is `nostalgia_launcher.json`, discovered next to the executable
(frozen) or in the repo root (running from source), or passed explicitly via
``--launcher-config``. A configuration chosen through the first-launch wizard
is persisted into the per-user config directory and takes precedence over
auto-discovery on later runs. Only ``server.base_url`` is required; every
other URL is derived from it unless overridden:

    {
      "server": {
        "name": "My Server",
        "base_url": "https://server.example",
        "realm": "server.example",
        "manifest_url": "https://server.example/api/file/latest/manifest.json",
        "client_url": "https://server.example/client/latest",
        "torrent_url": "https://server.example/client/latest/client.torrent",
        "torrent_magnet": "magnet:?xt=urn:btih:EXAMPLEINFOHASH&dn=client",
        "news_url": "https://server.example/news",
        "featured_news_url": "https://server.example/news/featured",
        "mods_registry_url": "https://server.example/api/mods.json",
        "addons_registry_url": "https://server.example/api/addons.json",
        "addons_registry_urls": [
          "https://server.example/api/addons.json",
          "https://server.example/addons-overrides.json"
        ]
      },
      "mods": [],
      "addons": [],
      "assets": [],
      "discord_url": "https://discord.gg/example",
      "theme": {
        "C_GOLD": "#d4a02f",
        "logo": "https://server.example/logo.png"
      },
      "mirrors": [
        {
          "name": "Backup",
          "base_url": "https://mirror.example",
          "manifest_url": "https://mirror.example/api/file/latest/manifest.json",
          "client_url": "https://dl.mirror.example/client/latest",
          "torrent_url": "https://dl.mirror.example/client/latest/client.torrent"
        }
      ]
    }

The manifest and client files are fetched from the configured endpoints; a
mirror's ``client_url`` may point at a separate CDN host, and mirrors are
optional (the server is the fallback).

The optional ``torrent_url`` (on the server or a mirror) advertises a
BitTorrent snapshot of the client files for the update backend: the launcher
fetches the ``.torrent`` over HTTPS and bulk-downloads the stale files via
libtorrent when it is available, falling back to per-file HTTP downloads
otherwise. A mirror's ``torrent_url`` takes precedence over the server's.

The optional server-only ``torrent_magnet`` is an alternative to
``torrent_url``: a ``magnet:?xt=urn:btih:…`` URI whose swarm serves the same
client snapshot. A torrent has one swarm, so mirrors — an HTTP-download
concept — do not apply: the field is accepted on the server object only.
libtorrent resolves the metadata from the swarm once; peers cannot forge it
because it must hash to the magnet's info-hash. When both are configured,
the HTTPS ``.torrent`` wins.

The optional ``theme`` object overrides the app's color theme per server:
color slots named like ``C_GOLD`` (each a ``#rrggbb`` hex value) plus an
optional ``logo`` URL shown as the header wordmark (see `core/themes`). It
is cosmetic and never validated strictly — a malformed theme falls back to
the default palette instead of failing startup.

The optional top-level ``mods`` list embeds mod catalog entries directly in
the config (same shape the remote mod catalog uses). Entries are kept raw
here and sanitized by `services/mods` with the same rules as remote entries;
embedded ids override the remote catalog, and the per-user custom file
overrides both. ``addons`` works the same way (sanitized by `services/addons`,
git-host allowlist included).

The optional top-level ``assets`` list embeds asset entries (single-file
server content patches such as MPQs) the same way, sanitized by
`services/assets`; ``server.assets_registry_url`` optionally points at a
remote assets catalog. Every embedded asset download URL and the registry
URL join the security allowlist.

**Import-time split**: `persist()` / `persist_text()` move the three content
sections out of the document before it is stored. Each lands in its own
local repo file (`local_<kind>_repo.json` in the config dir, shaped
``{"server": […], "custom": […]}`` — "server" mirrors the imported doc,
"custom" holds user-added entries that survive re-imports), and the
persisted config keeps only server/mirrors/theme. The ``validate_*``
helpers stay side-effect-free; a repo write failure aborts the whole
import.

A missing or invalid configuration is a hard startup error: the app has
nothing to point at. This module is network-free; `core/security_http` builds
its download allowlist from the configured hosts.
"""

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from .filesystem import atomic_write_text
from .log_sink import log
from .platform_support import config_dir, is_macos

LAUNCHER_FILE = "nostalgia_launcher.json"

_LOCK = threading.Lock()
_config: "LauncherConfig | None" = None
_path: str = ""
_error: str = ""
# Active-profile override for user_config_path() (see set_profile_
# launcher_path); empty means the legacy per-user default file.
_profile_launcher_path: str = ""


@dataclass
class Mirror:
    """One configured download mirror."""

    name: str
    base_url: str
    manifest_url: str
    client_url: str
    torrent_url: str | None = None


@dataclass
class LauncherConfig:
    """Validated launcher configuration with every endpoint resolved."""

    server_name: str
    server_url: str
    manifest_url: str
    client_url: str
    news_url: str
    featured_news_url: str
    mods_registry_url: str
    addons_registry_url: str
    realm: str
    addons_registry_urls: list[str] = field(default_factory=list)
    embedded_mods: list[dict] = field(default_factory=list)
    embedded_addons: list[dict] = field(default_factory=list)
    mods_registry_url_explicit: bool = False
    embedded_assets: list[dict] = field(default_factory=list)
    assets_registry_url: str = ""
    mirrors: list["Mirror"] = field(default_factory=list)
    discord_url: str | None = None
    theme: dict | None = None
    torrent_url: str | None = None
    torrent_magnet: str | None = None
    addon_git_hosts: list[str] = field(default_factory=list)
    torrent_root_marker: str = "WoW.exe"

    @property
    def configured(self) -> bool:
        return bool(self.server_url)

    def download_hosts(self) -> set[str]:
        """Every host the configured server and mirrors may serve from —
        the base URLs plus any custom manifest/client endpoints (e.g. a
        separate CDN host)."""
        hosts: set[str] = set()
        for url in self._all_urls():
            host = urlsplit(url).hostname
            if host:
                hosts.add(host)
        return hosts

    def addon_git_host_set(self) -> set[str]:
        """The base git hosts plus any community-supplied extra hosts
        (validated hostnames only)."""
        hosts: set[str] = set(ADDON_GIT_HOSTS)
        for h in self.addon_git_hosts:
            if isinstance(h, str) and h:
                hosts.add(h.lower())
        return hosts

    def has_torrent(self) -> bool:
        """Whether any configured source (server or mirror) advertises a
        ``torrent_url``, or the server a ``torrent_magnet``. Static — no
        network probing."""
        if self.torrent_url or self.torrent_magnet:
            return True
        return any(m.torrent_url for m in self.mirrors)

    def all_bases(self) -> list[str]:
        """The server followed by every mirror's base URL."""
        return [self.server_url] + [m.base_url for m in self.mirrors]

    def _all_urls(self) -> list[str]:
        """Every endpoint URL the app may contact: base URLs plus the
        resolved manifest/client endpoints of the server and mirrors, plus
        every asset download URL (embedded entries and the asset registry
        catalog) so the security allowlist covers them."""
        urls: list[str] = list(self.all_bases())
        urls += [self.manifest_url, self.client_url]
        if self.torrent_url:
            urls.append(self.torrent_url)
        if self.assets_registry_url:
            urls.append(self.assets_registry_url)
        for a in self.embedded_assets:
            if isinstance(a, dict) and isinstance(a.get("url"), str):
                urls.append(a["url"])
        for m in self.mirrors:
            urls += [m.manifest_url, m.client_url]
            if m.torrent_url:
                urls.append(m.torrent_url)
        return urls


# Base set of git hosts allowlisted for addon installations. A configuration
# may extend this with community-run hosts via `server.addon_git_hosts`
# (validated hostnames only); see `LauncherConfig.addon_git_host_set()`.
ADDON_GIT_HOSTS = (
    "github.com",
    "raw.githubusercontent.com",
    "gitlab.com",
    "gitea.com",
    "codeberg.org",
)


def _parse_git_hosts(value) -> list[str]:
    """Validate `server.addon_git_hosts`: a list of plain hostnames (no
    scheme, no path, no port). Anything else is dropped. Returns a list of
    lowercased hosts."""
    if not isinstance(value, list):
        return []
    out = []
    for h in value:
        if not isinstance(h, str):
            continue
        h = h.strip().lower()
        if not h or any(ch in h for ch in "/\\:") or ".." in h:
            continue
        if all(c.isalnum() or c in ".-" for c in h):
            out.append(h)
    return out


def _parse_root_marker(value) -> str:
    """Validate `server.torrent_root_marker` — the filename used to detect
    the torrent root. Must be a single unsafe-free name. Defaults to
    `WoW.exe` for Vanilla WoW client compatibility."""
    if isinstance(value, str):
        v = value.strip()
        if v and "/" not in v and "\\" not in v and ".." not in v:
            return v
    return "WoW.exe"


def _https_url(value: str) -> str | None:
    url = (value or "").strip().rstrip("/")
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return None
    return url


def _magnet_uri(value: str) -> str | None:
    """Validate a ``magnet:`` URI: the scheme must be magnet and the query
    must carry at least one ``xt`` topic of ``urn:btih:`` (v1) or
    ``urn:btmh:`` (v2) — the info-hash that authenticates swarm-served
    metadata. Anything else is dropped (same silent-drop convention as
    non-HTTPS URLs)."""
    uri = (value or "").strip()
    if not uri:
        return None
    parts = urlsplit(uri)
    if parts.scheme != "magnet" or not parts.query:
        return None
    topics = [t.strip().lower() for t in parse_qs(parts.query).get("xt", [])]
    if not any(
        t.startswith("urn:btih:") or t.startswith("urn:btmh:") for t in topics
    ):
        return None
    return uri


def _default_manifest(base: str) -> str:
    """The derived manifest endpoint for a base URL (\"latest\" version)."""
    return base + "/api/file/latest/manifest.json"


def _default_client(base: str) -> str:
    """The derived client-files root for a base URL (\"latest\" version)."""
    return base + "/client/latest"


def _derive(data: dict) -> LauncherConfig:
    if not isinstance(data, dict):
        raise ValueError("launcher config must be a JSON object")
    server = data.get("server")
    if not isinstance(server, dict):
        raise ValueError("launcher config is missing the 'server' object")
    base = _https_url(server.get("base_url"))
    if base is None:
        raise ValueError(
            "launcher config 'server.base_url' must be an "
            "https URL and is required"
        )

    host = urlsplit(base).hostname or ""

    raw_discord_url = data.get("discord_url")
    if raw_discord_url is None:
        discord_url = None
    elif isinstance(raw_discord_url, str) and raw_discord_url.strip():
        discord_url = _https_url(raw_discord_url)
        if discord_url is None:
            raise ValueError(
                "launcher config 'discord_url' must be an https URL"
            )
    elif isinstance(raw_discord_url, str):
        discord_url = None
    else:
        raise ValueError("launcher config 'discord_url' must be an https URL")

    def _url(key, suffix):
        v = server.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        return base + suffix

    mirrors = []
    for m in data.get("mirrors") or []:
        if not isinstance(m, dict):
            continue
        mb = _https_url(m.get("base_url"))
        if mb is None:
            continue
        mhost = urlsplit(mb).hostname or ""
        mirrors.append(
            Mirror(
                name=(m.get("name") or mhost).strip(),
                base_url=mb,
                manifest_url=_https_url(m.get("manifest_url"))
                or _default_manifest(mb),
                client_url=_https_url(m.get("client_url"))
                or _default_client(mb),
                torrent_url=_https_url(m.get("torrent_url")),
            )
        )

    manifest_url = _https_url(server.get("manifest_url")) or _default_manifest(
        base
    )
    client_url = _https_url(server.get("client_url")) or _default_client(base)

    addons_registry_url = _url("addons_registry_url", "/api/addons.json")
    addons_registry_urls: list[str] = []
    raw_urls = server.get("addons_registry_urls")
    if isinstance(raw_urls, list) and raw_urls:
        for u in raw_urls:
            https = _https_url(u)
            if https:
                addons_registry_urls.append(https)
    if not addons_registry_urls:
        addons_registry_urls = [addons_registry_url]

    raw_theme = data.get("theme")
    theme = raw_theme if isinstance(raw_theme, dict) else None

    # Whether server.mods_registry_url was explicitly set (vs. derived from
    # the base URL): a config that embeds its mod list inline usually has
    # no real catalog endpoint, and must not be force-refetched.
    raw_mods_url = server.get("mods_registry_url")
    mods_registry_url_explicit = isinstance(raw_mods_url, str) and bool(
        raw_mods_url.strip()
    )

    # Asset registry URL — explicit-only (no base_url-derived default): a
    # config without one simply has no remote asset catalog. Assets may
    # also be embedded directly via the top-level "assets" list (kept raw;
    # services/assets sanitizes with catalog.validate_asset).
    raw_assets_url = server.get("assets_registry_url")
    assets_registry_url = _https_url(raw_assets_url) or ""
    raw_embedded_assets = data.get("assets")
    embedded_assets: list[dict] = (
        [e for e in raw_embedded_assets if isinstance(e, dict)]
        if isinstance(raw_embedded_assets, list)
        else []
    )

    # Mods embedded directly in the config. Kept raw — services/mods
    # sanitizes each entry with catalog.validate_mod (allowlisted source
    # kinds, https URLs, safe relative paths). Addons follow the same
    # pattern via the top-level "addons" list (sanitized by services/addons
    # with the git-host allowlist on top).
    raw_embedded_mods = data.get("mods")
    embedded_mods: list[dict] = (
        [e for e in raw_embedded_mods if isinstance(e, dict)]
        if isinstance(raw_embedded_mods, list)
        else []
    )
    raw_embedded_addons = data.get("addons")
    embedded_addons: list[dict] = (
        [e for e in raw_embedded_addons if isinstance(e, dict)]
        if isinstance(raw_embedded_addons, list)
        else []
    )

    addon_git_hosts = _parse_git_hosts(data.get("addon_git_hosts"))
    torrent_root_marker = _parse_root_marker(server.get("torrent_root_marker"))

    return LauncherConfig(
        server_name=(server.get("name") or host).strip(),
        server_url=base,
        manifest_url=manifest_url,
        client_url=client_url,
        news_url=_url("news_url", "/news.json"),
        featured_news_url=_url("featured_news_url", "/news/featured.json"),
        mods_registry_url=_url("mods_registry_url", "/api/mods.json"),
        addons_registry_url=addons_registry_url,
        addons_registry_urls=addons_registry_urls,
        realm=(server.get("realm") or host).strip(),
        mirrors=mirrors,
        embedded_mods=embedded_mods,
        embedded_addons=embedded_addons,
        mods_registry_url_explicit=mods_registry_url_explicit,
        embedded_assets=embedded_assets,
        assets_registry_url=assets_registry_url,
        discord_url=discord_url,
        theme=theme,
        torrent_url=_https_url(server.get("torrent_url")),
        torrent_magnet=_magnet_uri(server.get("torrent_magnet")),
        addon_git_hosts=addon_git_hosts,
        torrent_root_marker=torrent_root_marker,
    )


def discover_path() -> str:
    """Locate ``nostalgia_launcher.json``: next to the executable (frozen)
    or the repo root (source), then the current working directory. On macOS
    the frozen app also searches the folder *containing* the .app bundle, so
    the config can sit next to the bundle (e.g. in the DMG root) rather than
    buried in Contents/MacOS."""
    if getattr(sys, "frozen", False):
        roots = [os.path.dirname(os.path.abspath(sys.executable))]
        if is_macos():
            # <bundle>.app/Contents/MacOS/<exe> → <bundle>.app → parent dir
            roots.append(
                os.path.dirname(
                    os.path.dirname(
                        os.path.dirname(
                            os.path.dirname(os.path.abspath(sys.executable))
                        )
                    )
                )
            )
    else:
        roots = [
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        ]
    roots.append(os.getcwd())
    for root in roots:
        candidate = os.path.join(root, LAUNCHER_FILE)
        if os.path.exists(candidate):
            return candidate
    return ""


def user_config_path() -> str:
    """The per-user launcher-config file, where a first-launch selection is
    persisted so future launches reuse it (lives in the OS config dir).
    Redirected into the active profile's directory when a profile override
    is set (set_profile_launcher_path)."""
    if _profile_launcher_path:
        return _profile_launcher_path
    return os.path.join(config_dir(), LAUNCHER_FILE)


def set_profile_launcher_path(path: str):
    """Route persist()/persist_text() (and therefore auto-discovery of the
    previously imported config) at a profile-scoped launcher.json instead
    of the legacy top-level file. Called by the CLI for non-default
    profiles; empty string restores the default."""
    global _profile_launcher_path
    with _LOCK:
        _profile_launcher_path = path or ""


def _auto_path() -> str:
    """The configuration to use when no explicit path was given: the
    previously imported per-user file, else the auto-discovered one."""
    user = user_config_path()
    if os.path.exists(user):
        return user
    return discover_path()


# Content categories a server document may carry inline. At import time
# each one is split out of the persisted launcher config into its own
# local repo file (`local_<kind>_repo.json`): the config stays small and
# every category gets one authoritative on-disk home shaped as
# ``{"server": [...], "custom": [...]}`` — "server" mirrors the imported
# document, "custom" holds user-added entries and survives re-imports.
CONTENT_KINDS = ("mods", "addons", "assets")


def local_repo_path(kind: str) -> str:
    """The local repo file for a content kind (one of `CONTENT_KINDS`),
    scoped to the active profile (the legacy top-level file for the
    default profile). Function-local profiles import: profiles imports
    LAUNCHER_FILE/CONTENT_KINDS from this module."""
    from . import profiles

    return profiles.active().local_repo_path(kind)


def load_local_repo(kind: str) -> tuple[list, list]:
    """Read the local repo file for a content kind. Returns
    ``(server_entries, custom_entries)``; a missing file yields two empty
    lists. Raises ValueError when the file exists but is not the expected
    object with optional \"server\"/\"custom\" lists."""
    try:
        with open(local_repo_path(kind), encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return [], []
    except Exception as e:
        raise ValueError(f"local {kind} repo is unreadable: {e}") from e

    def _entries(key: str) -> list:
        value = raw.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"local {kind} repo '{key}' must be a list")
        return [e for e in value if isinstance(e, dict)]

    if not isinstance(raw, dict):
        raise ValueError(f"local {kind} repo must be a JSON object")
    return _entries("server"), _entries("custom")


def legacy_custom_path(kind: str) -> str:
    """Path of the pre-repo per-user custom file (the hand-edit escape
    hatch that predates the local repos), scoped to the active profile —
    the SAME file services.catalog.custom_file() serves, so the one-time
    migration seeds from what that profile's user actually had. Kept as a
    backup after the migration into the repo's "custom" list."""
    from . import profiles

    return profiles.active().custom_catalog_path(kind)


def legacy_custom_entries(kind: str) -> list:
    """The raw entries of the legacy per-user custom file, or [] when it
    doesn't exist / isn't readable. Used to seed a freshly-created local
    repo so pre-repo user additions survive the migration."""
    try:
        with open(legacy_custom_path(kind), encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def store_local_repo(
    kind: str, server_entries: list, custom_entries: list
) -> None:
    """Atomically write the local repo file for a content kind from its
    server-imported and user-custom entry lists."""
    payload = (
        json.dumps(
            {
                "server": list(server_entries),
                "custom": list(custom_entries),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    atomic_write_text(local_repo_path(kind), payload)


def _split_and_strip(data: dict, final=None) -> dict:
    """Split the content sections of an import document out into their
    local repo files — rewriting each repo's "server" list to mirror the
    document faithfully while preserving its "custom" list — then return a
    copy of the document with those sections removed (the stripped form
    persisted as the launcher config).

    The whole operation is transactional: `final` (the launcher-config
    write, when the caller supplies one) runs after every repo write
    succeeded, and any failure along the way rolls every already-written
    repo back to its prior bytes (newly created files are removed) before
    re-raising — an import is never half-applied."""
    staged: list[tuple[str, bytes | None]] = []
    try:
        for kind in CONTENT_KINDS:
            section = data.get(kind)
            entries = (
                [e for e in section if isinstance(e, dict)]
                if isinstance(section, list)
                else []
            )
            path = local_repo_path(kind)
            existed = os.path.exists(path)
            prior = None
            if existed:
                with open(path, "rb") as f:
                    prior = f.read()
                _prev_server, custom = load_local_repo(kind)
            else:
                # First-ever repo creation: seed "custom" from the legacy
                # per-user custom file so pre-repo user additions survive.
                custom = legacy_custom_entries(kind)
            store_local_repo(kind, entries, custom)
            staged.append((path, prior))
        if final is not None:
            final()
        return {k: v for k, v in data.items() if k not in CONTENT_KINDS}
    except Exception:
        for path, prior in reversed(staged):
            try:
                if prior is None:
                    if os.path.exists(path):
                        os.remove(path)
                else:
                    with open(path, "wb") as f:
                        f.write(prior)
            except OSError as e:
                log(f"  Could not roll back {path}: {e}", "err")
        raise


def _persist_data(data: dict) -> tuple[str, str]:
    """Validate an import document, split its content sections into the
    local repo files, and persist the stripped configuration into the
    per-user directory. The repo writes and the config write commit or
    roll back together. Returns (destination, error); exactly one is set."""
    dest = user_config_path()

    def write_config():
        stripped_text = (
            json.dumps(
                {k: v for k, v in data.items() if k not in CONTENT_KINDS},
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        atomic_write_text(dest, stripped_text)

    try:
        _derive(data)  # don't persist truncated/invalid configs
        _split_and_strip(data, final=write_config)
    except Exception as e:
        return "", f"Could not save the launcher configuration: {e}"
    return dest, ""


def persist(path: str) -> tuple[str, str]:
    """Import a validated launcher-config file into the per-user directory:
    its content sections (mods/addons/assets) are split into the local
    repo files and the remaining configuration is written atomically.
    Returns (destination, error); exactly one is set."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return "", f"Could not save the launcher configuration: {e}"
    return _persist_data(data)


def persist_text(text: str) -> tuple[str, str]:
    """Import already-fetched, validated launcher config *text* obtained
    over the network — same contract as `persist`."""
    try:
        data = json.loads(text)
    except Exception as e:
        return "", f"Could not save the launcher configuration: {e}"
    return _persist_data(data)


def validate_path(path: str) -> tuple["LauncherConfig | None", str]:
    """Validate a launcher config file WITHOUT storing it as the active config.
    Returns (config, error); exactly one is set. Never touches module globals."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return _derive(raw), ""
    except Exception as e:
        return None, str(e)


def validate_dict(data) -> tuple["LauncherConfig | None", str]:
    """Validate a launcher config *dict* WITHOUT storing it as the active
    config. Returns (config, error); exactly one is set. Never touches module
    globals. Used by the first-launch wizard to validate a config fetched
    over the network before accepting it."""
    try:
        return _derive(data), ""
    except Exception as e:
        return None, str(e)


def configure(path: str | None = None) -> tuple["LauncherConfig | None", str]:
    """Load and validate the launcher configuration from ``path`` (or an
    auto-discovered file). Returns (config, error); exactly one is set."""
    global _config, _path, _error
    with _LOCK:
        path = path or _auto_path()
        if not path:
            _config, _path, _error = (
                None,
                "",
                (
                    f"No {LAUNCHER_FILE} found. A launcher configuration is "
                    "required — create one or pass --launcher-config."
                ),
            )
            return None, _error
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            config = _derive(raw)
        except Exception as e:
            _config, _path, _error = (
                None,
                path,
                (f"Invalid launcher configuration ({path}): {e}"),
            )
            return None, _error
        _config, _path, _error = config, path, ""
        log(f"Launcher configuration loaded: {config.server_url}")
        return config, ""


def configure_from_dict(data: dict) -> "LauncherConfig | None":
    """Load from a dict (tests and programmatic callers). Returns None when
    invalid; the error is recorded for config_error()."""
    global _config, _path, _error
    with _LOCK:
        try:
            config = _derive(data)
        except Exception as e:
            _config, _path, _error = None, "", str(e)
            return None
        _config, _path, _error = config, "", ""
        return config


def reset():
    """Drop the loaded configuration (test teardown)."""
    global _config, _path, _error, _profile_launcher_path
    with _LOCK:
        _config, _path, _error = None, "", ""
        _profile_launcher_path = ""


def config() -> "LauncherConfig | None":
    with _LOCK:
        return _config


def config_error() -> str:
    with _LOCK:
        return _error


def server_url() -> str:
    c = config()
    return c.server_url if c else ""


def server_name() -> str:
    c = config()
    return c.server_name if c else ""


def discord_url() -> str | None:
    c = config()
    return c.discord_url if c else None


def news_url() -> str:
    c = config()
    return c.news_url if c else ""


def featured_news_url() -> str:
    c = config()
    return c.featured_news_url if c else ""


def mods_registry_url() -> str:
    c = config()
    return c.mods_registry_url if c else ""


def embedded_mods() -> list[dict]:
    """The mod entries embedded in the launcher config (raw; sanitized by
    `services/mods`). Empty when the config has no usable \"mods\" list."""
    c = config()
    return list(c.embedded_mods) if c else []


def embedded_addons() -> list[dict]:
    """The addon entries embedded in the launcher config (raw; sanitized by
    `services/addons`). Empty when the config has no usable \"addons\"
    list."""
    c = config()
    return list(c.embedded_addons) if c else []


def embedded_assets() -> list[dict]:
    """The asset entries embedded in the launcher config (raw; sanitized by
    `services/assets`). Empty when the config has no usable \"assets\" list."""
    c = config()
    return list(c.embedded_assets) if c else []


def assets_registry_url() -> str:
    """The launcher-configured remote asset catalog URL ('' when unset)."""
    c = config()
    return c.assets_registry_url if c else ""


def mods_registry_url_explicit() -> bool:
    """Whether server.mods_registry_url was explicitly configured (the
    derived base_url default does not count)."""
    c = config()
    return bool(c and c.mods_registry_url_explicit)


def addons_registry_urls() -> list[str]:
    """The ordered launcher-configured addon catalog URLs — later entries
    override earlier ones by addon folder name."""
    c = config()
    return list(c.addons_registry_urls) if c else []


def realm() -> str:
    c = config()
    return c.realm if c else ""


def mirrors() -> list["Mirror"]:
    c = config()
    return list(c.mirrors) if c else []
