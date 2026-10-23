"""Trust-panel tests: manifest-managed counts are network-free."""

from nostalgia_launcher.core import launcher
from nostalgia_launcher.core.trust import (
    _manifest_route_counts,
    trust_capabilities,
    trust_hosts,
)


def _config(data):
    launcher.reset()
    cfg = launcher.configure_from_dict(data)
    assert cfg is not None, launcher.config_error()
    return cfg


def _manifest_config():
    return _config(
        {
            "server": {
                "url": "https://srv.example",
                "download": {
                    "manifest": {
                        "url": "https://cdn.example/api/manifest",
                        "schema": {
                            "files": "files",
                            "version": "contentVersion",
                            "base_url": "filesBaseUrl",
                            "entry": {
                                "path": "path",
                                "size": "size",
                                "sha256": "sha256",
                            },
                            "download_url": "{base_url}{path}",
                        },
                        "routes": [
                            {"match": "Data/*.MPQ", "as": "asset"},
                            {"match": "Data/*.MPQ", "as": "asset"},
                            {
                                "match": "*.dll",
                                "as": "mod",
                                "type": "mod",
                            },
                        ],
                    }
                },
            }
        }
    )


def test_manifest_route_counts_by_target():
    cfg = _manifest_config()
    assert _manifest_route_counts(cfg) == (2, 1)


def test_trust_hosts_includes_manifest_host():
    cfg = _manifest_config()
    assert "cdn.example" in trust_hosts(cfg)


def test_trust_capabilities_manifest_does_not_feed_tabs():
    """Manifest routes feed the client overlay only — the community
    tabs stay unconfigured while the client row advertises the overlay."""
    cfg = _manifest_config()
    caps = dict((k, d) for k, _, d in trust_capabilities(cfg))
    assert caps["client"] == "Client — manifest overlay only"
    assert caps["assets"] == "Assets — not configured"
    assert caps["mods"] == "Mods — not configured"


def test_trust_capabilities_no_manifest_unchanged():
    cfg = _config({"server": {"url": "https://srv.example"}})
    caps = dict((k, d) for k, _, d in trust_capabilities(cfg))
    assert "manifest" not in caps["client"]
    assert caps["mods"] == "Mods — not configured"
