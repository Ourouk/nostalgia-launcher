"""Unit tests for the mods engine and self-update checks."""

import json
import os

import pytest
from _torrent_fakes import fake_urlopen

import nostalgia_launcher.core.config_store as config_store
import nostalgia_launcher.services.catalog as catalog
import nostalgia_launcher.services.mods as mods
import nostalgia_launcher.services.self_update as self_update
import nostalgia_launcher.services.sources.deploy as deploy
from nostalgia_launcher.controllers.mods import ModsController
from nostalgia_launcher.core import launcher
from nostalgia_launcher.state.events import (
    EventDispatcher,
    LogMessage,
    ModsLoaded,
)

# ── catalog / registry loading ───────────────────────────────────────────────


def test_mods_registry_empty_when_nothing_configured(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})

    def fail(*a, **k):
        raise AssertionError("no cache must not hit the network")

    monkeypatch.setattr(catalog, "secure_urlopen", fail)
    assert mods.mods_registry() == []


def test_fetch_mods_catalog_cached_never_network(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    cached = [
        {
            "id": "RemoteMod",
            "name": "RemoteMod",
            "source": {"kind": "direct_file"},
        }
    ]
    config_store.save_config(
        {"mods_catalog_cache": {"timestamp": 9999999999, "catalog": cached}}
    )

    def fail(*a, **k):
        raise AssertionError("cached catalog must not hit the network")

    monkeypatch.setattr(catalog, "secure_urlopen", fail)
    assert mods.fetch_mods_catalog() == cached
    assert any(m["id"] == "RemoteMod" for m in mods.mods_registry())


def test_fetch_mods_catalog_force_fetches_and_validates(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    raw = [
        {
            "id": "X",
            "name": "X",
            "source": {
                "kind": "direct_file",
                "url": "https://example.com/x.dll",
                "dest": "x.dll",
            },
        },
        {"id": "Evil", "source": {"kind": "exec_arbitrary"}},
    ]
    payload = json.dumps(raw).encode()

    monkeypatch.setattr(catalog, "secure_urlopen", fake_urlopen(payload))

    out = mods.fetch_mods_catalog(force=True)
    assert [m["id"] for m in out] == ["X"]


def test_fetch_mods_catalog_force_raises_offline_no_cache(
    tmp_path, monkeypatch
):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})

    def fail(*a, **k):
        raise ConnectionError("offline")

    monkeypatch.setattr(catalog, "secure_urlopen", fail)
    with pytest.raises(ConnectionError):
        mods.fetch_mods_catalog(force=True)


def test_mods_registry_merges_custom(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    # User-custom entries now live in the local repo's "custom" list.
    mods.catalog.write_local_repo(
        "mods",
        [],
        [
            {
                "id": "MyMod",
                "name": "My Mod",
                "source": {
                    "kind": "github_release",
                    "owner": "a",
                    "repo": "b",
                    "asset_pattern": "*.zip",
                },
            }
        ],
    )

    reg = mods.mods_registry()
    by_id = {m["id"]: m for m in reg}
    assert by_id["MyMod"]["type"] == "mod"
    # Without a bundled registry, only the custom entry is present.
    assert list(by_id) == ["MyMod"]


# ── mods embedded in the launcher config ─────────────────────────────────────

_EMB_VALID = {
    "id": "Emb",
    "name": "Emb",
    "source": {
        "kind": "direct_file",
        "url": "https://example.com/e.dll",
        "dest": "e.dll",
    },
}


def _configure_embedded(mods_entries, server=None):
    server = server or {"name": "S", "base_url": "https://srv.example"}
    launcher.configure_from_dict({"server": server, "mods": mods_entries})


def test_embedded_mods_served_without_network(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({"mods_default_enabled": False})
    _configure_embedded([_EMB_VALID, {"id": "Bad", "source": {}}])

    def fail(*a, **k):
        raise AssertionError("embedded-only registry must not hit network")

    monkeypatch.setattr(catalog, "secure_urlopen", fail)
    reg = mods.mods_registry()
    # The invalid entry is skipped; the valid one needs no URL or fetch.
    assert [m["id"] for m in reg] == ["Emb"]
    assert mods.has_remote_catalog() is False
    assert mods.catalog_is_stale() is False


def test_mods_registry_precedence_custom_over_embedded_over_remote(
    tmp_path, monkeypatch
):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    remote = [{"id": "M", "name": "Remote", "source": {"kind": "direct_file"}}]
    config_store.save_config(
        {"mods_catalog_cache": {"timestamp": 9999999999, "catalog": remote}}
    )
    _configure_embedded(
        [
            {
                "id": "M",
                "name": "Embedded",
                "source": {
                    "kind": "direct_file",
                    "url": "https://example.com/m.dll",
                    "dest": "m.dll",
                },
            },
            {
                "id": "E2",
                "name": "E2",
                "source": {
                    "kind": "direct_file",
                    "url": "https://example.com/e2.dll",
                    "dest": "e2.dll",
                },
            },
        ]
    )
    # User-custom entries now live in the local repo's "custom" list.
    mods.catalog.write_local_repo(
        "mods",
        [],
        [
            {
                "id": "M",
                "name": "Custom",
                "source": {
                    "kind": "github_release",
                    "owner": "a",
                    "repo": "b",
                    "asset_pattern": "*.zip",
                },
            }
        ],
    )

    by_id = {m["id"]: m for m in mods.mods_registry()}
    assert by_id["M"]["name"] == "Custom"  # custom > embedded > remote
    assert by_id["E2"]["name"] == "E2"
    assert set(by_id) == {"M", "E2"}


def test_catalog_stale_when_url_explicit_despite_embedded(
    tmp_path, monkeypatch
):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})  # no cached catalog
    _configure_embedded(
        [_EMB_VALID],
        {
            "name": "S",
            "base_url": "https://srv.example",
            "mods_registry_url": "https://srv.example/api/mods.json",
        },
    )
    assert mods.has_remote_catalog() is True
    assert mods.catalog_is_stale() is True


