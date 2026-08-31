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
  ``update`` is false. ``torrent.update`` (bool, optional) controls whether
  the torrent is used for incremental updates: explicit ``false`` means
  first-time download only; explicit ``true`` forces updates via torrent.
  When absent, inferred as ``bool(torrent_url)`` — a ``magnet``-only source
  defaults to first-time-only, a ``torrent_url`` defaults to updatable.
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
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from .filesystem import atomic_write_text
from .helpers import redact_url
from .log_sink import log
from .platform_support import is_macos

LAUNCHER_FILE = "nostalgia_launcher.json"

_LOCK = threading.Lock()
_config: "LauncherConfig | None" = None
_path: str = ""
_error: str = ""


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
    # Whether the torrent may be used for incremental updates (None = infer
    # from presence of torrent_url: magnet-only → first-time-only).
    download_torrent_update: bool | None = None
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
            or self.download_client_url
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

    def torrent_update_allowed(self) -> bool:
        """Whether the torrent may be used for incremental updates.

        Explicit ``server.download.torrent.update`` wins; otherwise inferred
        as ``bool(torrent_url)`` — magnet-only defaults to first-time-only.
        First-time acquisition via ``start_client_download`` does not honor
        this flag (a magnet still downloads the initial client)."""
        if not self.has_torrent():
            return False
        if self.download_torrent_update is not None:
            return bool(self.download_torrent_update)
        return bool(self.download_torrent_url)

    def download_capable(self) -> bool:
        """Whether any update source exists (torrent snapshot or HTTP
        manifest or HTTP client). Updates are only possible when this is true."""
        return bool(
            self.download_torrent_url
            or self.download_manifest_url
            or self.download_client_url
        )

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


def _https_url(value: str | None) -> str | None:
    url = (value or "").strip().rstrip("/")  # type: ignore[arg-type]
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return None
    return url


def _magnet_uri(value: str | None) -> str | None:
    """Validate a ``magnet:`` URI: the scheme must be magnet and the query
    must carry at least one ``xt`` topic of ``urn:btih:`` (v1) or
    ``urn:btmh:`` (v2) — the info-hash that authenticates swarm-served
    metadata. Anything else is dropped (same silent-drop convention as
    non-HTTPS URLs)."""
    uri = (value or "").strip()  # type: ignore[arg-type]
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


def _host_of(url: str | None) -> str:
    """The hostname of a URL, or '' when it isn't a parseable http(s) URL."""
    if not url:
        return ""
    return urlsplit(url).hostname or ""


# ── Pydantic models — authoritative schema for launcher config ────────────
# Structural validation (required fields, types, URL shape) is delegated to
# these models. Silent-drop policy (invalid https → None/"") stays in the
# field validators so ``_derive`` no longer needs repetitive
# ``isinstance``/``_https_url`` chains. Security-context checks (host
# allowlist) remain in domain logic, not the schema.


def _clean_https_or_none(v: object) -> str | None:
    if not isinstance(v, str):
        return None
    return _https_url(v)


def _clean_https_or_empty(v: object) -> str:
    if not isinstance(v, str):
        return ""
    return _https_url(v) or ""


def _clean_addon_hosts(v: object) -> list[str]:
    return _parse_git_hosts(v)


def _clean_root_marker(v: object) -> str:
    return _parse_root_marker(v) if isinstance(v, str) else "WoW.exe"


class _LauncherTorrentModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    torrent_url: str | None = None
    magnet: str | None = None
    update: bool | None = None

    @field_validator("torrent_url", mode="before")
    @classmethod
    def _v_url(cls, v: object) -> str | None:
        return _clean_https_or_none(v)

    @field_validator("magnet", mode="before")
    @classmethod
    def _v_magnet(cls, v: object) -> str | None:
        if not isinstance(v, str):
            return None
        return _magnet_uri(v)


class _LauncherHttpModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    manifest: str | None = None
    client: str | None = None

    @field_validator("manifest", "client", mode="before")
    @classmethod
    def _v_https(cls, v: object) -> str | None:
        return _clean_https_or_none(v)


class _LauncherContentModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["zip", "rar", "folder"] = "folder"

    @field_validator("type", mode="before")
    @classmethod
    def _v_type(cls, v: object) -> str:
        return v if v in ("zip", "rar", "folder") else "folder"


class _LauncherDownloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    update: bool = True
    torrent: _LauncherTorrentModel = Field(
        default_factory=_LauncherTorrentModel
    )
    http: _LauncherHttpModel = Field(default_factory=_LauncherHttpModel)
    content: _LauncherContentModel = Field(
        default_factory=_LauncherContentModel
    )


class _LauncherServerModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    url: str | None = None
    base_url: str | None = None
    name: str | None = None
    realm: str | None = None
    news_url: str | None = None
    featured_news_url: str | None = None
    mods_registry_url: str | None = None
    addons_registry_url: str | None = None
    addons_registry_urls: list[str] | None = None
    assets_registry_url: str | None = None
    trusted_hosts: list[str] | None = None
    torrent_root_marker: str | None = None
    download: _LauncherDownloadModel | None = None

    @field_validator(
        "url",
        "base_url",
        "news_url",
        "featured_news_url",
        "mods_registry_url",
        "addons_registry_url",
        "assets_registry_url",
        mode="before",
    )
    @classmethod
    def _v_https_empty(cls, v: object) -> str:
        return _clean_https_or_empty(v)

    @field_validator("addons_registry_urls", mode="before")
    @classmethod
    def _v_addons_urls(cls, v: object) -> list[str] | None:
        if not isinstance(v, list):
            return None
        out: list[str] = []
        for u in v:
            https = _https_url(u) if isinstance(u, str) else None
            if https:
                out.append(https)
        return out or None

    @field_validator("trusted_hosts", mode="before")
    @classmethod
    def _v_trusted(cls, v: object) -> list[str] | None:
        if not isinstance(v, list):
            return None
        out: list[str] = []
        for h in v:
            if isinstance(h, str):
                h = h.strip().lower()
                if h and _valid_host(h):
                    out.append(h)
        return out

    @field_validator("torrent_root_marker", mode="before")
    @classmethod
    def _v_marker(cls, v: object) -> str:
        return _clean_root_marker(v)


