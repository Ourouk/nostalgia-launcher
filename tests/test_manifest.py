"""Manifest engine tests: parse/routing/bundled entries (network-free)."""

import pytest

from nostalgia_launcher.services import manifest as _manifest


def _schema():
    return {
        "files": "files",
        "version": "contentVersion",
        "base_url": "filesBaseUrl",
        "entry": {"path": "path", "size": "size", "sha256": "sha256"},
        "download_url": "{base_url}{path}",
    }


def _doc():
    return {
        "contentVersion": "1.1.8",
        "filesBaseUrl": "https://updates.example.com/files/",
        "files": [
            {
                "path": "Data/patch-A.MPQ",
                "size": 100,
                "sha256": "a" * 64,
            },
            {
                "path": "EpochConnection.dll",
                "size": 10,
                "sha256": "b" * 64,
            },
            {"path": "Wow.exe", "size": 20, "sha256": ""},
        ],
    }


def test_parse_manifest_extracts_entries():
    version, entries = _manifest.parse_manifest(_doc(), _schema())
    assert version == "1.1.8"
    assert len(entries) == 3
    assert entries[0]["download_url"] == (
        "https://updates.example.com/files/Data/patch-A.MPQ"
    )
    assert entries[0]["size"] == 100


def test_parse_manifest_rejects_traversal():
    doc = _doc()
    doc["files"] = [{"path": "../evil", "size": 1, "sha256": "c" * 64}]
    with pytest.raises(ValueError):
        _manifest.parse_manifest(doc, _schema())


def test_parse_manifest_rejects_bad_hash():
    doc = _doc()
    doc["files"] = [{"path": "a/b", "size": 1, "sha256": "zzz"}]
    with pytest.raises(ValueError):
        _manifest.parse_manifest(doc, _schema())


def test_parse_manifest_rejects_non_https_base():
    doc = _doc()
    doc["filesBaseUrl"] = "http://updates.example.com/files/"
    with pytest.raises(ValueError):
        _manifest.parse_manifest(doc, _schema())


def test_route_entry_first_match_wins():
    routes = [
        {"match": "Data/*.MPQ", "as": "asset"},
        {"match": "Data/*", "as": "mod"},
    ]
    assert _manifest.route_entry("Data/patch-A.MPQ", routes)["as"] == "asset"
    assert _manifest.route_entry("other.txt", routes) is None


def test_stale_overlay_entries_missing_only(tmp_path):
    existing = tmp_path / "Data"
    existing.mkdir()
    (existing / "patch-A.MPQ").write_bytes(b"x")
    entries = [
        {"path": "Data/patch-A.MPQ"},
        {"path": "Data/patch-B.MPQ"},
    ]
    assert _manifest.stale_overlay_entries(str(tmp_path), entries) == [
        "Data/patch-B.MPQ"
    ]


def test_verify_manifest_version_bump_refreshes_all_listed(
    tmp_path, monkeypatch
):
    """A moved version cursor refreshes every manifest-listed file, not
    just the missing ones — existence alone can't clear a versioned
    overlay entry."""
    import nostalgia_launcher.core.config_store as config_store
    import nostalgia_launcher.services.update.workflow as w
    from nostalgia_launcher.state.events import (
        EventDispatcher,
        TorrentDiffReady,
    )

    client = tmp_path / "client"
    client.mkdir()
    (client / "Data").mkdir()
    (client / "Data" / "a.MPQ").write_bytes(b"old-bytes")
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    worker = w.VerifyWorker(
        str(client),
        EventDispatcher(),
        source=w.DownloadSource(None, ""),
    )
    monkeypatch.setattr(
        _manifest, "_manifest_block", lambda: {"url": "https://u/m"}
    )
    monkeypatch.setattr(
        _manifest,
        "fetch_snapshot",
        lambda **k: {
            "version": "2",
            "url": "https://u/m",
            "entries": [
                {"path": "Data/a.MPQ"},
                {"path": "EpochConnection.dll"},
            ],
        },
    )
    worker.run()
    diffs = [
        e
        for e in worker._dispatcher.drain()
        if isinstance(e, TorrentDiffReady)
    ]
    assert diffs, "expected a manifest verdict"
    assert diffs[0].stale == [
        "manifest:Data/a.MPQ",
        "manifest:EpochConnection.dll",
    ]


def test_verify_manifest_same_version_only_missing(tmp_path, monkeypatch):
    """Same cursor → only missing files go stale."""
    import nostalgia_launcher.core.config_store as config_store
    import nostalgia_launcher.services.update.workflow as w
    from nostalgia_launcher.state.events import (
        EventDispatcher,
        TorrentDiffReady,
    )

    client = tmp_path / "client"
    client.mkdir()
    (client / "Data").mkdir()
    (client / "Data" / "a.MPQ").write_bytes(b"old-bytes")
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(
        {
            "__manifest_installed__": {
                "version": "2",
                "url": "https://u/m",
            }
        }
    )
    worker = w.VerifyWorker(
        str(client),
        EventDispatcher(),
        source=w.DownloadSource(None, ""),
    )
    monkeypatch.setattr(
        _manifest, "_manifest_block", lambda: {"url": "https://u/m"}
    )
    monkeypatch.setattr(
        _manifest,
        "fetch_snapshot",
        lambda **k: {
            "version": "2",
            "url": "https://u/m",
            "entries": [
                {"path": "Data/a.MPQ"},
                {"path": "EpochConnection.dll"},
            ],
        },
    )
    worker.run()
    diffs = [
        e
        for e in worker._dispatcher.drain()
        if isinstance(e, TorrentDiffReady)
    ]
    assert diffs
    assert diffs[0].stale == ["manifest:EpochConnection.dll"]
