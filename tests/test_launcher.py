"""Unit tests for the launcher configuration (core/launcher)."""

import json

import pytest

from vanilla_wow_launcher.core import launcher


@pytest.fixture(autouse=True)
def _clean():
    launcher.reset()
    yield
    launcher.reset()


def _config(data):
    return launcher.configure_from_dict(data)


def test_derives_endpoints_from_base_url():
    cfg = _config({"server": {"name": "My", "base_url": "https://srv.example"}})
    assert cfg is not None
    assert cfg.server_url == "https://srv.example"
    assert cfg.news_url == (
        "https://srv.example/forum/octonews.php?mode=list&forum=2&limit=8")
    assert cfg.featured_news_url == (
        "https://srv.example/forum/octonews.php?forum=35&mode=full")
    assert cfg.mods_registry_url == "https://srv.example/api/mods.json"
    assert cfg.addons_registry_url == "https://srv.example/api/addons.json"
    assert cfg.realm == "srv.example"


def test_endpoint_overrides_and_realm():
    cfg = _config({"server": {
        "base_url": "https://srv.example",
        "realm": "realms.example",
        "news_url": "https://other.example/news",
        "mods_registry_url": "https://other.example/mods.json",
    }})
    assert cfg.news_url == "https://other.example/news"
    assert cfg.mods_registry_url == "https://other.example/mods.json"
    assert cfg.realm == "realms.example"


def test_missing_base_url_is_error():
    assert _config({"server": {"realm": "x"}}) is None
    assert "base_url" in launcher.config_error()
    assert _config({}) is None


def test_rejects_non_https_server():
    assert _config({"server": {"base_url": "http://insecure.example"}}) is None


def test_mirrors_parsed_with_default_endpoints():
    cfg = _config({"server": {"base_url": "https://srv.example"},
                   "mirrors": [
                       {"name": "Backup", "base_url": "https://m1.example"},
                       {"name": "Second",
                        "base_url": "https://m2.example",
                        "manifest_url": "https://m2.example/custom/manifest.json"},
                       {"name": "skip-http", "base_url": "http://nope.example"},
                   ]})
    assert len(cfg.mirrors) == 2
    assert cfg.mirrors[0].name == "Backup"
    assert cfg.mirrors[0].base_url == "https://m1.example"
    assert cfg.mirrors[0].manifest_url == (
        "https://m1.example/api/file/latest/manifest.json")
    assert cfg.mirrors[1].manifest_url == (
        "https://m2.example/custom/manifest.json")


def test_download_hosts_cover_server_and_mirrors():
    cfg = _config({"server": {"base_url": "https://srv.example"},
                   "mirrors": [{"base_url": "https://m1.example"}]})
    assert cfg.download_hosts() == {"srv.example", "m1.example"}


def test_configure_from_file(tmp_path):
    path = tmp_path / "vanilla_wow_launcher.json"
    path.write_text(json.dumps(
        {"server": {"base_url": "https://file.example"}}), encoding="utf-8")
    cfg, err = launcher.configure(str(path))
    assert err == ""
    assert cfg.server_url == "https://file.example"


def test_configure_invalid_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    cfg, err = launcher.configure(str(path))
    assert cfg is None
    assert "Invalid launcher configuration" in err


def test_configure_missing_returns_error():
    cfg, err = launcher.configure("")
    assert cfg is None
    assert "required" in err


def test_validate_path_valid(tmp_path):
    path = tmp_path / "vanilla_wow_launcher.json"
    path.write_text(json.dumps(
        {"server": {"base_url": "https://launcher.test"}}), encoding="utf-8")
    config, err = launcher.validate_path(str(path))
    assert err == ""
    assert config is not None
    assert config.server_url == "https://launcher.test"


def test_validate_path_missing_file(tmp_path):
    config, err = launcher.validate_path(str(tmp_path / "nope.json"))
    assert config is None
    assert err


def test_validate_path_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(b"not json")
    config, err = launcher.validate_path(str(path))
    assert config is None
    assert err


def test_validate_path_does_not_touch_active_config(tmp_path):
    launcher.configure_from_dict(
        {"server": {"base_url": "https://launcher.test"}})
    before = launcher.config()
    assert before is not None
    invalid = tmp_path / "bad.json"
    invalid.write_bytes(b"not json")
    config, err = launcher.validate_path(str(invalid))
    assert config is None
    assert err
    assert launcher.config() is before


def test_accessors_empty_when_not_configured():
    launcher.reset()
    assert launcher.server_url() == ""
    assert launcher.news_url() == ""
    assert launcher.mods_registry_url() == ""
    assert launcher.mirrors() == []
    assert launcher.is_configured() is False
