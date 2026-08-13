"""Unit tests for the addons engine."""

import io
import json
import os
import zipfile

import pytest

import addons
import config_store


def test_is_allowed_git_url():
    assert addons.is_allowed_git_url("https://github.com/a/b")
    assert addons.is_allowed_git_url("https://gitlab.com/a/b")
    assert addons.is_allowed_git_url("https://codeberg.org/a/b")
    assert not addons.is_allowed_git_url("http://github.com/a/b")
    assert not addons.is_allowed_git_url("https://evil.com/a/b")
    assert not addons.is_allowed_git_url("https://github.com.evil.com/a/b")
    assert not addons.is_allowed_git_url("not a url")


def test_git_parts_github():
    kind, repo_url, owner, repo, api = addons._git_parts(
        "https://github.com/Otari98/_LazyPig")
    assert kind == "github"
    assert owner == "Otari98"
    assert repo == "_LazyPig"
    assert api == "https://api.github.com"


def test_git_parts_strips_git_suffix():
    _k, repo_url, owner, repo, _api = addons._git_parts(
        "https://github.com/a/repo.git")
    assert (owner, repo) == ("a", "repo")


def test_git_parts_gitlab():
    kind, repo_url, _o, _r, api = addons._git_parts("https://gitlab.com/a/b")
    assert kind == "gitlab"
    assert api == "https://gitlab.com/api/v4"


def test_addon_zip_url_github():
    url = addons.addon_zip_url(
        "https://github.com/a/b", "abc123" * 6)
    assert url == "https://github.com/a/b/archive/abc123abc123abc123abc123abc123abc123.zip"


def test_addon_zip_url_gitlab():
    url = addons.addon_zip_url(
        "https://gitlab.com/a/b", "abc123")
    assert url == "https://gitlab.com/a/b/-/archive/abc123/b-abc123.zip"


def test_read_toc_file(tmp_path):
    toc_path = tmp_path / "x.toc"
    toc_path.write_text(
        "## Title: My Addon\n## Notes: Great\n## Interface: 11400\n", encoding="utf-8")
    toc = addons.read_toc_file(str(toc_path))
    assert toc["Title"] == "My Addon"
    assert toc["Interface"] == "11400"


def test_read_toc_file_missing(tmp_path):
    assert addons.read_toc_file(str(tmp_path / "nope.toc")) == {}


def test_fetch_addons_catalog_cached(tmp_path, monkeypatch):
    config_store.configure(str(tmp_path / "config.json"), str(tmp_path / "cache.json"))
    catalog = [{"name": "pfUI", "git": "https://github.com/brues-code/pfUI",
                "toc": {"Title": "pfUI", "Notes": "n", "Extra": "skip"}}]
    config_store.save_config({"addons_catalog_cache": {
        "timestamp": 9999999999, "catalog": catalog}})

    def fail(*a, **k):
        raise AssertionError("cached catalog must not hit the network")

    monkeypatch.setattr(addons, "secure_urlopen", fail)
    assert addons.fetch_addons_catalog() == catalog


def test_fetch_addons_catalog_slims_and_stores(tmp_path, monkeypatch):
    config_store.configure(str(tmp_path / "config.json"), str(tmp_path / "cache.json"))
    config_store.save_config({})
    raw = [{"name": "pfUI", "git": "https://github.com/brues-code/pfUI",
            "branch": "master", "ref": None, "description": "d",
            "toc": {"Title": "pfUI", "Notes": "n", "Extra": "skip"}}]
    payload = json.dumps(raw).encode()
    monkeypatch.setattr(
        addons, "secure_urlopen",
        lambda *a, **k: type("R", (), {"__enter__": lambda s: s,
                                       "__exit__": lambda *x: False,
                                       "read": lambda s=0: payload})())

    out = addons.fetch_addons_catalog()
    assert out[0]["toc"] == {"Title": "pfUI", "Notes": "n"}
    assert "Extra" not in out[0]["toc"]


def test_addon_remote_sha_cached(tmp_path, monkeypatch):
    config_store.configure(str(tmp_path / "config.json"), str(tmp_path / "cache.json"))
    key = "https://github.com/a/b#"
    config_store.save_config({"addon_sha_cache": {
        key: {"timestamp": 9999999999, "sha": "f" * 40}}})

    def fail(*a, **k):
        raise AssertionError("cached sha must not hit the network")

    monkeypatch.setattr(addons, "_api_json", fail)
    assert addons.addon_remote_sha("https://github.com/a/b") == "f" * 40


def test_addon_remote_sha_resolves_and_caches(tmp_path, monkeypatch):
    config_store.configure(str(tmp_path / "config.json"), str(tmp_path / "cache.json"))
    config_store.save_config({})
    sha = "abcd" * 10

    def fake_api_json(url, timeout=10):
        return {"sha": sha} if "/commits/" in url else [{"sha": sha}]

    monkeypatch.setattr(addons, "_api_json", fake_api_json)
    assert addons.addon_remote_sha("https://github.com/a/b") == sha
    assert addons.addon_cached_sha("https://github.com/a/b") == sha


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_install_addon_files_extracts(tmp_path, monkeypatch):
    client = tmp_path / "client"
    payload = _zip_bytes({
        "pfUI-master/pfUI.toc": "## Title: pfUI\n",
        "pfUI-master/lib/x.lua": "-- lib",
    })
    monkeypatch.setattr(
        addons, "secure_urlopen",
        lambda *a, **k: type("R", (), {"__enter__": lambda s: s,
                                       "__exit__": lambda *x: False,
                                       "read": lambda s=0: payload})())

    addons.install_addon_files(str(client), "pfUI",
                               "https://github.com/brues-code/pfUI",
                               "abcd" * 10)
    base = client / "Interface" / "AddOns" / "pfUI"
    assert (base / "pfUI.toc").exists()
    assert (base / "lib" / "x.lua").exists()
    assert not (client / "Interface" / "AddOns" / "pfUI.tmp_install").exists()


def test_install_addon_files_path_traversal_safe(tmp_path, monkeypatch):
    client = tmp_path / "client"
    payload = _zip_bytes({
        "x-master/ok.txt": "ok",
        "x-master/../../escape.txt": "evil",
    })
    monkeypatch.setattr(
        addons, "secure_urlopen",
        lambda *a, **k: type("R", (), {"__enter__": lambda s: s,
                                       "__exit__": lambda *x: False,
                                       "read": lambda s=0: payload})())

    addons.install_addon_files(str(client), "x",
                               "https://github.com/a/x", "abcd" * 10)
    assert (client / "Interface" / "AddOns" / "x" / "ok.txt").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_patch_pfui_installs_profile(tmp_path):
    client = tmp_path / "client"
    base = client / "Interface" / "AddOns" / "pfUI"
    (base / "env").mkdir(parents=True)
    (base / "modules").mkdir(parents=True)
    (base / "env" / "profiles.lua").write_text(
        'pfUI_profiles = {}\n', encoding="utf-8")

    addons.patch_pfui_default_profile(str(client))
    content = (base / "env" / "profiles.lua").read_text(encoding="utf-8")
    assert addons._PFUI_MARK_BEGIN in content
    assert 'pfUI_profiles["Default"]' in content

    # Idempotent: re-applying must not duplicate the block.
    addons.patch_pfui_default_profile(str(client))
    content2 = (base / "env" / "profiles.lua").read_text(encoding="utf-8")
    assert content2.count(addons._PFUI_MARK_BEGIN) == 1


def test_patch_pfui_missing_profile_returns_gracefully(tmp_path):
    client = tmp_path / "client"
    addons.patch_pfui_default_profile(str(client))  # no pfUI installed
