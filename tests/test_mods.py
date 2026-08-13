"""Unit tests for the mods engine and self-update checks."""

import json
import os

import pytest

import octo_updater.services.mods as mods
import octo_updater.services.self_update as self_update
import octo_updater.core.config_store as config_store


# ── registry ────────────────────────────────────────────────────────────────

def test_registry_order_vanillafixes_first():
    assert mods.MODS_REGISTRY[0]["id"] == "VanillaFixes"


def test_registry_entries_have_required_fields():
    for mod in mods.MODS_REGISTRY:
        assert mod["id"]
        assert mod["name"]
        assert mod["source"]["kind"] in (
            "github_release", "codeberg_release", "direct_file", "direct_tar")
        assert "installed_files" in mod


# ── asset selection / versions ───────────────────────────────────────────────

def test_pick_asset_matches_pattern_and_prefers_without_suffix():
    assets = [
        {"name": "vanillafixes-1.0-dxvk.zip"},
        {"name": "vanillafixes-1.0.zip"},
    ]
    assert mods._pick_asset(assets, "vanillafixes-*.zip", "-dxvk")["name"] == \
        "vanillafixes-1.0.zip"


def test_pick_asset_returns_none_without_match():
    assert mods._pick_asset([{"name": "x.dll"}], "*.zip", None) is None


def test_release_version_uses_asset_when_version_from_asset():
    rel = {"tag_name": "Release", "assets": [
        {"name": "SuperWoW 2.2.zip"},
    ]}
    mod = {"source": {"version_from": "asset",
                      "asset_pattern": "SuperWoW*.zip", "prefer_no": None}}
    assert mods._release_version(mod, rel) == "2.2"


def test_release_version_defaults_to_tag():
    rel = {"tag_name": "v1.2.3", "assets": []}
    mod = {"source": {}}
    assert mods._release_version(mod, rel) == "v1.2.3"


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


# ── update detection ─────────────────────────────────────────────────────────

def test_mod_supports_update_check():
    assert mods.mod_supports_update_check(
        {"source": {"kind": "github_release"}})
    assert not mods.mod_supports_update_check(
        {"source": {"kind": "direct_file"}})


def test_mod_update_available_logic():
    mod = {"source": {"kind": "github_release"}}
    live = {"latest_version": "2.0"}
    assert mods.mod_update_available(
        mod, {"enabled": True, "installed_version": "1.0",
              "ignore_updates": False}, live)
    assert not mods.mod_update_available(
        mod, {"enabled": True, "installed_version": "2.0",
              "ignore_updates": False}, live)
    assert not mods.mod_update_available(
        mod, {"enabled": False, "installed_version": "1.0",
              "ignore_updates": False}, live)
    assert not mods.mod_update_available(
        mod, {"enabled": True, "installed_version": "1.0",
              "ignore_updates": True}, live)


# ── dxvk conf ───────────────────────────────────────────────────────────────

def test_write_dxvk_conf(tmp_path):
    client = tmp_path / "client"
    client.mkdir()
    mods._write_dxvk_conf(str(client))
    assert (client / "dxvk.conf").exists()
    assert "d3d9.maxFrameLatency = 1" in (client / "dxvk.conf").read_text()


# ── install_mod (direct_file) ───────────────────────────────────────────────

def test_install_mod_direct_file(tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        mods, "secure_urlopen",
        lambda *a, **k: type("R", (), {"__enter__": lambda s: s,
                                       "__exit__": lambda *x: False,
                                       "read": lambda s=0: payload})())

    written = mods.install_mod(mod, str(client))
    assert written == ["transmogfix.dll"]
    assert (client / "transmogfix.dll").read_bytes() == payload
    assert mod["_resolved_version"] == "v0.7.0"


# ── self-update ─────────────────────────────────────────────────────────────

def test_updater_update_available():
    assert self_update.updater_update_available("v2.0.0")
    assert not self_update.updater_update_available("v1.1")
    assert not self_update.updater_update_available("")
    assert not self_update.updater_update_available("v1.2.0")


def test_fetch_updater_latest_tag_cached(tmp_path, monkeypatch):
    config_store.configure(str(tmp_path / "config.json"), str(tmp_path / "cache.json"))
    config_store.save_config({"updater_release_cache": {
        "timestamp": 9999999999, "tag": "v9.9.9"}})

    def fail(*a, **k):
        raise AssertionError("cached result must not hit the network")

    monkeypatch.setattr(self_update, "secure_urlopen", fail)
    assert self_update.fetch_updater_latest_tag() == "v9.9.9"


def test_fetch_updater_latest_tag_stores_result(tmp_path, monkeypatch):
    config_store.configure(str(tmp_path / "config.json"), str(tmp_path / "cache.json"))
    config_store.save_config({})

    payload = json.dumps({"tag_name": "v3.0.0"}).encode()
    monkeypatch.setattr(
        self_update, "secure_urlopen",
        lambda *a, **k: type("R", (), {"__enter__": lambda s: s,
                                       "__exit__": lambda *x: False,
                                       "read": lambda s=0: payload})())

    assert self_update.fetch_updater_latest_tag() == "v3.0.0"
    cache = config_store.load_config()["updater_release_cache"]
    assert cache["tag"] == "v3.0.0"
