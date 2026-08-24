"""Unit tests for the mods engine and self-update checks."""

import json
import os

import pytest

import nostalgia_launcher.core.config_store as config_store
import nostalgia_launcher.services.mods as mods
import nostalgia_launcher.services.self_update as self_update
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

    monkeypatch.setattr(mods, "secure_urlopen", fail)
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

    monkeypatch.setattr(mods, "secure_urlopen", fail)
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

    class _R:
        def __init__(self, data):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n=-1):
            data = self._data
            self._data = b""
            return data

    monkeypatch.setattr(mods, "secure_urlopen", lambda *a, **k: _R(payload))

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

    monkeypatch.setattr(mods, "secure_urlopen", fail)
    with pytest.raises(ConnectionError):
        mods.fetch_mods_catalog(force=True)


def test_mods_registry_merges_custom(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    monkeypatch.setattr(
        mods.catalog, "custom_file", lambda kind: str(tmp_path / "custom.json")
    )
    (tmp_path / "custom.json").write_text(
        '[{"id": "MyMod", "name": "My Mod", "essential": true,'
        ' "source": {"kind": "github_release", "owner": "a", "repo": "b",'
        ' "asset_pattern": "*.zip"}}]',
        encoding="utf-8",
    )

    reg = mods.mods_registry()
    by_id = {m["id"]: m for m in reg}
    assert by_id["MyMod"]["essential"] is True
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
    config_store.save_config({})
    _configure_embedded([_EMB_VALID, {"id": "Bad", "source": {}}])

    def fail(*a, **k):
        raise AssertionError("embedded-only registry must not hit network")

    monkeypatch.setattr(mods, "secure_urlopen", fail)
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
    monkeypatch.setattr(
        mods.catalog, "custom_file", lambda kind: str(tmp_path / "custom.json")
    )
    (tmp_path / "custom.json").write_text(
        '[{"id": "M", "name": "Custom", "source": {"kind": "github_release",'
        ' "owner": "a", "repo": "b", "asset_pattern": "*.zip"}}]',
        encoding="utf-8",
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


def test_has_remote_catalog_user_override(tmp_path):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    # The conftest launcher config only derives a default URL — that does
    # not count as a configured catalog.
    assert mods.has_remote_catalog() is False
    assert mods.set_registry_url("https://mine.example/mods.json") is None
    assert mods.has_remote_catalog() is True


def test_reload_catalog_republishes_when_embedded_only(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    _configure_embedded([_EMB_VALID])

    def fail(*a, **k):
        raise AssertionError("embedded-only reload must not hit the network")

    monkeypatch.setattr(mods, "secure_urlopen", fail)
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
        {"name": "vanillafixes-1.0-dxvk.zip"},
        {"name": "vanillafixes-1.0.zip"},
    ]
    assert (
        pick_asset(assets, "vanillafixes-*.zip", "-dxvk")["name"]
        == "vanillafixes-1.0.zip"
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
    mod = {"id": "m", "installed_files": ["a.dll"], "register_dll": "a.dll"}
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
    mod = {"id": "m", "register_dll": "m.dll"}
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
    registry = [{"id": "t", "register_dll": "Tracked.dll"}]
    assert mods.scan_unknown_mods(str(client), registry) == ["mystery.dll"]


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

    class _R:
        headers = {}

        def __init__(self):
            self._data = payload

        def __enter__(self):
            return self

        def __exit__(self, *x):
            return False

        def read(self, n=-1):
            out, self._data = self._data[:n], self._data[n:]
            return out

    monkeypatch.setattr(target, "secure_urlopen", lambda *a, **k: _R())


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


def test_checked_rel_rejects_traversal_and_absolute():
    assert mods._checked_rel("mod/mod.dll") == "mod/mod.dll"
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
            mods._checked_rel(bad)


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
    monkeypatch.setattr(
        self_update,
        "secure_urlopen",
        lambda *a, **k: type(
            "R",
            (),
            {
                "__enter__": lambda s: s,
                "__exit__": lambda *x: False,
                "read": lambda s=0: payload,
            },
        )(),
    )

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
    assert len(raw) == 1
    cleaned = catalog.validate_mod(raw[0])
    assert cleaned is not None
    assert cleaned["id"] == "example-tweak"
    assert cleaned["name"] == "ExampleTweak"
    assert cleaned["source"]["kind"] == "github_release"
    assert cleaned["source"]["owner"] == "example-community"
    assert cleaned["source"]["repo"] == "example-tweak"
    assert cleaned["register_dll"] == "ExampleTweak.dll"


# ── ModsController.apply_essential_mods ─────────────────────────────────────


def test_apply_essential_mods_toggles_missing_and_applies(
    tmp_path, monkeypatch
):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(
        {"mods": {"EssentialA": {"installed_version": "1.0"}}}
    )
    game = tmp_path / "game"
    game.mkdir()
    (game / "WoW.exe").write_bytes(b"MZ")
    registry = [
        {"id": "EssentialA", "essential": True, "name": "A"},
        {"id": "EssentialB", "essential": True, "name": "B"},
        {"id": "Optional", "essential": False, "name": "O"},
    ]
    monkeypatch.setattr(mods, "mods_registry", lambda *a, **k: registry)
    monkeypatch.setattr(
        mods,
        "mod_installed_files_present",
        lambda m, cd: m["id"] == "EssentialA",
    )

    applied = []
    controller = ModsController(
        EventDispatcher(), get_out_dir=lambda: str(game)
    )
    monkeypatch.setattr(
        controller, "apply", lambda *a, **k: (applied.append(1), True)[1]
    )

    assert controller.apply_essential_mods() is True
    # EssentialB (missing) toggled on; EssentialA (present) skipped; Optional
    # is not essential so never considered.
    assert controller.state.pending["EssentialB"].enabled is True
    assert "EssentialA" not in controller.state.pending
    assert "Optional" not in controller.state.pending
    assert applied == [1]


def test_apply_essential_mods_noop_when_all_present(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(
        {"mods": {"EssentialA": {"installed_version": "1.0"}}}
    )
    game = tmp_path / "game"
    game.mkdir()
    (game / "WoW.exe").write_bytes(b"MZ")
    registry = [{"id": "EssentialA", "essential": True, "name": "A"}]
    monkeypatch.setattr(mods, "mods_registry", lambda *a, **k: registry)
    monkeypatch.setattr(
        mods, "mod_installed_files_present", lambda m, cd: True
    )

    applied = []
    controller = ModsController(
        EventDispatcher(), get_out_dir=lambda: str(game)
    )
    monkeypatch.setattr(controller, "apply", lambda *a, **k: applied.append(1))

    assert controller.apply_essential_mods() is False
    assert applied == []


def test_apply_essential_mods_skips_without_client(tmp_path, monkeypatch):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    registry = [{"id": "EssentialA", "essential": True, "name": "A"}]
    monkeypatch.setattr(mods, "mods_registry", lambda *a, **k: registry)

    controller = ModsController(
        EventDispatcher(), get_out_dir=lambda: str(tmp_path)
    )
    monkeypatch.setattr(controller, "apply", lambda *a, **k: None)

    assert controller.apply_essential_mods() is False


def test_catalog_is_stale_branches(monkeypatch):
    import nostalgia_launcher.services.catalog as catalog
    from nostalgia_launcher.services import mods as mods_svc

    now = 1_000_000_000.0
    week = catalog.CATALOG_TTL
    cases = {
        None: True,  # never fetched
        now - week + 60: False,  # fresh (just under the TTL)
        now - week - 60: True,  # older than the weekly TTL
    }
    for ts, expected in cases.items():
        entry = {} if ts is None else {"timestamp": ts, "catalog": []}
        monkeypatch.setattr(
            mods_svc, "load_config", lambda e=entry: {"mods_catalog_cache": e}
        )
        assert mods_svc.catalog_is_stale(now=now) is expected, ts


def test_catalog_timestamp_roundtrip(monkeypatch):
    from nostalgia_launcher.services import mods as mods_svc

    monkeypatch.setattr(mods_svc, "load_config", lambda: {})
    assert mods_svc.catalog_timestamp() is None
    monkeypatch.setattr(
        mods_svc,
        "load_config",
        lambda: {"mods_catalog_cache": {"timestamp": 123.5, "catalog": []}},
    )
    assert mods_svc.catalog_timestamp() == 123.5


# ── local repo layer ─────────────────────────────────────────────────────────


def _mod(mid, name=None):
    return {
        "id": mid,
        "name": name or mid,
        "source": {
            "kind": "direct_file",
            "url": f"https://x.test/{mid}.dll",
            "dest": f"{mid}.dll",
        },
    }


@pytest.fixture
def repo_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(
        launcher,
        "local_repo_path",
        lambda kind: str(tmp_path / f"local_{kind}_repo.json"),
    )
    monkeypatch.setattr(
        launcher,
        "legacy_custom_path",
        lambda kind: str(tmp_path / f"legacy_{kind}.json"),
    )
    return tmp_path


def test_mods_registry_full_precedence(tmp_path, repo_paths, monkeypatch):
    """remote < repo.server < embedded < repo.custom < legacy custom."""
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(
        {
            "mods_catalog_cache": {
                "timestamp": 9999999999,
                "catalog": [_mod("X", "Remote"), _mod("OnlyRemote")],
            }
        }
    )
    catalog_svc = __import__(
        "nostalgia_launcher.services.catalog", fromlist=["catalog"]
    )
    catalog_svc.write_local_repo(
        "mods",
        [_mod("X", "RepoServer"), _mod("OnlyRepoServer")],
        [_mod("X", "RepoCustom")],
    )
    launcher.reset()
    launcher.configure_from_dict(
        {
            "server": {"base_url": "https://launcher.test"},
            "mods": [_mod("X", "Embedded")],
        }
    )

    def fail(*a, **k):
        raise AssertionError("cached registry must not hit the network")

    monkeypatch.setattr(mods, "secure_urlopen", fail)
    reg = {m["id"]: m["name"] for m in mods.mods_registry()}
    assert reg["X"] == "RepoCustom"
    assert reg["OnlyRemote"] == "OnlyRemote"
    assert reg["OnlyRepoServer"] == "OnlyRepoServer"


def test_catalog_is_stale_false_with_repo_content_only(tmp_path, repo_paths):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    catalog_svc = __import__(
        "nostalgia_launcher.services.catalog", fromlist=["catalog"]
    )
    catalog_svc.write_local_repo("mods", [_mod("Local")], [])
    assert not mods.has_remote_catalog()
    assert mods.catalog_is_stale() is False
