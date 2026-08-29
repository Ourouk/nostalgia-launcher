"""Launcher configuration — the distribution's server and endpoints.

Every endpoint the app talks to (client updates, news, mod/addon catalogs,
realm, downloads) comes from a single JSON file instead of hardcoded values,
so a distribution only needs to ship one file to point the launcher at its
own server.

The file is `nostalgia_launcher.json`, discovered next to the executable
(frozen) or in the repo root (running from source), or passed explicitly via
``--launcher-config``. A configuration chosen through the first-launch wizard
is persisted into the per-user config directory and takes precedence over
auto-discovery on later runs.

**No endpoint derivation.** Every URL is a direct, fully-qualified link — the
config declares exactly what the launcher talks to; there are no
server-specific path conventions spliced onto a base URL. The optional
``server.url`` is identity/display only (and falls back to the host of the
manifest when omitted); it is never used to build other endpoints.

    {
      "server": {
        "name": "My Server",
        "url": "https://server.example",
        "realm": "server.example",
        "news_url": "https://server.example/news.json",
        "featured_news_url": "https://server.example/news/featured.json",
        "mods_registry_url": "https://server.example/api/mods.json",
        "addons_registry_urls": ["https://server.example/api/addons.json"],
        "assets_registry_url": "https://server.example/api/assets.json",
        "download": {
          "update": true,
          "torrent": {
            "torrent_url": "https://server.example/client/latest/client.torrent",
            "magnet": "magnet:?xt=urn:btih:EXAMPLEINFOHASH&dn=client"
          },
          "http": {
            "manifest": "https://server.example/api/file/latest/manifest.json",
            "client": "https://server.example/client/latest"
          },
          "content": { "type": "folder" }
        }
      },
      "mods": [],
      "addons": [],
      "assets": [],
      "discord_url": "https://discord.gg/example",
      "theme": { "C_GOLD": "#d4a02f", "logo": "https://server.example/logo.png" }
    }

The ``server.download`` block is the single source of truth for client
acquisition:

* ``update`` (bool, default true) — the server-level "should the launcher
  verify/update the client" flag. A per-profile user override
  (``client_update_enabled``) wins when set; otherwise this default applies.
  Updates are only meaningful when a source exists (a torrent snapshot or an
  HTTP manifest).
* ``torrent`` — optional ``torrent_url`` (HTTPS ``.torrent``) and/or
  ``magnet`` (``magnet:?xt=urn:btih:…``). The HTTPS ``.torrent`` wins when
  both are present. This is also the only first-time acquisition path when
  ``update`` is false.
* ``http`` — explicit ``manifest`` and ``client`` URLs. The manifest is the
  per-file SHA-1 tree; ``client`` is the base the per-file HTTP downloads
  are joined to (``{client}/{relative_path}``).
* ``content`` — ``type`` is one of ``folder`` | ``zip`` | ``rar`` and describes
  how the delivered client is packaged. ``folder`` means the source already
  delivers extracted files; ``zip``/``rar`` means the acquired payload is an
  archive the launcher extracts into the game folder (used for first-time
  acquisition).

The optional ``theme`` object overrides the app's color theme per server:
color slots named like ``C_GOLD`` (each a ``#rrggbb`` hex value) plus an
optional ``logo`` URL shown as the header wordmark (see `core/themes`). It
is cosmetic and never validated strictly — a malformed theme falls back to
the default palette instead of failing startup.

The optional top-level ``mods``/``addons``/``assets`` lists embed catalog
entries directly in the config (same shape the remote catalogs use). Entries
are kept raw here and sanitized by `services/mods`, `services/addons` and
`services/assets`; embedded ids override the remote catalog, and the
per-user custom file overrides both.

**Import-time split**: `persist()` / `persist_text()` move the three content
sections out of the document before it is stored. Each lands in its own
local repo file (`local_<kind>_repo.json` in the config dir, shaped
``{"server": […], "custom": […]}`` — "server" mirrors the imported doc,
"custom" holds user-added entries that survive re-imports), and the
persisted config keeps only server/theme. The ``validate_*`` helpers stay
side-effect-free; a repo write failure aborts the whole import.

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
from .helpers import redact_url
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
class LauncherConfig:
    """Validated launcher configuration.

    Every endpoint is a direct, fully-qualified URL — nothing is derived from a
    base URL. The optional ``server_url`` is identity/display only.
    """

    server_name: str = ""
    server_url: str = ""
    realm: str = ""
    news_url: str = ""
    featured_news_url: str = ""
    mods_registry_url: str = ""
    addons_registry_url: str = ""
    addons_registry_urls: list[str] = field(default_factory=list)
    assets_registry_url: str = ""
    discord_url: str | None = None
    theme: dict | None = None
    addon_git_hosts: list[str] = field(default_factory=list)
    torrent_root_marker: str = "WoW.exe"
    # Server-specific trusted hosts for downloads (beyond auto-derived ones)
    trusted_hosts: set[str] = field(default_factory=set)
    embedded_mods: list[dict] = field(default_factory=list)
    embedded_addons: list[dict] = field(default_factory=list)
    embedded_assets: list[dict] = field(default_factory=list)
    # ── server.download block ────────────────────────────────────────────────
    # Whether the launcher should verify/update the client (server default; a
    # per-profile override wins when set).
    download_update: bool = True
    # BitTorrent source for updates and first-time acquisition.
    download_torrent_url: str | None = None
    download_torrent_magnet: str | None = None
    # HTTP update source: the manifest (per-file SHA-1 tree) and the client
    # base the per-file downloads are joined to.
    download_manifest_url: str | None = None
    download_client_url: str | None = None
    # How the delivered client is packaged: "folder" | "zip" | "rar".
    download_content_type: str = "folder"

    @property
    def configured(self) -> bool:
        """Whether the config points at a usable server (any endpoint set)."""
        return bool(
            self.server_name
            or self.server_url
            or self.download_manifest_url
            or self.download_torrent_url
            or self.news_url
            or self.mods_registry_url
        )

    def download_hosts(self) -> set[str]:
        """Every host the configured endpoints may serve from, plus any
        server-specific trusted hosts, so the security allowlist covers them."""
        hosts: set[str] = set()
        for url in self._all_urls():
            host = urlsplit(url).hostname
            if host:
                hosts.add(host)
        hosts |= self.trusted_hosts
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
        """Whether a config advertises a BitTorrent source (``torrent_url`` or
        ``magnet``). Static — no network probing."""
        return bool(self.download_torrent_url or self.download_torrent_magnet)

    def download_capable(self) -> bool:
        """Whether any update source exists (torrent snapshot or HTTP
        manifest). Updates are only possible when this is true."""
        return bool(self.download_torrent_url or self.download_manifest_url)

    def all_bases(self) -> list[str]:
        """The server identity URL (no longer a list of mirror bases)."""
        return [self.server_url] if self.server_url else []

    def _all_urls(self) -> list[str]:
        """Every endpoint URL the app may contact, so the security allowlist
        covers them."""
        urls: list[str] = []
        if self.server_url:
            urls.append(self.server_url)
        for u in (
            self.news_url,
            self.featured_news_url,
            self.mods_registry_url,
            self.addons_registry_url,
            self.assets_registry_url,
            self.download_manifest_url,
            self.download_client_url,
            self.download_torrent_url,
            self.discord_url or "",
        ):
            if u:
                urls.append(u)
        urls += self.addons_registry_urls
        for a in self.embedded_assets:
            if isinstance(a, dict) and isinstance(a.get("url"), str):
                urls.append(a["url"])
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


def _valid_host(host: str) -> bool:
    """Validate a hostname: non-empty, no path separators, no traversal."""
    if not host:
        return False
    if any(ch in host for ch in "/\\:"):
        return False
    if ".." in host:
        return False
    return True


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


def _host_of(url: str) -> str:
    """The hostname of a URL, or '' when it isn't a parseable http(s) URL."""
    if not url:
        return ""
    return urlsplit(url).hostname or ""