class _LauncherDocModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    server: _LauncherServerModel
    discord_url: str | None = None
    theme: dict | None = None
    addon_git_hosts: list[str] = Field(default_factory=list)
    assets: list[dict] | None = None
    mods: list[dict] | None = None
    addons: list[dict] | None = None

    @field_validator("discord_url", mode="before")
    @classmethod
    def _v_discord(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        if isinstance(v, str) and v.strip():
            https = _https_url(v)
            if https is None:
                raise ValueError(
                    "launcher config 'discord_url' must be an https URL"
                )
            return https
        raise ValueError("launcher config 'discord_url' must be an https URL")

    @field_validator("theme", mode="before")
    @classmethod
    def _v_theme(cls, v: object) -> dict | None:
        return v if isinstance(v, dict) else None

    @field_validator("addon_git_hosts", mode="before")
    @classmethod
    def _v_git_hosts(cls, v: object) -> list[str]:
        return _clean_addon_hosts(v)

    @field_validator("assets", "mods", "addons", mode="before")
    @classmethod
    def _v_embedded(cls, v: object) -> list[dict] | None:
        if isinstance(v, list):
            return [e for e in v if isinstance(e, dict)]
        return None


def _derive(data: dict) -> LauncherConfig:
    """Validate raw JSON via :class:`_LauncherDocModel` and map to
    :class:`LauncherConfig`."""
    if not isinstance(data, dict):
        raise ValueError("launcher config must be a JSON object")
    try:
        doc = _LauncherDocModel.model_validate(data)
    except ValidationError as e:
        # Preserve historic error messages for the two cases tests assert.
        msg = str(e)
        if "server" in msg and "Field required" in msg:
            raise ValueError(
                "launcher config is missing the 'server' object"
            ) from None
        # ``discord_url`` validation error is already the historic message.
        for err in e.errors():
            if err.get("loc") == ("discord_url",) and "discord_url" in str(
                err.get("ctx", {}).get("error", "")  # type: ignore[union-attr]
            ):
                raise ValueError(
                    str(err.get("ctx", {}).get("error", ""))  # type: ignore[union-attr]
                ) from None
            if "discord_url" in str(err):
                raise ValueError(
                    "launcher config 'discord_url' must be an https URL"
                ) from None
        raise ValueError(f"launcher config invalid: {e}") from None
    srv = doc.server
    # ``server`` is required by the model; Pydantic already raised if missing.
    host = _host_of(_https_url(srv.url) or "")

    # Direct URLs — silent drop already handled by model validators.
    news_url = srv.news_url or ""
    featured_news_url = srv.featured_news_url or ""
    mods_registry_url = srv.mods_registry_url or ""
    addons_registry_url = srv.addons_registry_url or ""
    addons_registry_urls = srv.addons_registry_urls or [addons_registry_url]
    assets_registry_url = srv.assets_registry_url or ""
    trusted_hosts = set(srv.trusted_hosts or [])
    torrent_root_marker = srv.torrent_root_marker or "WoW.exe"

    embedded_assets = doc.assets or []
    embedded_mods = doc.mods or []
    embedded_addons = doc.addons or []
    addon_git_hosts = doc.addon_git_hosts or []
    theme = doc.theme
    discord_url = doc.discord_url

    # server.download block — models provide defaults & silent-drop.
    dl = srv.download or _LauncherDownloadModel()
    download_update = bool(dl.update) if dl.update is not None else True
    download_torrent_url = dl.torrent.torrent_url if dl.torrent else None
    download_torrent_magnet = dl.torrent.magnet if dl.torrent else None
    download_torrent_update = dl.torrent.update if dl.torrent else None
    download_manifest_url = dl.http.manifest if dl.http else None
    download_client_url = dl.http.client if dl.http else None
    content_type = dl.content.type if dl.content else "folder"

    def _name_or_host(value: object) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return host

    server_url = (
        _https_url(srv.url)
        or _https_url(srv.base_url)
        or _host_of(download_manifest_url or "")
        or _host_of(download_torrent_url or "")
        or host
    )

    return LauncherConfig(
        server_name=_name_or_host(srv.name),
        server_url=server_url,
        realm=_name_or_host(srv.realm),
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
        download_torrent_update=download_torrent_update,
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
    persisted so future launches reuse it. Scoped to the active profile
    (the default profile resolves under ``profiles/default/``)."""
    from . import profiles

    return profiles.active().launcher_path()


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
    scoped to the active profile. Function-local profiles import: profiles
    imports LAUNCHER_FILE/CONTENT_KINDS from this module."""
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
            custom = []
            if existed:
                with open(path, "rb") as f:
                    prior = f.read()
                _prev_server, custom = load_local_repo(kind)
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
    global _config, _path, _error
    with _LOCK:
        _config, _path, _error = None, "", ""


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


def torrent_update_allowed() -> bool:
    """Whether the torrent may be used for incremental updates (Plan A)."""
    c = config()
    return bool(c and c.torrent_update_allowed())


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