def test_has_remote_catalog_user_override(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({"mods_default_enabled": False})
    # A launcher config that does not explicitly set a mods registry URL has
    # no remote catalog — only a user override counts.
    monkeypatch.setattr(launcher, "mods_registry_url_explicit", lambda: False)
    assert mods.has_remote_catalog() is False
    assert mods.set_registry_url("https://mine.example/mods.json") is None
    assert mods.has_remote_catalog() is True


def test_reload_catalog_republishes_when_embedded_only(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({"mods_default_enabled": False})
    _configure_embedded([_EMB_VALID])

    def fail(*a, **k):
        raise AssertionError("embedded-only reload must not hit the network")

    monkeypatch.setattr(catalog, "secure_urlopen", fail)
    controller = ModsController(EventDispatcher(), get_out_dir=lambda: "")
    assert controller.reload_catalog() is True
    events = controller._dispatcher.drain()
    texts = [e.text for e in events if isinstance(e, LogMessage)]
    assert any("embedded in the launcher config" in t for t in texts)
    assert any(isinstance(e, ModsLoaded) for e in events)
    assert controller._busy is False


# ── asset selection / versions (release backends) ────────────────────────────


def test_pick_asset_matches_pattern_and_prefers_without_suffix():
    from nostalgia_launcher.services.sources.github_release import pick_asset

    assets = [
        {"name": "example-loader-1.0-dxvk.zip"},
        {"name": "example-loader-1.0.zip"},
    ]
    assert (
        pick_asset(assets, "example-loader-*.zip", "-dxvk")["name"]
        == "example-loader-1.0.zip"
    )


def test_pick_asset_returns_none_without_match():
    from nostalgia_launcher.services.sources.github_release import pick_asset

    assert pick_asset([{"name": "x.dll"}], "*.zip", None) is None


def test_release_version_uses_asset_when_version_from_asset():
    from nostalgia_launcher.services.sources.github_release import (
        release_version,
    )

    rel = {
        "tag_name": "Release",
        "assets": [
            {"name": "SuperWoW 2.2.zip"},
        ],
    }
    source = {
        "version_from": "asset",
        "asset_pattern": "SuperWoW*.zip",
        "prefer_no": None,
    }
    assert release_version(source, rel) == "2.2"


def test_release_version_defaults_to_tag():
    from nostalgia_launcher.services.sources.github_release import (
        release_version,
    )

    rel = {"tag_name": "v1.2.3", "assets": []}
    assert release_version({}, rel) == "v1.2.3"


# ── dlls.txt ────────────────────────────────────────────────────────────────


def test_add_dll_dedupes(tmp_path):
    client = tmp_path / "client"
    client.mkdir()
    mods.add_dll(str(client), "VfPatcher.dll")
    mods.add_dll(str(client), "VfPatcher.dll")
    lines = (client / "dlls.txt").read_text().splitlines()
    assert lines == ["VfPatcher.dll"]


def test_remove_dll(tmp_path):
    client = tmp_path / "client"
    client.mkdir()
    (client / "dlls.txt").write_text("A.dll\nB.dll\n")
    mods.remove_dll(str(client), "A.dll")
    assert (client / "dlls.txt").read_text().splitlines() == ["B.dll"]
    mods.remove_dll(str(client), "B.dll")
    assert not (client / "dlls.txt").exists()


# ── filesystem detection (source of truth) ───────────────────────────────


def test_read_dlls_entries_lowercases_strips(tmp_path):
    client = tmp_path / "client"
    client.mkdir()
    assert mods.read_dlls_entries(str(client)) == set()
    (client / "dlls.txt").write_text("VfPatcher.dll\n  dxvk  \n\n")
    assert mods.read_dlls_entries(str(client)) == {"vfpatcher.dll", "dxvk"}


def test_mod_installed_files_present_requires_files_and_dlls_entry(
    tmp_path, monkeypatch
):
    client = tmp_path / "client"
    client.mkdir()
    monkeypatch.setattr(mods, "load_config", lambda: {})
    mod = {"id": "m", "installed_files": ["a.dll"], "register_dll": ["a.dll"]}
    # dlls.txt missing → not loaded.
    assert not mods.mod_installed_files_present(mod, str(client))
    # File present but not registered in dlls.txt → not loaded.
    (client / "a.dll").write_bytes(b"MZ")
    assert not mods.mod_installed_files_present(mod, str(client))
    # Registered + present → loaded.
    (client / "dlls.txt").write_text("a.dll\n")
    assert mods.mod_installed_files_present(mod, str(client))
    # File gone while registration remains → not loaded.
    (client / "a.dll").unlink()
    assert not mods.mod_installed_files_present(mod, str(client))


def test_mod_installed_files_present_uses_record_files(tmp_path, monkeypatch):
    client = tmp_path / "client"
    client.mkdir()
    (client / "data.patch").write_bytes(b"x")
    (client / "dlls.txt").write_text("m.dll\n")
    monkeypatch.setattr(
        mods,
        "load_config",
        lambda: {"mods": {"m": {"installed_files": ["data.patch"]}}},
    )
    mod = {"id": "m", "register_dll": ["m.dll"]}
    assert mods.mod_installed_files_present(mod, str(client))


def test_mod_installed_files_present_falls_back_to_record(
    tmp_path, monkeypatch
):
    client = tmp_path / "client"
    client.mkdir()
    monkeypatch.setattr(
        mods,
        "load_config",
        lambda: {"mods": {"m": {"installed_version": "1.0"}}},
    )
    assert mods.mod_installed_files_present({"id": "m"}, str(client))


def test_scan_unknown_mods_lists_unclaimed_dlls(tmp_path):
    client = tmp_path / "client"
    client.mkdir()
    (client / "dlls.txt").write_text("Tracked.dll\nmystery.dll\n")
    registry = [{"id": "t", "register_dll": ["Tracked.dll"]}]
    assert mods.scan_unknown_mods(str(client), registry) == ["mystery.dll"]


def test_scan_unknown_mods_claims_bundled_server_dlls(tmp_path, monkeypatch):
    """A server DLL (manifest-bundled) is claimed even when the visible
    registry doesn't list it — never proposed for removal."""
    import nostalgia_launcher.services.manifest as manifest

    client = tmp_path / "client"
    client.mkdir()
    (client / "dlls.txt").write_text("ServerConnection.dll\nmystery.dll\n")
    monkeypatch.setattr(
        manifest,
        "bundled_mods",
        lambda *a, **k: [
            {
                "id": "manifest-serverconnection.dll",
                "register_dll": ["ServerConnection.dll"],
            }
        ],
    )
    assert mods.scan_unknown_mods(str(client), []) == ["mystery.dll"]


def test_remove_unknown_mod_removes_line_and_file(tmp_path):
    client = tmp_path / "client"
    client.mkdir()
    (client / "dlls.txt").write_text("Keep.dll\nmystery.dll\n")
    (client / "mystery.dll").write_bytes(b"MZ")
    mods.remove_unknown_mod(str(client), "mystery.dll")
    assert (client / "dlls.txt").read_text().splitlines() == ["Keep.dll"]
    assert not (client / "mystery.dll").exists()


def test_remove_unknown_mod_never_deletes_outside_client(tmp_path):
    """A traversal entry in mod-written dlls.txt must not delete files
    outside the client dir (the dlls.txt line itself is still dropped)."""
    client = tmp_path / "client"
    client.mkdir()
    victim = tmp_path / "victim.dll"
    victim.write_bytes(b"MZ")
    (client / "dlls.txt").write_text("../victim.dll\n")
    mods.remove_unknown_mod(str(client), "../victim.dll")
    assert victim.exists()
    assert not (client / "dlls.txt").exists()


def test_add_dll_rejects_traversal_entry(tmp_path):
    client = tmp_path / "client"
    client.mkdir()
    mods.add_dll(str(client), "../../evil.dll")
    assert not (client / "dlls.txt").exists()


# ── update detection ─────────────────────────────────────────────────────────


def test_mod_supports_update_check():
    assert mods.mod_supports_update_check(
        {"source": {"kind": "github_release"}}
    )
    assert not mods.mod_supports_update_check(
        {"source": {"kind": "direct_file"}}
    )


def test_mod_update_available_logic():
    mod = {"source": {"kind": "github_release"}}
    live = {"latest_version": "2.0"}
    assert mods.mod_update_available(
        mod,
        {"enabled": True, "installed_version": "1.0"},
        live,
    )
    assert not mods.mod_update_available(
        mod,
        {"enabled": True, "installed_version": "2.0"},
        live,
    )
    assert not mods.mod_update_available(
        mod,
        {"enabled": False, "installed_version": "1.0"},
        live,
    )


# ── dxvk conf (hook registry) ───────────────────────────────────────────────


def test_write_dxvk_conf(tmp_path):
    from nostalgia_launcher.services.sources import hooks

    client = tmp_path / "client"
    client.mkdir()
    written = hooks.run("write_dxvk_conf", str(client))
    assert written == ["dxvk.conf"]
    assert "d3d9.maxFrameLatency = 1" in (client / "dxvk.conf").read_text()


# ── install_mod (direct_file) ───────────────────────────────────────────────


def _patch_stream_download(monkeypatch, payload, target):
    """Route the direct_file streaming fetch's secure_urlopen to a fake
    response carrying ``payload`` (patched on the backend module). The fake
    yields the whole body on the first read() then EOF, matching a real
    streamed response."""
    monkeypatch.setattr(
        target, "secure_urlopen", fake_urlopen(payload, headers={})
    )


def test_install_mod_direct_file(tmp_path, monkeypatch):
    import nostalgia_launcher.services.sources.direct_file as df

    client = tmp_path / "client"
    client.mkdir()
    mod = {
        "id": "transmogfix",
        "source": {
            "kind": "direct_file",
            "url": "https://codeberg.org/x/transmogfix.dll",
            "dest": "transmogfix.dll",
            "pinned_version": "v0.7.0",
        },
    }
    payload = b"DLLDATA"
    _patch_stream_download(monkeypatch, payload, df)

    written = mods.install_mod(mod, str(client))
    assert written == ["transmogfix.dll"]
    assert (client / "transmogfix.dll").read_bytes() == payload
    assert mod["_resolved_version"] == "v0.7.0"


def test_install_mod_rejects_traversal_dest(tmp_path, monkeypatch):
    """A crafted dest (catalog-controlled) must not escape the client dir."""
    import nostalgia_launcher.services.sources.direct_file as df

    client = tmp_path / "client"
    client.mkdir()
    mod = {
        "id": "evil",
        "source": {
            "kind": "direct_file",
            "url": "https://codeberg.org/x/evil.dll",
            "dest": "../../evil.dll",
        },
    }
    payload = b"DLLDATA"
    _patch_stream_download(monkeypatch, payload, df)

    with pytest.raises(RuntimeError, match="unsafe install path"):
        mods.install_mod(mod, str(client))
    assert not (tmp_path / "evil.dll").exists()
    assert not (tmp_path.parent / "evil.dll").exists()
    assert list(client.iterdir()) == []


@pytest.mark.parametrize("asset_name", ["Release.7z", "Release.7Z"])
def test_install_mod_routes_7z_by_extension(tmp_path, monkeypatch, asset_name):
    """A .7z release asset deploys via extract_7z_map (case-insensitive),
    never the zip path — the wow-optimize v3.19.2 shape."""
    from nostalgia_launcher.services.sources.base import FetchResult

    client = tmp_path / "client"
    client.mkdir()
    mod = {
        "id": "wow-optimize",
        "source": {
            "kind": "github_release",
            "owner": "suprepupre",
            "repo": "wow-optimize",
            "asset_pattern": "Release.*",
            "extract_map": {"wow_optimize.dll": "wow_optimize.dll"},
        },
    }

    class _Backend:
        def fetch(self, entry, client_dir=None, release=None):
            return FetchResult(
                data=b"fake-7z", version="v3.19.2", name=asset_name
            )

    monkeypatch.setattr(mods, "_source_get", lambda kind: _Backend())

    def _no_zip(*a, **k):
        raise AssertionError("7z payload must not take the zip path")

    monkeypatch.setattr(deploy, "extract_zip_map", _no_zip)
    called = {}

    def _fake_7z(client_dir, data, emap):
        called["emap"] = emap
        return ["wow_optimize.dll"]

    monkeypatch.setattr(deploy, "extract_7z_map", _fake_7z)
    written = mods.install_mod(mod, str(client))
    assert written == ["wow_optimize.dll"]
    assert called["emap"] == {"wow_optimize.dll": "wow_optimize.dll"}
    assert mod["_resolved_version"] == "v3.19.2"


def test_checked_rel_rejects_traversal_and_absolute():
    assert deploy.checked_rel("mod/mod.dll") == "mod/mod.dll"
    for bad in (
        "../evil.dll",
        "a/../../evil.dll",
        "/abs/evil.dll",
        "C:\\evil.dll",
        "a\x00b.dll",
        "",
        None,
    ):
        with pytest.raises(RuntimeError, match="unsafe install path"):
            deploy.checked_rel(bad)


# ── self-update ─────────────────────────────────────────────────────────────


def test_updater_update_available():
    assert self_update.updater_update_available("v2.0.0")
    assert not self_update.updater_update_available("v0.0.0")
    assert not self_update.updater_update_available("")
    assert not self_update.updater_update_available("v0.0.0")


def test_fetch_updater_latest_tag_cached(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(
        {
            "updater_release_cache": {
                "timestamp": 9999999999,
                "tag": "v9.9.9",
                "version": self_update.UPDATER_VERSION,
            }
        }
    )

    def fail(*a, **k):
        raise AssertionError("cached result must not hit the network")

    monkeypatch.setattr(self_update, "secure_urlopen", fail)
    assert self_update.fetch_updater_latest_tag() == "v9.9.9"


def test_fetch_updater_latest_tag_stores_result(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})

    payload = json.dumps({"tag_name": "v3.0.0"}).encode()

    monkeypatch.setattr(self_update, "secure_urlopen", fake_urlopen(payload))

    assert self_update.fetch_updater_latest_tag() == "v3.0.0"
    cache = config_store.load_config()["updater_release_cache"]
    assert cache["tag"] == "v3.0.0"


def test_example_community_mods_catalog_validates():
    """The bundled community example catalog must load and pass the
    validator, so the example config's mods_registry_url stays usable."""
    import nostalgia_launcher.services.catalog as catalog

    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "examples",
        "community.mods.example.json",
    )
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    assert isinstance(raw, list)
    assert len(raw) == 2
    by_id = {e["id"]: e for e in raw}
    cleaned = catalog.validate_mod(by_id["example-tweak"])
    assert cleaned is not None
    assert cleaned["name"] == "ExampleTweak"
    assert cleaned["source"]["kind"] == "github_release"
    assert cleaned["source"]["owner"] == "example-community"
    assert cleaned["source"]["repo"] == "example-tweak"
    assert cleaned["register_dll"] == ["ExampleTweak.dll"]
    assert "installation" not in cleaned
    assert cleaned["client_versions"] == ["1.12.1"]
    launcher_mod = catalog.validate_mod(by_id["example-loader"])
    assert launcher_mod is not None
    assert launcher_mod["type"] == "external-launcher"
    assert launcher_mod["executable"] == "ExampleLoader.exe"
    assert "installation" not in launcher_mod


# ── external-launcher executables (opt-in only) ─────────────────────────────────────


def test_external_launcher_executables_state_matrix(tmp_path, monkeypatch):
    """Only enabled external-launcher mods contribute executables."""
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    client = tmp_path / "game"
    client.mkdir()
    (client / "Required.exe").write_bytes(b"MZ")
    (client / "OptIn.exe").write_bytes(b"MZ")
    registry = [
        {
            "id": "loader",
            "type": "external-launcher",
            "executable": "Required.exe",
        },
        {
            "id": "optin",
            "type": "external-launcher",
            "executable": "OptIn.exe",
        },
        {"id": "plain", "executable": "Ignored.exe"},
    ]
    monkeypatch.setattr(mods, "mods_registry", lambda *a, **k: registry)

    # Nothing enabled → no executables.
    config_store.save_config({"mods": {}})
    assert mods.external_launcher_executables(str(client)) == []

    # Opt-in enabled → in registry order.
    config_store.save_config(
        {
            "mods": {
                "loader": {"enabled": True},
                "optin": {"enabled": True},
            }
        }
    )
    assert mods.external_launcher_executables(str(client)) == [
        "Required.exe",
        "OptIn.exe",
    ]

    # Enabled mod whose executable is missing from disk → excluded.
    (client / "Required.exe").unlink()
    config_store.save_config({"mods": {"optin": {"enabled": True}}})
    assert mods.external_launcher_executables(str(client)) == ["OptIn.exe"]