def _derive(data: dict) -> LauncherConfig:
    if not isinstance(data, dict):
        raise ValueError("launcher config must be a JSON object")
    server = data.get("server")
    if not isinstance(server, dict):
        raise ValueError("launcher config is missing the 'server' object")

    host = _host_of(_https_url(server.get("url")))

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

    # Explicit, direct URLs only — no base_url suffix derivation.
    news_url = _https_url(server.get("news_url")) or ""
    featured_news_url = _https_url(server.get("featured_news_url")) or ""
    mods_registry_url = _https_url(server.get("mods_registry_url")) or ""
    addons_registry_url = _https_url(server.get("addons_registry_url")) or ""

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

    # Server-specific trusted hosts for downloads (beyond auto-derived ones)
    raw_trusted_hosts = server.get("trusted_hosts")
    trusted_hosts: set[str] = set()
    if isinstance(raw_trusted_hosts, list):
        for h in raw_trusted_hosts:
            if isinstance(h, str):
                h = h.strip().lower()
                if h and _valid_host(h):
                    trusted_hosts.add(h)

    # Asset registry URL — explicit only: a config without one simply has no
    # remote asset catalog. Assets may also be embedded directly via the
    # top-level "assets" list (kept raw; services/assets sanitizes them).
    assets_registry_url = _https_url(server.get("assets_registry_url")) or ""
    raw_embedded_assets = data.get("assets")
    embedded_assets: list[dict] = (
        [e for e in raw_embedded_assets if isinstance(e, dict)]
        if isinstance(raw_embedded_assets, list)
        else []
    )

    # Mods/Addons embedded directly in the config (same shape as the remote
    # catalogs; sanitized by services/mods, services/addons, services/assets).
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

    # ── server.download block ──
    dl = server.get("download")
    dl = dl if isinstance(dl, dict) else {}
    download_update = bool(dl.get("update", True))
    torrent = dl.get("torrent")
    torrent = torrent if isinstance(torrent, dict) else {}
    http = dl.get("http")
    http = http if isinstance(http, dict) else {}
    content = dl.get("content")
    content = content if isinstance(content, dict) else {}
    content_type = content.get("type")
    if content_type not in ("zip", "rar", "folder"):
        content_type = "folder"
    download_torrent_url = _https_url(torrent.get("torrent_url"))
    download_torrent_magnet = _magnet_uri(torrent.get("magnet"))
    download_manifest_url = _https_url(http.get("manifest"))
    download_client_url = _https_url(http.get("client"))

    def _name_or_host(value) -> str:
        """A config-supplied display name, or the host fallback. Truthy
        non-strings (e.g. a numeric name) fall back like absent ones
        instead of crashing _derive with an AttributeError."""
        if isinstance(value, str) and value.strip():
            return value.strip()
        return host

    server_url = _https_url(server.get("url")) or _host_of(
        download_manifest_url
    ) or _host_of(download_torrent_url) or host

    return LauncherConfig(
        server_name=_name_or_host(server.get("name")),
        server_url=server_url,
        realm=_name_or_host(server.get("realm")),
        news_url=news_url,
        featured_news_url=featured_news_url,
        mods_registry_url=mods_registry_url,
        addons_registry_url=addons_registry_url,
        addons_registry_urls=addons_registry_urls,
        assets_registry_url=assets_registry_url,
        discord_url=discord_url,
        theme=theme,
        addon_git_hosts=addon_git_hosts,
        torrent_root_marker=torrent_root_marker,
        trusted_hosts=trusted_hosts,
        embedded_mods=embedded_mods,
        embedded_addons=embedded_addons,
        embedded_assets=embedded_assets,
        download_update=download_update,
        download_torrent_url=download_torrent_url,
        download_torrent_magnet=download_torrent_magnet,
        download_manifest_url=download_manifest_url,
        download_client_url=download_client_url,
        download_content_type=content_type,
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
        log(f"Launcher configuration loaded: {redact_url(config.server_url)}")
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
    """Whether a mod catalog URL is configured (no base_url derivation)."""
    c = config()
    return bool(c and c.mods_registry_url)


def news_url_explicit() -> bool:
    """Whether a news URL is configured (no base_url derivation)."""
    c = config()
    return bool(c and c.news_url)


def featured_news_url_explicit() -> bool:
    """Whether a featured-news URL is configured (no base_url derivation)."""
    c = config()
    return bool(c and c.featured_news_url)


def download_update_enabled() -> bool:
    """The server's ``server.download.update`` default."""
    c = config()
    return bool(c and c.download_update)


def download_content_type() -> str:
    """The server's ``server.download.content.type`` (folder/zip/rar)."""
    c = config()
    return c.download_content_type if c else "folder"


def download_manifest_url() -> str:
    c = config()
    return c.download_manifest_url or "" if c else ""


def download_client_url() -> str:
    c = config()
    return c.download_client_url or "" if c else ""


def download_torrent_url() -> str:
    c = config()
    return c.download_torrent_url or "" if c else ""


def download_torrent_magnet() -> str:
    c = config()
    return c.download_torrent_magnet or "" if c else ""


def effective_client_updates_enabled() -> bool:
    """The effective client-update switch.

    A per-profile user override (``client_update_enabled`` in the profile
    config) wins when set; otherwise the server's ``server.download.update``
    default applies. Network-free."""
    from .config_store import load_config

    default = download_update_enabled()
    user = load_config().get("client_update_enabled")
    if user is None:
        return default
    return bool(user)


def addons_registry_urls() -> list[str]:
    """The ordered launcher-configured addon catalog URLs — later entries
    override earlier ones by addon folder name."""
    c = config()
    return list(c.addons_registry_urls) if c else []


def realm() -> str:
    c = config()
    return c.realm if c else ""


def mirrors() -> list:
    """Mirrors were removed; the single download source lives in
    ``server.download``. Kept as an empty list for any legacy caller."""
    return []
