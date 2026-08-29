"""Unit tests for the assets engine (MPQ-style content patches)."""

import json

import pytest

import nostalgia_launcher.core.config_store as config_store
import nostalgia_launcher.services.assets as assets
import nostalgia_launcher.services.catalog as catalog
from nostalgia_launcher.core import launcher
from nostalgia_launcher.services.catalog import merge_assets, validate_asset

# ── entry validation ─────────────────────────────────────────────────────────


def _entry(**over):
    e = {
        "id": "patch3",
        "name": "Patch 3",
        "url": "https://server.test/uploads/patch-3.MPQ",
        "dest": "Data/patch-3.MPQ",
    }
    e.update(over)
    return e


def test_validate_asset_minimal():
    a = validate_asset(_entry())
    assert a is not None
    assert a["id"] == "patch3"
    assert a["dest"] == "Data/patch-3.MPQ"
    assert a["essential"] is False
    assert a["sha1"] is None and a["size"] is None
    assert a["version"] is None and a["probe"] is False


def test_validate_asset_full_metadata():
    a = validate_asset(
        _entry(
            sha1="A" * 40,  # upper-case accepted, normalized
            size=123,
            version="2026-08-01",
            essential=True,
            probe=True,
        )
    )
    assert a["sha1"] == "a" * 40
    assert a["size"] == 123
    assert a["version"] == "2026-08-01"
    assert a["probe"] is True


@pytest.mark.parametrize(
    "over",
    [
        {"url": "http://server.test/a.MPQ"},  # not https
        {"dest": "../evil.MPQ"},  # traversal
        {"dest": "/abs.MPQ"},  # absolute
        {"sha1": "zz" * 20},  # malformed pin → whole entry refused
        {"sha1": "abc"},  # too short
        {"size": -5},
        {"size": "big"},
        {"id": ""},
    ],
)
def test_validate_asset_rejects(over):
    assert validate_asset(_entry(**over)) is None


def test_validate_asset_rejects_empty():
    assert validate_asset({}) is None


def test_merge_assets_custom_overrides_remote_by_id():
    remote = [validate_asset(_entry(version="1"))]
    custom = [validate_asset(_entry(version="2", size=7))]
    merged = merge_assets(remote, custom)
    assert len(merged) == 1
    assert merged[0]["version"] == "2"
    assert merged[0]["size"] == 7


# ── launcher config parsing / allowlist ──────────────────────────────────────


def test_launcher_embeds_assets_and_extends_allowlist():
    launcher.configure_from_dict(
        {
            "server": {
                "name": "VP",
                "base_url": "https://vanilla.plus",
                "realm": "logon.vanilla.plus",
            },
            "assets": [
                {
                    "id": "p3",
                    "url": "https://dl.vanilla.plus/patch-3.MPQ",
                    "dest": "Data/patch-3.MPQ",
                }
            ],
        }
    )
    cfg = launcher.config()
    assert len(cfg.embedded_assets) == 1
    assert cfg.assets_registry_url == ""
    assert "dl.vanilla.plus" in cfg.download_hosts()


def test_launcher_assets_registry_url_explicit_only():
    launcher.configure_from_dict(
        {
            "server": {
                "base_url": "https://vanilla.plus",
                "assets_registry_url": "https://cdn.vanilla.plus/assets.json",
            }
        }
    )
    cfg = launcher.config()
    assert cfg.assets_registry_url == "https://cdn.vanilla.plus/assets.json"
    assert "cdn.vanilla.plus" in cfg.download_hosts()
    # No derived default: a plain server config has no asset registry.
    launcher.configure_from_dict({"server": {"base_url": "https://x.test"}})
    assert launcher.config().assets_registry_url == ""


# ── registry loading ─────────────────────────────────────────────────────────


def _isolated_config(tmp_path, config=None):
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(config or {})


def test_registry_embedded_offline_safe(tmp_path, monkeypatch):
    _isolated_config(tmp_path)
    launcher.configure_from_dict(
        {
            "server": {"base_url": "https://vanilla.plus"},
            "assets": [_entry()],
        }
    )

    def fail(*a, **k):
        raise AssertionError("embedded-only assets must not hit network")

    monkeypatch.setattr(catalog, "secure_urlopen", fail)
    reg = assets.assets_registry()
    assert [a["id"] for a in reg] == ["patch3"]
    assert assets.catalog_is_stale() is False
    assert assets.has_remote_catalog() is False


