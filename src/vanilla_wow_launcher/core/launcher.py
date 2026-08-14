"""Launcher configuration — the distribution's server, endpoints and mirrors.

Every endpoint the app talks to (client updates, news, mod/addon catalogs,
realm, downloads) comes from a single JSON file instead of hardcoded values,
so a distribution only needs to ship one file to point the launcher at its
own server.

The file is `vanilla_wow_launcher.json`, discovered next to the executable
(frozen) or in the repo root (running from source), or passed explicitly via
``--launcher-config``. Only ``server.base_url`` is required — every other URL
is derived from it unless overridden, and mirrors are optional:

    {
      "server": {
        "name": "My Server",
        "base_url": "https://server.example",
        "realm": "server.example",
        "news_url": "https://server.example/news",
        "featured_news_url": "https://server.example/news/featured",
        "mods_registry_url": "https://server.example/api/mods.json",
        "addons_registry_url": "https://server.example/api/addons.json"
      },
      "mirrors": [
        { "name": "Backup", "base_url": "https://mirror.example" }
      ]
    }

A missing or invalid configuration is a hard startup error: the app has
nothing to point at. This module is network-free; `core/security_http` builds
its download allowlist from the configured hosts.
"""

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .log_sink import log

LAUNCHER_FILE = "vanilla_wow_launcher.json"

_LOCK = threading.Lock()
_config: "LauncherConfig | None" = None
_path: str = ""
_error: str = ""


@dataclass
class Mirror:
    """One configured download mirror."""
    name: str
    base_url: str
    manifest_url: str
    client_url: str


@dataclass
class LauncherConfig:
    """Validated launcher configuration with every endpoint resolved."""
    server_name: str
    server_url: str
    news_url: str
    featured_news_url: str
    mods_registry_url: str
    addons_registry_url: str
    realm: str
    mirrors: list = field(default_factory=list)

    @property
    def configured(self) -> bool:
        return bool(self.server_url)

    def download_hosts(self) -> set:
        """Every host the configured server and mirrors may serve from."""
        hosts = set()
        for base in self.all_bases():
            host = urlsplit(base).hostname
            if host:
                hosts.add(host)
        return hosts

    def all_bases(self) -> list:
        """The server followed by every mirror's base URL."""
        return [self.server_url] + [m.base_url for m in self.mirrors]


def _https_url(value: str) -> str | None:
    url = (value or "").strip().rstrip("/")
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return None
    return url


def _derive(data: dict) -> LauncherConfig:
    if not isinstance(data, dict):
        raise ValueError("launcher config must be a JSON object")
    server = data.get("server")
    if not isinstance(server, dict):
        raise ValueError("launcher config is missing the 'server' object")
    base = _https_url(server.get("base_url"))
    if base is None:
        raise ValueError("launcher config 'server.base_url' must be an "
                         "https URL and is required")

    host = urlsplit(base).hostname or ""

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
        mirrors.append(Mirror(
            name=(m.get("name") or mhost).strip(),
            base_url=mb,
            manifest_url=_https_url(m.get("manifest_url"))
            or (mb + "/api/file/latest/manifest.json"),
            client_url=_https_url(m.get("client_url"))
            or (mb + "/client/latest"),
        ))

    return LauncherConfig(
        server_name=(server.get("name") or host).strip(),
        server_url=base,
        news_url=_url("news_url",
                      "/forum/octonews.php?mode=list&forum=2&limit=8"),
        featured_news_url=_url(
            "featured_news_url", "/forum/octonews.php?forum=35&mode=full"),
        mods_registry_url=_url("mods_registry_url", "/api/mods.json"),
        addons_registry_url=_url("addons_registry_url", "/api/addons.json"),
        realm=(server.get("realm") or host).strip(),
        mirrors=mirrors,
    )


def discover_path() -> str:
    """Locate ``vanilla_wow_launcher.json``: next to the executable (frozen)
    or the repo root (source), then the current working directory."""
    if getattr(sys, "frozen", False):
        roots = [os.path.dirname(os.path.abspath(sys.executable))]
    else:
        roots = [os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))]
    roots.append(os.getcwd())
    for root in roots:
        candidate = os.path.join(root, LAUNCHER_FILE)
        if os.path.exists(candidate):
            return candidate
    return ""


def configure(path: str | None = None) -> tuple["LauncherConfig | None", str]:
    """Load and validate the launcher configuration from ``path`` (or an
    auto-discovered file). Returns (config, error); exactly one is set."""
    global _config, _path, _error
    with _LOCK:
        path = path or discover_path()
        if not path:
            _config, _path, _error = None, "", (
                f"No {LAUNCHER_FILE} found. A launcher configuration is "
                "required — create one or pass --launcher-config.")
            return None, _error
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            config = _derive(raw)
        except Exception as e:
            _config, _path, _error = None, path, (
                f"Invalid launcher configuration ({path}): {e}")
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
    global _config, _path, _error
    with _LOCK:
        _config, _path, _error = None, "", ""


def config() -> "LauncherConfig | None":
    with _LOCK:
        return _config


def config_error() -> str:
    with _LOCK:
        return _error


def is_configured() -> bool:
    c = config()
    return bool(c and c.configured)


def server_url() -> str:
    c = config()
    return c.server_url if c else ""


def server_name() -> str:
    c = config()
    return c.server_name if c else ""


def news_url() -> str:
    c = config()
    return c.news_url if c else ""


def featured_news_url() -> str:
    c = config()
    return c.featured_news_url if c else ""


def mods_registry_url() -> str:
    c = config()
    return c.mods_registry_url if c else ""


def addons_registry_url() -> str:
    c = config()
    return c.addons_registry_url if c else ""


def realm() -> str:
    c = config()
    return c.realm if c else ""


def mirrors() -> list:
    c = config()
    return list(c.mirrors) if c else []
