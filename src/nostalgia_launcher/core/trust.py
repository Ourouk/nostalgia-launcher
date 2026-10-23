"""Configuration trust summary (network-free).

What a launcher configuration would let the app contact and download, for
the import wizard's trust stage: every host plus the per-category
capability rows. Pure functions over a validated launcher config.
"""

from urllib.parse import urlsplit


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _theme_logo_host(config) -> str:
    """The host of the config's theme logo URL, or "" when none."""
    theme = getattr(config, "theme", None)
    if not isinstance(theme, dict):
        return ""
    logo = theme.get("logo")
    if not isinstance(logo, str) or not logo.strip():
        return ""
    if urlsplit(logo.strip()).scheme != "https":
        return ""
    return _host(logo.strip())


def _embedded_hosts(config) -> list[str]:
    """Hosts named by entries embedded directly in the configuration."""
    hosts: list[str] = []

    def _add(url: object):
        if not isinstance(url, str):
            return
        h = _host(url.strip())
        if h and h not in hosts:
            hosts.append(h)

    for mod in getattr(config, "embedded_mods", []) or []:
        if not isinstance(mod, dict):
            continue
        _add(mod.get("repo_url"))
        source = mod.get("source")
        if isinstance(source, dict):
            _add(source.get("url"))
    for addon in getattr(config, "embedded_addons", []) or []:
        if not isinstance(addon, dict):
            continue
        _add(addon.get("git"))
    for asset in getattr(config, "embedded_assets", []) or []:
        if not isinstance(asset, dict):
            continue
        _add(asset.get("url"))
    return hosts


def trust_hosts(config) -> list[str]:
    """Every host the launcher would contact under this configuration.

    Covers the registry/feed/download endpoints, the community button, the
    theme logo, and URLs embedded directly in the configuration. Sorted
    and de-duplicated; network-free.
    """
    hosts: list[str] = []
    urls: list[str] = [
        config.server_url,
        config.news_url,
        config.featured_news_url,
        config.mods_registry_url,
        *config.addons_registry_urls,
        config.assets_registry_url,
        config.download_fallback_url or "",
        config.download_torrent_url or "",
        config.community_url or "",
        (config.download_manifest or {}).get("url", ""),
    ]
    for url in urls:
        h = _host(url)
        if h and h not in hosts:
            hosts.append(h)
    logo_host = _theme_logo_host(config)
    if logo_host and logo_host not in hosts:
        hosts.append(logo_host)
    for h in _embedded_hosts(config):
        if h not in hosts:
            hosts.append(h)
    hosts.sort()
    return hosts


def _catalog_summary(
    explicit_url: bool, embedded: int, manifest: int = 0
) -> str:
    """One-line description of where a content category comes from:
    remote catalog URL(s), manifest-managed entries, embedded entries,
    or a combination."""
    parts: list[str] = []
    if explicit_url:
        parts.append("catalog")
    if manifest:
        parts.append(f"{manifest} manifest-managed")
    if embedded:
        parts.append(f"{embedded} embedded")
    return " + ".join(parts) if parts else "not configured"


def _manifest_route_counts(config) -> tuple[int, int]:
    """(asset_count, mod_count) of manifest routes in the config.

    Network-free: counts the declared ``routes`` targets, so the trust
    panel lists what the manifest *would* manage before anything is
    fetched."""
    block = getattr(config, "download_manifest", None)
    if not isinstance(block, dict):
        return 0, 0
    routes = block.get("routes")
    if not isinstance(routes, list):
        return 0, 0
    assets = sum(
        1 for r in routes if isinstance(r, dict) and r.get("as") == "asset"
    )
    mods = sum(
        1 for r in routes if isinstance(r, dict) and r.get("as") == "mod"
    )
    return assets, mods


def trust_capabilities(config) -> list[tuple[str, bool, str]]:
    """The five trust rows: (key, enabled, label) for client, mods,
    addons, assets and news. Network-free — manifest counts come from
    the config's declared routes, never a fetch."""
    has_client = bool(config.download_fallback_url or config.has_torrent())
    if config.download_fallback_url and config.has_torrent():
        client_detail = "BitTorrent + HTTPS archive"
    elif config.has_torrent():
        client_detail = "BitTorrent"
    elif config.download_fallback_url:
        client_detail = "HTTPS archive"
    else:
        client_detail = "not configured"
    addon_urls = [u for u in (config.addons_registry_urls or []) if u]
    manifest_assets, manifest_mods = _manifest_route_counts(config)
    has_manifest_mods = manifest_mods > 0
    has_manifest_client_files = bool(
        getattr(config, "download_manifest", None)
    )
    if has_manifest_client_files and (
        config.download_fallback_url or config.has_torrent()
    ):
        client_detail = f"{client_detail} + manifest overlay"
    elif has_manifest_client_files:
        has_client = True
        client_detail = "manifest overlay only"
    return [
        ("client", has_client, f"Client — {client_detail}"),
        (
            "mods",
            bool(
                config.mods_registry_url
                or config.embedded_mods
                or has_manifest_mods
            ),
            "Mods — "
            + _catalog_summary(
                bool(config.mods_registry_url),
                len(config.embedded_mods),
                manifest_mods,
            ),
        ),
        (
            "addons",
            bool(addon_urls or config.embedded_addons),
            "Addons — "
            + _catalog_summary(bool(addon_urls), len(config.embedded_addons)),
        ),
        (
            "assets",
            bool(
                config.assets_registry_url
                or config.embedded_assets
                or manifest_assets > 0
            ),
            "Assets — "
            + _catalog_summary(
                bool(config.assets_registry_url),
                len(config.embedded_assets or []),
                manifest_assets,
            ),
        ),
        (
            "news",
            bool(config.news_url),
            f"News — {'feed configured' if config.news_url else 'no feed'}",
        ),
    ]