def test_registry_force_fetch_validates_and_caches(tmp_path, monkeypatch):
    _isolated_config(tmp_path)
    launcher.configure_from_dict(
        {
            "server": {
                "base_url": "https://vanilla.plus",
                "assets_registry_url": "https://vanilla.plus/assets.json",
            }
        }
    )
    payload = json.dumps(
        [_entry(), {"id": "bad", "url": "ftp://x", "dest": "x"}]
    ).encode()

    class _R:
        def __init__(self, data):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n=-1):
            out, self._data = self._data[:n], self._data[n:]
            return out

        headers = {}

    def fake_urlopen(req, **k):
        return _R(payload)

    monkeypatch.setattr(catalog, "secure_urlopen", fake_urlopen)
    got = assets.fetch_assets_catalog(force=True)
    assert [a["id"] for a in got] == ["patch3"]
    # Cached copy now serves non-forced loads without network.
    monkeypatch.setattr(
        assets,
        "secure_urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")),
    )
    assert assets.fetch_assets_catalog() == got
    assert [a["id"] for a in assets.assets_registry()] == ["patch3"]


# ── install / integrity ──────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, data, headers=None):
        self._data = data
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        out, self._data = self._data, b""
        return out


def _patch_download(monkeypatch, data, headers=None):
    seen = {}

    def fake(req, timeout=0, allowed_hosts=None):
        seen["url"] = req.full_url if hasattr(req, "full_url") else req
        return _FakeResponse(data, headers)

    import nostalgia_launcher.services.sources.direct_file as df

    monkeypatch.setattr(df, "secure_urlopen", fake)
    return seen


def test_install_asset_writes_file_and_records_probe_headers(
    tmp_path, monkeypatch
):
    _isolated_config(tmp_path)
    body = b"MPQ" * 10
    hdrs = {
        "ETag": '"abc"',
        "Last-Modified": "Mon, 24 Aug 2026 00:00:00 GMT",
        "Content-Length": str(len(body)),
    }
    _patch_download(monkeypatch, body, hdrs)
    client = tmp_path / "client"
    client.mkdir()
    asset = validate_asset(_entry(size=len(body)))
    result = assets.install_asset(asset, str(client))
    assert result["installed_files"] == ["Data/patch-3.MPQ"]
    assert (client / "Data" / "patch-3.MPQ").read_bytes() == body
    assert result["probe"]["etag"] == '"abc"'
    assert result["probe"]["last_modified"].startswith("Mon, 24 Aug 2026")
    assert result["probe"]["size"] == len(body)


def test_install_asset_sha1_mismatch_refuses(tmp_path, monkeypatch):
    _isolated_config(tmp_path)
    _patch_download(monkeypatch, b"corrupt")
    client = tmp_path / "client"
    client.mkdir()
    asset = validate_asset(_entry(sha1="a" * 40))
    with pytest.raises(RuntimeError, match="SHA-1 mismatch"):
        assets.install_asset(asset, str(client))
    # No truncated patch left behind (an empty Data dir is fine).
    assert not (client / "Data" / "patch-3.MPQ").exists()


def test_install_asset_size_mismatch_refuses(tmp_path, monkeypatch):
    _isolated_config(tmp_path)
    _patch_download(monkeypatch, b"short")
    client = tmp_path / "client"
    client.mkdir()
    asset = validate_asset(_entry(size=999))
    with pytest.raises(RuntimeError, match="expected"):
        assets.install_asset(asset, str(client))


def test_install_asset_unsafe_dest_refused(tmp_path, monkeypatch):
    import nostalgia_launcher.services.sources.direct_file as df

    def boom(*a, **k):
        raise AssertionError("must not download")

    monkeypatch.setattr(df, "secure_urlopen", boom)
    client = tmp_path / "client"
    client.mkdir()
    with pytest.raises(RuntimeError, match="unsafe install path"):
        assets.install_asset(
            {"dest": "../evil.MPQ", "url": "https://x.test/e"}, str(client)
        )


# ── staleness verdicts ───────────────────────────────────────────────────────


def _install_record(**over):
    rec = {
        "installed_version": None,
        "installed_files": ["Data/patch-3.MPQ"],
        "probe_state": {},
        "error": None,
    }
    rec.update(over)
    return rec


def test_verdict_no_metadata_never_stale(tmp_path):
    client = tmp_path / "c"
    (client / "Data").mkdir(parents=True)
    (client / "Data" / "patch-3.MPQ").write_bytes(b"data")
    asset = validate_asset(_entry())
    stale, _ = assets.asset_update_available(
        asset, _install_record(), str(client)
    )
    assert stale is False


def test_verdict_version_precedence(tmp_path):
    client = tmp_path / "c"
    (client / "Data").mkdir(parents=True)
    (client / "Data" / "patch-3.MPQ").write_bytes(b"data")
    asset = validate_asset(_entry(version="v2"))
    stale, why = assets.asset_update_available(
        asset, _install_record(installed_version="v1"), str(client)
    )
    assert stale and why == "server version changed"
    stale, _ = assets.asset_update_available(
        asset, _install_record(installed_version="v2"), str(client)
    )
    assert not stale


def test_verdict_sha1_pin(tmp_path):
    import hashlib

    client = tmp_path / "c"
    (client / "Data").mkdir(parents=True)
    digest = hashlib.sha1(b"data").hexdigest()
    path = client / "Data" / "patch-3.MPQ"
    path.write_bytes(b"data")
    asset = validate_asset(_entry(sha1=digest))
    stale, why = assets.asset_update_available(
        asset, _install_record(), str(client)
    )
    assert not stale
    path.write_bytes(b"tampered")
    stale, why = assets.asset_update_available(
        asset, _install_record(), str(client)
    )
    assert stale and why == "checksum changed"


def test_verdict_declared_size(tmp_path):
    client = tmp_path / "c"
    (client / "Data").mkdir(parents=True)
    path = client / "Data" / "patch-3.MPQ"
    path.write_bytes(b"1234")
    asset = validate_asset(_entry(size=4))
    stale, _ = assets.asset_update_available(
        asset, _install_record(), str(client)
    )
    assert not stale
    path.write_bytes(b"12345")
    stale, why = assets.asset_update_available(
        asset, _install_record(), str(client)
    )
    assert stale and why == "size changed"


def test_verdict_probe_compares_shared_headers_conservatively(
    tmp_path, monkeypatch
):
    client = tmp_path / "c"
    (client / "Data").mkdir(parents=True)
    (client / "Data" / "patch-3.MPQ").write_bytes(b"data")
    asset = validate_asset(_entry(probe=True))

    states = iter([])
    monkeypatch.setattr(
        assets, "remote_probe_state", lambda url: next(states, None)
    )

    # Probe failure (None) → never stale.
    states = iter([None])
    stale, _ = assets.asset_update_available(
        asset,
        _install_record(probe_state={"etag": '"a"', "size": 4}),
        str(client),
    )
    assert stale is False

    # Same etag, remote no longer sends a comparable size header → not
    # stale (only headers present on BOTH sides are compared).
    states = iter([{"etag": '"a"', "size": 999}])
    stale, _ = assets.asset_update_available(
        asset,
        _install_record(probe_state={"etag": '"a"'}),
        str(client),
    )
    assert stale is False

    states = iter([{"etag": '"b"'}])
    stale, why = assets.asset_update_available(
        asset,
        _install_record(probe_state={"etag": '"a"'}),
        str(client),
    )
    assert stale and why == "remote file changed"

    # No install-time snapshot → conservative not-stale.
    states = iter([{"etag": '"b"'}])
    stale, _ = assets.asset_update_available(
        asset, _install_record(probe_state={}), str(client)
    )
    assert stale is False


def test_verdict_uninstalled_asset_is_not_an_update(tmp_path):
    asset = validate_asset(_entry(version="v9"))
    stale, _ = assets.asset_update_available(
        asset, _install_record(installed_files=[]), str(tmp_path)
    )
    assert stale is False
    stale, _ = assets.asset_update_available(asset, None, str(tmp_path))
    assert stale is False


# ── probe state persistence ──────────────────────────────────────────────────


def test_probe_state_roundtrip_and_forget(tmp_path):
    _isolated_config(tmp_path)
    assets.remember_probe_state("p3", {"etag": '"e"'})
    assert config_store.load_config()["asset_probe_cache"]["p3"] == {
        "etag": '"e"'
    }
    assets.forget_probe_state("p3")
    assert "p3" not in config_store.load_config().get("asset_probe_cache", {})


def test_remove_asset_files(tmp_path):
    client = tmp_path / "c"
    (client / "Data").mkdir(parents=True)
    p = client / "Data" / "patch-3.MPQ"
    p.write_bytes(b"x")
    assets.remove_asset_files(["Data/patch-3.MPQ"], str(client))
    assert not p.exists()
    # Missing files are silently fine.
    assets.remove_asset_files(["Data/patch-3.MPQ"], str(client))


# ── resolved version ─────────────────────────────────────────────────────────


def test_resolved_version_precedence():
    assert (
        assets.resolved_version(validate_asset(_entry(version="v2"))) == "v2"
    )
    assert (
        assets.resolved_version(validate_asset(_entry(sha1="a" * 40)))
        == f"sha1:{'a' * 12}"
    )
    assert assets.resolved_version(validate_asset(_entry())) == "pinned"


def test_realmlist_writer(tmp_path, monkeypatch):
    from nostalgia_launcher.services.tweaks import write_realmlist_wtf

    client = tmp_path / "c"
    client.mkdir()
    write_realmlist_wtf(str(client))
    content = (client / "realmlist.wtf").read_text(encoding="utf-8")
    assert content.strip() == "SET realmlist launcher.test"


# ── local repo layer ─────────────────────────────────────────────────────────


def _asset(aid, name=None):
    return {
        "id": aid,
        "name": name or aid,
        "url": f"https://x.test/{aid}.mpq",
        "dest": f"Data/{aid}.mpq",
    }


def test_assets_registry_full_precedence(tmp_path, monkeypatch):
    """remote cache < repo.server < embedded < repo.custom < legacy."""
    from nostalgia_launcher.core import launcher
    from nostalgia_launcher.services import catalog as catalog_svc

    monkeypatch.setattr(
        launcher,
        "local_repo_path",
        lambda kind: str(tmp_path / f"local_{kind}_repo.json"),
    )
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(
        {
            "assets_catalog_cache": {
                "timestamp": 9999999999,
                "catalog": [_asset("X", "Remote")],
            }
        }
    )
    catalog_svc.write_local_repo(
        "assets",
        [_asset("X", "RepoServer")],
        [_asset("X", "RepoCustom")],
    )
    launcher.reset()
    launcher.configure_from_dict(
        {
            "server": {"base_url": "https://launcher.test"},
            "assets": [_asset("X", "Embedded")],
        }
    )
    reg = {a["id"]: a["name"] for a in assets.assets_registry()}
    assert reg["X"] == "RepoCustom"


def test_catalog_is_stale_false_with_repo_content_only(tmp_path, monkeypatch):
    from nostalgia_launcher.core import launcher
    from nostalgia_launcher.services import catalog as catalog_svc

    monkeypatch.setattr(
        launcher,
        "local_repo_path",
        lambda kind: str(tmp_path / f"local_{kind}_repo.json"),
    )
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    monkeypatch.setattr(launcher, "assets_registry_url", lambda: "")
    catalog_svc.write_local_repo("assets", [_asset("Local")], [])
    assert not assets.has_remote_catalog()
    assert assets.catalog_is_stale() is False


def test_remove_asset_files_skips_unsafe_recorded_paths(tmp_path):
    """Recorded paths are bookkeeping data: a tampered state file entry
    like "../important.doc" must never be joined onto the client dir."""
    outside = tmp_path / "important.doc"
    outside.write_bytes(b"x")
    client = tmp_path / "client"
    (client / "Data").mkdir(parents=True)
    inside = client / "Data" / "patch.mpq"
    inside.write_bytes(b"x")

    assets.remove_asset_files(
        ["../important.doc", "Data/patch.mpq", 42],
        str(client),
    )
    assert outside.exists()
    assert not inside.exists()


def test_verdict_probe_skipped_when_not_allowed(monkeypatch, tmp_path):
    """allow_probe=False must short-circuit before any network call — the
    GUI-thread render path relies on that."""
    calls = []

    def fail_probe(url):
        calls.append(url)
        return None

    monkeypatch.setattr(assets, "remote_probe_state", fail_probe)
    asset = {"id": "a", "url": "https://x.test/a.mpq", "probe": True}
    rec = {
        "installed_files": [str(tmp_path / "a.mpq")],
        "probe_state": {"size": 1},
    }
    stale, _reason = assets.asset_update_available(
        asset, rec, str(tmp_path), allow_probe=False
    )
    assert stale is False
    assert calls == []
